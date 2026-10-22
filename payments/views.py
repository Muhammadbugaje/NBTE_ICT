"""Finance (QA) — the internal payment screens (Master Plan §10, §13).

    Verification queue (first pass → second check → Director of Finance)
    Bills · Statement import · Unmatched lines (manual match) · Waivers ·
    Refunds · Webhook log · Settings → Fees & bank accounts, Payment methods

Institutions never reach these (internal host only). Staff never see the
institution's payment slips here: the queue shows the bill, the evidence
(proof, provider report, statement line) and the history — what the
decision needs (§10.4).
"""
from __future__ import annotations

import datetime

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from institutions.forms import QAForm
from qa_core import actions, flow
from qa_core.models import PendingAction
from qa_core.permissions import can_view_finance, can_view_qa_settings, require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import providers, services
from .models import (
    BankAccount,
    BankStatementImport,
    BankStatementLine,
    FeeSchedule,
    PaymentAdvice,
    PaymentAttempt,
    PaymentObligation,
    PaymentVerification,
    ProofOfPayment,
    Refund,
    WebhookLog,
)
from .money import naira


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def _hero(request):
    user = request.user
    open_items = PaymentVerification.objects.for_user(user).filter(is_open=True)
    first = open_items.filter(decision="").count()
    ready = sum(1 for i in open_items.exclude(decision="").select_related("obligation") if services.ready_for_final(i)[0])
    overdue = PaymentObligation.objects.for_user(user).filter(status="finance_confirmation_overdue").count()
    return {"first": first, "ready": ready, "overdue": overdue,
            "unmatched": BankStatementLine.objects.filter(match_status="unmatched").count(),
            "role": "director" if services.is_final_approver(user) else
            ("officer" if user.has_perm("payments.verify_payment") else "viewer")}


# ===========================================================================
# Verification queue
# ===========================================================================
STAGES = [("", "Everything open"), ("first", "Needs first pass"), ("second", "Needs second check"),
          ("final", "Ready for the Director"), ("info", "Needs information"), ("overdue", "Overdue")]
AGES = {"24h": 1, "3d": 3, "7d": 7}


def _queue_rows(request):
    f = request.GET  # Phase 9: the rows come from services.queue_items (shared with the API)
    return services.queue_items(request.user, source=f.get("source", ""), age=f.get("age", ""),
                                q=f.get("q", ""), stage=f.get("stage", ""))


@require(can_view_finance)
@require_GET
def queue(request):
    q = (request.GET.get("q") or "").strip()
    jump = None
    if q and q.upper().replace(" ", "").replace("-", "").startswith("NBTEQAP"):
        found = services.extract_references(q)
        if found:
            jump = PaymentObligation.objects.for_user(request.user).filter(reference=found[0][0]).first()
    page = paginate(request, _queue_rows(request))
    ctx = {"page": page, "filters": request.GET, "stages": STAGES, "ages": AGES, "hero": _hero(request),
           "sources": PaymentVerification.SOURCE_CHOICES, "jump": jump}
    return render_qa(request, "payments/queue.html", ctx, {"qa-panel": "payments/_queue_panel.html",
                                                           "qa-rows": "payments/_queue_rows.html"})


def _item_or_404(request, pk):
    item = (PaymentVerification.objects.for_user(request.user).filter(pk=pk)
            .select_related("obligation", "obligation__institution", "obligation__fee_schedule", "attempt", "proof",
                            "statement_line", "verified_by", "second_verifier", "final_approver").first())
    if item is None:
        raise Http404
    return item


class FirstPassForm(QAForm):
    decision = forms.ChoiceField(choices=PaymentVerification.DECISION_CHOICES, widget=forms.RadioSelect)
    amount = forms.DecimalField(max_digits=16, decimal_places=2, required=False, label="Amount received (₦)")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


def _item_context(request, item, **extra):
    user = request.user
    ob = item.obligation
    instance = services.instance_of(ob)
    proofs = list(ProofOfPayment.objects.for_user(user).filter(obligation=ob).order_by("-created_at"))
    attempts = list(PaymentAttempt.objects.for_user(user).filter(obligation=ob).order_by("-created_at"))
    lines = list(BankStatementLine.objects.filter(matched_obligation=ob).select_related("statement"))
    suggestions = []
    if not lines:
        suggestions = list(BankStatementLine.objects.filter(match_status__in=("unmatched", "ambiguous"))
                           .filter(Q(amount=ob.balance) | Q(extracted_reference=ob.reference)
                                   | Q(candidates__icontains=ob.reference)).select_related("statement")[:5])
    ready, why = services.ready_for_final(item)
    rights = services.item_rights(item, user, impersonating=_impersonating(request))
    history = flow.history(instance)
    ctx = {
        "item": item, "ob": ob, "proofs": proofs, "attempts": attempts, "lines": lines, "suggestions": suggestions,
        "stage": services.stage_label(ob), "public_stage": services.stage_label(ob, public=True),
        "history": history, "ready": ready, "ready_why": why,
        "needs_second": services.needs_second_check(item),
        "threshold": naira(services._decimal("qa.payment.dual_verify_threshold", "5000000")),
        **rights,
        "first_form": extra.pop("first_form", None) or FirstPassForm(initial={"amount": ob.balance}),
        "advices": list(PaymentAdvice.objects.for_user(user).filter(obligation=ob)),
        "final_role": services.final_approver_role(),
    }
    ctx.update(extra)
    return ctx


