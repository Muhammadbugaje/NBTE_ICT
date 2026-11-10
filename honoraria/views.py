"""My profile -> Bank details (X33). Owner only; blocked while "acting as"."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from core import reauth

from . import services
from .models import Bank


def _login_required_either_host(view):
    """Like login_required, but an anonymous visitor on the QA portal host is
    sent to the portal sign-in (the internal "accounts:login" does not exist
    in the portal URLconf, so stock login_required would raise there)."""
    from functools import wraps

    from django.contrib.auth.views import redirect_to_login

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated and getattr(request, "is_qa_portal", False):
            return redirect_to_login(request.get_full_path(),
                                     login_url=reverse("qa_portal:login", urlconf="qa_core.portal_urls"))
        return login_required(view)(request, *args, **kwargs)
    return wrapped


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _context(request, *, form_errors=None, form_values=None, notice=None, error=None, revealed=None,
             show_form=False):
    user = request.user
    account = services.active_account(user)
    return {
        "account": services.summary(account),
        "banks": services.active_banks(),
        "bank_url": _bank_url(request),
        "is_qa_portal": bool(getattr(request, "is_qa_portal", False)),
        "history": services.history_for_owner(user),
        "form_errors": form_errors or {},
        "form_values": form_values or {},
        "notice": notice, "error": error, "revealed": revealed,
        "show_form": show_form or account is None,
        "reconfirm_days": services.reconfirm_days(),
    }


def _bank_url(request):
    if getattr(request, "is_qa_portal", False):
        return reverse("qa_portal:honoraria:my_bank", urlconf="qa_core.portal_urls")
    return reverse("honoraria:my_bank")


def _with_portal_shell(request, section, context):
    if not getattr(request, "is_qa_portal", False):
        return context
    from qa_core.portal_views import _shell_context

    return _shell_context(request, section, **context)


def _blocked(request, exc):
    template = "honoraria/_bank_blocked.html" if _is_htmx(request) else "honoraria/my_bank_blocked.html"
    context = {"message": str(exc)}
    if not _is_htmx(request):
        context = _with_portal_shell(request, "profile", context)
    return render(request, template, context, status=403)


def _render(request, status=200, **ctx):
    template = "honoraria/_bank_panel.html" if _is_htmx(request) else "honoraria/my_bank.html"
    context = _context(request, **ctx)
    if not _is_htmx(request):
        context = _with_portal_shell(request, "profile", context)
    return render(request, template, context, status=status)


@_login_required_either_host
@require_http_methods(["GET", "POST"])
def my_bank(request):
    try:
        services.assert_owner_request(request)
    except services.NotOwner as exc:
        return _blocked(request, exc)
    if request.method == "GET":
        return _render(request, show_form=request.GET.get("change") == "1")

    action = request.POST.get("action", "save")
    if action == "confirm":
        try:
            services.confirm_correct(request)
        except ValidationError as exc:
            return _render(request, status=400, error=" ".join(exc.messages))
        if not _is_htmx(request):
            messages.success(request, "Thank you. Your bank details are confirmed.")
            return redirect(reverse("honoraria:my_bank"))
        return _render(request, notice="Thank you. Your bank details are confirmed.")

    if action == "reveal":
        account = services.active_account(request.user)
        if account is None:
            return _render(request, status=400, error="No bank details yet.")
        try:
            number = services.reveal_for_owner(request, account, request.POST.get("password", ""))
        except reauth.ReauthRequired as exc:
            return _render(request, status=403, error=exc.message)
        return _render(request, revealed=number)

    # save
    values = {"bank": request.POST.get("bank", ""), "account_number": request.POST.get("account_number", ""),
              "account_name": request.POST.get("account_name", "")}
    bank = Bank.objects.filter(pk=values["bank"] or 0, is_active=True).first() if values["bank"].isdigit() else None
    try:
        services.save_for_owner(request, bank=bank, account_number=values["account_number"],
                                account_name=values["account_name"], password=request.POST.get("password", ""))
    except reauth.ReauthRequired as exc:
        return _render(request, status=403, error=exc.message, form_values=values, show_form=True)
    except ValidationError as exc:
        errors = exc.message_dict if hasattr(exc, "error_dict") else {"general": exc.messages}
        return _render(request, status=400, form_errors={("general" if k == "__all__" else k): " ".join(v)
                                                         for k, v in errors.items()},
                       form_values=values, show_form=True)
    if not _is_htmx(request):
        messages.success(request, "Bank details saved. We sent you an e-mail about the change.")
        return redirect(reverse("honoraria:my_bank"))
    return _render(request, notice="Bank details saved. We sent you an e-mail about the change.")


@_login_required_either_host
@require_POST
def banner_confirm(request):
    """The site-wide banner's "These are correct" button."""
    try:
        services.confirm_correct(request)
    except (services.NotOwner, ValidationError):
        return redirect(reverse("honoraria:my_bank"))
    messages.success(request, "Thank you. Your bank details are confirmed.")
    nxt = request.POST.get("next") or "/"
    from core.urlsafe import safe_next

    return redirect(safe_next(request, nxt, "/"))


