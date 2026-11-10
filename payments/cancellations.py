"""v2.5 — cancel a QA request or an onboarding application, and refund under
the policy (``payments.refund_policy``).

    cancel(subject, actor=…, reason=…)        institution (chief executive
                                              only) or NBTE (by_nbte)
      → the workflow stops (flow ends on its cancelled / withdrawn stage; an
        open QA case and the SSQs end too), open tasks and work assignments
        close, unpaid bills are voided, a Cancellation is recorded with the
        refund quote frozen;
      → when money was paid and the policy gives some back, a RefundRequest:

    awaiting_details → awaiting_finance → awaiting_approval → approved → refunded
          ↑ (Finance asks again)  │              └→ rejected (reason)
          └───────────────────────┘    awaiting_details → lapsed (window passed)

Finance verifies (``payments.initiate_refund``); the Executive Secretary
approves (or the Director of Finance when ``qa.refund.requires_es_approval``
is off) and is never the verifier; Finance records the payment reference.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging
import types

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import audit
from qa_core import flow, timeline

from . import refund_policy as policy
from .models import ZERO, Cancellation, PaymentObligation, RefundRequest
from .money import naira

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------
def _is_staff(user):
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "user_type", "staff") == "staff"
                and user.is_active)


CE_ONLY_MESSAGE = "Only the Chief Executive can cancel"


def can_cancel_as_institution(user, subject):
    """Owner decision: only the institution's Chief Executive cancels and asks
    for the refund (not the institution administrator or department sub-accounts)."""
    from accreditation.qa_services import can_manage_requests
    from institutions.services import is_chief_executive

    return bool(getattr(user, "is_authenticated", False) and can_manage_requests(user, subject.institution)
                and is_chief_executive(user))


def is_institution_non_ce(user, subject):
    """An institution account of the subject's institution that is not its Chief
    Executive — shown "Only the Chief Executive can cancel" instead of the button."""
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "user_type", "") == "institution"
                and getattr(user, "institution_id", None) == subject.institution_id
                and not can_cancel_as_institution(user, subject))


def can_withdraw_for_nbte(user):
    import org

    return bool(_is_staff(user) and (user.is_superuser or org.is_es(user)
                                     or user.has_perm("payments.withdraw_for_nbte")))


def can_verify(user):
    return bool(_is_staff(user) and (user.is_superuser or user.has_perm("payments.initiate_refund")))


def can_record_payment(user):
    return bool(_is_staff(user) and (user.is_superuser or user.has_perm("payments.initiate_refund")
                                     or user.has_perm("payments.confirm_refund")))


def is_approver(user, rr=None):
    """ES (or the Director of Finance when ES approval is switched off)."""
    from . import bill_requests, services

    kind = (rr.approver_kind if rr is not None and rr.approver_kind else
            ("es" if policy.requires_es_approval() else "finance"))
    if kind == "es":
        return bill_requests.is_approver(user)
    return bool(_is_staff(user) and (user.is_superuser or services.is_final_approver(user)))


def can_view(user):
    from qa_core.permissions import can_view_finance

    return bool(can_view_finance(user) or is_approver(user) or can_withdraw_for_nbte(user))


def cancellation_of(subject):
    ct = ContentType.objects.get_for_model(type(subject))
    return Cancellation.unscoped.filter(subject_type=ct, subject_id=str(subject.pk)).first()


def refund_of(subject):
    c = cancellation_of(subject)
    return RefundRequest.unscoped.filter(cancellation=c).first() if c is not None else None


def is_cancellable(subject):
    if cancellation_of(subject) is not None:
        return False
    return policy.position(subject)[2] != policy.NONE


# ---------------------------------------------------------------------------
# Stopping the workflow
# ---------------------------------------------------------------------------
def _terminate(instance, end_code, actor, remark, metadata=None):
    """End a flow instance on ``end_code`` (or with no stage when the flow has
    none), recording a 'cancel' event. Task-bridge listeners close the tasks
    of the stages left."""
    from qa_core.models import FlowEvent, FlowInstance

    if instance is None:
        return None
    instance = FlowInstance.objects.select_for_update().get(pk=instance.pk)
    if instance.completed_at is not None:
        return instance
    before = list(instance.current_stages)
    has_end = end_code and instance.flow_version.stages.filter(code=end_code).exists()
    instance.current_stages = [end_code] if has_end else []
    instance.completed_at = timezone.now()
    instance.outcome = end_code or "cancelled"
    instance.save(update_fields=["current_stages", "completed_at", "outcome", "updated_at"])
    FlowEvent.objects.create(instance=instance, from_stage=",".join(before), to_stage=end_code or "cancelled",
                             action_code="cancel", actor=actor if getattr(actor, "pk", None) else None,
                             actor_role_label=getattr(getattr(actor, "role", None), "name", "")[:100],
                             remark=(remark or "").strip(), metadata={"refund_policy": True, **(metadata or {})})
    subject = instance.subject
    if has_end:
        flow._sync_status(instance, subject)
    flow._notify_listeners(instance, before, None, actor, remark, subject)
    return instance


def _close_work(subject, actor, reason="cancelled"):
    """Close every open QA task link and work assignment on ``subject``."""
    from qa_core import task_bridge
    from qa_core.models import QATaskLink

    ct = ContentType.objects.get_for_model(subject, for_concrete_model=True)
    for link in QATaskLink.objects.filter(content_type=ct, object_id=subject.pk, closed_at__isnull=True) \
            .select_related("task"):
        task_bridge._close_link(link, reason=reason, actor=actor)
    try:
        from assignments import work

        work.close_for_subject(subject, actor=actor if getattr(actor, "pk", None) else None, reason="withdrawn")
    except Exception:  # an assignment that cannot close must not keep the request alive
        logger.exception("Could not close work assignments on %s", subject)


def _set_status(subject, value):
    model = type(subject)
    manager = getattr(model, "unscoped", None) or model._base_manager
    manager.filter(pk=subject.pk).update(status=value, updated_at=timezone.now())
    subject.status = value


def _stop_case(req, actor, remark):
    from accreditation.models import SSQ, QACase

    case = QACase.unscoped.filter(qa_request=req).first()
    if case is not None and case.status not in ("closed", "cancelled"):
        _terminate(flow.instance_for(case, "qa_case"), None, actor, remark)
        _set_status(case, "cancelled")
        QACase.unscoped.filter(pk=case.pk).update(completed_at=timezone.now())
        _close_work(case, actor)
    for ssq in SSQ.unscoped.filter(qa_request_programme__qa_request=req):
        instance = flow.instance_for(ssq)
        if instance is not None and instance.completed_at is None:
            _terminate(instance, None, actor, remark)
        if ssq.status not in ("cancelled",):
            _set_status(ssq, "cancelled")
        _close_work(ssq, actor)


def _try_transition(instance, action, actor, remark):
    """Take the flow's own cancel / withdraw transition when it has one."""
    if instance is None or instance.completed_at is not None or not flow.can(instance, action):
        return False
    try:
        with transaction.atomic():
            flow.transition(instance, action, actor, remark=remark)
    except (ValidationError, PermissionDenied):
        return False
    return True