@require_or_404(can_view_finance)
@require_GET
def item_detail(request, pk):
    item = _item_or_404(request, pk)
    return render_qa(request, "payments/item.html", _item_context(request, item),
                     {"qa-panel": "payments/_item_panel.html"})


@require_or_404(can_view_finance)
@require_POST
def item_first_pass(request, pk):
    item = _item_or_404(request, pk)
    form = FirstPassForm(request.POST)
    error = ""
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.first_pass(item, user=request.user, decision=d["decision"], amount=d.get("amount"),
                                reason=d.get("reason", ""))
        except (ValidationError, PermissionDenied) as exc:
            error = " ".join(getattr(exc, "messages", [str(exc)]))
    else:
        error = "Choose a recommendation."
    item = _item_or_404(request, pk)
    response = render(request, "payments/_item_panel.html",
                      _item_context(request, item, error=error, first_form=form if error else None),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not recorded", error)
    return toast(response, "success", "First pass recorded",
                 "Only the Director of Finance can now confirm or reject this payment.")


@require_or_404(can_view_finance)
@require_POST
def item_second_pass(request, pk):
    item = _item_or_404(request, pk)
    error = ""
    try:
        services.second_pass(item, user=request.user, decision=request.POST.get("decision", ""),
                             reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        error = " ".join(getattr(exc, "messages", [str(exc)]))
    item = _item_or_404(request, pk)
    response = render(request, "payments/_item_panel.html", _item_context(request, item, error=error),
                      status=422 if error else 200)
    return toast(response, "error" if error else "success", "Not recorded" if error else "Second check recorded",
                 error)


@require_or_404(can_view_finance)
@require_POST
def item_final(request, pk):
    """The Director of Finance's decision — never optimistic, waits for the server (§20)."""
    item = _item_or_404(request, pk)
    decision = request.POST.get("decision", "")
    error = ""
    try:
        services.final_decision(item, user=request.user, decision=decision, reason=request.POST.get("reason", ""),
                                impersonating=_impersonating(request))
    except (ValidationError, PermissionDenied) as exc:
        error = " ".join(getattr(exc, "messages", [str(exc)]))
    item = _item_or_404(request, pk)
    response = render(request, "payments/_item_panel.html", _item_context(request, item, error=error),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not decided", error)
    if decision == "confirmed":
        return toast(response, "success", f"{item.obligation.reference} confirmed",
                     "The Payment Advice was generated and the institution notified.")
    return toast(response, "warning", f"{item.obligation.reference} rejected", "The institution sees your reason.")


# ===========================================================================
# Bills
# ===========================================================================
@require(can_view_finance)
@require_GET
def obligations(request):
    qs = PaymentObligation.objects.for_user(request.user).select_related("institution", "fee_schedule")
    f = request.GET
    groups = {"due": PaymentObligation.OPEN_FOR_PAYMENT, "checking": PaymentObligation.CLAIMED,
              "settled": PaymentObligation.SETTLED, "closed": ("cancelled", "expired"), "issued": ("issued",)}
    if f.get("group") in groups:
        qs = qs.filter(status__in=groups[f["group"]])
    if f.get("fee"):
        qs = qs.filter(fee_schedule__code=f["fee"])
    if f.get("flag"):
        qs = qs.filter(flags__icontains=f["flag"])
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q) | Q(institution__code__iexact=q)
                       | Q(source_reference__icontains=q))
    page = paginate(request, qs.order_by("-created_at"))
    for ob in page["rows"]:
        ob.stage = services.stage_label(ob)
    totals = PaymentObligation.objects.for_user(request.user).filter(status__in=("confirmed", "overpaid")) \
        .aggregate(s=Sum("amount_paid"))
    ctx = {"page": page, "filters": f, "fees": FeeSchedule.objects.all(), "hero": _hero(request),
           "confirmed_total": naira(totals["s"] or 0),
           "waived": PaymentObligation.objects.for_user(request.user).filter(status="waived").count()}
    return render_qa(request, "payments/obligations.html", ctx, {"qa-panel": "payments/_obligations_panel.html",
                                                                 "qa-rows": "payments/_obligation_rows.html"})


class ManualProofForm(QAForm):
    amount_claimed = forms.DecimalField(max_digits=16, decimal_places=2, min_value=0, label="Amount on the teller (₦)")
    paid_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    bank_name = forms.CharField(max_length=120)
    teller_number = forms.CharField(max_length=60)
    file = forms.FileField(label="Scan of the teller")


def _ob_or_404(request, pk):
    ob = PaymentObligation.objects.for_user(request.user).filter(pk=pk).select_related(
        "institution", "fee_schedule").first()
    if ob is None:
        raise Http404
    return ob


def _ob_context(request, ob, **extra):
    user = request.user
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(PaymentObligation)
    pending = list(actions.visible_to(user).filter(content_type=ct, object_id=str(ob.pk)).order_by("-initiated_at"))
    ctx = {
        "ob": ob, "stage": services.stage_label(ob), "history": flow.history(services.instance_of(ob)),
        "attempts": list(PaymentAttempt.objects.for_user(user).filter(obligation=ob).order_by("-created_at")),
        "proofs": list(ProofOfPayment.objects.for_user(user).filter(obligation=ob).order_by("-created_at")),
        "advices": list(PaymentAdvice.objects.for_user(user).filter(obligation=ob)),
        "verifications": list(PaymentVerification.objects.for_user(user).filter(obligation=ob)
                              .select_related("verified_by", "final_approver").order_by("-opened_at")),
        "refunds": list(Refund.objects.for_user(user).filter(obligation=ob).order_by("-created_at")),
        "lines": list(BankStatementLine.objects.filter(matched_obligation=ob).select_related("statement")),
        "pending": pending, "manual_form": extra.pop("manual_form", None) or ManualProofForm(
            initial={"amount_claimed": ob.balance, "paid_on": timezone.localdate()}),
        "can_waive": user.has_perm("payments.initiate_waiver") and ob.status in ("awaiting_payment", "rejected",
                                                                                 "partially_paid")
        and providers.get("offline").is_enabled(),
        "can_cancel": user.has_perm("payments.initiate_cancel_obligation") and ob.status in ("issued",
                                                                                             "awaiting_payment"),
        "can_record": user.has_perm("payments.verify_payment") and ob.status in ("awaiting_payment", "rejected",
                                                                                 "partially_paid"),
        "can_overpay": user.has_perm("payments.initiate_refund") and ob.overpaid_amount > 0,
        "snapshot": ob.formula_snapshot,
    }
    ctx.update(extra)
    return ctx


@require_or_404(can_view_finance)
@require_GET
def obligation_detail(request, pk):
    ob = _ob_or_404(request, pk)
    return render_qa(request, "payments/obligation.html", _ob_context(request, ob),
                     {"qa-panel": "payments/_obligation_panel.html"})


@require_or_404(can_view_finance)
@require_POST
def obligation_action(request, pk, verb):
    ob = _ob_or_404(request, pk)
    user = request.user
    reason = (request.POST.get("reason") or "").strip()
    error, title = "", ""
    try:
        if verb == "waive":
            pa = actions.initiate("payments.waive", actor=user, target=ob, payload={"reason": reason}, reason=reason,
                                  impersonating=_impersonating(request))
            title = f"{pa.reference}: waiver sent to the Director of Finance"
        elif verb == "cancel":
            pa = actions.initiate("payments.cancel_obligation", actor=user, target=ob, payload={"reason": reason},
                                  reason=reason, impersonating=_impersonating(request))
            title = f"{pa.reference}: cancellation sent for confirmation"
        elif verb == "record":
            form = ManualProofForm(request.POST, request.FILES)
            if not form.is_valid():
                raise ValidationError("Complete every field of the teller.")
            if not user.has_perm("payments.verify_payment"):
                raise PermissionDenied("You do not hold payments.verify_payment.")
            d = form.cleaned_data
            services.submit_proof(ob, user=user, upload=d["file"], paid_on=d["paid_on"],
                                  amount_claimed=d["amount_claimed"], bank_name=d["bank_name"],
                                  teller_number=d["teller_number"], payer_name="Recorded by NBTE Finance")
            title = "Teller recorded — the payment is now in the verification queue"
        elif verb == "credit":
            if not user.has_perm("payments.initiate_refund"):
                raise PermissionDenied
            services.hold_as_credit(ob, actor=user, note=reason)
            title = "Overpayment held as credit"
        elif verb == "refund":
            if not user.has_perm("payments.initiate_refund"):
                raise PermissionDenied
            refund = services.create_refund(ob, amount=ob.overpaid_amount, actor=user,
                                            reason=reason or "Overpayment refund", source="overpayment")
            pa = actions.initiate("payments.refund", actor=user, target=refund, payload={"reason": reason},
                                  reason=reason or "Overpayment refund", impersonating=_impersonating(request))
            title = f"{pa.reference}: refund sent to the Director of Finance"
        else:
            raise Http404
    except (ValidationError, PermissionDenied) as exc:
        error = " ".join(getattr(exc, "messages", [str(exc)]))
    ob = _ob_or_404(request, pk)
    response = render(request, "payments/_obligation_panel.html", _ob_context(request, ob, error=error),
                      status=422 if error else 200)
    return toast(response, "error" if error else "success", "Not done" if error else title, error)


# ===========================================================================
# Statements and matching
# ===========================================================================
class StatementForm(QAForm):
    bank_account = forms.ModelChoiceField(BankAccount.objects.filter(is_placeholder=False), required=False)
    file = forms.FileField(label="Statement (CSV)")


def _can_import(user):
    return user.is_superuser or user.has_perm("payments.import_bank_statement")


@require(lambda u: can_view_finance(u))
@require_http_methods(["GET", "POST"])
def statements(request):
    form = StatementForm(request.POST or None, request.FILES or None)
    error = ""
    if request.method == "POST":
        if not _can_import(request.user):
            raise PermissionDenied
        if form.is_valid():
            try:
                st = services.import_statement(form.cleaned_data["file"], user=request.user,
                                               bank_account=form.cleaned_data.get("bank_account"))
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("qa_fin:statement", args=[st.pk])
                if is_htmx(request):
                    return toast(response, "success", "Statement imported",
                                 f"{st.parsed_rows} credit lines: {st.matched} matched, {st.ambiguous} ambiguous, "
                                 f"{st.unmatched} unmatched.")
                from django.shortcuts import redirect

                return redirect("qa_fin:statement", pk=st.pk)
        else:
            error = "Choose a CSV file."
    page = paginate(request, BankStatementImport.objects.select_related("bank_account", "uploaded_by")
                    .order_by("-created_at"))
    ctx = {"form": form, "page": page, "error": error, "hero": _hero(request), "can_import": _can_import(request.user),
           "column_map": services.column_map_for()}
    response = render_qa(request, "payments/statements.html", ctx, {"qa-panel": "payments/_statements_panel.html"},
                         status=422 if error else 200)
    if error:
        toast(response, "error", "Not imported", error)
    return response


@require_or_404(can_view_finance)
@require_GET
def statement_detail(request, pk):
    st = BankStatementImport.objects.filter(pk=pk).select_related("bank_account", "uploaded_by").first()
    if st is None:
        raise Http404
    lines = BankStatementLine.objects.filter(statement=st).select_related("matched_obligation")
    status = request.GET.get("match", "")
    if status:
        lines = lines.filter(match_status=status)
    page = paginate(request, lines.order_by("row_number"))
    ctx = {"st": st, "page": page, "filters": request.GET, "matches": BankStatementLine.MATCH_CHOICES}
    return render_qa(request, "payments/statement.html", ctx, {"qa-panel": "payments/_statement_panel.html",
                                                               "qa-rows": "payments/_line_rows.html"})


@require(can_view_finance)
@require_GET
def unmatched(request):
    lines = BankStatementLine.objects.filter(match_status__in=("unmatched", "ambiguous")).select_related("statement")
    f = request.GET
    if f.get("match"):
        lines = lines.filter(match_status=f["match"])
    if f.get("typo") == "yes":
        lines = lines.filter(reference_check_failed=True)
    q = (f.get("q") or "").strip()
    if q:
        lines = lines.filter(Q(narration__icontains=q) | Q(extracted_reference__icontains=q))
    if f.get("min"):
        try:
            lines = lines.filter(amount__gte=f["min"])
        except Exception:
            pass
    page = paginate(request, lines.order_by("-value_date", "row_number"))
    ctx = {"page": page, "filters": f, "hero": _hero(request)}
    return render_qa(request, "payments/unmatched.html", ctx, {"qa-panel": "payments/_unmatched_panel.html",
                                                               "qa-rows": "payments/_line_rows.html"})


def _line_or_404(pk):
    line = BankStatementLine.objects.filter(pk=pk).select_related("statement", "matched_obligation").first()
    if line is None:
        raise Http404
    return line


@require_or_404(can_view_finance)
@require_http_methods(["GET", "POST"])
def line_match(request, pk):
    """Manual match: search by institution, amount and date; remark required (§10.6)."""
    line = _line_or_404(pk)
    user = request.user
    error = ""
    if request.method == "POST":
        ob = PaymentObligation.objects.for_user(user).filter(pk=request.POST.get("obligation") or 0).first()
        try:
            if request.POST.get("verb") == "ignore":
                services.ignore_line(line, user=user, remark=request.POST.get("remark", ""))
            else:
                if ob is None:
                    raise ValidationError("Choose the bill this money is for.")
                services.manual_match(line, ob, user=user, remark=request.POST.get("remark", ""))
        except (ValidationError, PermissionDenied) as exc:
            error = " ".join(getattr(exc, "messages", [str(exc)]))
        line = _line_or_404(pk)
    q = (request.GET.get("q") or "").strip()
    candidates = PaymentObligation.objects.for_user(user).exclude(status__in=("cancelled",)).select_related("institution")
    if q:
        candidates = candidates.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q)
                                       | Q(institution__code__iexact=q))
    else:
        refs = list(line.candidates or [])
        candidates = candidates.filter(Q(reference__in=refs) | Q(balance=line.amount))
    ctx = {"line": line, "candidates": list(candidates.order_by("-created_at")[:25]), "q": q, "error": error}
    response = render_qa(request, "payments/line_match.html", ctx, {"qa-panel": "payments/_line_match_panel.html"},
                         status=422 if error else 200)
    if request.method == "POST":
        return toast(response, "error" if error else "success", "Not done" if error else "Line updated", error)
    return response