# ===========================================================================
# v2.1 X32/X34: honoraria payment schedules
# ===========================================================================
import datetime as _dt
from decimal import Decimal

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.core.exceptions import PermissionDenied
from django.db.models import Sum

from . import schedules as sched
from .models import HonorariumEntry


def _date(raw):
    try:
        return _dt.date.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None


SCHEDULE_FILTERS = [("all", "All"), ("draft", "Awaiting approval"), ("approved", "Approved"), ("paid", "Paid"),
                    ("cancelled", "Withdrawn")]


def _schedule_list_extras(request, schedules):
    """v2.3: totals, filter pills with counts and the filtered list."""
    rows = list(schedules)
    counts = {key: 0 for key, _ in SCHEDULE_FILTERS}
    counts["all"] = len(rows)
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    show = request.GET.get("show") or "all"
    if show not in counts:
        show = "all"
    total = {status: sum((r.total for r in rows if r.status == status), Decimal("0"))
             for status in ("draft", "approved", "paid")}
    return {"schedules": [r for r in rows if show == "all" or r.status == show], "show": show,
            "filters": [(k, label, counts[k]) for k, label in SCHEDULE_FILTERS], "totals": total,
            "can_view_all": sched.can_view_all(request.user)}


@login_required
def schedule_list(request):
    schedules = sched.schedules_for(request.user).select_related("approved_by", "honorarium_file__institution")
    can_build = sched.can_approve(request.user)
    return render(request, "honoraria/schedules.html", {
        **_schedule_list_extras(request, schedules),
        "can_build": can_build,
        "can_view_report": sched.can_view_finance_report(request.user),
        "today": _dt.date.today(),
        "period_start": request.GET.get("from") or "",
        "period_end": request.GET.get("to") or "",
        "error": request.GET.get("error", ""),
    })


def _is_file_pay_officer(user, schedule):
    from . import files

    return files.is_pay_officer(user, schedule.honorarium_file)