def _stop_request(req, actor, reason):
    """End a QA request: through its own ``cancel`` transition while the flow
    has one (R-25 stages), otherwise by ending the flow and running the same
    clean-up (``qa_services.on_cancelled``)."""
    from accreditation import qa_services as svc

    instance = svc.request_instance(req)
    if not _try_transition(instance, "cancel", actor, reason):
        _terminate(instance, "cancelled", actor, reason)
        _set_status(req, "cancelled")
        svc.on_cancelled(types.SimpleNamespace(subject=req, actor=actor, remark=reason, instance=instance,
                                               metadata={}, argument="", transition=None))
    req.refresh_from_db()
    _stop_case(req, actor, reason)
    _close_work(req, actor)


def _stop_application(app, actor, reason):
    instance = flow.instance_for(app, "onboarding")
    if not _try_transition(instance, "withdraw", actor, reason):
        _terminate(instance, "withdrawn", actor, reason)
        _set_status(app, "withdrawn")
    app.refresh_from_db()
    _close_work(app, actor)
    for req in policy._onboarding_requests(app):  # the self-study is part of the application
        _stop_request(req, actor, f"Onboarding application {app.reference} cancelled: {reason}")


def _void_unpaid(obligations, actor, reason):
    """Cancel the unpaid bills among ``obligations`` (listed before the
    workflow stopped: a request's own cancel step may already have voided
    some). Returns the references of every unpaid bill now cancelled."""
    from . import services

    voided = []
    for ob in obligations:
        was_unpaid = ob.status in policy.UNPAID_VOIDABLE and (ob.amount_paid or ZERO) == 0
        ob.refresh_from_db()
        if not was_unpaid:
            continue
        if ob.status in policy.UNPAID_VOIDABLE and (ob.amount_paid or ZERO) == 0 and policy.void_unpaid_bills():
            services.cancel(ob, actor=actor, reason=reason)
        if ob.status == "cancelled" or PaymentObligation.unscoped.filter(pk=ob.pk, status="cancelled").exists():
            voided.append(ob.reference)
    return voided


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------
def _reference(prefix, model):
    year = timezone.localdate().year
    n = model.unscoped.filter(reference__startswith=f"{prefix}-{year}-").count() + 1
    while model.unscoped.filter(reference=f"{prefix}-{year}-{n:05d}").exists():
        n += 1
    return f"{prefix}-{year}-{n:05d}"