# ===========================================================================
# Waivers, refunds, webhook log
# ===========================================================================
@require(can_view_finance)
@require_GET
def waivers(request):
    waived = PaymentObligation.objects.for_user(request.user).filter(status="waived").select_related("institution",
                                                                                                     "waived_by")
    pending = actions.visible_to(request.user).filter(policy_id="payments.waive").order_by("-initiated_at")[:50]
    ctx = {"waived": list(waived.order_by("-waived_at")[:200]), "pending": list(pending), "hero": _hero(request)}
    return render_qa(request, "payments/waivers.html", ctx, {"qa-panel": "payments/_waivers_panel.html"})


@require(can_view_finance)
@require_GET
def refunds(request):
    qs = Refund.objects.for_user(request.user).select_related("obligation", "obligation__institution", "requested_by",
                                                              "approved_by")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    page = paginate(request, qs.order_by("-created_at"))
    ctx = {"page": page, "filters": request.GET, "hero": _hero(request),
           "statuses": [("requested", "Requested"), ("approved", "Approved"), ("paid", "Paid"), ("rejected", "Declined")]}
    return render_qa(request, "payments/refunds.html", ctx, {"qa-panel": "payments/_refunds_panel.html"})


@require_or_404(can_view_finance)
@require_POST
def refund_action(request, pk, verb):
    refund = Refund.objects.for_user(request.user).filter(pk=pk).select_related("obligation").first()
    if refund is None:
        raise Http404
    error, title = "", ""
    try:
        if verb == "approve":
            if not request.user.has_perm("payments.initiate_refund"):
                raise PermissionDenied
            pa = actions.initiate("payments.refund", actor=request.user, target=refund,
                                  payload={"reason": request.POST.get("reason", "")}, reason=refund.reason,
                                  impersonating=_impersonating(request))
            title = f"{pa.reference} sent to the Director of Finance"
        elif verb == "paid":
            if not request.user.has_perm("payments.initiate_refund"):
                raise PermissionDenied
            services.mark_refund_paid(refund, actor=request.user, paid_reference=request.POST.get("paid_reference", ""))
            title = "Refund recorded as paid"
        else:
            raise Http404
    except (ValidationError, PermissionDenied) as exc:
        error = " ".join(getattr(exc, "messages", [str(exc)]))
    qs = Refund.objects.for_user(request.user).select_related("obligation", "obligation__institution")
    page = paginate(request, qs.order_by("-created_at"))
    response = render(request, "payments/_refunds_panel.html",
                      {"page": page, "filters": {}, "hero": _hero(request), "error": error,
                       "statuses": [("requested", "Requested"), ("approved", "Approved"), ("paid", "Paid"),
                                    ("rejected", "Declined")]}, status=422 if error else 200)
    return toast(response, "error" if error else "success", "Not done" if error else title, error)


