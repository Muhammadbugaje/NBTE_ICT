"""Payments on the portal (§10.2 step 3): bills, slips, pay online, "I have
paid", receipts and advices — plus the provider webhooks, the public slip /
advice verification page (from the QR code) and the development payment
simulator. Institutions never see the Finance queue (internal host only)."""
from __future__ import annotations

import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
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
    # v2.3 portal redesign: money by status for the payments chart (display only).
    money = {"due": 0.0, "checking": 0.0, "settled": 0.0}
    for status, amount, balance in all_qs.values_list("status", "amount", "balance"):
        if status in PaymentObligation.OPEN_FOR_PAYMENT:
            money["due"] += float(balance or 0)
        elif status in PaymentObligation.CLAIMED:
            money["checking"] += float(amount or 0)
        elif status in PaymentObligation.SETTLED:
            money["settled"] += float(amount or 0)
    pay_chart = [{"id": "pp-pay-status", "type": "doughnut", "colors": ["#c8962a", "#2f7cf6", "#1f6b3e"],
                  "data": [{"label": "To pay", "value": money["due"]},
                           {"label": "Being confirmed", "value": money["checking"]},
                           {"label": "Paid", "value": money["settled"]}] if any(money.values()) else []}]
    ctx = _shell_context(request, "payments", page=page, filters=request.GET, groups=STATUS_GROUPS,
                         pay_chart=pay_chart, money=money,
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


METHOD_INFO = {
    "remita": {"label": "Remita", "letter": "R", "tone": "remita",
               "blurb": "Card, bank branch or internet banking with a Remita reference (RRR)."},
    "paystack": {"label": "Paystack", "letter": "P", "tone": "paystack",
                 "blurb": "Card, bank transfer or USSD. Paystack's charge is added at checkout."},
    "bank_transfer": {"label": "Bank transfer", "letter": "", "tone": "bank",
                      "blurb": "Pay into NBTE's account with the slip, then upload the teller or transfer receipt."},
}


def _method_list(ob):
    return [{"code": code, **METHOD_INFO[code]} for code in services.available_methods(ob)]


def _detail_context(request, ob, **extra):
    all_attempts = list(PaymentAttempt.objects.for_user(request.user).filter(obligation=ob).order_by("-created_at"))
    attempts = services.visible_attempts(ob, all_attempts)  # P9: only the chosen method, never abandoned
    proofs = list(ProofOfPayment.objects.for_user(request.user).filter(obligation=ob).order_by("-created_at"))
    advices = list(PaymentAdvice.objects.for_user(request.user).filter(obligation=ob).order_by("-generated_at"))
    slip = PaymentSlip.objects.for_user(request.user).filter(obligation=ob, is_current=True).order_by("-version").first()
    open_item = PaymentVerification.objects.for_user(request.user).filter(obligation=ob, is_open=True).first()
    rejected = (PaymentVerification.objects.for_user(request.user).filter(obligation=ob, final_decision="rejected")
                .order_by("-final_decided_at").first())
    confirmed_item = (PaymentVerification.objects.for_user(request.user).filter(obligation=ob, final_decision="confirmed")
                      .select_related("attempt").order_by("-final_decided_at").first())
    needs_info = open_item if open_item and open_item.decision == "needs_info" else None
    readonly = getattr(request, "is_impersonating", False)
    options = pay_options(ob, all_attempts, proofs, readonly=readonly, item=open_item)
    payable, online = options["payable"], options["online_methods"]
    methods = _method_list(ob)
    chosen = ob.chosen_method if ob.chosen_method in [m["code"] for m in methods] else ""
    if not chosen and len(methods) == 1:
        chosen = methods[0]["code"]  # one method only (e.g. bank transfer fallback): show its steps at once
    remita_attempt = next((a for a in attempts if a.provider == "remita" and a.status in ("initiated", "pending")), None)
    ctx = {
        "ob": ob, "public_label": services.stage_label(ob, public=True), "slip": slip, "attempts": attempts,
        "proofs": proofs, "advices": advices, "payable": payable, "online_methods": online,
        "bank_transfer": options["bank_transfer"],
        "proof_form": extra.pop("proof_form", None) or ProofForm(initial={"amount_claimed": ob.balance,
                                                                            "paid_on": timezone.localdate()}),
        "receipt_form": ReceiptForm(), "receipt_needed": options["receipt_needed"], "rejected": rejected,
        "needs_info": needs_info, "readonly": readonly,
        "allow_partial": str(config.get("qa.payment.allow_partial", False)).lower() in ("true", "1"),
        "proof_exts": ",".join("." + e for e in (config.get("qa.payment.proof_extensions", ["pdf", "jpg", "jpeg", "png"])
                                                  or [])),
        "proof_max_mb": config.get("qa.payment.proof_max_mb", 5),
        "steps": _steps(ob),
        "slip_expired": bool(slip and slip.is_expired),
        # v2.1 F.4 / F.18
        "breakdown": _public_breakdown(ob.breakdown), "methods": methods, "chosen": chosen,
        "chosen_info": METHOD_INFO.get(chosen), "hidden_labels": [m["label"] for m in methods if m["code"] != chosen],
        "remita_rrr": (remita_attempt.provider_reference if remita_attempt else "") or (ob.remita_rrr if chosen == "remita" else ""),
        "remita_attempt": remita_attempt,
        "gateway_verified": bool(open_item and services.is_gateway_item(open_item)),
        "checking_payment": bool(open_item and open_item.amount_matches is False),
        "timeline": _status_timeline(ob, open_item, attempts, proofs, confirmed_item),
        "paid": ob.status in ("confirmed", "overpaid", "refund_requested", "refunded"),
        "receipt_item": confirmed_item, "receipt": advices[0] if advices else None,
        "next_step": services.next_step(ob),
        "receipt_qr": _qr_svg(advices[0].qr_payload) if advices and ob.status in PaymentObligation.SETTLED else "",
        "poll": ob.status in PaymentObligation.CLAIMED and not readonly,
    }
    ctx.update(extra)
    return _shell_context(request, "payments", **ctx)


def _public_breakdown(rows):
    """v2.3: institutions never see internal process codes (e.g. "(SOP-02)") in fee names."""
    import re

    out = []
    for row in rows or []:
        if isinstance(row, dict):
            row = dict(row)
            for key in ("label", "description"):
                if isinstance(row.get(key), str):
                    row[key] = re.sub(r"\s*\(SOP[- ]?\d+\)", "", row[key])
        out.append(row)
    return out


def _qr_svg(payload):
    """Inline SVG QR for the on-screen receipt (qrcode is already a dependency)."""
    import io

    import qrcode
    import qrcode.image.svg
    from django.utils.safestring import mark_safe

    img = qrcode.make(payload, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    svg = svg[svg.find("<svg"):]
    return mark_safe(svg)


def _status_timeline(ob, item, attempts, proofs, confirmed_item):
    """Bill issued → payment evidence → Director of Finance decision."""
    label = METHOD_INFO.get(ob.chosen_method, {}).get("label", "")
    claimed = ob.status in PaymentObligation.CLAIMED
    settled = ob.status in PaymentObligation.SETTLED
    rows = [{"title": "Bill issued", "sub": timezone.localtime(ob.created_at).strftime("%a %d %b"), "state": "done",
             "icon": "file"}]
    if ob.chosen_method:
        rows.append({"title": f"You chose {label}", "sub": timezone.localtime(ob.chosen_at).strftime("%d %b, %H:%M")
                     if ob.chosen_at else "", "state": "done", "icon": "check"})
    else:
        rows.append({"title": "Choose how to pay", "sub": "Remita, Paystack or bank transfer",
                     "state": "now" if not (claimed or settled) else "done", "icon": "card"})
    if claimed or settled:
        paid_sub = "Received by the provider" if any(a.status == "provider_reported_paid" for a in attempts) \
            else ("Proof uploaded" if proofs else "Recorded")
        rows.append({"title": "Payment evidence", "sub": paid_sub, "state": "done", "icon": "card"})
    else:
        rows.append({"title": "Payment", "sub": f"Waiting for {label}" if label else "After you choose a method",
                     "state": "now" if ob.chosen_method else "todo", "icon": "card"})
    gateway = bool(item and services.is_gateway_item(item)) or bool(
        confirmed_item and confirmed_item.verified_via == "gateway")
    if gateway and claimed:
        rows.append({"title": "Provider verification", "sub": "Reference, amount and currency checked",
                     "state": "done", "icon": "shield"})
    rows.append({"title": "Director of Finance confirms", "sub": "Confirmed" if settled else
                 "Evidence received; awaiting the Director's decision" if claimed else
                 "You are told on your dashboard when this is confirmed",
                 "state": "done" if settled else ("now" if claimed else "todo"),
                 "icon": "shield"})
    return rows


def pay_options(ob, attempts, proofs, *, readonly=False, item=None):
    """What the institution can do on a bill now (the payment page and the
    mobile API, Phase 9): pay at all, which online providers, bank transfer,
    and whether the provider's receipt is still needed. v2.1 F.4 (P9): once a
    method is chosen only that method is offered; F.18 (X21): a
    gateway-verified payment never asks for the receipt."""
    payable = ob.status in ("awaiting_payment", "rejected", "partially_paid") and not readonly
    online = [p for p in providers.enabled_methods() if p.online] if payable else []
    bank = payable and providers.get("bank_transfer").is_enabled() or (payable and not online)
    chosen = ob.chosen_method
    if chosen and payable:
        online = [p for p in online if p.code == chosen]
        bank = bank and chosen == "bank_transfer"
    if item is None:
        item = services.open_verification(ob)
    gateway = bool(item is not None and services.is_gateway_item(item))
    receipt_needed = (ob.status in ("provider_reported_paid", "awaiting_finance_confirmation",
                                    "finance_confirmation_overdue")
                      and not gateway
                      and any(a.status == "provider_reported_paid" for a in attempts)
                      and not any(p.kind == "provider_receipt" for p in proofs)
                      and str(config.get("qa.payment.receipt_required_for_provider_payments", False)).lower()
                      in ("true", "1") and not readonly)
    return {"payable": payable, "online_methods": online, "receipt_needed": receipt_needed,
            "bank_transfer": bank, "chosen_method": chosen, "gateway_verified": gateway}


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


def _welcome_redirect(request, ob):
    """P10: the first visit after the Director of Finance confirmed the bill
    goes to the dashboard welcome card (then the bill shows the receipt)."""
    if ob.status not in ("confirmed", "overpaid") or ob.welcomed_at or getattr(request, "is_impersonating", False):
        return None
    services.mark_welcomed(ob)
    url = f"{reverse('qa_portal:dashboard')}?welcome={ob.pk}"
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return HttpResponseRedirect(url)


@institution_required
def payment(request, pk):
    ob = _obligation(request, pk)
    redirect = _welcome_redirect(request, ob)
    if redirect is not None:
        return redirect
    if request.GET.get("poll") and is_htmx(request):
        # Live status card while Finance confirms (design 08 "Updated live").
        return render(request, "qa_portal/payments/_status.html", _detail_context(request, ob))
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
            if getattr(attempt, "verify_unreachable", False):
                return toast(response, "warning", "Checking your payment",
                             "We could not reach the provider to confirm it yet. Do not pay again — we keep "
                             "checking automatically and this page updates when it is confirmed.")
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
def payment_method(request, pk):
    """P9: choose a method (the others disappear) or, with an empty method,
    "Change method" (every method comes back; open attempts are abandoned)."""
    ob = _obligation(request, pk)
    if getattr(request, "is_impersonating", False):
        raise Http404
    method = (request.POST.get("method") or "").strip()
    try:
        if method:
            services.choose_method(ob, method, user=request.user)
        else:
            services.change_method(ob, user=request.user)
    except ValidationError as exc:
        response = render(request, "qa_portal/payments/detail.html",
                          _detail_context(request, ob, error=" ".join(exc.messages)), status=422)
        return toast(response, "error", "Not changed", " ".join(exc.messages))
    ob.refresh_from_db()
    return render(request, "qa_portal/payments/detail.html", _detail_context(request, ob))


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
        kind = getattr(exc, "kind", "refused")
        titles = {"unreachable": "Can't reach the payment provider",
                  "config": "Online payment is unavailable right now",
                  "already_paid": "Already paid",
                  "refused": "Could not start the payment"}
        ob.refresh_from_db()
        response = render(request, "qa_portal/payments/detail.html",
                          _detail_context(request, ob, error=" ".join(exc.messages), error_kind=kind),
                          status=200 if kind == "already_paid" else 422)
        return toast(response, "success" if kind == "already_paid" else "error", titles.get(kind, titles["refused"]),
                     " ".join(exc.messages))
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
# Development payment simulator (QA_PAYMENTS_SIMULATE): the fake checkout the
# provider's authorization URL points at in tests / the owner's demo DB.
# ---------------------------------------------------------------------------
def pay_simulator(request, provider, ref):
    from django.conf import settings

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


# ---------------------------------------------------------------------------
# Public verification page (QR code on slips and advices)
# ---------------------------------------------------------------------------
def verify_rate_limit():
    """Requests per IP per hour on the public verify page (qa.verify.rate_limit_per_hour)."""
    try:
        return max(1, int(config.get("qa.verify.rate_limit_per_hour", 60)))
    except (TypeError, ValueError):
        return 60


@never_cache
def verify(request, token):
    """Public (no login) check of a slip/advice QR code. Phase 0 (X8 slice,
    S-18): rate-limited per IP like the staff-ID verify page, never indexed,
    no referrer leaks the token."""
    from core import ratelimit

    ident = ratelimit.client_ip(request)
    if ratelimit.is_limited("payment-verify", ident, verify_rate_limit()):
        response = render(request, "qa_portal/verify.html",
                          {"portal_name": PORTAL_NAME, "base_template": FULL_BASE, "section": "verify",
                           "v": None, "limited": True}, status=429)
    else:
        ratelimit.hit("payment-verify", ident, 60 * 60)
        summary = services.verification_summary(token)
        if summary is None:
            raise Http404("This verification code is not valid.")
        response = render(request, "qa_portal/verify.html", {"portal_name": PORTAL_NAME, "base_template": FULL_BASE,
                                                             "section": "verify", "v": summary})
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    if response.status_code == 429:
        response["Retry-After"] = "3600"
    return response


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
    ob = app_return.obligation_for_token(pk)
    if ob is None:
        raise Http404
    attempt = None
    if any(k in request.GET for k in ("reference", "trxref", "RRR", "rrr", "orderID")):
        attempt = services.verify_on_return(ob, request.GET)
    if attempt is None:
        attempt = app_return.latest_attempt(ob)
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
# v2.1 F.4 (P10): the dashboard welcome card after a confirmed payment
# ---------------------------------------------------------------------------
def welcome_context(request):
    """{ob, next} when the dashboard is opened with ?welcome=<bill> for one
    of the user's own settled bills; None otherwise."""
    try:
        pk = int(request.GET.get("welcome") or 0)
    except (TypeError, ValueError):
        return None
    if not pk:
        return None
    ob = (PaymentObligation.objects.for_user(request.user).filter(pk=pk, status__in=("confirmed", "overpaid"))
          .select_related("fee_schedule").first())
    if ob is None:
        return None
    return {"ob": ob, "next": services.next_step(ob)}