def _clean_bank(bank):
    bank = {k: (str(v or "").strip()) for k, v in (bank or {}).items()}
    if not any(bank.get(k) for k in ("bank_name", "account_name", "account_number")):
        return None
    missing = [label for key, label in (("bank_name", "bank"), ("account_name", "account name"),
                                        ("account_number", "account number")) if not bank.get(key)]
    if missing:
        raise ValidationError(f"Give the {', '.join(missing)} for the refund, or leave all bank fields empty "
                              "and add them later.")
    number = "".join(ch for ch in bank["account_number"] if ch.isdigit())
    if len(number) != 10:
        raise ValidationError("The account number must be the 10-digit NUBAN.")
    return {"bank_name": bank["bank_name"][:120], "account_name": bank["account_name"][:200],
            "account_number": number}


@transaction.atomic
def cancel(subject, *, actor, reason, declaration=False, by_nbte=False, bank=None):
    """Cancel ``subject`` (QARequest or Application). Returns the Cancellation."""
    model = type(subject)
    subject = model.unscoped.select_for_update().select_related("institution").get(pk=subject.pk)
    kind = policy.kind_of(subject)
    reason = (reason or "").strip()
    if by_nbte:
        if not can_withdraw_for_nbte(actor):
            raise PermissionDenied("Only the Executive Secretary (or staff given that right) can withdraw on "
                                   "NBTE's side.")
        if len(reason) < 10:
            raise ValidationError("Give the reason NBTE is withdrawing it (at least 10 characters); the "
                                  "institution sees it.")
    else:
        if not can_cancel_as_institution(actor, subject):
            raise PermissionDenied(CE_ONLY_MESSAGE + " this and request a refund.")
        if len(reason) < 5:
            raise ValidationError("Say why you are cancelling (at least 5 characters).")
        if not declaration:
            raise ValidationError("Tick the declaration to confirm the cancellation.")
    if cancellation_of(subject) is not None:
        raise ValidationError(f"{subject.reference} has already been cancelled.")
    quote = policy.refund_quote(subject, by_nbte=by_nbte)
    if not quote["cancellable"]:
        raise ValidationError(f"{subject.reference} can no longer be cancelled ({quote['stage_label'].lower()}).")
    bank = _clean_bank(bank) if quote["refund_amount"] > 0 else None
    label = "withdrawn by NBTE" if by_nbte else "cancelled"
    remark = f"{'Withdrawn by NBTE' if by_nbte else 'Cancelled by the institution'}: {reason}"

    bills = policy.obligations_of(subject)
    if kind == "qa_request":
        _stop_request(subject, actor, remark)
    else:
        _stop_application(subject, actor, remark)
    voided = _void_unpaid(bills, actor, f"{subject.reference} {label}.")

    try:
        with transaction.atomic():
            cancellation = Cancellation.unscoped.create(
                institution=subject.institution, kind=kind, subject_type=ContentType.objects.get_for_model(model),
                subject_id=str(subject.pk), subject_reference=subject.reference, by_nbte=by_nbte, reason=reason,
                declaration_accepted=bool(declaration), cancelled_by=actor if getattr(actor, "pk", None) else None,
                stage_code=quote["stage"], stage_label=quote["stage_label"][:200],
                quote=policy.quote_for_storage(quote), voided_obligations=voided)
    except IntegrityError:
        raise ValidationError(f"{subject.reference} has already been cancelled.") from None
    refund = None
    if quote["refund_amount"] > 0:
        refund = _open_refund(cancellation, quote, bank=bank, actor=actor)
    audit.log("other", cancellation, actor=actor,
              note=f"{subject.reference} {label} at {quote['stage_label']}: refund {quote['percent']}% = "
                   f"{naira(quote['refund_amount'])}"[:255])
    public = (f"{subject.reference} was {label}. " +
              (f"Refund under the policy: {quote['percent']}% of {naira(quote['paid'])} = "
               f"{naira(quote['refund_amount'])} ({refund.reference})." if refund else
               ("No refund is due under the policy." if quote["paid"] > 0 else "Nothing had been paid.")))
    timeline.record(subject.institution, "refund_policy.cancelled", subject=subject, actor=actor,
                    visibility="public", public_text=public, internal_text=reason)
    transaction.on_commit(lambda: _notify_cancelled(cancellation, refund, actor))
    return cancellation