def _can_see_webhooks(user):
    return can_view_finance(user) or user.has_perm("qa_core.view_oversight_board")


@require(_can_see_webhooks)
@require_GET
def webhooks(request):
    qs = WebhookLog.objects.all()
    f = request.GET
    if f.get("provider"):
        qs = qs.filter(provider=f["provider"])
    if f.get("valid") in ("yes", "no"):
        qs = qs.filter(signature_valid=f["valid"] == "yes")
    if f.get("q"):
        qs = qs.filter(Q(reference__icontains=f["q"]) | Q(outcome__icontains=f["q"]))
    days = {"1d": 1, "7d": 7, "30d": 30}.get(f.get("period", ""))
    if days:
        import datetime

        from django.utils import timezone as tz

        qs = qs.filter(received_at__gte=tz.now() - datetime.timedelta(days=days))
    page = paginate(request, qs.order_by("-received_at"))
    return render_qa(request, "payments/webhooks.html", {"page": page, "filters": f, "hero": _hero(request)},
                     {"qa-panel": "payments/_webhooks_panel.html", "qa-rows": "payments/_webhook_rows.html"})


# ===========================================================================
# Settings → Fees and bank accounts, Payment methods
# ===========================================================================
def _can_see_fee_settings(user):
    return can_view_qa_settings(user) or user.has_perm("payments.view_obligation") or \
        user.has_perm("payments.manage_bank_accounts")


