"""Payment services (Master Plan §10) — the ONLY code that changes an
obligation's state, always through the seeded ``payment`` flow.

The non-negotiable rule (v1.1, Amendment 1):

* evidence — a verified provider report, an uploaded proof, a statement
  match — moves an obligation at most to ``awaiting_finance_confirmation``;
* a Finance officer records a first-pass recommendation;
* only the holder of the role in ``qa.payment.final_approver_role`` (the
  Director of Finance), holding ``payments.final_confirm_payment``, who did
  not do the first pass, can take ``final_confirm`` into ``confirmed``.

There is no other function here that reaches ``confirmed``.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.dispatch import Signal
from django.utils import timezone

import config
from core import audit
from qa_core import flow, references, timeline
from qa_core.files import validate_upload
from qa_core.formula import FormulaError, evaluate_setting

from . import providers
from .money import amount_in_words, naira, to_money
from .models import (
    ZERO,
    BankAccount,
    BankStatementImport,
    BankStatementLine,
    FeeSchedule,
    PaymentAdvice,
    PaymentAttempt,
    PaymentCredit,
    PaymentObligation,
    PaymentSlip,
    PaymentVerification,
    ProofOfPayment,
    Refund,
    WebhookLog,
)

logger = logging.getLogger(__name__)
FLOW = "payment"

# Provider statuses that end an attempt (Paystack words, Remita codes).
DEFINITIVE_FAILURES = ("failed", "abandoned", "reversed", "02", "022")

# Signals for the source workflows (accreditation listens):
payment_claimed = Signal()     # obligation entered the Finance queue
payment_rejected = Signal()    # the Director of Finance rejected it
payment_settled = Signal()     # balance is zero: confirmed by the Director, or waived


# ===========================================================================
# Settings helpers
# ===========================================================================
def _int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool(key, default=False):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _decimal(key, default):
    try:
        return to_money(config.get(key, default))
    except (TypeError, ValueError):
        return to_money(default)


def final_approver_role():
    return str(config.get("qa.payment.final_approver_role", "qa-finance-director") or "").strip()


def is_final_approver(user):
    role = getattr(getattr(user, "role", None), "code", "")
    return bool(user and user.is_authenticated and role and role == final_approver_role()
                and user.has_perm("payments.final_confirm_payment"))


# ===========================================================================
# Flow helpers
# ===========================================================================
def instance_of(obligation):
    return flow.instance_for(obligation, FLOW)


def _move(obligation, action, actor=None, *, remark="", metadata=None, confirmed=False):
    instance = instance_of(obligation)
    if instance is None:
        raise ValidationError(f"{obligation.reference} has no payment workflow.")
    if confirmed:
        flow.apply_confirmed(instance, action, actor, remark=remark, metadata=metadata)
    else:
        flow.transition(instance, action, actor, remark=remark, metadata=metadata)
    obligation.refresh_from_db()
    return obligation


def _can(obligation, action):
    return flow.can(instance_of(obligation), action)


def stage_label(obligation, *, public=False):
    instance = instance_of(obligation)
    if instance is None:
        return obligation.status
    st = flow.stage(instance, obligation.status)
    if st is None:
        return obligation.status
    return (st.public_label or st.label) if public else st.label


# ===========================================================================
# Verification links (QR codes)
# ===========================================================================
VERIFY_SALT = "qa.payments.verify"


def _signing_key():
    return settings.PAYMENT_QR_SIGNING_KEY or settings.SECRET_KEY


def verify_token(kind, reference, version=0):
    return signing.dumps({"k": kind, "r": reference, "v": version}, key=_signing_key(), salt=VERIFY_SALT,
                         compress=True)


def read_verify_token(token):
    try:
        data = signing.loads(token, key=_signing_key(), salt=VERIFY_SALT)
    except signing.BadSignature:
        return None
    return data if isinstance(data, dict) and data.get("r") else None


def verify_url(kind, reference, version=0):
    from qa_core.hosts import portal_base_url

    token = verify_token(kind, reference, version)
    base = (settings.PAYMENT_VERIFY_BASE_URL or f"{portal_base_url()}/verify/").rstrip("/") + "/"
    return f"{base}{token}/"


# ===========================================================================
# Issuing obligations (§10.2 step 1)
# ===========================================================================
def default_bank_account():
    return (BankAccount.objects.filter(is_active=True, is_placeholder=False).order_by("-is_default", "pk").first())


def bank_for(schedule):
    if schedule.revenue_account_id and schedule.revenue_account.usable:
        return schedule.revenue_account
    return default_bank_account()


def stream_variables(rows):
    """Fee variables for a list of (programme, streams) pairs (Phase 8):
    programmes, new_programmes, streams (each programme counts at least 1)
    and max_streams."""
    streams = [max(1, int(n or 1)) for _p, n in rows]
    return {"programmes": len(rows),
            "new_programmes": sum(1 for p, _n in rows if getattr(p, "status", "") == "proposed"),
            "streams": sum(streams), "max_streams": max(streams, default=0)}


def fee_variables(institution, **extra):
    """Every fee formula gets the documented variables
    (``qa_core.settings_catalogue.FEE_VARIABLES``). Without ``extra`` the
    programme figures cover every programme on the institution's record that
    is not dropped (SOP-01: the programmes it selected, with their streams)."""
    from institutions.models import Programme

    programmes = list(Programme.unscoped.filter(institution=institution).exclude(status="dropped"))
    variables = {
        "ownership": institution.ownership.code if institution.ownership_id else "public",
        "institution_type": institution.institution_type.code if institution.institution_type_id else "",
        **stream_variables([(p, p.requested_streams) for p in programmes]),
    }
    variables.update(extra)
    return variables


def fee_estimate(institution, schedule_code, rows):
    """A live estimate for the institution (HTMX on the portal): the bill the
    formula would give for ``rows`` = [(programme, streams)], with one line per
    programme. Lines are the formula for that programme alone minus the
    formula for none; a fixed part and any non-linear adjustment are shown as
    their own lines so the lines always add up to the total.

    Returns {"total", "lines": [(label, amount)], "error", "blocked"}."""
    from decimal import Decimal

    schedule = FeeSchedule.objects.filter(code=schedule_code, is_active=True).first()
    out = {"total": None, "lines": [], "error": "", "blocked": False, "schedule": schedule}
    if schedule is None:
        out["error"] = "This fee is not set up yet."
        return out
    if schedule.confirmed_setting and not _bool(schedule.confirmed_setting, False):
        out["blocked"] = True
        out["error"] = "NBTE has not confirmed this fee yet; it is issued once the amount is confirmed."
        return out

    def amount(pairs):
        return to_money(evaluate_setting(schedule.formula, fee_variables(institution,
                                                                         **stream_variables(pairs)))["result"])
    try:
        total = amount(rows)
    except FormulaError as exc:
        out["error"] = f"The fee could not be worked out ({exc})."
        return out
    out["total"] = total
    try:
        fixed = amount([])
        lines = [(f"{p} — {max(1, int(n or 1))} stream{'s' if max(1, int(n or 1)) != 1 else ''}",
                  amount([(p, n)]) - fixed) for p, n in rows]
    except FormulaError:
        return out
    if fixed:
        lines.insert(0, ("Fixed amount", fixed))
    rest = total - sum((a for _l, a in lines), Decimal("0"))
    if rest:
        lines.append(("Adjustment for the combination", rest))
    out["lines"] = lines
    return out


@transaction.atomic
def issue_obligation(institution, schedule_code, *, source=None, actor=None, variables=None, description=""):
    """Create a bill. Nobody types the amount: the fee schedule's formula
    setting computes it now, and the expression, inputs and result are frozen
    in `formula_snapshot` (a later formula change never alters this bill)."""
    schedule = FeeSchedule.objects.filter(code=schedule_code, is_active=True).first()
    if schedule is None:
        raise ValidationError(f"Fee schedule '{schedule_code}' is missing or inactive.")
    if schedule.confirmed_setting and not _bool(schedule.confirmed_setting, False):
        raise ValidationError(f"{schedule.name}: the amount is not confirmed yet ({schedule.confirmed_setting} "
                              "is false), so bills cannot be issued.")
    inputs = fee_variables(institution, **(variables or {}))
    try:
        snapshot = evaluate_setting(schedule.formula, inputs)
    except FormulaError as exc:
        raise ValidationError(f"The fee formula {schedule.formula} could not be evaluated: {exc}") from None
    amount = to_money(snapshot["result"])
    if amount <= 0:
        raise ValidationError(f"The fee formula gave {naira(amount)}; a bill must be more than zero.")
    due_days = schedule.due_days or _int("qa.payment.due_days", 30)
    src_ct = ContentType.objects.get_for_model(source, for_concrete_model=True) if source is not None else None
    obligation = PaymentObligation.unscoped.create(
        reference=references.issue("payment", inst=institution.code),
        institution=institution, source_type=src_ct, source_id=str(source.pk) if source is not None else "",
        source_reference=getattr(source, "reference", "") if source is not None else "",
        fee_schedule=schedule, description=description or schedule.description or schedule.name,
        currency=schedule.currency, amount=amount, amount_in_words=amount_in_words(amount),
        formula_snapshot=snapshot, due_at=timezone.now() + datetime.timedelta(days=due_days),
        status="issued", balance=amount, issued_by=actor if getattr(actor, "pk", None) else None,
    )
    flow.start(FLOW, obligation, actor=actor)
    if _bool("qa.payment.apply_credit_automatically"):
        apply_credits(obligation, actor=actor)
    timeline.record(institution, "payment.issued", subject=obligation, actor=actor, visibility="public",
                    public_text=f"Bill {obligation.reference} issued: {obligation.description}, {naira(amount)}.")
    publish(obligation, actor=actor)
    return obligation


def publish(obligation, *, actor=None):
    """Generate the first slip and move issued → awaiting_payment. Refused
    (bill stays `issued`) while only the placeholder bank account exists."""
    if obligation.status != "issued":
        return obligation
    bank = bank_for(obligation.fee_schedule)
    if bank is None:
        timeline.record(obligation.institution, "payment.slip_blocked", subject=obligation, visibility="internal",
                        internal_text="Slip not issued: Finance has not set a real bank account yet.")
        return obligation
    generate_slip(obligation, actor=actor)
    _move(obligation, "publish_slip", actor)
    from institutions.services import notify_institution

    transaction.on_commit(lambda: notify_institution(
        obligation.institution, "payment.issued",
        {"reference": obligation.reference, "description": obligation.description,
         "amount": naira(obligation.balance), "due": timezone.localtime(obligation.due_at).strftime("%d %b %Y")},
        url=f"/payments/{obligation.pk}/"))
    return obligation


def publish_waiting(actor=None):
    """Publish every bill held at `issued` (run after Finance sets an account)."""
    count = 0
    for obligation in PaymentObligation.unscoped.filter(status="issued"):
        before = obligation.status
        publish(obligation, actor=actor)
        count += obligation.status != before
    return count


# ===========================================================================
# Slips (§10.2 step 2, Appendix C)
# ===========================================================================
@transaction.atomic
def generate_slip(obligation, *, actor=None):
    from django.core.files.base import ContentFile

    from .pdfs import render_slip

    bank = bank_for(obligation.fee_schedule)
    if bank is None:
        raise ValidationError("No active bank account is set (Settings → Fees and bank accounts).")
    previous = PaymentSlip.unscoped.filter(obligation=obligation).order_by("-version").first()
    version = (previous.version + 1) if previous else 1
    PaymentSlip.unscoped.filter(obligation=obligation, is_current=True).update(is_current=False)
    amount = obligation.balance if obligation.balance > 0 else obligation.amount
    rrr = _remita_rrr_for(obligation, amount, actor)
    now = timezone.now()
    slip = PaymentSlip(
        obligation=obligation, version=version, amount=amount, amount_in_words=amount_in_words(amount),
        qr_payload=verify_url("slip", obligation.reference, version), barcode_value=obligation.reference,
        remita_rrr=rrr, generated_at=now,
        expires_at=now + datetime.timedelta(days=_int("qa.payment.slip_validity_days", 30)),
        bank_snapshot={"bank_name": bank.bank_name, "account_name": bank.account_name,
                       "account_number": bank.account_number, "sort_code": bank.sort_code},
        is_current=True,
    )
    pdf = render_slip(slip)
    slip.file.save(f"slip-{obligation.reference}-v{version}.pdf", ContentFile(pdf), save=False)
    slip.save()
    return slip


def _remita_rrr_for(obligation, amount, actor):
    """An RRR for the slip when Remita is enabled; best effort (a provider
    outage must not stop the slip)."""
    provider = providers.get("remita")
    if not provider.is_enabled():
        return ""
    if obligation.remita_rrr:
        attempt = (PaymentAttempt.unscoped.filter(obligation=obligation, provider="remita",
                                                   provider_reference=obligation.remita_rrr).first())
        if attempt is not None and attempt.amount == amount and attempt.status in ("initiated", "pending"):
            return obligation.remita_rrr
    try:
        order_id = _next_order_id(obligation, "R")
        rrr, req, resp = provider.generate_rrr(obligation, actor, amount, order_id)
    except providers.ProviderError as exc:
        logger.warning("Remita RRR for %s failed: %s", obligation.reference, exc)
        return ""
    PaymentAttempt.unscoped.create(obligation=obligation, provider="remita", provider_reference=rrr, amount=amount,
                                   status="pending", raw_request={**req, "orderId": order_id}, raw_response=resp,
                                   initiated_by=actor if getattr(actor, "pk", None) else None,
                                   checkout_url=provider.checkout_url(rrr))
    obligation.remita_rrr = rrr
    obligation.save(update_fields=["remita_rrr", "updated_at"])
    return rrr


def current_slip(obligation):
    return PaymentSlip.unscoped.filter(obligation=obligation, is_current=True).order_by("-version").first()


def regenerate_slip(obligation, *, actor=None):
    """Same reference, new version (expired slip, new balance, new bank details)."""
    if obligation.status not in ("awaiting_payment", "partially_paid", "rejected", "issued"):
        raise ValidationError("A new slip can be generated only while the bill is unpaid.")
    if obligation.status == "issued":
        publish(obligation, actor=actor)
        return current_slip(obligation)
    return generate_slip(obligation, actor=actor)


# ===========================================================================
# Bringing an obligation back to awaiting_payment before a new attempt
# ===========================================================================
def _ensure_awaiting_payment(obligation, actor):
    if obligation.status == "awaiting_payment":
        return obligation
    if obligation.status == "rejected":
        return _move(obligation, "repay", actor)
    if obligation.status == "partially_paid":
        return _move(obligation, "pay_balance", actor)
    if obligation.status == "provider_processing":
        return _move(obligation, "provider_failed", actor, remark="Superseded by a new payment attempt.")
    raise ValidationError(f"{obligation.reference} cannot take a payment now ({stage_label(obligation)}).")


def _open_verification(obligation, source, *, attempt=None, proof=None, line=None):
    open_item = PaymentVerification.unscoped.filter(obligation=obligation, is_open=True).first()
    if open_item is not None:
        changed = []
        if attempt is not None and open_item.attempt_id is None:
            open_item.attempt = attempt
            changed.append("attempt")
        if proof is not None and open_item.proof_id is None:
            open_item.proof = proof
            changed.append("proof")
        if line is not None and open_item.statement_line_id is None:
            open_item.statement_line = line
            changed.append("statement_line")
        if changed:
            open_item.save(update_fields=changed + ["updated_at"])
        return open_item
    hours = _int("qa.payment.finance_confirmation_window_hours", 24)
    now = timezone.now()
    item = PaymentVerification.unscoped.create(obligation=obligation, source=source, attempt=attempt, proof=proof,
                                               statement_line=line, opened_at=now,
                                               sla_due_at=now + datetime.timedelta(hours=hours))
    transaction.on_commit(lambda: _notify_finance_officers(item))
    return item


def _enter_queue(obligation, actor, source, **evidence):
    """provider_reported_paid / proof_submitted → awaiting_finance_confirmation."""
    _move(obligation, "enter_finance_queue", actor)
    item = _open_verification(obligation, source, **evidence)
    transaction.on_commit(lambda: payment_claimed.send(sender=PaymentObligation, obligation=obligation))
    return item


# ===========================================================================
# "I have paid" — proof upload (§10.2 step 3, §10.4)
# ===========================================================================
@transaction.atomic
def submit_proof(obligation, *, user, upload, payer_name="", paid_on=None, amount_claimed=None, bank_name="",
                 teller_number="", kind="teller", attempt=None):
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=obligation.pk)
    exts = config.get("qa.payment.proof_extensions", ["pdf", "jpg", "jpeg", "png"]) or ["pdf"]
    validate_upload(upload, extensions=exts, max_mb=_int("qa.payment.proof_max_mb", 5), label="proof of payment")
    try:
        amount_claimed = to_money(amount_claimed if amount_claimed not in (None, "") else 0)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    if kind == "teller" and amount_claimed <= 0:
        raise ValidationError("Enter the amount you paid.")
    if paid_on and paid_on > timezone.localdate():
        raise ValidationError("The payment date cannot be in the future.")
    receipt_only = obligation.status in ("provider_reported_paid", "awaiting_finance_confirmation",
                                         "finance_confirmation_overdue", "provider_processing")
    if not receipt_only and obligation.status not in ("awaiting_payment", "rejected", "partially_paid"):
        raise ValidationError(f"{obligation.reference} is not waiting for a payment ({stage_label(obligation)}).")
    proof = ProofOfPayment(obligation=obligation, attempt=attempt,
                           kind="provider_receipt" if receipt_only else kind,
                           payer_name=payer_name.strip(), paid_on=paid_on, amount_claimed=amount_claimed,
                           bank_name=bank_name.strip(), teller_number=teller_number.strip(), uploaded_by=user)
    proof.file = upload
    proof.save()
    # §10.4 duplicate detection: the same file on a different bill is a fraud flag.
    dup = (ProofOfPayment.unscoped.filter(sha256=proof.sha256).exclude(pk=proof.pk)
           .exclude(obligation=obligation).first()) if proof.sha256 else None
    if dup is not None:
        proof.fraud_flag = True
        proof.duplicate_of = dup
        proof.save(update_fields=["fraud_flag", "duplicate_of", "updated_at"])
        alert(obligation, f"The proof uploaded for {obligation.reference} is the same file as the proof for "
                          f"{dup.obligation.reference}. Check before confirming.")
    timeline.record(obligation.institution, "payment.proof_uploaded", subject=obligation, actor=user,
                    visibility="public", public_text=f"Proof of payment uploaded for {obligation.reference}.")
    if receipt_only:
        item = PaymentVerification.unscoped.filter(obligation=obligation, is_open=True).first()
        if item is not None and item.proof_id is None:
            item.proof = proof
            item.save(update_fields=["proof", "updated_at"])
        return proof
    _ensure_awaiting_payment(obligation, user)
    _move(obligation, "submit_proof", user)
    _enter_queue(obligation, user, "proof", proof=proof)
    return proof


# ===========================================================================
# Online payments (§10.1)
# ===========================================================================
def _next_order_id(obligation, letter):
    n = PaymentAttempt.unscoped.filter(obligation=obligation).count() + 1
    return obligation.reference if n == 1 else f"{obligation.reference}-{letter}{n}"


@transaction.atomic
def start_online_payment(obligation, provider_code, *, user, amount=None, callback_url="", request=None):
    """Create a PaymentAttempt and ask the provider for a checkout. Returns the
    attempt (its `checkout_url` is where the institution goes next)."""
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=obligation.pk)
    provider = providers.get(provider_code)
    if not provider.online or not provider.is_enabled():
        raise ValidationError(f"{provider.label} is not available.")
    due = obligation.balance
    if amount in (None, ""):
        amount = due
    try:
        amount = to_money(amount)
    except ValueError as exc:
        raise ValidationError(str(exc)) from None
    if amount <= 0:
        raise ValidationError("Enter an amount above zero.")
    if amount > due:
        raise ValidationError(f"The balance is {naira(due)}; you cannot pay more online.")
    if amount < due and not (_bool("qa.payment.allow_partial") and provider.supports_partial()):
        raise ValidationError(f"Part payments are not accepted online. Pay the full balance of {naira(due)}.")
    _ensure_awaiting_payment(obligation, user)
    attempt = PaymentAttempt.unscoped.create(
        obligation=obligation, provider=provider.code, amount=amount, status="initiated",
        provider_reference=_next_order_id(obligation, "P" if provider.code == "paystack" else "R"),
        initiated_by=user)
    attempt.raw_request = {"orderId": attempt.provider_reference}
    try:
        provider.initiate(obligation, user, amount, request=request, attempt=attempt, callback_url=callback_url)
    except providers.ProviderError as exc:
        attempt.status = "failed"
        attempt.failure_reason = str(exc)[:255]
        attempt.save()
        raise ValidationError(f"{provider.label}: {exc}") from None
    attempt.status = "pending"
    attempt.save()
    if provider.code == "remita":
        obligation.remita_rrr = attempt.provider_reference
        obligation.save(update_fields=["remita_rrr", "updated_at"])
    _move(obligation, "pay_online", user, metadata={"attempt": attempt.pk, "provider": provider.code})
    timeline.record(obligation.institution, "payment.online_started", subject=obligation, actor=user,
                    visibility="internal", internal_text=f"{provider.label} attempt {attempt.provider_reference} "
                                                         f"for {naira(amount)}.")
    return attempt


def _find_attempt(provider_code, reference):
    return (PaymentAttempt.unscoped.select_related("obligation").filter(provider=provider_code,
                                                                       provider_reference=reference).first())


@transaction.atomic
def record_provider_result(attempt, result, *, via="webhook"):
    """Apply a server-side verification result. The obligation can move to
    `provider_reported_paid` → `awaiting_finance_confirmation`, never further."""
    attempt = PaymentAttempt.unscoped.select_for_update().get(pk=attempt.pk)
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=attempt.obligation_id)
    attempt.verify_result = {"paid": result.paid, "status": result.status,
                             "amount": str(result.amount) if result.amount is not None else None,
                             "message": result.message, "via": via, "at": timezone.now().isoformat()}
    attempt.last_polled_at = timezone.now()
    if attempt.status == "provider_reported_paid":
        attempt.save(update_fields=["verify_result", "last_polled_at", "updated_at"])
        return attempt  # already recorded: webhooks and polls are idempotent
    if not result.paid:
        if via == "webhook":
            # The webhook said success but the provider's own verify did not: verify wins.
            attempt.status = "failed"
            attempt.failure_reason = f"Verify did not confirm the webhook ({result.status or 'no status'})."
            attempt.flags = list(set((attempt.flags or []) + ["verify_mismatch"]))
            attempt.save()
            if obligation.status == "provider_processing":
                _move(obligation, "provider_failed", None, remark=attempt.failure_reason)
            alert(obligation, f"{attempt.get_provider_display()} reported success for {attempt.provider_reference} "
                              f"but the server-side verify did not. The attempt is recorded as failed.", ict=True)
        elif str(result.status).lower() in DEFINITIVE_FAILURES:
            attempt.status = "failed"
            attempt.failure_reason = f"The provider reports the payment as {result.status}."
            attempt.save()
            if obligation.status == "provider_processing":
                _move(obligation, "provider_failed", None, remark=attempt.failure_reason)
        else:
            attempt.save(update_fields=["verify_result", "last_polled_at", "updated_at"])
        return attempt
    attempt.status = "provider_reported_paid"
    attempt.webhook_verified = True
    attempt.completed_at = timezone.now()
    attempt.save()
    timeline.record(obligation.institution, "payment.provider_reported", subject=obligation, visibility="public",
                    public_text=f"{attempt.get_provider_display()} reported a payment for {obligation.reference}. "
                                "Institution claims paid — awaiting Finance confirmation.")
    if obligation.status in PaymentObligation.SETTLED or obligation.status == "cancelled":
        _money_after_settlement(obligation, result.amount or attempt.amount, f"{attempt.get_provider_display()} "
                                f"{attempt.provider_reference}")
        return attempt
    if obligation.status in ("awaiting_finance_confirmation", "finance_confirmation_overdue"):
        _open_verification(obligation, "provider", attempt=attempt)
        return attempt
    if obligation.status in ("awaiting_payment", "rejected", "partially_paid"):
        _ensure_awaiting_payment(obligation, None)
        _move(obligation, "pay_online", None, metadata={"attempt": attempt.pk, "via": via})
    if obligation.status == "provider_processing":
        _move(obligation, "provider_report", None, metadata={"attempt": attempt.pk, "via": via})
        _enter_queue(obligation, None, "provider", attempt=attempt)
    return attempt


def handle_webhook(provider_code, request):
    """Entry point for both providers' callbacks. Returns (http_status, message).
    Every call is logged; only signature-valid (Paystack) / allowed (Remita)
    calls are acted on, and only after a server-side verify."""
    provider = providers.get(provider_code)
    ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR", "")
    log = WebhookLog.objects.create(provider=provider_code, remote_ip=ip[:60],
                                    body=(request.body or b"")[:8000].decode("utf-8", "replace"))
    try:
        if provider_code == "paystack":
            valid, reference, _payload = provider.parse_webhook(request)
            log.signature_valid = valid
            refs = [reference] if reference else []
            if not valid:
                log.outcome = "rejected: invalid signature"
                log.save()
                return 401, "invalid signature"
        else:
            valid, refs, _payload = provider.parse_webhook(request)
            log.signature_valid = valid
            if not valid:
                log.outcome = "rejected: caller not on the allow-list"
                log.save()
                return 403, "caller not allowed"
        if not refs:
            log.outcome = "ignored: no payment reference"
            log.save()
            return 200, "ignored"
        outcomes = []
        for ref in refs:
            log.reference = ref[:100]
            attempt = _find_attempt(provider_code, ref)
            if attempt is None:
                outcomes.append(f"{ref}: unknown reference")
                continue
            log.attempt = attempt
            try:
                result = provider.verify(attempt)
            except providers.ProviderError as exc:
                outcomes.append(f"{ref}: verify failed ({exc}); will be polled")
                continue
            record_provider_result(attempt, result, via="webhook")
            attempt.refresh_from_db()
            outcomes.append(f"{ref}: {attempt.status}")
        log.outcome = "; ".join(outcomes)[:255]
        log.save()
        return 200, "ok"
    except Exception:  # never 500 to a provider; it will retry and we keep the log
        logger.exception("Webhook processing failed (%s)", provider_code)
        log.outcome = "error (see server log)"
        log.save()
        return 200, "logged"


def verify_on_return(obligation, params):
    """The institution came back from the provider's checkout page
    (Paystack adds ?reference=/trxref=, Remita ?RRR=/orderID=). Check that
    attempt with the provider at once, exactly as a webhook would, so the
    page shows the result without waiting for the webhook or the sweep.
    Returns the attempt, or None when nothing matched."""
    refs = [str(params.get(k) or "").strip() for k in ("reference", "trxref", "RRR", "rrr", "orderID")]
    refs = [r for r in refs if r]
    if not refs:
        return None
    attempt = (PaymentAttempt.unscoped.filter(obligation=obligation, provider_reference__in=refs)
               .order_by("-created_at").first())
    if attempt is None or attempt.status not in ("initiated", "pending", "provider_processing"):
        return attempt
    provider = providers.get(attempt.provider)
    try:
        result = provider.verify(attempt)
    except providers.ProviderError:
        return attempt  # the webhook or the sweep will catch up
    record_provider_result(attempt, result, via="return")
    attempt.refresh_from_db()
    return attempt


def poll_pending_attempts(now=None):
    """Scheduled poll (§10.1): re-verify online attempts pending > 10 minutes."""
    now = now or timezone.now()
    minutes = _int("qa.payment.provider_poll_minutes", 15)
    cutoff = now - datetime.timedelta(minutes=10)
    recent = now - datetime.timedelta(minutes=max(1, minutes))
    checked = 0
    for attempt in (PaymentAttempt.unscoped.filter(status__in=("pending", "initiated"), provider__in=providers.ONLINE,
                                                   created_at__lte=cutoff)
                    .exclude(last_polled_at__gt=recent)[:200]):
        provider = providers.get(attempt.provider)
        if not provider.is_enabled():
            continue
        try:
            result = provider.verify(attempt)
        except providers.ProviderError:
            continue
        record_provider_result(attempt, result, via="poll")
        checked += 1
    return checked


# ===========================================================================
# Finance queue (§10.2 step 4): first pass, second check, Director's decision
# ===========================================================================
def needs_second_check(item):
    amount = item.amount_confirmed if item.amount_confirmed is not None else item.obligation.balance
    return amount > _decimal("qa.payment.dual_verify_threshold", "5000000")


def receipt_on_file(item):
    if item.proof_id:
        return True
    return ProofOfPayment.unscoped.filter(obligation=item.obligation, kind="provider_receipt",
                                          created_at__gte=item.opened_at - datetime.timedelta(days=30)).exists()


def ready_for_final(item):
    """(ready, reason) for the Director's decision."""
    if not item.is_open:
        return False, "This item is closed."
    if item.decision not in ("approved", "rejected"):
        return False, "Waiting for the Finance officer's first pass."
    if needs_second_check(item) and item.decision == "approved" and item.second_decision not in ("approved",
                                                                                                "rejected"):
        return False, "Above the dual-verification threshold: a second first-pass check is needed."
    if (item.source == "provider" and item.decision == "approved"
            and _bool("qa.payment.receipt_required_for_provider_payments", True) and not receipt_on_file(item)):
        return False, "The provider's receipt must be uploaded by the institution before confirmation."
    return True, ""