@login_required
def schedule_detail(request, pk):
    schedule = get_object_or_404(sched.PaymentSchedule.objects.all(), pk=pk)
    lines = sched.lines_for(request.user, schedule)
    if (not sched.can_view_all(request.user) and schedule.honorarium_file_id
            and _is_file_pay_officer(request.user, schedule)):
        lines = schedule.lines.select_related("user", "resource_person")  # v2.3: masked; no payment details
    if not sched.can_view_all(request.user) and not lines.exists():
        raise Http404
    is_finance_director = sched.can_pay(request.user)
    can_pay = False
    details_by_line = {}
    missing_accounts = []
    funding_accounts = []
    if is_finance_director and schedule.status == "approved":
        unpaid_lines = [line for line in lines if line.paid_at is None]
        details_by_line, missing_accounts = sched.payment_details(
            schedule, user=request.user, lines=unpaid_lines)
        for line in lines:
            line.payment_details = details_by_line.get(line.pk)
            line.calculated_amount = line.rate * line.quantity
        from payments.models import BankAccount

        funding_accounts = BankAccount.objects.filter(
            is_active=True, is_placeholder=False).order_by("bank_name", "account_name")
        can_pay = bool(details_by_line and funding_accounts.exists())
    for line in lines:
        line.can_confirm = bool(
            line.paid_at and (line.user_id == request.user.pk
                              or getattr(line.resource_person, "user_id", None) == request.user.pk))
    paid_line_count = sum(1 for line in lines if line.paid_at is not None)
    response = render(request, "honoraria/schedule_detail.html", {
        "schedule": schedule, "lines": lines,
        "can_approve": sched.can_approve(request.user) and schedule.status == "draft",
        "can_pay": can_pay,
        "is_finance_director": is_finance_director,
        "has_lines": lines.exists(),
        "missing_accounts": missing_accounts,
        "funding_accounts": funding_accounts,
        "can_confirm": paid_line_count > 0,
        "paid_line_count": paid_line_count,
        "line_count": len(lines),
        "error": request.GET.get("error", ""),
    })
    if details_by_line:
        response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def schedule_build(request):
    start = _date(request.POST.get("period_start"))
    end = _date(request.POST.get("period_end"))
    try:
        schedule = sched.build_schedule(start, end, user=request.user, title=request.POST.get("title", ""))
    except ValidationError as exc:
        return render(request, "honoraria/schedules.html", {
            **_schedule_list_extras(request, sched.schedules_for(request.user)),
            "can_build": sched.can_approve(request.user),
            "today": _dt.date.today(), "period_start": request.POST.get("period_start", ""),
            "period_end": request.POST.get("period_end", ""), "error": " ".join(exc.messages),
        }, status=422)
    except Exception as exc:  # PermissionDenied and missing reference formats
        messages.error(request, str(exc))
        return redirect("honoraria_sched:list")
    return redirect("honoraria_sched:detail", pk=schedule.pk)


@login_required
@require_POST
def schedule_approve(request, pk):
    schedule = get_object_or_404(sched.PaymentSchedule.objects.all(), pk=pk)
    try:
        sched.approve(schedule, user=request.user)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("honoraria_sched:detail", pk=schedule.pk)


@login_required
@require_POST
def schedule_pay(request, pk):
    schedule = get_object_or_404(sched.PaymentSchedule.objects.all(), pk=pk)
    try:
        sched.mark_paid(
            schedule, user=request.user,
            funding_account_id=request.POST.get("funding_account"),
            payment_reference=request.POST.get("payment_reference"),
            line_ids=request.POST.getlist("lines"),
            amounts={key.removeprefix("amount_"): value for key, value in request.POST.items()
                     if key.startswith("amount_")},
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, str(exc))
    return redirect("honoraria_sched:detail", pk=schedule.pk)


@login_required
@require_POST
def line_confirm(request, pk):
    line = get_object_or_404(sched.PaymentScheduleLine.objects.select_related("schedule"), pk=pk)
    try:
        sched.confirm_receipt(line, user=request.user)
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("honoraria_sched:detail", pk=line.schedule_id)


INCOME_PRESETS = [("month", "This month"), ("last_month", "Last month"), ("quarter", "This quarter"),
                  ("year", "This year"), ("12m", "Last 12 months")]