class BankForm(QAForm):
    bank_name = forms.CharField(max_length=120)
    account_name = forms.CharField(max_length=150)
    account_number = forms.CharField(max_length=10, min_length=10)
    sort_code = forms.CharField(max_length=20, required=False)
    is_default = forms.BooleanField(required=False, initial=True)
    is_active = forms.BooleanField(required=False, initial=True)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5)


class FeeForm(QAForm):
    formula = forms.CharField(max_length=150)
    due_days = forms.IntegerField(required=False, min_value=1, max_value=365)
    remita_service_type_id = forms.CharField(max_length=40, required=False, label="Remita service type ID")
    revenue_account = forms.ModelChoiceField(BankAccount.objects.filter(is_placeholder=False), required=False)
    description = forms.CharField(max_length=255)
    is_active = forms.BooleanField(required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5)


class NewFeeForm(QAForm):
    name = forms.CharField(max_length=150, help_text="As Finance and institutions will see it.")
    code = forms.SlugField(max_length=51, help_text="Lower-case, e.g. late_submission. Workflows and QA types "
                                                    "refer to the fee by this code.")
    description = forms.CharField(max_length=255, required=False, label="Text on the slip")
    expr = forms.CharField(label="Formula", widget=forms.Textarea(attrs={"rows": 2, "spellcheck": "false"}),
                           help_text="e.g. 50000 * programmes + 25000 * max(streams - programmes, 0)")
    due_days = forms.IntegerField(required=False, min_value=1, max_value=365,
                                  help_text="Blank = qa.payment.due_days.")
    remita_service_type_id = forms.CharField(max_length=40, required=False, label="Remita service type ID")
    revenue_account = forms.ModelChoiceField(BankAccount.objects.filter(is_placeholder=False), required=False,
                                             help_text="Blank = the default account.")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5)