@transaction.atomic
def first_pass(item, *, user, decision, amount=None, reason=""):
    item = PaymentVerification.unscoped.select_for_update().select_related("obligation").get(pk=item.pk)
    if not user.has_perm("payments.verify_payment"):
        raise PermissionDenied("You do not hold payments.verify_payment.")
    if not item.is_open:
        raise ValidationError("This item is already decided.")
    if item.final_decision:
        raise ValidationError("The Director of Finance has already decided this item.")
    if decision not in dict(PaymentVerification.DECISION_CHOICES):
        raise ValidationError("Choose recommend confirm, recommend reject or needs information.")
    reason = (reason or "").strip()
    if decision == "approved":
        try:
            amount = to_money(amount)
        except ValueError as exc:
            raise ValidationError(f"Amount received: {exc}") from None
        if amount <= 0:
            raise ValidationError("Enter the amount actually received.")
        item.amount_confirmed = amount
    elif len(reason) < 3:
        raise ValidationError("Give a reason.")
    item.decision, item.reason = decision, reason
    item.verified_by, item.verified_at = user, timezone.now()
    item.second_decision, item.second_verifier, item.second_verified_at, item.second_reason = "", None, None, ""
    item.save()
    obligation = item.obligation
    timeline.record(obligation.institution, "payment.first_pass", subject=obligation, actor=user,
                    visibility="internal", internal_text=f"First pass: {item.get_decision_display()}"
                                                         f"{' — ' + naira(item.amount_confirmed) if decision == 'approved' else ''}"
                                                         f"{' — ' + reason if reason else ''}")
    if decision == "needs_info":
        from institutions.services import notify_institution

        timeline.record(obligation.institution, "payment.needs_info", subject=obligation, actor=user,
                        visibility="public", public_text=f"Finance needs more information about "
                                                         f"{obligation.reference}: {reason}")
        transaction.on_commit(lambda: notify_institution(obligation.institution, "payment.needs_info",
                                                         {"reference": obligation.reference, "reason": reason},
                                                         url=f"/payments/{obligation.pk}/"))
    else:
        ready, _why = ready_for_final(item)
        if ready:
            transaction.on_commit(lambda: _notify_final_approvers(item))
    return item


