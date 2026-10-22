"""Payments on the portal (§10.2 step 3): bills, slips, pay online, "I have
paid", receipts and advices — plus the provider webhooks, the public slip /
advice verification page (from the QR code) and the development payment
simulator. Institutions never see the Finance queue (internal host only)."""
from __future__ import annotations

import datetime

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

import config
from institutions.forms import QAForm
from qa_core.portal_views import PORTAL_NAME, FULL_BASE, _shell_context, institution_required
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import providers, services
from .models import PaymentAdvice, PaymentAttempt, PaymentObligation, PaymentSlip, PaymentVerification, ProofOfPayment
from .money import naira

STATUS_GROUPS = [("", "All bills"), ("due", "To pay"), ("checking", "Being confirmed"), ("settled", "Settled"),
                 ("closed", "Cancelled / expired")]
PERIODS = {"30d": 30, "90d": 90, "1y": 365}


def _group_filter(qs, group):
    if group == "due":
        return qs.filter(status__in=PaymentObligation.OPEN_FOR_PAYMENT)
    if group == "checking":
        return qs.filter(status__in=PaymentObligation.CLAIMED)
    if group == "settled":
        return qs.filter(status__in=PaymentObligation.SETTLED)
    if group == "closed":
        return qs.filter(status__in=("cancelled", "expired"))
    return qs


@institution_required
def payments(request):
    qs = PaymentObligation.objects.for_user(request.user).select_related("fee_schedule")
    group = request.GET.get("group", "")
    qs = _group_filter(qs, group)
    fee = request.GET.get("fee", "")
    if fee:
        qs = qs.filter(fee_schedule__code=fee)
    period = request.GET.get("period", "")
    if period in PERIODS:
        qs = qs.filter(created_at__gte=timezone.now() - datetime.timedelta(days=PERIODS[period]))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(description__icontains=q) | Q(source_reference__icontains=q))
    page = paginate(request, qs.order_by("-created_at"))
    for ob in page["rows"]:
        ob.public_label = services.stage_label(ob, public=True)
    from .models import FeeSchedule

    all_qs = PaymentObligation.objects.for_user(request.user)
    ctx = _shell_context(request, "payments", page=page, filters=request.GET, groups=STATUS_GROUPS,
                         fees=FeeSchedule.objects.filter(is_active=True), periods=PERIODS,
                         due_total=sum((o.balance for o in all_qs.filter(status__in=PaymentObligation.OPEN_FOR_PAYMENT)),
                                       start=0),
                         counts={"due": all_qs.filter(status__in=PaymentObligation.OPEN_FOR_PAYMENT).count(),
                                 "checking": all_qs.filter(status__in=PaymentObligation.CLAIMED).count(),
                                 "settled": all_qs.filter(status__in=PaymentObligation.SETTLED).count()})
    return render_qa(request, "qa_portal/payments/list.html", ctx, {"qa-pay-rows": "qa_portal/payments/_rows.html"})


class ProofForm(QAForm):
    amount_claimed = forms.DecimalField(max_digits=16, decimal_places=2, min_value=0, label="Amount paid (₦)")
    paid_on = forms.DateField(label="Date paid", widget=forms.DateInput(attrs={"type": "date"}))
    bank_name = forms.CharField(max_length=120, label="Bank")
    teller_number = forms.CharField(max_length=60, label="Teller / transaction number")
    payer_name = forms.CharField(max_length=150, label="Paid by", required=False)
    file = forms.FileField(label="Teller or transfer receipt")


class ReceiptForm(QAForm):
    file = forms.FileField(label="Provider receipt (PDF or image)")


