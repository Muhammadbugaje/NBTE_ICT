"""Portal door: bills and payments — JSON shapes of what
``payments.portal_views`` shows. Paying is the website's own logic
(``payments.services.start_online_payment`` / ``verify_on_return`` /
``submit_proof`` / ``regenerate_slip``); the app never sees card data, it
opens ``checkout_url`` in the browser."""
import datetime

from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from .portal import file_dict, local_date, money


def obligation_or_404(user, pk):
    """payments.portal_views._obligation."""
    from payments.models import PaymentObligation

    ob = (PaymentObligation.objects.for_user(user).filter(pk=pk)
          .select_related("fee_schedule", "institution").first())
    if ob is None:
        raise Http404
    return ob


def payments_queryset(user, params):
    """payments.portal_views.payments' query and filters."""
    from payments.models import PaymentObligation
    from payments.portal_views import PERIODS, _group_filter

    qs = PaymentObligation.objects.for_user(user).select_related("fee_schedule")
    qs = _group_filter(qs, params.get("group", ""))
    fee = params.get("fee", "")
    if fee:
        qs = qs.filter(fee_schedule__code=fee)
    period = params.get("period", "")
    if period in PERIODS:
        qs = qs.filter(created_at__gte=timezone.now() - datetime.timedelta(days=PERIODS[period]))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(description__icontains=q) | Q(source_reference__icontains=q))
    return qs.order_by("-created_at")


def summary_payload(user):
    """The list page's header numbers and filter choices."""
    from payments.models import FeeSchedule, PaymentObligation
    from payments.portal_views import STATUS_GROUPS

    all_qs = PaymentObligation.objects.for_user(user)
    due_total = sum((o.balance for o in all_qs.filter(status__in=PaymentObligation.OPEN_FOR_PAYMENT)), start=0)
    total, display = money(due_total)
    return {"due_total": total, "due_total_display": display,
            "counts": {"due": all_qs.filter(status__in=PaymentObligation.OPEN_FOR_PAYMENT).count(),
                       "checking": all_qs.filter(status__in=PaymentObligation.CLAIMED).count(),
                       "settled": all_qs.filter(status__in=PaymentObligation.SETTLED).count()},
            "groups": [{"value": v, "label": str(lab)} for v, lab in STATUS_GROUPS],
            "fees": [{"code": f.code, "name": f.name} for f in FeeSchedule.objects.filter(is_active=True)]}


def bill_row(ob):
    from payments import services

    amount, display = money(ob.amount)
    balance, balance_display = money(ob.balance)
    return {"id": ob.pk, "reference": ob.reference, "description": ob.description,
            "fee": ob.fee_schedule.name if ob.fee_schedule_id else "", "amount": amount, "amount_display": display,
            "balance": balance, "balance_display": balance_display, "status": ob.status,
            "status_label": str(services.stage_label(ob, public=True)), "is_settled": bool(ob.is_settled),
            "created_at": ob.created_at, "due_date": local_date(ob.due_at),
            "source_reference": ob.source_reference or ""}