@transaction.atomic
def second_pass(item, *, user, decision, reason=""):
    item = PaymentVerification.unscoped.select_for_update().get(pk=item.pk)
    if not user.has_perm("payments.second_verify_payment"):
        raise PermissionDenied("You do not hold payments.second_verify_payment.")
    if item.decision not in ("approved", "rejected"):
        raise ValidationError("The first pass has not been recorded yet.")
    if user.pk == item.verified_by_id:
        raise PermissionDenied("The second check must be done by a different officer.")
    if decision not in ("approved", "rejected"):
        raise ValidationError("Choose agree (recommend confirm) or recommend reject.")
    if decision == "rejected" and len((reason or "").strip()) < 3:
        raise ValidationError("Give a reason.")
    item.second_decision, item.second_reason = decision, (reason or "").strip()
    item.second_verifier, item.second_verified_at = user, timezone.now()
    item.save()
    if ready_for_final(item)[0]:
        transaction.on_commit(lambda: _notify_final_approvers(item))
    return item


@transaction.atomic
def final_decision(item, *, user, decision, reason="", impersonating=False):
    """The Director of Finance confirms or rejects. THE ONLY PATH TO `confirmed`."""
    item = PaymentVerification.unscoped.select_for_update().select_related("obligation").get(pk=item.pk)
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=item.obligation_id)
    if impersonating:
        raise PermissionDenied("You cannot decide payments while impersonating.")
    if decision not in ("confirmed", "rejected"):
        raise ValidationError("Choose confirm or reject.")
    if not is_final_approver(user):
        raise PermissionDenied(f"Only the holder of the role '{final_approver_role()}' with "
                               "payments.final_confirm_payment can decide payments.")
    if user.pk in (item.verified_by_id, item.second_verifier_id):
        raise PermissionDenied("You did a first-pass check on this payment, so another approver must decide.")
    ready, why = ready_for_final(item)
    if decision == "confirmed" and not ready:
        raise ValidationError(why)
    if decision == "rejected" and item.decision not in ("approved", "rejected"):
        raise ValidationError("Waiting for the Finance officer's first pass.")
    reason = (reason or "").strip()
    if decision == "rejected" and len(reason) < 3:
        raise ValidationError("Give the reason the institution will see.")
    now = timezone.now()
    item.final_approver, item.final_decision, item.final_reason, item.final_decided_at = user, decision, reason, now
    item.is_open = False
    if decision == "rejected":
        _move(obligation, "final_reject", user, remark=reason)
        item.save()
        _after_reject(obligation, item, user, reason)
        return item

    amount = item.amount_confirmed or ZERO
    obligation.amount_paid = (obligation.amount_paid or ZERO) + amount
    due = obligation.amount - (obligation.credit_applied or ZERO)
    balance = due - obligation.amount_paid
    if balance > 0:
        obligation.balance = balance
        obligation.save(update_fields=["amount_paid", "balance", "updated_at"])
        _move(obligation, "final_confirm_partial", user, remark=reason or "Part payment confirmed",
              metadata={"verification": item.pk, "amount": str(amount)})
        item.save()
        advice = generate_advice(obligation, item)
        generate_slip(obligation, actor=user)
        _after_confirm(obligation, item, advice, user, settled=False)
        return item
    obligation.balance = ZERO
    obligation.confirmed_at = now
    excess = -balance
    obligation.overpaid_amount = excess if excess > 0 else ZERO
    obligation.save(update_fields=["amount_paid", "balance", "confirmed_at", "overpaid_amount", "updated_at"])
    _move(obligation, "final_confirm", user, remark=reason or "Confirmed",
          metadata={"verification": item.pk, "amount": str(amount)})
    item.save()
    if excess > 0:
        _move(obligation, "record_overpayment", user, remark=f"Overpaid by {naira(excess)}")
    advice = generate_advice(obligation, item)
    _after_confirm(obligation, item, advice, user, settled=True)
    return item