def _fee_variables_ctx(request):
    from . import fee_admin

    return [{"name": n, "help": h, "checked": n in request.POST.getlist("vars"),
             "sample": request.POST.get(f"sample_{n}", fee_admin.SAMPLE_DEFAULTS[n])} for n, h in fee_admin.VARIABLE_HELP]


def _fees_ctx(request, **extra):
    from qa_core.views import _settings_context

    from . import fee_admin

    schedules = list(FeeSchedule.objects.select_related("revenue_account").order_by("name"))
    from config.models import ConfigEntry

    for s in schedules:
        entry = ConfigEntry.objects.filter(key=s.formula).first()
        s.expr = (entry.parsed() or {}).get("expr", "") if entry and entry.value_type == "formula" else ""
        s.blocked = bool(s.confirmed_setting and str(ConfigEntry.objects.filter(key=s.confirmed_setting)
                                                     .values_list("value", flat=True).first()).lower() != "true")
    events = fee_admin.issuing_events()
    for s in schedules:
        s.events = events.get(s.code, [])
    pending = list(actions.visible_to(request.user).filter(policy_id__in=("payments.update_bank_account",
                                                                          "payments.update_fee_schedule",
                                                                          "payments.create_fee_schedule"),
                                                           status="pending"))
    ctx = _settings_context(request, "fees", schedules=schedules, accounts=list(BankAccount.objects.all()),
                            fee_variables=fee_admin.VARIABLE_HELP,
                            pending=pending, bank_form=extra.pop("bank_form", None) or BankForm(),
                            can_propose=request.user.has_perm("payments.view_obligation") or request.user.is_superuser,
                            usable=services.default_bank_account(), default_account=services.default_bank_account())
    ctx.update(extra)
    return ctx


@require(_can_see_fee_settings)
@require_GET
def settings_fees(request):
    return render_qa(request, "payments/settings/fees.html", _fees_ctx(request),
                     {"qa-panel": "payments/settings/_fees_panel.html"})