def bill_detail_payload(request, ob):
    """payments.portal_views._detail_context, as JSON."""
    from payments.models import PaymentAdvice, PaymentAttempt, PaymentSlip, PaymentVerification, ProofOfPayment
    from payments.portal_views import _steps, pay_options

    import config

    user = request.user
    attempts = list(PaymentAttempt.objects.for_user(user).filter(obligation=ob).order_by("-created_at"))
    proofs = list(ProofOfPayment.objects.for_user(user).filter(obligation=ob).order_by("-created_at"))
    advices = list(PaymentAdvice.objects.for_user(user).filter(obligation=ob).order_by("-generated_at"))
    slip = PaymentSlip.objects.for_user(user).filter(obligation=ob, is_current=True).order_by("-version").first()
    open_item = PaymentVerification.objects.for_user(user).filter(obligation=ob, is_open=True).first()
    rejected = (PaymentVerification.objects.for_user(user).filter(obligation=ob, final_decision="rejected")
                .order_by("-final_decided_at").first())
    needs_info = open_item if open_item and open_item.decision == "needs_info" else None
    options = pay_options(ob, attempts, proofs, readonly=bool(getattr(request, "is_impersonating", False)))
    paid, paid_display = money(ob.amount_paid or 0)
    out = bill_row(ob)
    bank = None
    if slip is not None and options["bank_transfer"]:
        snap = slip.bank_snapshot or {}
        bank = {"bank_name": snap.get("bank_name", ""), "account_name": snap.get("account_name", ""),
                "account_number": snap.get("account_number", "")}
    exts = config.get("qa.payment.proof_extensions", ["pdf", "jpg", "jpeg", "png"]) or []
    from payments import services as pay

    out.update({
        "breakdown": [{"code": str(line.get("code", "")), "label": str(line.get("label", "")),
                       "description": str(line.get("description", "") or ""), "kind": str(line.get("kind", "")),
                       "amount": money(line["amount"])[0], "amount_display": money(line["amount"])[1]}
                      for line in ob.breakdown],
        "chosen_method": ob.chosen_method or "",
        "methods": [{"code": c, "label": dict(ob.METHOD_CHOICES)[c]} for c in pay.available_methods(ob)]
        if options["payable"] else [],
        "gateway_verified": bool(options.get("gateway_verified")),
        "amount_in_words": ob.amount_in_words or "", "amount_paid": paid, "amount_paid_display": paid_display,
        "remita_rrr": ob.remita_rrr or "", "steps": _steps(ob),
        "rejected_reason": (rejected.final_reason or "") if rejected and ob.status == "rejected" else "",
        "needs_info": (needs_info.reason or "") if needs_info else "",
        "awaiting_confirmation": ob.status in ("provider_reported_paid", "awaiting_finance_confirmation",
                                               "finance_confirmation_overdue", "proof_submitted"),
        "payable": bool(options["payable"]),
        "online_methods": [{"code": m.code, "label": str(m.label), "test_mode": bool(m.is_simulated())}
                           for m in options["online_methods"]],
        "bank_transfer": bool(options["bank_transfer"]),
        "bank_account": bank,
        "receipt_needed": bool(options["receipt_needed"]),
        "allow_partial": str(config.get("qa.payment.allow_partial", False)).lower() in ("true", "1"),
        "proof_extensions": list(exts), "proof_max_mb": config.get("qa.payment.proof_max_mb", 5),
        "slip": None if slip is None else {
            "id": slip.pk, "version": slip.version, "expires_at": slip.expires_at, "is_expired": bool(slip.is_expired),
            "file": file_dict(slip, reverse("api:portal_payment_slip", args=[ob.pk]))},
        "can_regenerate_slip": bool(options["payable"] and (slip is None or slip.is_expired)),
        "advices": [{"id": a.pk, "reference": a.reference, "amount": money(a.amount)[0],
                     "amount_display": money(a.amount)[1], "generated_at": a.generated_at,
                     "file": file_dict(a)} for a in advices],
        "proofs": [{"id": p.pk, "kind": p.kind, "kind_label": p.get_kind_display(),
                    "amount_claimed": money(p.amount_claimed or 0)[0],
                    "amount_claimed_display": money(p.amount_claimed or 0)[1], "paid_on": p.paid_on,
                    "uploaded_at": p.created_at, "file": file_dict(p)} for p in proofs],
        "attempts": [{"id": a.pk, "provider": a.provider, "provider_label": a.get_provider_display(),
                      "reference": a.provider_reference, "amount": money(a.amount)[0],
                      "amount_display": money(a.amount)[1], "status": a.status,
                      "status_label": a.get_status_display(), "created_at": a.created_at,
                      "failure_reason": a.failure_reason or ""} for a in pay.visible_attempts(ob, attempts)],
    })
    return out


def attempt_dict(attempt):
    if attempt is None:
        return None
    return {"id": attempt.pk, "provider": attempt.provider, "reference": attempt.provider_reference,
            "status": attempt.status, "status_label": attempt.get_status_display(),
            "failure_reason": attempt.failure_reason or ""}


def verify_message(attempt):
    """The toast the payment page shows after the provider's return."""
    if attempt is None:
        return ""
    if attempt.status == "provider_reported_paid":
        return "Payment received by the provider. NBTE Finance will now confirm it. You will be notified."
    if attempt.status == "failed":
        return attempt.failure_reason or "The payment did not go through. Try again or choose another way to pay."
    return ""