def _close_open_attempts(obligation, reason):
    """Pending online attempts (e.g. the RRR printed on an old slip) end when
    the bill is settled or cancelled; money that still arrives on them is
    treated as a duplicate and routed to refund (§10.6)."""
    PaymentAttempt.unscoped.filter(obligation=obligation, status__in=("initiated", "pending")).update(
        status="abandoned", failure_reason=reason[:255], updated_at=timezone.now())


def _after_confirm(obligation, item, advice, user, *, settled):
    from institutions.services import notify_institution

    text = (f"Payment confirmed by the Director of Finance: {naira(item.amount_confirmed)} for "
            f"{obligation.reference}. Payment Advice {advice.reference}.")
    if not settled:
        text += f" Balance outstanding: {naira(obligation.balance)}."
    timeline.record(obligation.institution, "payment.confirmed", subject=obligation, actor=user,
                    visibility="public", public_text=text)
    audit.log(audit._action("state_change"), obligation, actor=user,
              changes={"status": {"new": obligation.status}}, note=f"final decision: confirmed ({item.pk})")
    transaction.on_commit(lambda: notify_institution(
        obligation.institution, "payment.confirmed", {"reference": obligation.reference, "advice": advice.reference},
        url=f"/payments/{obligation.pk}/"))
    if settled:
        _close_open_attempts(obligation, "Bill settled")
        transaction.on_commit(lambda: payment_settled.send(sender=PaymentObligation, obligation=obligation))


