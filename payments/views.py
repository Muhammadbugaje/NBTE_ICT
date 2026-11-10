"""Finance (QA) — the internal payment screens (Master Plan §10, §13).

    Verification queue (Director of Finance final decision)
    Bills · Statement import · Unmatched lines (manual match) · Waivers ·
    Refunds · Webhook log · Settings → Fees & bank accounts, Payment methods

Institutions never reach these (internal host only). Staff never see the
institution's payment slips here: the queue shows the bill, the evidence
(proof, provider report, statement line) and the history — what the
decision needs (§10.4).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from institutions.forms import QAForm
from core import audit
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


def can_view_queue(user):
    """Finance staff, and ICT QA monitors (read-only + "Send reminder", P4)."""
    return can_view_finance(user) or services.is_ict_monitor(user)


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def _hero(request):
    user = request.user
    open_items = PaymentVerification.objects.for_user(user).filter(is_open=True)
    first = open_items.filter(decision="").count()
    ready = sum(1 for i in open_items.exclude(decision="").select_related("obligation") if services.ready_for_final(i)[0])
    overdue = PaymentObligation.objects.for_user(user).filter(status="finance_confirmation_overdue").count()
    from . import bill_requests as br_svc
    from .models import BillRequest

    return {"first": first, "open": open_items.count(), "ready": ready, "overdue": overdue,
            "unmatched": BankStatementLine.objects.filter(match_status="unmatched").count(),
            "bills_awaiting": BillRequest.unscoped.filter(status=BillRequest.AWAITING).count(),
            "is_es": br_svc.is_approver(user), "can_prepare": br_svc.can_prepare(user),
            "role": "director" if services.is_final_approver(user) else
            ("officer" if user.has_perm("payments.verify_payment") else "viewer")}


# ===========================================================================
# Verification queue
# ===========================================================================
STAGES = [("", "Everything open"), ("final", "Ready for the Director"),
          ("info", "Needs information"), ("overdue", "Overdue")]
AGES = {"24h": 1, "3d": 3, "7d": 7}


def _queue_rows(request):
    f = request.GET  # Phase 9: the rows come from services.queue_items (shared with the API)
    return services.queue_items(request.user, source=f.get("source", ""), age=f.get("age", ""),
                                q=f.get("q", ""), stage=f.get("stage", ""))


@require(can_view_queue)
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


def _finance_seat_holder():
    """v2.2: who sits in the Director of Finance seat (shown to anyone in
    Finance who expected to confirm, so a mis-assigned seat is obvious)."""
    import org
    from qa_core.departments import qa_department

    dept = qa_department(services.finance_department_code())
    return org.director_of(dept) if dept is not None else None


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
        # v2.1 F.4 / F.18
        "approvals_required": services.approvals_required(item), "approvals_have": len(services.approvals(item)),
        "first_mode": services.first_pass_mode(), "next_reminder": services.next_reminder_at(item),
        "extra_approvals": item.extra_approvals or [],
        "first_changes": item.first_pass_changes or [],
        **rights,
        "first_form": extra.pop("first_form", None) or FirstPassForm(initial={"amount": ob.balance}),
        "advices": list(PaymentAdvice.objects.for_user(user).filter(obligation=ob)),
        "final_role": services.final_approver_role(),
        "seat_holder": _finance_seat_holder(),
    }
    ctx.update(extra)
    return ctx


@require_or_404(can_view_queue)
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


def _panel(request, item_pk, *, error="", ok_title="", ok_text=""):
    item = _item_or_404(request, item_pk)
    response = render(request, "payments/_item_panel.html", _item_context(request, item, error=error),
                      status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", ok_title, ok_text)


@require_or_404(can_view_finance)
@require_POST
def item_change_first(request, pk):
    """P8: unlock (clear) a first pass — reason required, audited."""
    item = _item_or_404(request, pk)
    try:
        services.change_first_pass(item, user=request.user, reason=request.POST.get("reason", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _panel(request, pk, error=" ".join(getattr(exc, "messages", [str(exc)])))
    return _panel(request, pk, ok_title="First pass reopened", ok_text="Record the first pass again.")


@require_or_404(can_view_queue)
@require_POST
def item_remind(request, pk):
    """P4: ICT sends the Director of Finance a reminder (once per window)."""
    item = _item_or_404(request, pk)
    try:
        services.remind_director(item, user=request.user, note=request.POST.get("note", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _panel(request, pk, error=" ".join(getattr(exc, "messages", [str(exc)])))
    return _panel(request, pk, ok_title="Reminder sent", ok_text="The Director of Finance was notified.")


@require_or_404(can_view_finance)
@require_POST
def item_final(request, pk):
    """The Director of Finance's decision — never optimistic, waits for the server (§20)."""
    item = _item_or_404(request, pk)
    decision = request.POST.get("decision", "")
    error = ""
    try:
        services.final_decision(item, user=request.user, decision=decision, reason=request.POST.get("reason", ""),
                                impersonating=_impersonating(request), amount=request.POST.get("amount") or None)
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
    page["rows"] = services.stage_labels(page["rows"])   # v2.2: 3 queries for the page, not 2 per row
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
    # v2.5: the Refunds tab now lists cancellation refunds with these overpayment refunds below.
    from . import refund_views

    response = render(request, "payments/refunds/_list_panel.html",
                      refund_views._list_ctx(request, legacy_error=error), status=422 if error else 200)
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
            "qa.payment.provider_confirmation_is_evidence_only", "qa.payment.finance_department"]
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
# v2.4: Issue a bill — pick an institution, tick programmes / fees, the fee
# rules price them, the Executive Secretary approves, then it is sent.
# (payments.bill_requests holds the rules; IssueBillHandler stays for the API.)
# ===========================================================================
def _bill_hero(request, **extra):
    hero = _hero(request)
    hero.update(extra)
    return hero