def _detail_context(request, ob, **extra):
    attempts = list(PaymentAttempt.objects.for_user(request.user).filter(obligation=ob).order_by("-created_at"))
    proofs = list(ProofOfPayment.objects.for_user(request.user).filter(obligation=ob).order_by("-created_at"))
    advices = list(PaymentAdvice.objects.for_user(request.user).filter(obligation=ob).order_by("-generated_at"))
    slip = PaymentSlip.objects.for_user(request.user).filter(obligation=ob, is_current=True).order_by("-version").first()
    open_item = PaymentVerification.objects.for_user(request.user).filter(obligation=ob, is_open=True).first()
    rejected = (PaymentVerification.objects.for_user(request.user).filter(obligation=ob, final_decision="rejected")
                .order_by("-final_decided_at").first())
    needs_info = open_item if open_item and open_item.decision == "needs_info" else None
    readonly = getattr(request, "is_impersonating", False)
    options = pay_options(ob, attempts, proofs, readonly=readonly)
    payable, online = options["payable"], options["online_methods"]
    receipt_needed = options["receipt_needed"]
    ctx = {
        "ob": ob, "public_label": services.stage_label(ob, public=True), "slip": slip, "attempts": attempts,
        "proofs": proofs, "advices": advices, "payable": payable, "online_methods": online,
        "bank_transfer": options["bank_transfer"],
        "proof_form": extra.pop("proof_form", None) or ProofForm(initial={"amount_claimed": ob.balance,
                                                                            "paid_on": timezone.localdate()}),
        "receipt_form": ReceiptForm(), "receipt_needed": receipt_needed, "rejected": rejected,
        "needs_info": needs_info, "readonly": readonly,
        "allow_partial": str(config.get("qa.payment.allow_partial", False)).lower() in ("true", "1"),
        "proof_exts": ",".join("." + e for e in (config.get("qa.payment.proof_extensions", ["pdf", "jpg", "jpeg", "png"])
                                                  or [])),
        "proof_max_mb": config.get("qa.payment.proof_max_mb", 5),
        "steps": _steps(ob),
        "slip_expired": bool(slip and slip.is_expired),
    }
    ctx.update(extra)
    return _shell_context(request, "payments", **ctx)


def pay_options(ob, attempts, proofs, *, readonly=False):
    """What the institution can do on a bill now (the payment page and the
    mobile API, Phase 9): pay at all, which online providers, bank transfer,
    and whether the provider's receipt is still needed."""
    payable = ob.status in ("awaiting_payment", "rejected", "partially_paid") and not readonly
    online = [p for p in providers.enabled_methods() if p.online] if payable else []
    receipt_needed = (ob.status in ("provider_reported_paid", "awaiting_finance_confirmation",
                                    "finance_confirmation_overdue")
                      and any(a.status == "provider_reported_paid" for a in attempts)
                      and not any(p.kind == "provider_receipt" for p in proofs)
                      and str(config.get("qa.payment.receipt_required_for_provider_payments", True)).lower()
                      in ("true", "1") and not readonly)
    return {"payable": payable, "online_methods": online, "receipt_needed": receipt_needed,
            "bank_transfer": payable and providers.get("bank_transfer").is_enabled() or (payable and not online)}


def _steps(ob):
    order = ["issued", "paid", "checking", "confirmed"]
    state = {"issued": 0, "awaiting_payment": 0, "rejected": 0, "partially_paid": 1, "provider_processing": 1,
             "provider_reported_paid": 2, "proof_submitted": 2, "awaiting_finance_confirmation": 2,
             "finance_confirmation_overdue": 2, "confirmed": 3, "overpaid": 3, "refund_requested": 3,
             "refunded": 3, "waived": 3}.get(ob.status, 0)
    labels = {"issued": "Bill issued", "paid": "You pay", "checking": "Finance checks",
              "confirmed": "Director of Finance confirms"}
    out = []
    for i, key in enumerate(order):
        out.append({"label": labels[key], "state": "done" if i < state else ("current" if i == state else "todo")})
    if ob.status in ("confirmed", "overpaid", "waived", "refunded", "refund_requested"):
        out[-1]["state"] = "done"
    return out


def _obligation(request, pk):
    ob = PaymentObligation.objects.for_user(request.user).filter(pk=pk).select_related(
        "fee_schedule", "institution").first()
    if ob is None:
        raise Http404
    return ob