def _open_refund(cancellation, quote, *, bank=None, actor=None):
    now = timezone.now()
    rr = RefundRequest(
        reference=_reference("RF", RefundRequest), institution=cancellation.institution, cancellation=cancellation,
        band=quote["band"], rule_label=quote["rule_label"][:255], percent=quote["percent"],
        paid_amount=quote["paid"], admin_fee=quote["admin_fee"], refund_amount=quote["refund_amount"],
        obligations=quote["obligations"], expires_at=now + datetime.timedelta(days=policy.window_days()),
        approver_kind="es" if policy.requires_es_approval() else "finance")
    if bank:
        rr.bank_name, rr.account_name, rr.account_number = bank["bank_name"], bank["account_name"], \
            bank["account_number"]
        rr.details_by, rr.details_at = (actor if getattr(actor, "pk", None) else None), now
        rr.status = RefundRequest.FINANCE
    rr.save()
    return rr


# ---------------------------------------------------------------------------
# Refund lifecycle
# ---------------------------------------------------------------------------
def _lock(rr):
    return RefundRequest.unscoped.select_for_update().select_related("institution", "cancellation").get(pk=rr.pk)


def lapse_overdue(now=None):
    """Refunds still without bank details after the window → lapsed."""
    now = now or timezone.now()
    n = 0
    for rr in RefundRequest.unscoped.filter(status=RefundRequest.DETAILS, expires_at__lt=now):
        rr.status = RefundRequest.LAPSED
        rr.save(update_fields=["status", "updated_at"])
        timeline.record(rr.institution, "refund_policy.lapsed", subject=rr, visibility="public",
                        public_text=f"Refund {rr.reference} lapsed: no bank details were given within "
                                    f"{policy.window_days()} days.")
        n += 1
    return n