def _bill_requests_qs(user):
    from . import bill_requests as br_svc
    from .models import BillRequest

    if br_svc.is_approver(user):
        base = BillRequest.unscoped.all()
    else:
        # Their scope, plus every bill request they prepared themselves (they may
        # bill any institution — see _bill_institutions).
        base = BillRequest.unscoped.filter(Q(pk__in=BillRequest.objects.for_user(user).values("pk"))
                                           | Q(created_by_id=user.pk))
    return base.select_related("institution", "created_by", "decided_by", "obligation")


def _bill_institutions(user):
    """Owner decision: whoever may issue bills picks from EVERY institution
    (never-billed ones too), not only their scoped ones. Other finance pages
    keep their scoping."""
    from institutions.models import Institution

    from . import bill_requests as br_svc

    base = Institution.unscoped.all() if br_svc.can_prepare(user) else Institution.objects.for_user(user)
    return base.exclude(status="delisted").order_by("name").only("code", "name")


def _bill_ctx(request, inst=None, *, draft=None, picks=None, reason="", description="", **extra):
    from . import bill_requests as br_svc

    ctx = {"institutions": _bill_institutions(request.user), "inst": inst, "draft": draft,
           "hero": _bill_hero(request), "reason": reason, "description": description,
           "fin_tab": "bills"}
    if inst is not None:
        cat = br_svc.catalogue(inst)
        items = picks if picks is not None else (draft.items if draft else [])
        chosen = {i.get("key"): i for i in items if i.get("key")}
        for row in cat["has"] + cat["can_offer"]:
            pick = chosen.get(row["key"])
            row["checked"] = pick is not None
            if pick:
                row["streams"] = pick.get("streams") or row["streams"]
        chosen_fees = {i.get("fee_code") for i in items if i.get("kind") == br_svc.FEE}
        ctx.update({"cat": cat, "other_fees": [{"schedule": s, "checked": s.code in chosen_fees}
                                               for s in br_svc.other_fees()],
                    "priced": br_svc.price(inst, items) if items else None,
                    "recent": list(_bill_requests_qs(request.user).filter(institution=inst)[:6]),
                    "existing_open": list(PaymentObligation.unscoped
                                          .filter(institution=inst, status__in=PaymentObligation.OPEN_FOR_PAYMENT)
                                          .order_by("-created_at")[:5])})
    ctx.update(extra)
    return ctx


def br_can_view(user):
    from . import bill_requests as br_svc

    return br_svc.can_view(user)


