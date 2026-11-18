"""v2.3/v2.4: honorarium files (one per institution) — list, detail, prepare,
DoF review -> ES approval -> DoF payment, pay officer, printable/CSV payment
list. Bank numbers are masked here; the DoF releases full details only for the
payment list (logged). The ES and super admins can reveal one through
``views.roller_bank`` (logged)."""
from __future__ import annotations

import csv
import datetime as _dt
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import files as hf
from . import schedules as sched
from .finance_view import can_view_bank_details
from .models import HonorariumFile, HonorariumFileLine, PaymentSchedule, PaymentScheduleLine

FL = HonorariumFileLine

FILTERS = [("all", "All files"), ("ready", "Ready to pay"), ("approval", "Awaiting approval"),
           ("confirm", "Awaiting confirmation"), ("settled", "Settled")]


def _errors(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _visible_files(user):
    qs = HonorariumFile.objects.select_related("institution", "pay_officer")
    if sched.can_view_all(user):
        return qs
    row = hf.standing_officer()
    if row is not None and row.officer_id == user.pk:
        return qs.filter(Q(pay_officer__isnull=True) | Q(pay_officer=user))
    return qs.filter(pay_officer=user)


def _bucket(summary):
    c = summary["counts"]
    if c[FL.PENDING]:
        return "ready"
    if c[FL.PREPARED] or c[FL.APPROVED]:
        return "approval"
    if c[FL.PAID]:
        return "confirm"
    return "settled"


def _float(v):
    return float(v or 0)


@login_required
def files_list(request):
    if not hf.can_view_files(request.user):
        raise Http404
    if sched.can_view_all(request.user):
        hf.sync_unfiled()  # brings in work completed before files existed (idempotent)
    files = list(_visible_files(request.user).prefetch_related("lines"))
    open_batches = {}
    for s in (PaymentSchedule.objects.filter(
            honorarium_file__in=files, status__in=("draft", "awaiting_es", "approved"))
            .select_related("created_by", "sent_to_es_by").order_by("-created_at")):
        s.paid_count = s.lines.filter(paid_at__isnull=False).count()
        s.unpaid_count = s.lines.filter(paid_at__isnull=True).count()
        open_batches.setdefault(s.honorarium_file_id, []).append(s)
    for f in files:
        f.summary = hf.summary(f, f.lines.all())
        f.bucket = _bucket(f.summary)
        f.steps = hf.steps(f.summary["phase"])
        f.batches = open_batches.get(f.pk, [])
        f.drafts = [b for b in f.batches if b.status == "draft"]
        f.with_es = [b for b in f.batches if b.status == "awaiting_es"]
        f.to_pay = [b for b in f.batches if b.status == "approved" and b.unpaid_count]
        f.officer = hf.officer_for(f)
    counts = {key: 0 for key, _ in FILTERS}
    counts["all"] = len(files)
    for f in files:
        counts[f.bucket] += 1
    show = request.GET.get("show") or "all"
    if show not in counts:
        show = "all"
    order = {"approval": 0, "ready": 1, "confirm": 2, "settled": 3}
    files.sort(key=lambda f: (order[f.bucket], -f.summary["outstanding"], f.institution.name))
    shown = [f for f in files if show == "all" or f.bucket == show]

    owed_by_stage = {code: Decimal("0") for code, _ in FL.STAGE_CHOICES}
    for f in files:
        for st in f.summary["stages"]:
            owed_by_stage[st["code"]] += st["owed"]
    totals = {
        "outstanding": sum((f.summary["outstanding"] for f in files), Decimal("0")),
        "ready": sum((f.summary["pending"] for f in files), Decimal("0")),
        "approval": sum((f.summary["prepared"] for f in files), Decimal("0")),
        "confirm": sum((f.summary["paid"] for f in files), Decimal("0")),
        "people": len({line.payee_key for f in files for line in f.lines.all() if line.status in FL.OUTSTANDING}),
    }
    year_start = timezone.localdate().replace(month=1, day=1)
    totals["paid_year"] = (PaymentScheduleLine.objects.filter(paid_at__date__gte=year_start)
                           .aggregate(t=Sum("amount"))["t"] or Decimal("0"))
    is_dof = hf.is_dof(request.user)
    awaiting = [s for f in files for s in f.batches]
    charts = [
        {"id": "owed-stage", "type": "doughnut",
         "data": [{"label": hf.STAGE_LABELS[k], "value": _float(v)} for k, v in owed_by_stage.items() if v],
         "colors": ["#1f6b3e", "#c8962a", "#2f7cf6"]},
        {"id": "paid-month", "type": "bar", "data": _paid_by_month(5), "colors": ["#13a37c"]},
    ]
    return render(request, "honoraria/files.html", {
        "files": shown, "filters": [(k, label, counts[k]) for k, label in FILTERS], "show": show,
        "totals": totals, "is_dof": is_dof, "awaiting": awaiting,
        "standing": hf.standing_officer(), "candidates": hf.pay_officer_candidates() if is_dof else [],
        "can_view_all": sched.can_view_all(request.user), "charts": charts,
        "owed_by_stage": [(hf.STAGE_LABELS[k], v) for k, v in owed_by_stage.items()],
    })


def _paid_by_month(months):
    today = timezone.localdate()
    first = (today.replace(day=1) - _dt.timedelta(days=31 * (months - 1))).replace(day=1)
    rows = (PaymentScheduleLine.objects.filter(paid_at__date__gte=first)
            .annotate(m=TruncMonth("paid_at")).values("m").annotate(t=Sum("amount")))
    by = {(r["m"].year, r["m"].month): r["t"] for r in rows if r["m"]}
    out = []
    y, m = first.year, first.month
    for _ in range(months):
        out.append({"label": _dt.date(y, m, 1).strftime("%b"), "value": _float(by.get((y, m)))})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


@login_required
def file_detail(request, pk):
    file = get_object_or_404(HonorariumFile.objects.select_related("institution", "pay_officer"), pk=pk)
    if not hf.can_view_file(request.user, file):
        raise Http404
    hf.reconcile(file)
    lines = list(file.lines.select_related("payee_user", "resource_person", "schedule_line__schedule", "case")
                 .order_by("stage", "name", "pk"))
    hf.bank_status(lines)
    summary = hf.summary(file, lines)
    can_prepare = hf.can_prepare(request.user, file)
    for line in lines:
        line.selectable = can_prepare and line.status == FL.PENDING and line.bank_ok

    # v2.4: filter the people the DoF will pay (stage, status, name/work/bank).
    stage_filter = request.GET.get("stage") or "all"
    status_filter = request.GET.get("status") or "all"
    query = " ".join((request.GET.get("q") or "").split())
    if stage_filter not in dict(FL.STAGE_CHOICES):
        stage_filter = "all"
    if status_filter not in dict(FL.STATUS_CHOICES):
        status_filter = "all"

    def matches(line):
        if stage_filter != "all" and line.stage != stage_filter:
            return False
        if status_filter != "all" and line.status != status_filter:
            return False
        if query:
            haystack = " ".join(filter(None, [line.name, line.role, line.work_item,
                                              line.case.reference if line.case_id else ""])).lower()
            if query.lower() not in haystack:
                return False
        return True

    shown_lines = [line for line in lines if matches(line)]
    status_counts = {code: 0 for code, _ in FL.STATUS_CHOICES}
    stage_counts = {code: 0 for code, _ in FL.STAGE_CHOICES}
    for line in lines:
        status_counts[line.status] = status_counts.get(line.status, 0) + 1
        stage_counts[line.stage] = stage_counts.get(line.stage, 0) + 1
    stages = summary["stages"]
    for st in stages:
        st["lines"] = [line for line in shown_lines if line.stage == st["code"] and line.status != FL.VOIDED]
        st["selectable"] = sum(1 for line in st["lines"] if line.selectable)
        st["shown"] = len(st["lines"])
        st["all"] = stage_counts.get(st["code"], 0)

    batches = list(file.batches.exclude(status="cancelled").select_related(
        "created_by", "sent_to_es_by", "es_decided_by", "approved_by", "funding_account").order_by("-created_at"))
    for b in batches:
        b.line_count = b.lines.count()
        b.confirmed = b.lines.filter(confirmed_at__isnull=False).count()
        b.paid_count = b.lines.filter(paid_at__isnull=False).count()
        b.unpaid_count = b.lines.filter(paid_at__isnull=True).count()
        b.disputed_count = b.lines.filter(disputed_at__isnull=False).count()
        b.can_send = hf.can_send_to_es(request.user, b)
        b.can_es_decide = hf.can_es_decide(request.user, b)
    is_dof = hf.is_dof(request.user)
    drafts = [b for b in batches if b.status == "draft"]
    with_es = [b for b in batches if b.status == "awaiting_es"]
    to_pay = [b for b in batches if b.status == "approved" and b.unpaid_count]
    from payments.models import BankAccount

    funding_accounts = list(BankAccount.objects.filter(is_active=True, is_placeholder=False)
                            .order_by("bank_name", "account_name"))
    ready = [line for line in lines if line.status == FL.PENDING]
    ready_ok = [line for line in ready if line.bank_ok]
    awaiting_confirm = [line for line in lines if line.status == FL.PAID]
    disputes = [line for line in lines if line.schedule_line_id and line.schedule_line.disputed_at]
    can_decide_es = any(hf.can_es_decide(request.user, b) for b in with_es)
    if is_dof and drafts:
        nxt = {"kind": "send_es", "batch": drafts[0]}
    elif with_es:
        nxt = {"kind": "es_decide" if can_decide_es else "awaiting_es", "batch": with_es[0]}
    elif is_dof and to_pay:
        nxt = {"kind": "pay", "batch": to_pay[0]}
    elif can_prepare and ready_ok:
        nxt = {"kind": "prepare", "count": len(ready_ok), "total": sum((x.amount for x in ready_ok), Decimal("0"))}
    elif drafts:
        nxt = {"kind": "waiting_dof", "batch": drafts[0]}
    elif ready and not ready_ok:
        nxt = {"kind": "bank", "count": len(ready)}
    elif awaiting_confirm:
        nxt = {"kind": "confirm", "count": len({x.payee_key for x in awaiting_confirm})}
    elif lines:
        nxt = {"kind": "done"}
    else:
        nxt = {"kind": "empty"}
    response = render(request, "honoraria/file_detail.html", {
        "file": file, "lines": lines, "summary": summary, "stages": stages, "steps": hf.steps(summary["phase"]),
        "batches": batches, "drafts": drafts, "with_es": with_es, "to_pay": to_pay,
        "is_dof": is_dof, "can_prepare": can_prepare, "can_decide_es": can_decide_es,
        "can_view_bank": can_view_bank_details(request.user, request), "officer": hf.officer_for(file),
        "candidates": hf.pay_officer_candidates() if is_dof else [], "funding_accounts": funding_accounts,
        "next": nxt, "ready_ok": len(ready_ok), "missing_bank": len(ready) - len(ready_ok),
        "ready_total": sum((x.amount for x in ready_ok), Decimal("0")),
        "disputes": disputes, "dispute_count": len(disputes),
        "stage_filter": stage_filter, "status_filter": status_filter, "query": query,
        "shown_count": len(shown_lines), "total_count": len(lines),
        "status_options": [(code, label, status_counts.get(code, 0)) for code, label in FL.STATUS_CHOICES],
        "stage_options": [(code, label, stage_counts.get(code, 0)) for code, label in FL.STAGE_CHOICES],
        "charts": [{"id": "file-stage", "type": "doughnut",
                    "data": [{"label": st["short"], "value": _float(st["total"])} for st in stages if st["total"]],
                    "colors": ["#1f6b3e", "#c8962a", "#2f7cf6"]}],
    })
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def file_prepare(request, pk):
    file = get_object_or_404(HonorariumFile, pk=pk)
    if not hf.can_view_file(request.user, file):
        raise Http404
    try:
        batch = hf.prepare_batch(file, user=request.user, line_ids=request.POST.getlist("line"),
                                 funding_account_id=request.POST.get("funding_account") or None,
                                 payment_reference=request.POST.get("payment_reference", ""))
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        if hf.is_dof(request.user):
            messages.success(request, f"{batch.reference} prepared (₦{batch.total:,.2f}). Review the payment "
                                      "list, print or download it for the ES, then send it for approval.")
        else:
            messages.success(request, f"{batch.reference} prepared (₦{batch.total:,.2f}) and sent to the "
                                      "Director of Finance to review and send to the Executive Secretary.")
    return redirect("honoraria_sched:file", pk=file.pk)


def _batch(pk):
    batch = get_object_or_404(PaymentSchedule.objects.select_related("honorarium_file__institution"), pk=pk)
    if batch.honorarium_file_id is None:
        raise Http404
    return batch


def _after_batch(request, batch):
    if request.POST.get("next") == "file" and batch.honorarium_file_id:
        return redirect("honoraria_sched:file", pk=batch.honorarium_file_id)
    return redirect("honoraria_sched:detail", pk=batch.pk)


@login_required
@require_POST
def batch_send(request, pk):
    """The DoF sends the reviewed batch to the Executive Secretary."""
    batch = _batch(pk)
    try:
        hf.send_to_es(batch, user=request.user, remark=request.POST.get("remark", ""))
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        messages.success(request, f"{batch.reference} sent to the Executive Secretary for approval. "
                                  "Nothing has been paid yet.")
    return _after_batch(request, batch)


@login_required
@require_POST
def batch_es_approve(request, pk):
    batch = _batch(pk)
    try:
        hf.es_approve(batch, user=request.user, remark=request.POST.get("remark", ""))
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        messages.success(request, f"{batch.reference} approved by the Executive Secretary. It is back with "
                                  "the Director of Finance to pay.")
    return _after_batch(request, batch)


@login_required
@require_POST
def batch_es_return(request, pk):
    batch = _batch(pk)
    try:
        hf.es_return(batch, user=request.user, remark=request.POST.get("remark", ""))
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        messages.success(request, f"{batch.reference} returned to the Director of Finance with your note.")
    return _after_batch(request, batch)


@login_required
@require_POST
def batch_pay(request, pk):
    """The DoF pays the ES-approved batch: everyone, or only the ticked payees."""
    batch = _batch(pk)
    scope = request.POST.get("scope") or "selected"
    line_ids = None if scope == "all" else request.POST.getlist("lines")
    try:
        hf.pay_batch(batch, user=request.user,
                     payment_reference=request.POST.get("payment_reference", ""),
                     funding_account_id=request.POST.get("funding_account") or None,
                     line_ids=line_ids)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        paid = batch.lines.filter(paid_at__isnull=False).count()
        left = batch.lines.filter(paid_at__isnull=True).count()
        messages.success(request, f"Payment recorded for {paid} payee line{'' if paid == 1 else 's'} on "
                                  f"{batch.reference}." + (f" {left} left for a later run." if left else
                                                           " Each payee was asked to confirm receipt."))
    return _after_batch(request, batch)


@login_required
@require_POST
def batch_cancel(request, pk):
    batch = _batch(pk)
    try:
        hf.cancel_batch(batch, user=request.user, reason=request.POST.get("reason", ""))
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        messages.success(request, f"{batch.reference} withdrawn. Its lines are ready to pay again.")
    return _after_batch(request, batch)


def _payment_list(request, pk):
    """Shared gate for the printable/CSV payment list: DoF only, unpaid lines,
    full bank details released through the logged disbursement path."""
    batch = get_object_or_404(
        PaymentSchedule.objects.select_related("honorarium_file__institution", "funding_account"), pk=pk)
    if not sched.can_pay(request.user):
        raise PermissionDenied("Only the Director of Finance may print payee bank details.")
    if batch.status not in ("draft", "awaiting_es", "approved"):
        raise Http404
    lines = list(batch.lines.select_related("user", "resource_person")
                 .filter(paid_at__isnull=True).order_by("name", "pk"))
    details, missing = sched.payment_details(batch, user=request.user, lines=lines)
    for line in lines:
        line.payment_details = details.get(line.pk)
    return batch, lines, missing


@login_required
def batch_print(request, pk):
    batch, lines, missing = _payment_list(request, pk)
    response = render(request, "honoraria/batch_print.html", {
        "batch": batch, "lines": lines, "missing": missing,
        "institution": batch.honorarium_file.institution if batch.honorarium_file_id else None,
        "generated_at": timezone.now(),
    })
    response["Cache-Control"] = "no-store, private"
    return response


@login_required
def batch_export(request, pk):
    batch, lines, _missing = _payment_list(request, pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="honoraria-{batch.reference}.csv"'
    response["Cache-Control"] = "no-store, private"
    writer = csv.writer(response)
    writer.writerow(["Institution", batch.honorarium_file.institution.name if batch.honorarium_file_id else ""])
    writer.writerow(["Batch", batch.reference])
    writer.writerow(["Teller / reference", batch.payment_reference])
    writer.writerow(["Prepared by", batch.created_by.get_full_name() if batch.created_by else ""])
    if batch.funding_account:
        writer.writerow(["Paid from", f"{batch.funding_account.bank_name} · {batch.funding_account.account_name} · "
                                     f"{batch.funding_account.account_number}"])
    writer.writerow([])
    writer.writerow(["No.", "Payee", "Work", "Amount (NGN)", "Bank", "Account name", "Account number",
                     "Teller / reference"])
    for index, line in enumerate(lines, 1):
        details = line.payment_details
        writer.writerow([
            index,
            line.name or line.person,
            line.role,
            f"{line.amount:.2f}",
            details["bank"] if details else "",
            details["account_name"] if details else "",
            details["account_number"] if details else "No confirmed account",
            batch.payment_reference,
        ])
    writer.writerow([])
    writer.writerow(["", "", "Total", f"{sum((line.amount for line in lines), Decimal('0')):.2f}"])
    return response


@login_required
@require_POST
def pay_officer(request):
    from accounts.models import User

    raw = request.POST.get("officer") or ""
    officer = User.objects.filter(pk=int(raw)).first() if raw.isdigit() else None
    file = None
    if (request.POST.get("file") or "").isdigit():
        file = get_object_or_404(HonorariumFile, pk=int(request.POST["file"]))
    standing = request.POST.get("standing") == "1" or file is None
    try:
        hf.assign_pay_officer(officer, user=request.user, file=file, standing=standing)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _errors(exc))
    else:
        who = officer.get_full_name() or officer.email
        messages.success(request, f"{who} now prepares honoraria payments"
                         + (" for every file." if standing else f" for {file.institution.name}."))
    if file is not None:
        return redirect("honoraria_sched:file", pk=file.pk)
    return redirect("honoraria_sched:files")


@login_required
@require_POST
def files_refresh(request):
    if not sched.can_view_all(request.user):
        raise PermissionDenied
    from institutions.models import Institution

    added = 0
    inst_ids = set(HonorariumFile.objects.values_list("institution_id", flat=True))
    from .models import HonorariumEntry

    inst_ids |= set(HonorariumEntry.objects.exclude(institution__isnull=True).values_list("institution_id", flat=True))
    from accreditation.models import QACase

    inst_ids |= set(QACase.unscoped.filter(Q(completed_at__isnull=False) | Q(inspections__closed_at__isnull=False))
                    .values_list("institution_id", flat=True))
    for inst in Institution.unscoped.filter(pk__in=inst_ids):
        _file, new = hf.sync(inst, announce=False, actor=request.user)
        added += len(new)
    messages.success(request, f"Files are up to date ({added} new item{'s' if added != 1 else ''} added).")
    return redirect("honoraria_sched:files")