def _preset_range(key, today):
    first = today.replace(day=1)
    if key == "last_month":
        end = first - _dt.timedelta(days=1)
        return end.replace(day=1), end
    if key == "quarter":
        return today.replace(month=3 * ((today.month - 1) // 3) + 1, day=1), today
    if key == "year":
        return today.replace(month=1, day=1), today
    if key == "12m":
        y, m = (today.year - 1, today.month + 1) if today.month < 12 else (today.year, 1)
        return _dt.date(y, m, 1), today
    return first, today


def _months_back(end, count):
    y, m = end.year, end.month
    out = []
    for _ in range(count):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _monthly_money(end, bank_account_id=None, count=12):
    """Income (confirmed fees) and expenditure (honoraria paid) per month,
    for the ``count`` months ending with ``end``'s month."""
    from django.db.models.functions import TruncMonth
    from payments.models import PaymentObligation

    months = _months_back(end, count)
    start = _dt.date(months[0][0], months[0][1], 1)
    income_qs = PaymentObligation.unscoped.filter(confirmed_at__date__gte=start, confirmed_at__date__lte=end)
    spend_qs = sched.PaymentScheduleLine.objects.filter(paid_at__date__gte=start, paid_at__date__lte=end,
                                                        schedule__status__in=("approved", "paid"))
    if bank_account_id is not None:
        income_qs = income_qs.filter(fee_schedule__revenue_account_id=bank_account_id)
        spend_qs = spend_qs.filter(funding_account_id=bank_account_id)
    inc = {(r["m"].year, r["m"].month): r["t"] for r in income_qs.annotate(m=TruncMonth("confirmed_at"))
           .values("m").annotate(t=Sum("amount_paid")) if r["m"]}
    out = {(r["m"].year, r["m"].month): r["t"] for r in spend_qs.annotate(m=TruncMonth("paid_at"))
           .values("m").annotate(t=Sum("amount")) if r["m"]}
    return [{"month": _dt.date(y, m, 1).strftime("%b %y"), "income": float(inc.get((y, m)) or 0),
             "spend": float(out.get((y, m)) or 0)} for y, m in months]


def _activity_of(line):
    from .files import STAGE_LABELS

    stages = {fl.stage for fl in line.file_lines.all()}
    if stages:
        return STAGE_LABELS[sorted(stages)[0]]
    kinds = {entry.get_kind_display() for entry in line.entries.all()}
    if kinds:
        return sorted(kinds)[0]
    claims = list(line.service_record_claims.all())
    if claims:
        record = claims[0].service_record
        return "Visit (resource person)" if record.visit_count else "SSQ review"
    return line.get_source_display()


@login_required
def income_report(request):
    if not sched.can_view_finance_report(request.user):
        raise Http404
    today = _dt.date.today()
    preset = request.GET.get("range") or ""
    if preset in dict(INCOME_PRESETS):
        start, end = _preset_range(preset, today)
    else:
        start = _date(request.GET.get("from")) or today.replace(day=1)
        end = _date(request.GET.get("to")) or today
        preset = "" if (request.GET.get("from") or request.GET.get("to")) else "month"
    if end < start:
        start, end = end, start
    from django.db.models import Q
    from payments.models import BankAccount

    accounts = (BankAccount.objects.filter(
        Q(is_active=True, is_placeholder=False)
        | Q(honoraria_schedules__paid_at__isnull=False)
        | Q(honoraria_lines__paid_at__isnull=False)
        | Q(fee_schedules__obligations__confirmed_at__isnull=False))
        .distinct().order_by("bank_name", "account_name"))
    raw_account = str(request.GET.get("bank") or "").strip()
    selected_account = None
    if raw_account:
        if not raw_account.isdigit():
            raise Http404
        selected_account = accounts.filter(pk=int(raw_account)).first()
        if selected_account is None:
            raise Http404
    bank_id = selected_account.pk if selected_account else None
    report = sched.income_expenditure(start, end, bank_account_id=bank_id)
    from django.db.models import prefetch_related_objects

    prefetch_related_objects(report["paid_lines"], "file_lines")
    by_activity = {}
    for line in report["paid_lines"]:
        label = _activity_of(line)
        by_activity[label] = by_activity.get(label, Decimal("0")) + line.amount
    activities = sorted(by_activity.items(), key=lambda kv: -kv[1])
    income, spend = report["income"], report["expenditure"]
    flow = income + spend
    payees = len({(line.resource_person_id, line.user_id) for line in report["paid_lines"]})
    confirmed = sum(1 for line in report["paid_lines"] if line.confirmed_at)
    charts = [
        {"id": "money-12m", "type": "money", "data": _monthly_money(end, bank_id)},
        {"id": "income-fee", "type": "doughnut",
         "data": [{"label": r["label"], "value": float(r["amount"])} for r in report["income_by_fee"] if r["amount"]],
         "colors": ["#1f6b3e", "#c8962a", "#2f7cf6", "#13a37c", "#9a4fd0", "#0aa2c4", "#f08a00", "#e5484d"]},
        {"id": "spend-activity", "type": "bar",
         "data": [{"label": k, "value": float(v)} for k, v in activities],
         "colors": ["#c8962a", "#2f7cf6", "#9a4fd0", "#13a37c", "#0aa2c4", "#f08a00"]},
    ]
    return render(request, "honoraria/income_report.html", {
        "report": report, "from": start, "to": end, "funding_accounts": accounts,
        "selected_account": selected_account, "presets": INCOME_PRESETS, "preset": preset, "charts": charts,
        "activities": activities, "payees": payees, "confirmed": confirmed,
        "income_share": int(income * 100 / flow) if flow else 50,
        "spend_share": (100 - int(income * 100 / flow)) if flow else 50,
        "cover": (income / spend).quantize(Decimal("0.1")) if spend else None,
        "can_view_all": sched.can_view_all(request.user),
    })


@_login_required_either_host
@require_http_methods(["GET", "POST"])
def my_earnings(request):
    if request.method == "POST":
        if request.POST.get("line"):
            line = get_object_or_404(sched.payee_lines_for(request.user), pk=request.POST.get("line"))
            try:
                sched.confirm_receipt(line, user=request.user)
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
            else:
                messages.success(request, "Receipt confirmed.")
            return redirect("honoraria_earnings:list")
        entry = get_object_or_404(sched.earnings_for(request.user), pk=request.POST.get("entry"))
        try:
            sched.confirm_entry_received(entry, user=request.user)
        except (PermissionDenied, ValidationError) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
        else:
            messages.success(request, "Receipt confirmed.")
        return redirect("honoraria_earnings:list")

    entries = sched.earnings_for(request.user)
    schedule_lines = list(sched.earning_lines_for(request.user))
    totals = {
        status: entries.filter(status=status).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        for status, _label in HonorariumEntry.STATUS_CHOICES
    }
    totals[HonorariumEntry.ACCRUED] = (
        entries.filter(status=HonorariumEntry.ACCRUED).aggregate(total=Sum("amount"))["total"] or Decimal("0"))
    totals[HonorariumEntry.SCHEDULED] = sum(
        (line.amount for line in schedule_lines if line.paid_at is None), Decimal("0"))
    totals[HonorariumEntry.PAID] = sum(
        (line.amount for line in schedule_lines if line.paid_at and not line.confirmed_at), Decimal("0"))
    totals[HonorariumEntry.RECEIVED] = sum(
        (line.amount for line in schedule_lines if line.confirmed_at), Decimal("0"))
    is_resource_person = getattr(request.user, "user_type", "") == "resource_person"
    unclaimed_records = []
    records_by_line = {}
    if is_resource_person:
        unclaimed_records = sched.unclaimed_resource_person_records(request.user)
        rate = sched.academic_rate()
        totals[HonorariumEntry.ACCRUED] = (
            sum(row.visit_count for row in unclaimed_records) * rate)
        for record in unclaimed_records:
            record.estimated_amount = record.visit_count * rate
        visible_lines = list(schedule_lines)
        from .models import HonorariumServiceRecordClaim

        claims = HonorariumServiceRecordClaim.objects.filter(
            line__in=visible_lines).select_related(
                "line", "service_record__programme", "service_record__institution")
        for claim in claims:
            records_by_line.setdefault(claim.line_id, []).append(claim.service_record)
        for line in visible_lines:
            line.work_records = records_by_line.get(line.pk, [])
        schedule_lines = visible_lines
        totals[HonorariumEntry.SCHEDULED] = sum(
            (line.amount for line in schedule_lines if line.paid_at is None), Decimal("0"))
        totals[HonorariumEntry.PAID] = sum(
            (line.amount for line in schedule_lines if line.paid_at and not line.confirmed_at), Decimal("0"))
        totals[HonorariumEntry.RECEIVED] = sum(
            (line.amount for line in schedule_lines if line.confirmed_at), Decimal("0"))
    for line in schedule_lines:
        line.can_confirm = bool(
            line.paid_at and (line.user_id == request.user.pk
                              or getattr(line.resource_person, "user_id", None) == request.user.pk))
    context = {
        "entries": entries, "totals": totals,
        "schedule_lines": schedule_lines,
        "unclaimed_records": unclaimed_records, "is_resource_person": is_resource_person,
        "bank_url": _bank_url(request),
    }
    return render(request, "honoraria/my_earnings.html",
                  _with_portal_shell(request, "earnings", context))


# ---------------------------------------------------------------------------
# v2.2 — the payment roller ("who is next to be paid")
# ---------------------------------------------------------------------------
@login_required
def roller(request):
    from . import finance_view

    if not sched.can_view_all(request.user):
        raise Http404
    rows = finance_view.roller()
    recent = (sched.PaymentScheduleLine.objects.filter(paid_at__isnull=False)
              .select_related("schedule", "paid_by").order_by("-paid_at")[:15])
    return render(request, "honoraria/roller.html", {
        "rows": rows, "can_pay": sched.can_pay(request.user),
        "can_view_bank": finance_view.can_view_bank_details(request.user, request),
        "total_owed": sum((r["amount"] for r in rows), Decimal("0")),
        "missing_bank": sum(1 for r in rows if not r["bank_ok"]), "recent": recent,
    })


@login_required
@require_POST
def roller_prepare(request):
    """Build a draft schedule for the payees Finance ticked (in roller order)."""
    if not sched.can_approve(request.user):
        raise PermissionDenied
    keys = []
    for raw in request.POST.getlist("payee"):
        kind, _, pk = raw.partition(":")
        if kind in ("user", "rp") and pk.isdigit():
            keys.append((kind, int(pk)))
    if not keys:
        messages.error(request, "Tick at least one person to pay.")
        return redirect("honoraria_sched:roller")
    try:
        schedule = sched.build_schedule(_dt.date(2000, 1, 1), _dt.date.today(), user=request.user,
                                        title=request.POST.get("title") or f"Honoraria {_dt.date.today():%d %b %Y}",
                                        payee_keys=keys)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("honoraria_sched:roller")
    messages.success(request, f"Draft {schedule.reference} prepared for {len(keys)} payee(s). Approve it, then pay.")
    return redirect("honoraria_sched:detail", pk=schedule.pk)


@login_required
@require_POST
def roller_bank(request):
    """Show one payee's full bank details to the DoF / ES / super admin (logged)."""
    from inspection.models import ResourcePerson
    from accounts.models import User

    from . import finance_view

    if not finance_view.can_view_bank_details(request.user, request):
        raise PermissionDenied
    kind, _, pk = (request.POST.get("payee") or "").partition(":")
    if not pk.isdigit():
        raise Http404
    if kind == "rp":
        rp = get_object_or_404(ResourcePerson, pk=int(pk))
        acc = finance_view.account_for(resource_person=rp)
    else:
        acc = finance_view.account_for(user=get_object_or_404(User, pk=int(pk)))
    if acc is None:
        details = None
    else:
        details = finance_view.reveal_for_finance(request, acc, reason=request.POST.get("reason", ""))
    response = render(request, "honoraria/_roller_bank.html", {"details": details})
    response["Cache-Control"] = "no-store, private"
    return response