@require(br_can_view)
@require_http_methods(["GET", "POST"])
def issue_bill(request):
    from . import bill_requests as br_svc
    from .models import BillRequest

    if not br_svc.can_prepare(request.user):
        raise PermissionDenied("Issuing a bill needs payments.view_obligation (Finance).")
    data = request.POST if request.method == "POST" else request.GET
    draft = None
    draft_pk = data.get("draft") or ""
    if draft_pk.isdigit():
        draft = _bill_requests_qs(request.user).filter(pk=int(draft_pk), status=BillRequest.DRAFT).first()
    code = (data.get("institution") or (draft.institution.code if draft else "")).strip()
    inst = _bill_institutions(request.user).filter(code__iexact=code).first() if code else None
    if request.method == "GET":
        error = "No institution with this code (or it is delisted)." if code and inst is None else ""
        ctx = _bill_ctx(request, inst, draft=draft, reason=draft.reason if draft else "",
                        description=draft.description if draft else "", error=error)
        return render_qa(request, "payments/issue_bill.html", ctx, {"qa-panel": "payments/_issue_bill_panel.html"})
    if inst is None:
        ctx = _bill_ctx(request, None, error="Choose an institution first.")
        return render_qa(request, "payments/issue_bill.html", ctx, {"qa-panel": "payments/_issue_bill_panel.html"},
                         status=422)
    items = br_svc.items_from_post(inst, request.POST)
    reason, description = request.POST.get("reason", ""), request.POST.get("description", "")
    op = request.POST.get("op", "price")
    if op == "price":
        ctx = _bill_ctx(request, inst, draft=draft, picks=items, reason=reason, description=description)
        return render_qa(request, "payments/issue_bill.html", ctx, {"qa-panel": "payments/_issue_bill_panel.html",
                                                                    "ib-summary": "payments/_bill_summary.html"})
    try:
        br = br_svc.save(inst, items, actor=request.user, reason=reason, description=description, request=draft,
                         submit=(op == "send"))
    except (ValidationError, PermissionDenied) as exc:
        msg = " ".join(getattr(exc, "messages", [str(exc)]))
        ctx = _bill_ctx(request, inst, draft=draft, picks=items, reason=reason, description=description, error=msg)
        response = render_qa(request, "payments/issue_bill.html", ctx,
                             {"qa-panel": "payments/_issue_bill_panel.html"}, status=422)
        return toast(response, "error", "Not sent" if op == "send" else "Not saved", msg[:200])
    if op == "send":
        title, body = (f"{br.reference} sent to the Executive Secretary",
                       f"{naira(br.total)} for {inst.name}. The institution sees it only after the ES approves.")
    else:
        title, body = f"Draft {br.reference} saved", "Send it to the Executive Secretary when it is ready."
    ctx = _bill_request_ctx(request, br, flash={"kind": "success", "title": title, "message": body})
    response = render_qa(request, "payments/bill_request.html", ctx, {"qa-panel": "payments/_bill_request_panel.html"})
    if is_htmx(request):
        response["HX-Push-Url"] = reverse("qa_fin:bill_request", args=[br.pk])
    return toast(response, "success", title, body)


BILL_REQUEST_TABS = [("awaiting_es", "Awaiting ES approval"), ("draft", "Drafts"), ("approved", "Approved & sent"),
                     ("rejected", "Rejected"), ("all", "All")]


@require(br_can_view)
@require_GET
def bill_requests(request):
    from django.db.models import Count

    from .models import BillRequest

    qs = _bill_requests_qs(request.user)
    status = request.GET.get("status") or "awaiting_es"
    counts = dict(qs.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    rows = qs if status == "all" else qs.filter(status=status)
    q = (request.GET.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q)
                           | Q(institution__code__iexact=q))
    page = paginate(request, rows.order_by("-submitted_at", "-created_at"))
    awaiting_total = qs.filter(status=BillRequest.AWAITING).aggregate(s=Sum("total"))["s"] or 0
    ctx = {"page": page, "status": status, "tabs": BILL_REQUEST_TABS, "counts": counts, "q": q,
           "awaiting_total": awaiting_total, "hero": _bill_hero(request), "fin_tab": "approvals",
           "can_prepare": br_can_prepare(request.user)}
    return render_qa(request, "payments/bill_requests.html", ctx, {"qa-panel": "payments/_bill_requests_panel.html"})


def br_can_prepare(user):
    from . import bill_requests as br_svc

    return br_svc.can_prepare(user)


def _bill_request_ctx(request, br, **extra):
    from . import bill_requests as br_svc

    can_decide, why = br_svc.can_decide(br, request.user, impersonating=_impersonating(request))
    steps = [("draft", "Draft", br.created_at), ("awaiting_es", "Awaiting ES approval", br.submitted_at),
             ("rejected" if br.status == "rejected" else "approved",
              "Rejected" if br.status == "rejected" else "Approved & sent", br.decided_at)]
    order = {"draft": 0, "awaiting_es": 1, "approved": 2, "rejected": 2, "withdrawn": -1}
    at = order.get(br.status, 0)
    ctx = {"br": br, "lines": br.priced_lines, "hero": _bill_hero(request), "fin_tab": "approvals",
           "steps": [{"code": c, "label": lbl, "when": w, "done": i < at or (i == at and i == 2),
                      "now": i == at and i < 2} for i, (c, lbl, w) in enumerate(steps)],
           "can_decide": can_decide, "decide_why": why, "is_es": br_svc.is_approver(request.user),
           "is_mine": br.created_by_id == request.user.pk,
           "groups": _group_lines(br.priced_lines)}
    ctx.update(extra)
    return ctx