def _after_reject(obligation, item, user, reason):
    from institutions.services import notify_institution

    timeline.record(obligation.institution, "payment.rejected", subject=obligation, actor=user, visibility="public",
                    public_text=f"Payment for {obligation.reference} was not confirmed: {reason}")
    audit.log(audit._action("state_change"), obligation, actor=user, changes={"status": {"new": "rejected"}},
              note=f"final decision: rejected ({item.pk})")
    transaction.on_commit(lambda: notify_institution(obligation.institution, "payment.rejected",
                                                     {"reference": obligation.reference, "reason": reason},
                                                     url=f"/payments/{obligation.pk}/"))
    transaction.on_commit(lambda: payment_rejected.send(sender=PaymentObligation, obligation=obligation))


# ===========================================================================
# Payment Advice (A4/R-26)
# ===========================================================================
def generate_advice(obligation, item):
    from django.core.files.base import ContentFile

    from .pdfs import render_advice

    ref = references.issue("advice", inst=obligation.institution.code)
    advice = PaymentAdvice(obligation=obligation, verification=item, reference=ref,
                           amount=item.amount_confirmed or ZERO, qr_payload=verify_url("advice", ref),
                           generated_at=timezone.now())
    pdf = render_advice(advice)
    advice.file.save(f"advice-{ref}.pdf", ContentFile(pdf), save=False)
    advice.save()
    return advice


# ===========================================================================
# Waivers, cancellation, refunds, credits (two-step handlers call these)
# ===========================================================================
def waive(obligation, *, actor, reason):
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=obligation.pk)
    if obligation.status == "issued":
        raise ValidationError("Publish the bill before waiving it (Finance must set a bank account).")
    _ensure_awaiting_payment(obligation, actor)
    obligation.waived_by, obligation.waiver_reason, obligation.waived_at = actor, reason, timezone.now()
    obligation.save(update_fields=["waived_by", "waiver_reason", "waived_at", "updated_at"])
    _move(obligation, "waive", actor, remark=reason, confirmed=True)
    _close_open_attempts(obligation, "Bill waived")
    timeline.record(obligation.institution, "payment.waived", subject=obligation, actor=actor, visibility="public",
                    public_text=f"The fee {obligation.reference} was waived.", internal_text=reason)
    transaction.on_commit(lambda: payment_settled.send(sender=PaymentObligation, obligation=obligation))
    return obligation