@transaction.atomic
def submit_bank_details(rr, *, actor, bank):
    rr = _lock(rr)
    if not can_cancel_as_institution(actor, rr):
        raise PermissionDenied("Only the Chief Executive can give the refund bank details.")
    if rr.status != RefundRequest.DETAILS:
        raise ValidationError(f"{rr.reference} is {rr.get_status_display().lower()}; the bank details cannot be "
                              "changed now.")
    if rr.expires_at and rr.expires_at < timezone.now():
        lapse_overdue()
        raise ValidationError(f"The {policy.window_days()}-day window for this refund has passed.")
    bank = _clean_bank(bank)
    if bank is None:
        raise ValidationError("Give the bank, account name and 10-digit account number.")
    rr.bank_name, rr.account_name, rr.account_number = bank["bank_name"], bank["account_name"], bank["account_number"]
    rr.details_by, rr.details_at, rr.status = actor, timezone.now(), RefundRequest.FINANCE
    rr.save()
    audit.log("other", rr, actor=actor, note=f"Bank details given for refund {rr.reference}")
    transaction.on_commit(lambda: _notify_finance(rr, f"Refund to verify: {rr.reference}",
                                                  f"{rr.institution.name} gave its bank details for "
                                                  f"{naira(rr.refund_amount)}."))
    return rr


@transaction.atomic
def verify(rr, *, actor, note="", impersonating=False):
    rr = _lock(rr)
    if not can_verify(actor):
        raise PermissionDenied("Verifying a refund needs Finance access (payments.initiate_refund).")
    if impersonating:
        raise PermissionDenied("You cannot verify while impersonating another user.")
    if rr.status != RefundRequest.FINANCE:
        raise ValidationError(f"{rr.reference} is {rr.get_status_display().lower()}.")
    rr.verified_by, rr.verified_at, rr.finance_note = actor, timezone.now(), (note or "").strip()
    rr.status = RefundRequest.APPROVAL
    rr.save()
    audit.log("other", rr, actor=actor, note=f"Refund {rr.reference} verified by Finance")
    who = "the Executive Secretary" if rr.approver_kind == "es" else "the Director of Finance"
    timeline.record(rr.institution, "refund_policy.verified", subject=rr, actor=actor, visibility="public",
                    public_text=f"Finance verified refund {rr.reference}; it is with {who} for approval.")
    transaction.on_commit(lambda: _notify_approvers(rr, actor))
    return rr


@transaction.atomic
def return_to_institution(rr, *, actor, note):
    rr = _lock(rr)
    if not can_verify(actor):
        raise PermissionDenied("Needs Finance access (payments.initiate_refund).")
    if rr.status != RefundRequest.FINANCE:
        raise ValidationError(f"{rr.reference} is {rr.get_status_display().lower()}.")
    note = (note or "").strip()
    if len(note) < 5:
        raise ValidationError("Say what is wrong with the bank details (at least 5 characters).")
    rr.status, rr.finance_note = RefundRequest.DETAILS, note
    rr.expires_at = max(rr.expires_at or timezone.now(), timezone.now() + datetime.timedelta(days=14))
    rr.save()
    timeline.record(rr.institution, "refund_policy.details_returned", subject=rr, actor=actor, visibility="public",
                    public_text=f"Finance asked for new bank details for refund {rr.reference}: {note}")
    transaction.on_commit(lambda: _notify_institution(rr, f"Check your bank details: {rr.reference}", note))
    return rr


def can_decide(rr, user, *, impersonating=False):
    if rr.status != RefundRequest.APPROVAL:
        return False, f"{rr.reference} is {rr.get_status_display().lower()}."
    if not is_approver(user, rr):
        return False, ("Only the Executive Secretary approves refunds." if rr.approver_kind == "es"
                       else "Only the Director of Finance approves refunds.")
    if impersonating:
        return False, "You cannot approve while impersonating another user."
    if rr.verified_by_id and rr.verified_by_id == user.pk:
        return False, "You verified this refund, so someone else must approve it."
    return True, ""