def _group_lines(lines):
    from . import bill_requests as br_svc

    titles = {br_svc.NEW: "New programme approval", br_svc.REACC: "Re-accreditation", br_svc.FEE: "Other fees"}
    out = {}
    for line in lines:
        g = out.setdefault(line.get("group") or "fee", {"title": titles.get(line.get("group"), "Fees"),
                                                       "lines": [], "subtotal": Decimal("0")})
        g["lines"].append(line)
        g["subtotal"] += line["amount"]
    return list(out.values())


@require(br_can_view)
@require_GET
def bill_request_detail(request, pk):
    br = get_object_or_404(_bill_requests_qs(request.user), pk=pk)
    return render_qa(request, "payments/bill_request.html", _bill_request_ctx(request, br),
                     {"qa-panel": "payments/_bill_request_panel.html"})


@require(br_can_view)
@require_POST
def bill_request_action(request, pk, verb):
    from . import bill_requests as br_svc

    br = get_object_or_404(_bill_requests_qs(request.user), pk=pk)
    remark = (request.POST.get("remark") or "").strip()
    messages_ = {
        "submit": ("Sent to the Executive Secretary", "The institution sees the bill only after the ES approves."),
        "approve": ("Bill approved and sent", "The bill was issued and the institution has been notified."),
        "reject": ("Bill rejected", "The officer who prepared it has been told why."),
        "withdraw": ("Bill withdrawn", "Nothing was sent to the institution."),
    }
    if verb not in messages_:
        raise Http404
    try:
        if verb == "submit":
            if br.created_by_id != request.user.pk and not request.user.is_superuser:
                raise PermissionDenied("Only the officer who prepared this draft can send it.")
            br = br_svc.submit(br, actor=request.user)
        elif verb == "approve":
            br = br_svc.approve(br, actor=request.user, remark=remark, impersonating=_impersonating(request))
        elif verb == "reject":
            br = br_svc.reject(br, actor=request.user, remark=remark, impersonating=_impersonating(request))
        else:
            br = br_svc.withdraw(br, actor=request.user)
    except (ValidationError, PermissionDenied) as exc:
        msg = " ".join(getattr(exc, "messages", [str(exc)]))
        br.refresh_from_db()
        ctx = _bill_request_ctx(request, br, flash={"kind": "danger", "title": "Not done", "message": msg})
        response = render_qa(request, "payments/bill_request.html", ctx,
                             {"qa-panel": "payments/_bill_request_panel.html"},
                             status=403 if isinstance(exc, PermissionDenied) else 422)
        return toast(response, "error", "Not done", msg[:200])
    title, body = messages_[verb]
    if verb == "approve" and br.obligation_id:
        body = f"Issued as {br.obligation.reference} ({naira(br.total)}). {br.institution.name} has been notified."
    ctx = _bill_request_ctx(request, br, flash={"kind": "danger" if verb == "reject" else "success",
                                                "title": title, "message": body})
    response = render_qa(request, "payments/bill_request.html", ctx, {"qa-panel": "payments/_bill_request_panel.html"})
    return toast(response, "success", title, body)


@require(can_view_finance)
@require_GET
def finance_settings(request):
    context = {"fin_tab": "settings", "fin_title": "Decision policy",
               "fin_lead": "Payment evidence goes directly to the Director of Finance for a final decision.",
               "hero": _hero(request)}
    return render_qa(request, "payments/settings.html", context,
                     {"qa-panel": "payments/_settings_panel.html"})