@institution_required
def payment(request, pk):
    ob = _obligation(request, pk)
    if request.method == "GET" and any(k in request.GET for k in ("reference", "trxref", "RRR", "rrr", "orderID")):
        attempt = services.verify_on_return(ob, request.GET)
        if attempt is not None:
            ob.refresh_from_db()
            response = render(request, "qa_portal/payments/detail.html", _detail_context(request, ob))
            if attempt.status == "provider_reported_paid":
                return toast(response, "success", "Payment received by the provider",
                             "NBTE Finance will now confirm it. You will be notified.")
            if attempt.status == "failed":
                return toast(response, "error", "The payment did not go through",
                             attempt.failure_reason or "Try again or choose another way to pay.")
            return response
    return render(request, "qa_portal/payments/detail.html", _detail_context(request, ob))


@institution_required
@require_POST
def payment_slip(request, pk):
    """Generate a new slip version (expired slip, new balance)."""
    ob = _obligation(request, pk)
    try:
        services.regenerate_slip(ob, actor=request.user)
    except ValidationError as exc:
        response = render(request, "qa_portal/payments/detail.html",
                          _detail_context(request, ob, error=" ".join(exc.messages)))
        return toast(response, "error", "No new slip", " ".join(exc.messages))
    ob.refresh_from_db()
    response = render(request, "qa_portal/payments/detail.html", _detail_context(request, ob))
    return toast(response, "success", "New slip ready", "Same payment reference, new version.")


@institution_required
@require_POST
def payment_online(request, pk):
    ob = _obligation(request, pk)
    provider = request.POST.get("provider", "")
    amount = request.POST.get("amount") or None
    from qa_core.hosts import portal_absolute

    callback = portal_absolute(reverse("qa_portal:payment", args=[ob.pk]), request)
    try:
        attempt = services.start_online_payment(ob, provider, user=request.user, amount=amount,
                                                callback_url=callback, request=request)
    except ValidationError as exc:
        response = render(request, "qa_portal/payments/detail.html",
                          _detail_context(request, ob, error=" ".join(exc.messages)), status=422)
        return toast(response, "error", "Could not start the payment", " ".join(exc.messages))
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = attempt.checkout_url
        return response
    return HttpResponseRedirect(attempt.checkout_url)