@require(_can_see_fee_settings)
@require_POST
def settings_bank_form(request):
    form = BankForm(request.POST)
    error = ""
    target = BankAccount.objects.filter(pk=request.POST.get("account") or 0).first()
    if form.is_valid():
        d = form.cleaned_data
        payload = {k: d[k] for k in ("bank_name", "account_name", "account_number", "sort_code", "is_default",
                                     "is_active")}
        payload["reason"] = d["reason"]
        try:
            pa = actions.initiate("payments.update_bank_account", actor=request.user, target=target, payload=payload,
                                  reason=d["reason"], impersonating=_impersonating(request))
        except (ValidationError, PermissionDenied) as exc:
            error = " ".join(getattr(exc, "messages", [str(exc)]))
        else:
            response = render(request, "payments/settings/_fees_panel.html", _fees_ctx(request))
            return toast(response, "success", f"{pa.reference} waiting for the Director of Finance",
                         "Bank details change only when a second officer confirms.")
    else:
        error = "Check the bank details."
    response = render(request, "payments/settings/_fees_panel.html", _fees_ctx(request, bank_form=form, error=error),
                      status=422)
    return toast(response, "error", "Not sent", error)


@require(_can_see_fee_settings)
@require_http_methods(["GET", "POST"])
def settings_fee_form(request, pk):
    schedule = FeeSchedule.objects.filter(pk=pk).first()
    if schedule is None:
        raise Http404
    form = FeeForm(request.POST or None, initial={"formula": schedule.formula, "due_days": schedule.due_days,
                                                  "remita_service_type_id": schedule.remita_service_type_id,
                                                  "revenue_account": schedule.revenue_account_id,
                                                  "description": schedule.description, "is_active": schedule.is_active})
    error = ""
    if request.method == "POST":
        if form.is_valid():
            d = form.cleaned_data
            payload = {"formula": d["formula"], "due_days": d.get("due_days"),
                       "remita_service_type_id": d.get("remita_service_type_id", ""),
                       "revenue_account_id": d["revenue_account"].pk if d.get("revenue_account") else None,
                       "description": d["description"], "is_active": d.get("is_active", False), "reason": d["reason"]}
            try:
                pa = actions.initiate("payments.update_fee_schedule", actor=request.user, target=schedule,
                                      payload=payload, reason=d["reason"], impersonating=_impersonating(request))
            except (ValidationError, PermissionDenied) as exc:
                error = " ".join(getattr(exc, "messages", [str(exc)]))
            else:
                response = render(request, "payments/settings/_fees_panel.html", _fees_ctx(request))
                return toast(response, "success", f"{pa.reference} waiting for the Director of Finance")
        else:
            error = "Check the form."
    return render(request, "payments/settings/_fee_form.html", {"schedule": schedule, "form": form, "error": error},
                  status=422 if error else 200)


@require(_can_see_fee_settings)
@require_GET
def settings_methods(request):
    from config.models import ConfigEntry
    from qa_core.views import _settings_context

    keys = ["qa.payment.remita.enabled", "qa.payment.paystack.enabled", "qa.payment.bank_transfer.enabled",
            "qa.payment.offline.enabled", "qa.payment.method_order", "qa.payment.paystack.fee_bearer",
            "qa.payment.allow_partial", "qa.payment.dual_verify_threshold",
            "qa.payment.finance_confirmation_window_hours", "qa.payment.receipt_required_for_provider_payments",
            "qa.payment.provider_confirmation_is_evidence_only", "qa.payment.final_approver_role"]
    entries = {e.key: e for e in ConfigEntry.objects.filter(key__in=keys)}
    report = providers.status_report()
    offered = [p.code for p in providers.enabled_methods()]
    ctx = _settings_context(request, "methods", report=report, offered=offered,
                            entries=[entries[k] for k in keys if k in entries],
                            forced_bank=offered == ["bank_transfer"] and not providers.get("bank_transfer").setting_enabled(),
                            missing=[r for r in report if r["setting"] and r["missing"] and not r["simulated"]])
    return render_qa(request, "payments/settings/methods.html", ctx,
                     {"qa-panel": "payments/settings/_methods_panel.html"})


# ===========================================================================
# Phase 8: add a new fee (two officers) and test its formula
# ===========================================================================
def _can_propose_fee(user):
    return user.is_superuser or user.has_perm("payments.view_obligation")


@require(_can_see_fee_settings)
@require_http_methods(["GET", "POST"])
def settings_fee_new(request):
    from . import fee_admin

    if not _can_propose_fee(request.user):
        raise PermissionDenied("Proposing a fee needs payments.view_obligation (Finance).")
    form = NewFeeForm(request.POST or None)
    error = ""
    if request.method == "POST":
        if form.is_valid():
            d = form.cleaned_data
            payload = {"code": d["code"], "name": d["name"], "description": d.get("description") or d["name"],
                       "expr": d["expr"], "vars": request.POST.getlist("vars"), "due_days": d.get("due_days"),
                       "remita_service_type_id": d.get("remita_service_type_id", ""),
                       "revenue_account_id": d["revenue_account"].pk if d.get("revenue_account") else None,
                       "sample": {n: request.POST.get(f"sample_{n}", "") for n, _h in fee_admin.VARIABLE_HELP},
                       "reason": d["reason"]}
            try:
                pa = actions.initiate("payments.create_fee_schedule", actor=request.user, payload=payload,
                                      reason=d["reason"], impersonating=_impersonating(request))
            except (ValidationError, PermissionDenied) as exc:
                error = " ".join(getattr(exc, "messages", [str(exc)]))
            else:
                response = render(request, "payments/settings/_fees_panel.html", _fees_ctx(request))
                return toast(response, "success", f"{pa.reference} waiting for a second officer",
                             "The fee is created only when the Director of Finance confirms it.")
        else:
            error = "Check the form."
    ctx = {"form": form, "error": error, "variables": _fee_variables_ctx(request)}
    return render(request, "payments/settings/_fee_new.html", ctx, status=422 if error else 200)