# ===========================================================================
# v2.2 — Settings → Fees by institution category
# ===========================================================================
TYPE_FEE_FIELDS = [
    ("New institution (SOP-01)", [
        ("registration_fee", "Registration fee", "Paid once when a new institution registers."),
        ("registration_processing_fee", "Registration processing fee", "Added to the registration bill."),
    ]),
    ("Programmes added to NBTE", [
        ("programme_fee", "Programme fee (per programme)", "Charged for each programme on the bill."),
        ("programme_processing_fee", "Programme processing fee", "Once per bill, or per programme (switch below)."),
        ("extra_stream_fee", "Extra stream fee", "Each stream asked for beyond one per programme."),
    ]),
    ("QA requests by existing institutions (SOP-02)", [
        ("qa_request_base_fee", "QA request base fee", "Fixed part of an accreditation / re-accreditation bill."),
        ("qa_request_fee_per_programme", "QA request fee (per programme)", "Charged for each programme in the request."),
        ("qa_request_processing_fee", "QA request processing fee", "Added once to the QA request bill."),
    ]),
]


def can_edit_type_fees(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    import org
    return bool(user.is_superuser or org.is_es(user) or org.is_super_admin(user)
                or services.is_final_approver(user)
                or user.has_perm("payments.change_institutiontypefee"))


def _type_fees_ctx(request, **extra):
    from qa_core.views import _settings_context
    from institutions.models import InstitutionType
    from .fee_table_seed import seed_type_fees
    from .models import InstitutionTypeFee

    seed_type_fees(InstitutionType, InstitutionTypeFee, activate=False)   # a new category gets an (off) row
    rows = list(InstitutionTypeFee.objects.select_related("institution_type", "updated_by")
                .order_by("institution_type__sort_order", "institution_type__name"))
    for r in rows:
        r.groups = [(title, [(f, label, help_, getattr(r, f)) for f, label, help_ in fields])
                    for title, fields in TYPE_FEE_FIELDS]
        r.example_registration = r.registration_fee + r.registration_processing_fee
        times = 3 if r.programme_processing_per_programme else 1
        r.example_programmes = r.programme_fee * 3 + r.programme_processing_fee * times
    ctx = _settings_context(request, "type_fees", rows=rows, can_edit=can_edit_type_fees(request.user))
    ctx.update(extra)
    return ctx


@require(_can_see_fee_settings)
@require_GET
def settings_type_fees(request):
    return render_qa(request, "payments/settings/type_fees.html", _type_fees_ctx(request),
                     {"qa-panel": "payments/settings/_type_fees_panel.html"})


@require(_can_see_fee_settings)
@require_POST
def settings_type_fee_save(request, pk):
    from decimal import Decimal, InvalidOperation
    from .models import InstitutionTypeFee

    if not can_edit_type_fees(request.user) or _impersonating(request):
        raise PermissionDenied
    row = get_object_or_404(InstitutionTypeFee, pk=pk)
    errors, changes = [], {}
    for _title, fields in TYPE_FEE_FIELDS:
        for f, label, _h in fields:
            raw = (request.POST.get(f) or "0").replace(",", "").replace("₦", "").strip() or "0"
            try:
                value = Decimal(raw).quantize(Decimal("0.01"))
                if value < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                errors.append(f"{label}: enter an amount in naira (numbers only).")
                continue
            if getattr(row, f) != value:
                changes[f] = {"old": str(getattr(row, f)), "new": str(value)}
                setattr(row, f, value)
    for flag in ("programme_processing_per_programme", "is_confirmed", "is_active"):
        value = request.POST.get(flag) == "on"
        if getattr(row, flag) != value:
            changes[flag] = {"old": getattr(row, flag), "new": value}
            setattr(row, flag, value)
    row.notes = (request.POST.get("notes") or "")[:255]
    if errors:
        response = render(request, "payments/settings/_type_fees_panel.html",
                          _type_fees_ctx(request, error=" ".join(errors), open_pk=row.pk), status=422)
        return toast(response, "error", "Not saved", errors[0])
    if row.is_active and not any(getattr(row, f) > 0 for f in ("registration_fee", "programme_fee",
                                                                  "qa_request_fee_per_programme",
                                                                  "qa_request_base_fee")):
        response = render(request, "payments/settings/_type_fees_panel.html",
                          _type_fees_ctx(request, error="Enter at least one fee before switching this category on.",
                                         open_pk=row.pk), status=422)
        return toast(response, "error", "Not saved", "Enter at least one fee first.")
    row.updated_by = request.user
    row.save()
    if changes:
        audit.log("fee_table_change", row, actor=request.user, changes=changes,
                  note=f"Fees for {row.institution_type.name}"[:255])
    response = render(request, "payments/settings/_type_fees_panel.html",
                      _type_fees_ctx(request, saved_pk=row.pk))
    return toast(response, "success", f"{row.institution_type.name} fees saved",
                 "New bills use these figures. Bills already issued keep their amounts.")


# ===========================================================================
# v2.2 — Ledger and reconciliation
# ===========================================================================
def _can_run_reconciliation(user):
    import org
    return bool(user.is_superuser or org.is_es(user) or org.is_super_admin(user) or services.is_final_approver(user))


@require(can_view_finance)
@require_GET
def reconciliation(request, pk=None):
    return _reconciliation_response(request, pk)


def _reconciliation_response(request, pk=None):
    from . import ledger
    from .models import ReconciliationIssue, ReconciliationRun

    runs = list(ReconciliationRun.objects.all()[:15])
    run = (ReconciliationRun.objects.filter(pk=pk).first() if pk else (runs[0] if runs else None))
    if pk and run is None:
        raise Http404
    issues = list(run.issues.select_related("obligation", "attempt", "resolved_by")) if run else []
    open_issues = ReconciliationIssue.objects.filter(resolved_at__isnull=True).count()
    trial = ledger.trial_balance()
    ctx = {"runs": runs, "run": run, "issues": issues, "open_issues": open_issues, "hero": _hero(request),
           "trial": trial, "tb": _trial_groups(trial), "can_run": _can_run_reconciliation(request.user),
           "severity": {k: sum(1 for i in issues if i.severity == k and not (i.auto_fixed or i.resolved_at))
                        for k in ("critical", "warning", "info")},
           "fixed": sum(1 for i in issues if i.auto_fixed or i.resolved_at)}
    balances = {acct.code: b for acct, _d, _c, b in trial}
    ctx["cards"] = {"bank": balances.get(ledger.BANK, 0), "receivable": balances.get(ledger.RECEIVABLE, 0),
                    "income": balances.get(ledger.INCOME, 0), "credits": balances.get(ledger.CREDITS, 0),
                    "waived": balances.get(ledger.WAIVED, 0)}
    return render_qa(request, "payments/reconciliation.html", ctx, {"qa-panel": "payments/_reconciliation_panel.html"})


TB_GROUPS = [("asset", "Assets"), ("liability", "Liabilities"), ("income", "Income"), ("expense", "Expenses")]


def _trial_groups(trial):
    """Trial balance rows grouped by account kind, with subtotals and the
    grand totals (debits must equal credits)."""
    zero = Decimal("0.00")
    groups = []
    for kind, title in TB_GROUPS:
        rows = [{"acct": a, "debit": d, "credit": c, "balance": b} for a, d, c, b in trial if a.kind == kind]
        if rows:
            groups.append({"kind": kind, "title": title, "rows": rows,
                           "debit": sum((r["debit"] for r in rows), zero),
                           "credit": sum((r["credit"] for r in rows), zero),
                           "balance": sum((r["balance"] for r in rows), zero)})
    debit = sum((d for _a, d, _c, _b in trial), zero)
    credit = sum((c for _a, _d, c, _b in trial), zero)
    return {"groups": groups, "debit": debit, "credit": credit, "difference": debit - credit,
            "balanced": debit == credit, "accounts": len(trial)}


@require(can_view_finance)
@require_POST
def reconciliation_run(request):
    from . import reconcile

    if not _can_run_reconciliation(request.user):
        raise PermissionDenied
    try:
        run = reconcile.run(trigger="manual", actor=request.user)
    except Exception as exc:  # report, never 500
        response = _reconciliation_response(request)
        return toast(response, "error", "Reconciliation did not finish", str(exc)[:200])
    response = _reconciliation_response(request, run.pk)
    return toast(response, "success", "Reconciliation finished",
                 f"{run.issues_found} issue(s), {run.auto_fixed} corrected automatically.")


@require(can_view_finance)
@require_POST
def reconciliation_resolve(request, issue_pk):
    from .models import ReconciliationIssue

    if not _can_run_reconciliation(request.user):
        raise PermissionDenied
    issue = get_object_or_404(ReconciliationIssue, pk=issue_pk)
    note = (request.POST.get("resolution") or "").strip()
    if len(note) < 5:
        response = _reconciliation_response(request, issue.run_id)
        return toast(response, "error", "Not resolved", "Write what was done (at least 5 characters).")
    issue.resolved_at, issue.resolved_by, issue.resolution = timezone.now(), request.user, note[:2000]
    issue.save(update_fields=["resolved_at", "resolved_by", "resolution"])
    audit.log("other", issue.obligation or issue.run, actor=request.user,
              note=f"Reconciliation issue resolved: {note[:200]}")
    response = _reconciliation_response(request, issue.run_id)
    return toast(response, "success", "Marked resolved", "")