@transaction.atomic
def approve(rr, *, actor, remark="", impersonating=False):
    rr = _lock(rr)
    ok, why = can_decide(rr, actor, impersonating=impersonating)
    if not ok:
        raise PermissionDenied(why)
    rr.status, rr.decided_by, rr.decided_at = RefundRequest.APPROVED, actor, timezone.now()
    rr.decision_remark = (remark or "").strip()
    rr.save()
    audit.log("other", rr, actor=actor, note=f"Refund {rr.reference} approved: {naira(rr.refund_amount)}")
    timeline.record(rr.institution, "refund_policy.approved", subject=rr, actor=actor, visibility="public",
                    public_text=f"Refund {rr.reference} of {naira(rr.refund_amount)} was approved; Finance will "
                                f"pay it to the account ending {rr.account_number[-4:]}.")
    transaction.on_commit(lambda: (_notify_finance(rr, f"Refund approved — pay it: {rr.reference}",
                                                   f"{naira(rr.refund_amount)} to {rr.account_name}, "
                                                   f"{rr.bank_name}."),
                                   _notify_institution(rr, f"Refund approved: {rr.reference}",
                                                       f"{naira(rr.refund_amount)} will be paid to your account.")))
    return rr


@transaction.atomic
def reject(rr, *, actor, remark, impersonating=False):
    rr = _lock(rr)
    ok, why = can_decide(rr, actor, impersonating=impersonating)
    if not ok:
        raise PermissionDenied(why)
    remark = (remark or "").strip()
    if len(remark) < 5:
        raise ValidationError("Give the reason for rejecting (at least 5 characters); the institution sees it.")
    rr.status, rr.decided_by, rr.decided_at, rr.decision_remark = (RefundRequest.REJECTED, actor, timezone.now(),
                                                                   remark)
    rr.save()
    audit.log("other", rr, actor=actor, note=f"Refund {rr.reference} rejected: {remark}"[:255])
    timeline.record(rr.institution, "refund_policy.rejected", subject=rr, actor=actor, visibility="public",
                    public_text=f"Refund {rr.reference} was not approved: {remark}")
    transaction.on_commit(lambda: _notify_institution(rr, f"Refund not approved: {rr.reference}", remark))
    return rr


@transaction.atomic
def mark_refunded(rr, *, actor, paid_reference):
    rr = _lock(rr)
    if not can_record_payment(actor):
        raise PermissionDenied("Recording a refund payment needs Finance access.")
    if rr.status != RefundRequest.APPROVED:
        raise ValidationError(f"{rr.reference} is {rr.get_status_display().lower()}; only an approved refund "
                              "can be marked paid.")
    paid_reference = (paid_reference or "").strip()
    if len(paid_reference) < 3:
        raise ValidationError("Enter the bank / payment reference of the refund.")
    rr.status, rr.paid_reference, rr.paid_at, rr.paid_by = (RefundRequest.REFUNDED, paid_reference[:100],
                                                            timezone.now(), actor)
    rr.save()
    audit.log("other", rr, actor=actor, note=f"Refund {rr.reference} paid: {paid_reference}"[:255])
    timeline.record(rr.institution, "refund_policy.refunded", subject=rr, actor=actor, visibility="public",
                    public_text=f"Refund {rr.reference} of {naira(rr.refund_amount)} was paid (reference "
                                f"{paid_reference}).")
    transaction.on_commit(lambda: _notify_institution(rr, f"Refund paid: {rr.reference}",
                                                      f"{naira(rr.refund_amount)} was paid. Bank reference: "
                                                      f"{paid_reference}."))
    return rr


# ---------------------------------------------------------------------------
# Money confirmed after the cancellation (a claim Finance confirms later)
# ---------------------------------------------------------------------------
def on_payment_settled(sender=None, obligation=None, **kwargs):
    """A bill of an already-cancelled subject is confirmed: add the money to
    the refund at the percentage frozen at cancellation."""
    if obligation is None or obligation.status not in policy.PAID_STATUSES:
        return
    try:
        _add_late_payment(obligation)
    except Exception:
        logger.exception("Refund policy: could not record late payment on %s", obligation)