@require(_can_see_fee_settings)
@require_POST
def settings_fee_test(request):
    """HTMX: evaluate the new formula with the sample inputs (nothing is saved)."""
    from . import fee_admin

    amount, error = fee_admin.test_formula(request.POST.get("expr", ""), request.POST.getlist("vars"),
                                           {n: request.POST.get(f"sample_{n}", "") for n, _h in fee_admin.VARIABLE_HELP})
    return render(request, "payments/settings/_fee_test.html", {"amount": amount, "error": error})


# ===========================================================================
# Phase 8: issue a bill by hand (a Finance officer proposes, the Director confirms)
# ===========================================================================
class IssueBillForm(QAForm):
    institution = forms.CharField(max_length=20, label="Institution code")
    fee = forms.ModelChoiceField(FeeSchedule.objects.filter(is_active=True), to_field_name="code",
                                 label="Fee schedule")
    programmes = forms.IntegerField(required=False, min_value=0, max_value=500,
                                    help_text="Blank = every programme on the institution's record.")
    streams = forms.IntegerField(required=False, min_value=0, max_value=5000,
                                 help_text="Blank = the streams on the institution's record.")
    description = forms.CharField(max_length=255, required=False, label="Text on the slip",
                                  help_text="Blank = the fee schedule's own text.")
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5)


def _bill_institution(request, code):
    from institutions.models import Institution

    return Institution.objects.for_user(request.user).filter(code__iexact=(code or "").strip()).first()


def _bill_variables(d):
    out = {}
    for key in ("programmes", "streams"):
        if d.get(key) is not None:
            out[key] = d[key]
    if "programmes" in out and "streams" not in out:
        out["streams"] = out["programmes"]
    if "streams" in out:
        out["max_streams"] = out["streams"]
    return out


@require(can_view_finance)
@require_http_methods(["GET", "POST"])
def issue_bill(request):
    if not _can_propose_fee(request.user):
        raise PermissionDenied("Issuing a bill needs payments.view_obligation (Finance).")
    form = IssueBillForm(request.POST or None, initial={"institution": request.GET.get("institution", "")})
    error, estimate = "", None
    inst = None
    if request.method == "POST":
        if form.is_valid():
            d = form.cleaned_data
            inst = _bill_institution(request, d["institution"])
            if inst is None:
                form.add_error("institution", "No institution with this code in your scope.")
            elif request.POST.get("op") == "preview":
                estimate = _bill_estimate(inst, d)
            else:
                payload = {"fee_code": d["fee"].code, "variables": _bill_variables(d),
                           "description": d.get("description", ""), "reason": d["reason"]}
                try:
                    pa = actions.initiate("payments.issue_bill", actor=request.user, target=inst, payload=payload,
                                          reason=d["reason"], impersonating=_impersonating(request))
                except (ValidationError, PermissionDenied) as exc:
                    error = " ".join(getattr(exc, "messages", [str(exc)]))
                else:
                    response = render(request, "payments/_issue_bill_panel.html",
                                      {"form": IssueBillForm(initial={"institution": inst.code}), "sent": pa,
                                       "hero": _hero(request)})
                    return toast(response, "success", f"{pa.reference} waiting for the Director of Finance",
                                 "The bill is issued when a second officer confirms.")
        if not form.is_valid() and not error:
            error = "Check the form."
    ctx = {"form": form, "error": error, "estimate": estimate, "inst": inst, "hero": _hero(request)}
    return render_qa(request, "payments/issue_bill.html", ctx, {"qa-panel": "payments/_issue_bill_panel.html"},
                     status=422 if error else 200)


def _bill_estimate(inst, d):
    from qa_core.formula import FormulaError, evaluate_setting

    from .money import to_money

    try:
        snap = evaluate_setting(d["fee"].formula, services.fee_variables(inst, **_bill_variables(d)))
    except FormulaError as exc:
        return {"error": str(exc)}
    return {"amount": to_money(snap["result"]), "inputs": snap["inputs"], "expr": snap["expr"]}