def cancel(obligation, *, actor, reason):
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=obligation.pk)
    if obligation.status not in ("issued", "awaiting_payment"):
        raise ValidationError("Only an unpaid bill can be cancelled.")
    obligation.cancelled_reason = reason
    obligation.save(update_fields=["cancelled_reason", "updated_at"])
    _move(obligation, "cancel", actor, remark=reason, confirmed=True)
    _close_open_attempts(obligation, "Bill cancelled")
    PaymentSlip.unscoped.filter(obligation=obligation, is_current=True).update(is_current=False)
    timeline.record(obligation.institution, "payment.cancelled", subject=obligation, actor=actor,
                    visibility="public", public_text=f"Bill {obligation.reference} was cancelled.",
                    internal_text=reason)
    return obligation


def _money_after_settlement(obligation, amount, what):
    """Duplicate payment on a settled bill, or money after cancellation:
    flag it and open a refund request (§10.6)."""
    amount = to_money(amount or 0)
    flag = "paid_after_cancellation" if obligation.status == "cancelled" else "duplicate"
    obligation.flags = sorted(set((obligation.flags or []) + [flag]))
    obligation.save(update_fields=["flags", "updated_at"])
    refund = create_refund(obligation, amount=amount, actor=None,
                           reason=f"{'Payment received after cancellation' if flag != 'duplicate' else 'Duplicate payment'}"
                                  f" ({what}).", source="after_cancellation" if flag != "duplicate" else "duplicate")
    alert(obligation, f"{what}: {naira(amount)} arrived for {obligation.reference}, which is "
                      f"{stage_label(obligation).lower()}. A refund request was opened.")
    return refund


def create_refund(obligation, *, amount, actor, reason, source="other"):
    amount = to_money(amount)
    if amount <= 0:
        raise ValidationError("A refund must be more than zero.")
    refund = Refund.unscoped.create(obligation=obligation, amount=amount, reason=reason, source=source,
                                    requested_by=actor if getattr(actor, "pk", None) else None)
    flow.start("refund", refund, actor=actor)
    timeline.record(obligation.institution, "payment.refund_requested", subject=obligation, actor=actor,
                    visibility="internal", internal_text=f"Refund of {naira(amount)} requested: {reason}")
    return refund


def approve_refund(refund, *, actor):
    instance = flow.instance_for(refund, "refund")
    flow.apply_confirmed(instance, "approve", actor)
    refund.refresh_from_db()
    refund.approved_by, refund.approved_at = actor, timezone.now()
    refund.save(update_fields=["approved_by", "approved_at", "updated_at"])
    obligation = refund.obligation
    if refund.amount >= (obligation.amount_paid or ZERO) and obligation.status in ("confirmed", "overpaid"):
        _move(obligation, "request_refund", actor, remark=refund.reason, confirmed=True)
    return refund


def decline_refund(refund, *, actor, remark):
    instance = flow.instance_for(refund, "refund")
    if instance is not None and flow.can(instance, "reject"):
        flow.transition(instance, "reject", actor, remark=remark or "Declined by the Director of Finance")
    return refund


@transaction.atomic
def mark_refund_paid(refund, *, actor, paid_reference):
    if len((paid_reference or "").strip()) < 3:
        raise ValidationError("Enter the payment reference of the refund.")
    instance = flow.instance_for(refund, "refund")
    flow.transition(instance, "mark_paid", actor, remark=paid_reference)
    refund.refresh_from_db()
    refund.paid_reference, refund.paid_at, refund.paid_by = paid_reference.strip(), timezone.now(), actor
    refund.save(update_fields=["paid_reference", "paid_at", "paid_by", "updated_at"])
    obligation = refund.obligation
    if obligation.status == "refund_requested":
        # Covered by the approval PendingAction (two-step); this records the money leaving.
        _move(obligation, "confirm_refund", actor, remark=paid_reference, confirmed=True)
    timeline.record(obligation.institution, "payment.refunded", subject=obligation, actor=actor, visibility="public",
                    public_text=f"A refund of {naira(refund.amount)} was paid for {obligation.reference}.")
    return refund


@transaction.atomic
def hold_as_credit(obligation, *, actor, note=""):
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=obligation.pk)
    if obligation.overpaid_amount <= 0:
        raise ValidationError("There is no overpayment on this bill.")
    if PaymentCredit.unscoped.filter(source_obligation=obligation).exists() or \
            Refund.unscoped.filter(obligation=obligation, source="overpayment").exclude(status="rejected").exists():
        raise ValidationError("The overpayment has already been handled.")
    credit = PaymentCredit.unscoped.create(institution=obligation.institution, amount=obligation.overpaid_amount,
                                           source_obligation=obligation, created_by=actor, note=note)
    timeline.record(obligation.institution, "payment.credit_held", subject=obligation, actor=actor,
                    visibility="public", public_text=f"{naira(credit.amount)} overpaid on {obligation.reference} is "
                                                     "held as credit for your next bill.")
    return credit


def apply_credits(obligation, *, actor=None):
    applied = ZERO
    for credit in PaymentCredit.unscoped.select_for_update().filter(institution=obligation.institution,
                                                                     status="held").order_by("created_at"):
        if obligation.balance <= 0:
            break
        use = min(credit.amount, obligation.balance)
        if use < credit.amount:
            PaymentCredit.unscoped.create(institution=credit.institution, amount=credit.amount - use,
                                          source_obligation=credit.source_obligation, created_by=actor,
                                          note=f"Remainder of credit #{credit.pk}")
            credit.amount = use
        credit.status, credit.applied_to, credit.applied_at = "applied", obligation, timezone.now()
        credit.save()
        obligation.credit_applied = (obligation.credit_applied or ZERO) + use
        obligation.balance = obligation.amount - obligation.credit_applied - (obligation.amount_paid or ZERO)
        applied += use
    if applied:
        obligation.save(update_fields=["credit_applied", "balance", "updated_at"])
        timeline.record(obligation.institution, "payment.credit_applied", subject=obligation, actor=actor,
                        visibility="public", public_text=f"{naira(applied)} of credit applied to {obligation.reference}.")
    return applied


# ===========================================================================
# Bank statement import and matching (§10.3)
# ===========================================================================
REF_PATTERN = re.compile(r"NBTE[\s\-_/.]*QAP[\s\-_/.]*(\d{4})[\s\-_/.]*([A-Z0-9]{3,6})[\s\-_/.]*(\d{6})[\s\-_/.]*(\d{2})",
                         re.IGNORECASE)


def extract_references(text):
    """Every payment reference in a narration, tolerant of separators and
    case. Returns [(canonical, check_ok)]."""
    out = []
    for m in REF_PATTERN.finditer(text or ""):
        year, inst, seq, chk = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
        canonical = f"NBTE-QAP-{year}-{inst}-{seq}-{chk}"
        out.append((canonical, references.verify(canonical, "mod97")))
    return out


def typo_candidates(canonical, limit=5):
    """Closest valid references for a reference whose check digits fail."""
    m = REF_PATTERN.fullmatch(canonical) or REF_PATTERN.search(canonical)
    if not m:
        return []
    year, inst, seq = m.group(1), m.group(2).upper(), m.group(3)
    qs = PaymentObligation.unscoped.filter(reference__startswith=f"NBTE-QAP-{year}-")
    scored = []
    for ref in qs.values_list("reference", flat=True)[:5000]:
        parts = ref.split("-")
        if len(parts) < 6:
            continue
        r_inst, r_seq = parts[3], parts[4]
        dist = _distance(f"{inst}{seq}", f"{r_inst}{r_seq}")
        if dist <= 2:
            scored.append((dist, ref))
    return [ref for _d, ref in sorted(scored)[:limit]]


def _distance(a, b):
    """Damerau–Levenshtein (adjacent transpositions count as one)."""
    d = {}
    for i in range(-1, len(a) + 1):
        d[(i, -1)] = i + 1
    for j in range(-1, len(b) + 1):
        d[(-1, j)] = j + 1
    for i in range(len(a)):
        for j in range(len(b)):
            cost = 0 if a[i] == b[j] else 1
            d[(i, j)] = min(d[(i - 1, j)] + 1, d[(i, j - 1)] + 1, d[(i - 1, j - 1)] + cost)
            if i and j and a[i] == b[j - 1] and a[i - 1] == b[j]:
                d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + cost)
    return d[(len(a) - 1, len(b) - 1)]