@transaction.atomic
def _add_late_payment(obligation):
    from accreditation.models import Application, QACase, QARequest

    subject = obligation.source
    if isinstance(subject, QACase):
        subject = subject.qa_request
    if not isinstance(subject, (QARequest, Application)):
        return
    cancellation = cancellation_of(subject)
    if cancellation is None and isinstance(subject, QARequest) and subject.qa_type.code == "onboarding_ssq":
        app = Application.unscoped.filter(institution_id=subject.institution_id).order_by("-created_at").first()
        cancellation = cancellation_of(app) if app is not None else None
    if cancellation is None:
        return
    quote = cancellation.quote or {}
    if any(str(o.get("id")) == str(obligation.pk) for o in quote.get("obligations") or []):
        return
    amount = obligation.amount_paid or ZERO
    if amount <= 0:
        return
    percent = int(quote.get("percent") or 0)
    fee, refund = policy.compute(amount, percent, apply_min_fee=False)
    quote.setdefault("obligations", []).append({"id": obligation.pk, "reference": obligation.reference,
                                                "paid": str(amount), "late": True})
    cancellation.quote = quote
    cancellation.save(update_fields=["quote", "updated_at"])
    if refund <= 0:
        return
    rr = RefundRequest.unscoped.select_for_update().filter(cancellation=cancellation).first()
    if rr is None:
        rr = _open_refund(cancellation, {**quote, "paid": amount, "admin_fee": fee, "refund_amount": refund,
                                         "obligations": [quote["obligations"][-1]],
                                         "rule_label": quote.get("rule_label", ""),
                                         "band": quote.get("band", ""), "percent": percent})
    elif rr.status in (RefundRequest.DETAILS, RefundRequest.FINANCE):
        rr.paid_amount += amount
        rr.admin_fee += fee
        rr.refund_amount += refund
        rr.obligations = (rr.obligations or []) + [quote["obligations"][-1]]
        rr.save()
    else:
        return
    timeline.record(rr.institution, "refund_policy.late_payment", subject=rr, visibility="internal",
                    internal_text=f"{obligation.reference} confirmed after cancellation: {naira(refund)} added "
                                  f"to refund {rr.reference}.")


def connect():
    from . import services

    services.payment_settled.connect(on_payment_settled, sender=PaymentObligation,
                                     dispatch_uid="payments.refund_policy.late_payment", weak=False)


# ---------------------------------------------------------------------------
# Notifications (in-app)
# ---------------------------------------------------------------------------
def _staff_url(rr):
    return f"/qa/finance/refund-requests/{rr.pk}/"


def _institution_users(inst):
    from accounts.models import User

    return list(User.objects.filter(institution=inst, user_type="institution", is_active=True)[:50])


def _notify_institution(rr, title, body):
    from notifications.services import notify_many

    notify_many(_institution_users(rr.institution), title=title, body=body, url="/refunds/")


def _notify_finance(rr, title, body):
    from notifications.services import notify_many

    from . import services

    people = {u.pk: u for u in services.finance_officers() + services.final_approvers()}
    notify_many(list(people.values()), title=title, body=f"{rr.institution.name}: {body}", url=_staff_url(rr))


def _notify_approvers(rr, actor):
    from notifications.services import notify_many

    from . import bill_requests, services

    people = bill_requests.es_users() if rr.approver_kind == "es" else services.final_approvers()
    notify_many(people, title=f"Refund awaiting your approval: {rr.reference}",
                body=f"{rr.institution.name}: {naira(rr.refund_amount)} ({rr.percent:.0f}% of "
                     f"{naira(rr.paid_amount)} paid). Verified by "
                     f"{(actor.get_full_name() or actor.email) if actor else 'Finance'}.",
                url=_staff_url(rr), actor=actor)


def _notify_cancelled(cancellation, refund, actor):
    from notifications.services import notify_many

    if cancellation.by_nbte:
        title = f"NBTE withdrew {cancellation.subject_reference}"
        body = cancellation.reason
        if refund is not None:
            body += f" A full refund of {naira(refund.refund_amount)} is due — add your bank details on the " \
                    "Refunds page."
        notify_many(_institution_users(cancellation.institution), title=title, body=body, url="/refunds/")
    if refund is not None:
        _notify_finance(refund, f"Refund opened: {refund.reference}",
                        f"{cancellation.subject_reference} cancelled; {naira(refund.refund_amount)} due "
                        f"({refund.percent:.0f}%).")