@institution_required
@require_POST
def payment_proof(request, pk):
    ob = _obligation(request, pk)
    form = ProofForm(request.POST, request.FILES)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.submit_proof(ob, user=request.user, upload=d["file"], payer_name=d.get("payer_name", ""),
                                  paid_on=d["paid_on"], amount_claimed=d["amount_claimed"], bank_name=d["bank_name"],
                                  teller_number=d["teller_number"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            ob.refresh_from_db()
            response = render(request, "qa_portal/payments/detail.html", _detail_context(request, ob))
            return toast(response, "success", "Proof uploaded",
                         "Institution claims paid — awaiting Finance confirmation.")
    response = render(request, "qa_portal/payments/detail.html",
                      _detail_context(request, ob, proof_form=form, open_proof=True), status=422)
    return toast(response, "error", "Check the form", "Some details are missing or not valid.")


@institution_required
@require_POST
def payment_receipt(request, pk):
    ob = _obligation(request, pk)
    form = ReceiptForm(request.POST, request.FILES)
    attempt = PaymentAttempt.objects.for_user(request.user).filter(obligation=ob, status="provider_reported_paid") \
        .order_by("-created_at").first()
    if form.is_valid() and attempt is not None:
        try:
            services.submit_proof(ob, user=request.user, upload=form.cleaned_data["file"], kind="provider_receipt",
                                  attempt=attempt, amount_claimed=attempt.amount)
        except ValidationError as exc:
            response = render(request, "qa_portal/payments/detail.html",
                              _detail_context(request, ob, error=" ".join(exc.messages)), status=422)
            return toast(response, "error", "Receipt not uploaded", " ".join(exc.messages))
        ob.refresh_from_db()
        response = render(request, "qa_portal/payments/detail.html", _detail_context(request, ob))
        return toast(response, "success", "Receipt uploaded", "Finance will check it with the provider's report.")
    raise Http404


# ---------------------------------------------------------------------------
# Provider webhooks (portal host; CSRF-exempt by necessity, signature-checked)
# ---------------------------------------------------------------------------
@csrf_exempt
@require_POST
def webhook_paystack(request):
    status, message = services.handle_webhook("paystack", request)
    return HttpResponse(message, status=status, content_type="text/plain")


@csrf_exempt
@require_POST
def webhook_remita(request):
    status, message = services.handle_webhook("remita", request)
    return HttpResponse(message, status=status, content_type="text/plain")


# ---------------------------------------------------------------------------
# Public verification page (QR code on slips and advices)
# ---------------------------------------------------------------------------
def verify(request, token):
    summary = services.verification_summary(token)
    if summary is None:
        raise Http404("This verification code is not valid.")
    return render(request, "qa_portal/verify.html", {"portal_name": PORTAL_NAME, "base_template": FULL_BASE,
                                                     "section": "verify", "v": summary})


# ---------------------------------------------------------------------------
# Phase 9: back to the mobile app after an online payment (payments/app_return.py)
# ---------------------------------------------------------------------------
@require_http_methods(["GET"])
def payment_app_return(request, pk, token):
    """The provider's return page for a payment started in the app: verify
    the attempt as the portal page does, then hand over to the app. No
    website session needed - the signed token is the permission."""
    from . import app_return

    if not app_return.token_ok(token, pk):
        raise Http404
    ob = PaymentObligation.unscoped.filter(pk=pk).first()
    if ob is None:
        raise Http404
    attempt = None
    if any(k in request.GET for k in ("reference", "trxref", "RRR", "rrr", "orderID")):
        attempt = services.verify_on_return(ob, request.GET)
    if attempt is None:
        attempt = PaymentAttempt.unscoped.filter(obligation=ob).order_by("-created_at").first()
    ob.refresh_from_db()
    status = app_return.status_of(ob, attempt)
    response = render(request, "qa_portal/payments/app_return.html", {
        "portal_name": PORTAL_NAME, "status": status, "deep_link": app_return.deep_link(ob, attempt),
        "reference": attempt.provider_reference if attempt is not None else "",
        "failure_reason": (attempt.failure_reason or "") if attempt is not None and status == "failed" else "",
    })
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------------------
# Development payment simulator (QA_PAYMENTS_SIMULATE only)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def pay_simulator(request, provider, ref):
    from .providers import simulator

    if not getattr(settings, "QA_PAYMENTS_SIMULATE", False) or provider not in ("paystack", "remita"):
        raise Http404
    if not providers.get(provider).is_simulated():
        raise Http404
    state = simulator.lookup(provider, ref)
    if state is None:
        raise Http404
    attempt = services.simulator_attempt(provider, ref)
    if request.method == "POST":
        outcome = request.POST.get("outcome", "success")
        if outcome not in ("success", "failed", "webhook_only"):
            outcome = "success"
        services.simulate_provider_callback(provider, ref, outcome)
        from . import app_return

        if app_return.is_app_attempt(attempt):
            # Phase 9: a payment started from the app goes back to the app
            # (the simulator ignores return URLs, so it adds the reference
            # the real provider would).
            ob = attempt.obligation
            return HttpResponseRedirect(app_return.return_url(ob, request) + "?" + (
                f"reference={ref}" if provider == "paystack" else f"RRR={ref}"))
        if attempt is not None:
            from qa_core.hosts import portal_absolute

            return HttpResponseRedirect(portal_absolute(reverse("qa_portal:payment", args=[attempt.obligation_id]),
                                                        request))
        return HttpResponseRedirect(reverse("qa_portal:dashboard"))
    amount = state.get("amount")
    display = naira(int(amount) / 100) if provider == "paystack" else naira(amount)
    return render(request, "qa_portal/payments/simulator.html",
                  {"portal_name": PORTAL_NAME, "base_template": FULL_BASE, "section": "simulator",
                   "provider": provider, "ref": ref, "amount": display, "attempt": attempt})