def column_map_for(bank_name=""):
    raw = config.get("qa.payment.statement_column_map", {}) or {}
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    mapping = raw.get(bank_name) or raw.get("default") or {}
    return {"date": mapping.get("date", "Value Date"), "narration": mapping.get("narration", "Narration"),
            "amount": mapping.get("amount", "Credit"), "date_format": mapping.get("date_format", "%d/%m/%Y")}


def _parse_date(text, fmt):
    text = (text or "").strip()
    for f in (fmt, "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(text, f).date()
        except (ValueError, TypeError):
            continue
    return None


def parse_statement_csv(content, mapping):
    """[(row_number, date, narration, amount)] for credit rows; raises
    ValidationError naming missing columns."""
    import csv
    import io

    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(content))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    lookup = {h.lower(): h for h in (reader.fieldnames or [])}
    missing = [mapping[k] for k in ("date", "narration", "amount") if mapping[k].lower() not in lookup]
    if missing:
        raise ValidationError(f"The CSV has no column named {', '.join(repr(m) for m in missing)}. "
                              f"Columns found: {', '.join(headers) or '(none)'}. Check the column map.")
    rows = []
    for number, row in enumerate(reader, start=2):
        amount_raw = (row.get(lookup[mapping["amount"].lower()]) or "").strip()
        if not amount_raw:
            continue
        try:
            amount = to_money(amount_raw)
        except ValueError:
            continue
        if amount <= 0:
            continue
        rows.append((number, _parse_date(row.get(lookup[mapping["date"].lower()]), mapping["date_format"]),
                     (row.get(lookup[mapping["narration"].lower()]) or "").strip(), amount))
    return rows


@transaction.atomic
def import_statement(upload, *, user, bank_account=None, bank_name=""):
    validate_upload(upload, extensions=["csv"], max_mb=10, label="statement")
    mapping = column_map_for(bank_name or (bank_account.bank_name if bank_account else ""))
    upload.seek(0)
    content = upload.read()
    rows = parse_statement_csv(content, mapping)
    statement = BankStatementImport(bank_account=bank_account, column_map=mapping, uploaded_by=user,
                                    parsed_rows=len(rows))
    upload.seek(0)
    statement.file = upload
    statement.save()
    dates = [r[1] for r in rows if r[1]]
    statement.period_start = min(dates) if dates else None
    statement.period_end = max(dates) if dates else None
    for number, value_date, narration, amount in rows:
        line = BankStatementLine.objects.create(statement=statement, row_number=number, value_date=value_date,
                                                narration=narration[:2000], amount=amount)
        _auto_match(line, user)
    _recount(statement)
    audit.log("other", statement, actor=user, note=f"statement imported: {statement.parsed_rows} credit line(s)")
    return statement


def _recount(statement):
    lines = statement.lines.all()
    statement.matched = lines.filter(match_status=BankStatementLine.MATCHED).count()
    statement.ambiguous = lines.filter(match_status=BankStatementLine.AMBIGUOUS).count()
    statement.unmatched = lines.filter(match_status=BankStatementLine.UNMATCHED).count()
    statement.save(update_fields=["period_start", "period_end", "matched", "ambiguous", "unmatched", "updated_at"])


def _auto_match(line, user):
    found = extract_references(line.narration)
    valid = [ref for ref, ok in found if ok]
    if len(set(valid)) > 1:
        line.match_status = BankStatementLine.AMBIGUOUS
        line.candidates = sorted(set(valid))
        line.extracted_reference = valid[0]
        line.save()
        return line
    if not valid:
        if found:
            line.extracted_reference = found[0][0]
            line.reference_check_failed = True
            line.candidates = typo_candidates(found[0][0])
        line.match_status = BankStatementLine.UNMATCHED
        line.save()
        return line
    ref = valid[0]
    line.extracted_reference = ref
    obligation = PaymentObligation.unscoped.filter(reference=ref).first()
    if obligation is None:
        line.match_status = BankStatementLine.UNMATCHED
        line.save()
        return line
    if line.amount != obligation.balance and obligation.status not in PaymentObligation.SETTLED \
            and obligation.status != "cancelled":
        line.match_status = BankStatementLine.AMBIGUOUS
        line.candidates = [ref]
        line.flags = ["amount_differs"]
        line.save()
        return line
    _apply_match(line, obligation, user, remark="Matched automatically by reference.")
    return line


@transaction.atomic
def _apply_match(line, obligation, user, *, remark):
    obligation = PaymentObligation.unscoped.select_for_update().get(pk=obligation.pk)
    line.match_status = BankStatementLine.MATCHED
    line.matched_obligation = obligation
    line.matched_by = user if getattr(user, "pk", None) else None
    line.matched_at = timezone.now()
    line.match_remark = remark
    line.save()
    if obligation.status in PaymentObligation.SETTLED or obligation.status == "cancelled":
        line.flags = sorted(set((line.flags or []) + ["duplicate" if obligation.status != "cancelled"
                                                       else "after_cancellation"]))
        line.save(update_fields=["flags", "updated_at"])
        _money_after_settlement(obligation, line.amount, f"Bank statement line {line.row_number}")
        return line
    if obligation.status in ("awaiting_finance_confirmation", "finance_confirmation_overdue"):
        _open_verification(obligation, "statement", line=line)
        return line
    if obligation.status in ("rejected", "partially_paid", "provider_processing"):
        _ensure_awaiting_payment(obligation, user)
    if obligation.status == "awaiting_payment":
        _move(obligation, "statement_match", user, metadata={"statement_line": line.pk})
        _open_verification(obligation, "statement", line=line)
        transaction.on_commit(lambda: payment_claimed.send(sender=PaymentObligation, obligation=obligation))
    elif obligation.status == "proof_submitted":
        _enter_queue(obligation, user, "statement", line=line)
    return line


def manual_match(line, obligation, *, user, remark):
    if not user.has_perm("payments.manual_match_payment"):
        raise PermissionDenied("You do not hold payments.manual_match_payment.")
    if len((remark or "").strip()) < 3:
        raise ValidationError("A remark is required for a manual match.")
    if line.match_status == BankStatementLine.MATCHED:
        raise ValidationError("This line is already matched.")
    _apply_match(line, obligation, user, remark=f"Manual match: {remark.strip()}")
    _recount(line.statement)
    return line


def ignore_line(line, *, user, remark):
    if len((remark or "").strip()) < 3:
        raise ValidationError("Say why the line is being ignored.")
    line.match_status = BankStatementLine.IGNORED
    line.match_remark = remark.strip()
    line.matched_by, line.matched_at = user, timezone.now()
    line.save()
    _recount(line.statement)
    return line


# ===========================================================================
# Scheduled sweep: expired slips, confirmation window, reminders, polls
# ===========================================================================
def sweep(now=None):
    now = now or timezone.now()
    report = {"expired_slips": 0, "overdue": 0, "reminders": 0, "polled": 0}
    report["expired_slips"] = PaymentSlip.unscoped.filter(is_current=True, expires_at__lte=now).count()
    percents = config.get("qa.stage.reminder_at_percent", [50, 90]) or [50, 90]
    for item in PaymentVerification.unscoped.filter(is_open=True).select_related("obligation"):
        if not item.sla_due_at:
            continue
        total = (item.sla_due_at - item.opened_at).total_seconds() or 1
        elapsed = (now - item.opened_at).total_seconds()
        for pct in percents:
            try:
                pct = int(pct)
            except (TypeError, ValueError):
                continue
            if elapsed >= total * pct / 100 and pct not in (item.reminders_sent or []) and pct < 100:
                item.reminders_sent = sorted(set((item.reminders_sent or []) + [pct]))
                item.save(update_fields=["reminders_sent", "updated_at"])
                _notify_finance_reminder(item, pct)
                report["reminders"] += 1
        obligation = item.obligation
        if now >= item.sla_due_at and obligation.status == "awaiting_finance_confirmation":
            _move(obligation, "confirmation_timeout", None, remark="Finance confirmation window passed")
            report["overdue"] += 1
            _notify_overdue(item)
    report["polled"] = poll_pending_attempts(now)
    return report


# ===========================================================================
# Notifications and alerts
# ===========================================================================
def finance_officers():
    from accounts.models import User

    return [u for u in User.objects.filter(is_active=True, user_type="staff",
                                           role__code="qa-finance-officer")[:100]]


def final_approvers():
    from accounts.models import User

    return [u for u in User.objects.filter(is_active=True, user_type="staff",
                                           role__code=final_approver_role())[:50] if is_final_approver(u)]


def _notify_finance_officers(item):
    from institutions.services import notify_role

    source = dict(PaymentVerification.SOURCE_CHOICES).get(item.source, item.source)
    notify_role("payment.claimed", {"reference": item.obligation.reference, "institution": item.obligation.institution.name,
                                    "source": source}, finance_officers(), url=f"/qa/finance/queue/{item.pk}/")


def _notify_final_approvers(item):
    from institutions.services import notify_role

    notify_role("payment.awaiting_final", {
        "reference": item.obligation.reference,
        "verifier": (item.verified_by.get_full_name() or item.verified_by.email) if item.verified_by_id else "",
        "decision": item.get_decision_display()}, final_approvers(), url=f"/qa/finance/queue/{item.pk}/")


def _notify_finance_reminder(item, pct):
    from institutions.services import notify_role

    users = final_approvers() if item.decision else finance_officers()
    notify_role("payment.reminder", {"reference": item.obligation.reference, "percent": pct}, users,
                url=f"/qa/finance/queue/{item.pk}/")


def _notify_overdue(item):
    from institutions.services import notify_role

    notify_role("payment.overdue", {"reference": item.obligation.reference}, final_approvers(),
                url=f"/qa/finance/queue/{item.pk}/")


def alert(obligation, message, *, ict=False):
    from accounts.models import User
    from institutions.services import notify_role

    users = finance_officers() + final_approvers()
    if ict:
        users += list(User.objects.filter(is_active=True, user_type="staff", role__code="ict-qa-monitor")[:20])
    timeline.record(obligation.institution, "payment.alert", subject=obligation, visibility="internal",
                    internal_text=message)
    transaction.on_commit(lambda: notify_role("payment.alert", {"reference": obligation.reference,
                                                                "message": message}, users,
                                              url="/qa/finance/obligations/"))


# ===========================================================================
# Public verification (QR) and the development simulator
# ===========================================================================
def verification_summary(token):
    """What the public verification page may show for a signed QR token."""
    data = read_verify_token(token)
    if not data:
        return None
    if data.get("k") == "slip":
        obligation = PaymentObligation.unscoped.select_related("institution").filter(reference=data["r"]).first()
        if obligation is None:
            return None
        slip = PaymentSlip.unscoped.filter(obligation=obligation, version=data.get("v") or 0).first()
        if slip is None:
            return None
        return {"kind": "slip", "title": "Payment slip", "reference": obligation.reference,
                "institution": obligation.institution.name, "code": obligation.institution.code,
                "description": obligation.description, "amount": naira(slip.amount), "version": slip.version,
                "issued": slip.generated_at, "due": obligation.due_at, "current": slip.is_current,
                "expired": slip.is_expired, "status": stage_label(obligation, public=True),
                "paid": obligation.status in PaymentObligation.SETTLED}
    if data.get("k") == "advice":
        advice = PaymentAdvice.unscoped.select_related("obligation__institution").filter(reference=data["r"]).first()
        if advice is None:
            return None
        return {"kind": "advice", "title": "Payment Advice", "reference": advice.reference,
                "payment_reference": advice.obligation.reference, "institution": advice.obligation.institution.name,
                "code": advice.obligation.institution.code, "description": advice.obligation.description,
                "amount": naira(advice.amount), "issued": advice.generated_at, "paid": True,
                "status": "Payment confirmed by the Director of Finance"}
    return None


def simulator_attempt(provider_code, ref):
    return _find_attempt(provider_code, ref)


class _SimRequest:
    """The minimum of an HttpRequest that the webhook parsers read."""

    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers or {}
        self.META = {"REMOTE_ADDR": "127.0.0.1"}


def simulate_provider_callback(provider_code, ref, outcome="success"):
    """Development only: finish a simulated checkout and deliver the provider's
    callback through the real webhook path (signature check + verify)."""
    from .providers import simulator

    if not getattr(settings, "QA_PAYMENTS_SIMULATE", False):
        raise PermissionDenied("The payment simulator is switched off.")
    if provider_code == "paystack":
        body, signature = simulator.paystack_complete(ref, outcome)
        result = handle_webhook("paystack", _SimRequest(body, {"x-paystack-signature": signature}))
    else:
        body = simulator.remita_complete(ref, outcome)
        result = handle_webhook("remita", _SimRequest(body))
    if outcome == "failed":
        attempt = _find_attempt(provider_code, ref)
        if attempt is not None:
            record_provider_result(attempt, providers.get(provider_code).verify(attempt), via="poll")
    return result


# ===========================================================================
# Verification queue rows and per-item rights (Phase 9: shared by the
# Finance screens and the mobile API)
# ===========================================================================
QUEUE_STAGES = ("first", "second", "final", "info", "overdue")
QUEUE_AGES = {"24h": 1, "3d": 3, "7d": 7}


def queue_items(user, *, source="", age="", q="", stage=""):
    """Open verification items ``user`` may see, oldest first, each with
    ``ready``/``ready_why``, ``needs_second``, ``overdue`` and ``sla_pct``,
    filtered like the queue page (source, age, search, stage tab)."""
    from django.db.models import Q

    qs = (PaymentVerification.objects.for_user(user).filter(is_open=True)
          .select_related("obligation", "obligation__institution", "verified_by", "attempt", "proof", "statement_line"))
    if source:
        qs = qs.filter(source=source)
    if age in QUEUE_AGES:
        qs = qs.filter(opened_at__lte=timezone.now() - datetime.timedelta(days=QUEUE_AGES[age]))
    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(obligation__reference__icontains=q.replace(" ", "")) | Q(obligation__institution__name__icontains=q)
                       | Q(obligation__institution__code__iexact=q) | Q(proof__teller_number__icontains=q)
                       | Q(attempt__provider_reference__icontains=q))
    out = []
    now = timezone.now()
    for item in qs.order_by("opened_at"):
        item.ready, item.ready_why = ready_for_final(item)
        item.needs_second = needs_second_check(item) and item.decision == "approved" and not item.second_decision
        item.overdue = item.obligation.status == "finance_confirmation_overdue" or bool(
            item.sla_due_at and item.sla_due_at <= now)
        if item.sla_due_at:
            total = max(1, (item.sla_due_at - item.opened_at).total_seconds())
            item.sla_pct = min(100, int(100 * (now - item.opened_at).total_seconds() / total))
        else:
            item.sla_pct = 0
        if stage == "first" and item.decision:
            continue
        if stage == "second" and not item.needs_second:
            continue
        if stage == "final" and not item.ready:
            continue
        if stage == "info" and item.decision != "needs_info":
            continue
        if stage == "overdue" and not item.overdue:
            continue
        out.append(item)
    return out


def item_rights(item, user, *, impersonating=False):
    """What ``user`` may do on one verification item (the decision panel)."""
    is_director = is_final_approver(user)
    did_first_pass = user.pk in (item.verified_by_id, item.second_verifier_id)
    return {
        "can_first": bool(user.has_perm("payments.verify_payment") and item.is_open and not item.final_decision
                          and not impersonating),
        "can_second": bool(user.has_perm("payments.second_verify_payment") and item.is_open
                           and item.decision in ("approved", "rejected") and user.pk != item.verified_by_id
                           and needs_second_check(item) and not item.second_decision),
        "is_director": is_director, "did_first_pass": did_first_pass,
        "can_final": bool(is_director and item.is_open and not did_first_pass and not impersonating),
    }
