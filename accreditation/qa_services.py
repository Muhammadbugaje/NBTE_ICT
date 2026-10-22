"""SOP-02 QA requests and the SSQ (Master Plan §5.4, §5.7; Phase 3) — system code.

The state machines are the seeded ``qa_request`` and ``ssq`` flows (data)::

    draft --submit--> submitted --route--> [programme_clearance] --> fee_due
        --payment_claimed--> payment_pending_confirmation --payment_confirmed--> fee_confirmed
        --auto--> ssq_in_progress --(every SSQ submitted)--> ssq_submitted --auto--> in_workflow (QA case)

    cancel / drop_programme / replace_programme exist only on draft, submitted, fee_due and
    payment_pending_confirmation (B6/R-25): this module asks the flow (``flow.can``), it never
    hard-codes the stages.

    SSQ: draft ⇄ returned_to_department → submitted_by_department → submitted → accepted | returned
         returned → a new version supersedes it (Requirement 16).

Views pass in objects they loaded through ``for_user``; this module reads with
``unscoped`` because it runs as the system.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config
from institutions import services as inst_services
from institutions.models import Institution, InstitutionDocument, InstitutionRequest, InstitutionRequestType, Programme
from qa_core import flow, references, timeline

from . import ssq as engine
from .models import (
    ApplicationCycle, Evidence, QACase, QARequest, QARequestProgramme, QAType, Query, Response, SSQ, Submission,
)

logger = logging.getLogger(__name__)
REQUEST_FLOW = "qa_request"
SSQ_FLOW = "ssq"
OPEN_STATUSES = ("draft", "submitted", "programme_clearance", "fee_due", "payment_pending_confirmation",
                 "fee_confirmed", "ssq_in_progress", "ssq_submitted", "in_workflow")


def _int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _json_list(key, default):
    value = config.get(key, default)
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            value = default
    return list(value or [])


# ===========================================================================
# Lookups
# ===========================================================================
def request_instance(req):
    return flow.instance_for(req, req.flow_code)


def ssq_instance(ssq):
    return flow.instance_for(ssq, SSQ_FLOW)


def public_status(req):
    return flow.public_label(request_instance(req)) or req.status


def ssq_public_status(ssq):
    return flow.public_label(ssq_instance(ssq)) or ssq.status


def is_cancellable(req):
    """Derived from the flow: only stages that have a `cancel` transition (R-25)."""
    return flow.can(request_instance(req), "cancel")


def can_change_programmes(req, action="drop_programme"):
    return flow.can(request_instance(req), action)


def cancellable_stage_labels(req=None, flow_code=REQUEST_FLOW):
    from qa_core.models import FlowTransition

    version = flow.current_version(flow_code) if req is None else request_instance(req).flow_version
    if version is None:
        return []
    return list(FlowTransition.objects.filter(flow=version, action_code="cancel")
                .select_related("from_stage").values_list("from_stage__label", flat=True))


def current_ssq(row):
    return SSQ.unscoped.filter(qa_request_programme=row, is_current=True).order_by("-version").first()


def request_ssqs(req, *, current_only=True):
    qs = SSQ.unscoped.filter(qa_request_programme__qa_request=req).select_related(
        "qa_request_programme__programme__level", "template")
    if current_only:
        qs = qs.filter(is_current=True, qa_request_programme__status=QARequestProgramme.ACTIVE)
    return list(qs.order_by("qa_request_programme__programme__name", "-version"))


# ===========================================================================
# Eligibility (Requirement 4 and the QA type rules)
# ===========================================================================
def window_months():
    return _int("qa.programme.reassessment_window_months", 6)


def window_opens(programme):
    """The date from which a programme may apply for reassessment."""
    if programme.eligible_for_reassessment_at:
        return programme.eligible_for_reassessment_at
    if programme.expiry_date is None:
        return None
    months = window_months()
    y, m = programme.expiry_date.year, programme.expiry_date.month - months
    while m <= 0:
        m += 12
        y -= 1
    import calendar

    day = min(programme.expiry_date.day, calendar.monthrange(y, m)[1])
    return datetime.date(y, m, day)


def programme_eligibility(programme, qa_type, *, today=None):
    """(eligible, reason)."""
    today = today or timezone.localdate()
    if programme.status == "dropped":
        return False, "This programme has been dropped."
    if qa_type.admin_programme_only and not programme.is_institutional_admin:
        return False, "Only the Institutional Administration programme."
    if not qa_type.admin_programme_only and programme.is_institutional_admin:
        return False, "Use the Institutional Administration QA type."
    statuses = list(qa_type.eligible_programme_statuses or [])
    if qa_type.includes_new_programmes and "proposed" not in statuses:
        statuses.append("proposed")
    if statuses and programme.status not in statuses:
        return False, f"{qa_type.name} is for programmes that are " \
                      f"{', '.join(s.replace('_', ' ') for s in statuses)} (this one is " \
                      f"{programme.get_status_display().lower()})."
    if qa_type.uses_reassessment_window and programme.status != "expired":
        opens = window_opens(programme)
        if opens is None:
            return False, "The programme has no expiry date on record."
        if today < opens:
            return False, (f"Too early: reassessment opens on {opens:%d %b %Y}, "
                           f"{window_months()} months before expiry (Requirement 4).")
    busy = (QARequestProgramme.unscoped.filter(programme=programme, status=QARequestProgramme.ACTIVE,
                                               qa_request__status__in=OPEN_STATUSES)
            .select_related("qa_request").first())
    if busy is not None:
        return False, f"Already in an open request ({busy.qa_request.reference})."
    return True, ""


def eligible_programmes(inst, qa_type):
    rows = []
    for programme in (Programme.unscoped.filter(institution=inst).exclude(status="dropped")
                      .select_related("level", "category").order_by("name")):
        ok, why = programme_eligibility(programme, qa_type)
        rows.append({"programme": programme, "eligible": ok, "reason": why})
    return rows


def type_availability(inst, qa_type):
    """(available, reason) for starting a new request of this type."""
    if inst.status != Institution.APPROVED:
        return False, "QA requests are for approved institutions."
    if not qa_type.is_active:
        return False, "This QA type is switched off."
    if qa_type.ssq_template_id is None:
        return False, "NBTE has not published the questionnaire for this QA type yet."
    open_count = QARequest.unscoped.filter(institution=inst, qa_type=qa_type, status__in=OPEN_STATUSES).count()
    if open_count >= _int("qa.qa_request.max_open_per_type", 1):
        return False, "You already have an open request of this type."
    cycle_ok, _cycle = cycle_for(qa_type)
    if not cycle_ok:
        return False, "Requests of this type are closed at the moment."
    return True, ""


def cycle_for(qa_type):
    """(accepting, cycle). Types not listed on any cycle are always open."""
    cycles = ApplicationCycle.objects.filter(qa_types=qa_type)
    if not cycles.exists():
        return True, None
    for cycle in cycles.order_by("-opens_at"):
        if cycle.accepting():
            return True, cycle
    return False, None


# ===========================================================================
# Requests
# ===========================================================================
def _role_code(user):
    return getattr(getattr(user, "role", None), "code", "")


def is_department_user(user):
    return getattr(user, "user_type", "") == "institution" and _role_code(user) == "inst-academic-dept"


def can_manage_requests(user, inst):
    """Institution administrators and chief executives (not department sub-accounts)."""
    return (getattr(user, "user_type", "") == "institution" and user.institution_id == inst.pk
            and not is_department_user(user))


# ===========================================================================
# Streams (Phase 8): how many streams the institution asks for per programme
# ===========================================================================
def max_streams():
    return max(1, _int("qa.streams.max_per_programme", 10))


def clean_streams(value, *, label="Number of streams"):
    """An int between 1 and qa.streams.max_per_programme, or ValidationError."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{label}: enter a whole number.") from None
    top = max_streams()
    if n < 1 or n > top:
        raise ValidationError(f"{label}: enter a number from 1 to {top}.")
    return n


def default_streams(programme, qa_type=None):
    """What the streams box starts at: a new programme its proposed streams,
    an increase in carrying capacity one more than approved, otherwise the
    approved streams (at least 1)."""
    approved = programme.approved_streams or 0
    if programme.status == "proposed":
        return max(1, programme.requested_streams or 1)
    if qa_type is not None and qa_type.code == "increase_capacity":
        return min(max_streams(), approved + 1)
    return max(1, approved)


def _streams_for(programme, qa_type, given):
    if not qa_type.asks_streams or given is None:
        return default_streams(programme, qa_type)
    n = clean_streams(given, label=str(programme))
    if qa_type.code == "increase_capacity" and n <= (programme.approved_streams or 0):
        raise ValidationError(f"{programme}: ask for more than the {programme.approved_streams} stream(s) already "
                              "approved.")
    return n


@transaction.atomic
def set_streams(req, row, value, *, user):
    """Change the streams asked for on a draft request."""
    if req.status != "draft":
        raise ValidationError("Streams can only be changed while the request is a draft.")
    if not req.qa_type.asks_streams:
        raise ValidationError(f"{req.qa_type.name} requests do not ask for streams.")
    n = _streams_for(row.programme, req.qa_type, value)
    if n != row.requested_streams:
        row.requested_streams = n
        row.save(update_fields=["requested_streams", "updated_at"])
        timeline.record(req.institution, "qa_request.streams", subject=req, actor=user, visibility="public",
                        public_text=f"{row.programme}: {n} stream(s) asked for on {req.reference}.")
    return row


@transaction.atomic
def create_request(inst, qa_type, programmes, *, user, streams=None):
    if user is not None and getattr(user, "user_type", "") == "institution" and not can_manage_requests(user, inst):
        raise PermissionDenied("Your account cannot start QA requests.")
    ok, why = type_availability(inst, qa_type)
    if not ok:
        raise ValidationError(why)
    programmes = list(programmes)
    if not programmes:
        raise ValidationError("Choose at least one programme.")
    if qa_type.max_programmes and len(programmes) > qa_type.max_programmes:
        raise ValidationError(f"At most {qa_type.max_programmes} programme(s) per {qa_type.name} request.")
    for programme in programmes:
        if programme.institution_id != inst.pk:
            raise ValidationError("Choose your own programmes.")
        eligible, reason = programme_eligibility(programme, qa_type)
        if not eligible:
            raise ValidationError(f"{programme}: {reason}")
    streams = streams or {}
    asked = {p.pk: _streams_for(p, qa_type, streams.get(p.pk)) for p in programmes}
    _accepting, cycle = cycle_for(qa_type)
    req = QARequest.unscoped.create(reference=references.issue("qa_request", inst=inst.code), institution=inst,
                                    qa_type=qa_type, cycle=cycle, status="draft",
                                    created_by=user if getattr(user, "pk", None) else None)
    for programme in programmes:
        QARequestProgramme.unscoped.create(qa_request=req, programme=programme,
                                           is_new_programme=programme.status == "proposed",
                                           requested_streams=asked[programme.pk])
    container_type = InstitutionRequestType.objects.filter(code="qa").first()
    if container_type is not None:
        container = InstitutionRequest.unscoped.create(
            institution=inst, request_type=container_type, reference=req.reference, status="draft",
            title=f"{qa_type.name} — {len(programmes)} programme(s)",
            content_type=ContentType.objects.get_for_model(QARequest), object_id=str(req.pk))
        req.institution_request = container
        req.save(update_fields=["institution_request", "updated_at"])
    flow.start(req.flow_code, req, actor=user)
    _refresh_joint_qa(req)
    timeline.record(inst, "qa_request.created", subject=req, actor=user, visibility="public",
                    public_text=f"{qa_type.name} request {req.reference} started for "
                                f"{', '.join(str(p) for p in programmes)}.")
    return req


def _sync_container(req, status):
    if req.institution_request_id:
        InstitutionRequest.unscoped.filter(pk=req.institution_request_id).update(status=status,
                                                                                 updated_at=timezone.now())


@transaction.atomic
def add_programme(req, programme, *, user, remark="", streams=None):
    req = QARequest.unscoped.select_for_update().get(pk=req.pk)
    instance = request_instance(req)
    if not flow.can(instance, "add_programme"):
        raise ValidationError(f"Programmes can only be added while the request is "
                              f"{_stage_list(instance, 'add_programme')}.")
    if programme.institution_id != req.institution_id:
        raise ValidationError("That programme belongs to another institution.")
    if QARequestProgramme.unscoped.filter(qa_request=req, programme=programme,
                                          status=QARequestProgramme.ACTIVE).exists():
        raise ValidationError(f"{programme} is already on this request.")
    eligible, reason = programme_eligibility(programme, req.qa_type)
    if not eligible:
        raise ValidationError(f"{programme}: {reason}")
    row = QARequestProgramme.unscoped.create(qa_request=req, programme=programme,
                                             is_new_programme=programme.status == "proposed",
                                             requested_streams=_streams_for(programme, req.qa_type, streams))
    flow.transition(instance, "add_programme", user, remark=remark or f"Added {programme}",
                    metadata={"programme": programme.pk})
    timeline.record(req.institution, "qa_request.programme_added", subject=req, actor=user, visibility="public",
                    public_text=f"{programme} was added to {req.reference}.")
    _refresh_joint_qa(req)
    _refresh_unpaid_fee(req, user)
    return row


@transaction.atomic
def drop_programme(req, row, *, user, reason):
    req = QARequest.unscoped.select_for_update().get(pk=req.pk)
    instance = request_instance(req)
    if not flow.can(instance, "drop_programme"):
        raise ValidationError("A programme can only be dropped before the SSQ stage — while the request is "
                              f"{_stage_list(instance, 'drop_programme')} (R-25).")
    if row.qa_request_id != req.pk or row.status != QARequestProgramme.ACTIVE:
        raise ValidationError("That programme is not active on this request.")
    if len((reason or "").strip()) < 3:
        raise ValidationError("Say why the programme is dropped.")
    if QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE).count() <= 1:
        raise ValidationError("This is the last programme on the request — cancel the request instead.")
    flow.transition(instance, "drop_programme", user, remark=reason, metadata={"programme": row.programme_id})
    row.status, row.dropped_reason, row.dropped_at = QARequestProgramme.DROPPED, reason.strip(), timezone.now()
    row.save(update_fields=["status", "dropped_reason", "dropped_at", "updated_at"])
    timeline.record(req.institution, "qa_request.programme_dropped", subject=req, actor=user, visibility="public",
                    public_text=f"{row.programme} was dropped from {req.reference}.", internal_text=reason)
    _refresh_joint_qa(req)
    _refresh_unpaid_fee(req, user)
    return row


@transaction.atomic
def replace_programme(req, row, new_programme, *, user, reason):
    req = QARequest.unscoped.select_for_update().get(pk=req.pk)
    instance = request_instance(req)
    if not flow.can(instance, "replace_programme"):
        raise ValidationError("A programme can only be replaced while the request is "
                              f"{_stage_list(instance, 'replace_programme')} (R-25).")
    if row.qa_request_id != req.pk or row.status != QARequestProgramme.ACTIVE:
        raise ValidationError("That programme is not active on this request.")
    if new_programme.institution_id != req.institution_id or new_programme.pk == row.programme_id:
        raise ValidationError("Choose a different programme of your institution.")
    eligible, why = programme_eligibility(new_programme, req.qa_type)
    if not eligible:
        raise ValidationError(f"{new_programme}: {why}")
    if len((reason or "").strip()) < 3:
        raise ValidationError("Say why the programme is replaced.")
    flow.transition(instance, "replace_programme", user, remark=reason,
                    metadata={"from": row.programme_id, "to": new_programme.pk})
    new_row = QARequestProgramme.unscoped.create(qa_request=req, programme=new_programme,
                                                 is_new_programme=new_programme.status == "proposed",
                                                 requested_streams=default_streams(new_programme, req.qa_type))
    row.status, row.replaced_by, row.dropped_reason = QARequestProgramme.REPLACED, new_row, reason.strip()
    row.dropped_at = timezone.now()
    row.save(update_fields=["status", "replaced_by", "dropped_reason", "dropped_at", "updated_at"])
    timeline.record(req.institution, "qa_request.programme_replaced", subject=req, actor=user,
                    visibility="public", public_text=f"{row.programme} was replaced by {new_programme} on "
                                                     f"{req.reference}.")
    _refresh_joint_qa(req)
    return new_row


def _refresh_joint_qa(req):
    """Phase 6: flag the request as a joint QA exercise when a programme is
    mapped to a professional body."""
    from institutions.professional_bodies import refresh_joint_qa

    return refresh_joint_qa(req)


def _stage_list(instance, action):
    from qa_core.models import FlowTransition

    labels = list(FlowTransition.objects.filter(flow=instance.flow_version, action_code=action)
                  .values_list("from_stage__label", flat=True))
    return ", ".join(label.lower() for label in labels) or "never"


@transaction.atomic
def submit(req, *, user, declaration=False):
    req = QARequest.unscoped.select_for_update().get(pk=req.pk)
    if req.status != "draft":
        raise ValidationError("Only a draft request can be submitted.")
    if not declaration:
        raise ValidationError("Confirm the declaration first.")
    rows = req.active_programmes()
    if not rows:
        raise ValidationError("Add at least one programme.")
    for row in rows:
        eligible, why = programme_eligibility_for_submit(row)
        if not eligible:
            raise ValidationError(f"{row.programme}: {why}")
    req.declaration_accepted = True
    req.save(update_fields=["declaration_accepted", "updated_at"])
    _refresh_joint_qa(req)
    instance = request_instance(req)
    flow.transition(instance, "submit", user)
    needs_clearance = any(r.is_new_programme and r.clearance_status != "cleared" for r in rows)
    flow.transition(instance, "route_to_clearance" if needs_clearance else "route_to_fee", user)
    req.refresh_from_db()
    return req


def programme_eligibility_for_submit(row):
    """Eligibility again at submission, ignoring this request's own hold."""
    programme = row.programme
    qa_type = row.qa_request.qa_type
    others = QARequestProgramme.unscoped.filter(programme=programme, status=QARequestProgramme.ACTIVE,
                                                qa_request__status__in=OPEN_STATUSES).exclude(qa_request=row.qa_request)
    if others.exists():
        return False, f"Already in an open request ({others.first().qa_request.reference})."
    ok, why = programme_eligibility(programme, qa_type)
    if not ok and why.startswith("Already in an open request"):
        return True, ""
    return ok, why


@transaction.atomic
def cancel(req, *, user, reason):
    """Cancel a request (institution or a confirmed staff action). Only in the
    stages whose flow has a `cancel` transition (R-25)."""
    req = QARequest.unscoped.select_for_update().get(pk=req.pk)
    instance = request_instance(req)
    if not flow.can(instance, "cancel"):
        raise ValidationError("This request can no longer be cancelled: cancellation is allowed only while it is "
                              f"{_stage_list(instance, 'cancel')} (R-25).")
    if len((reason or "").strip()) < 3:
        raise ValidationError("Say why the request is cancelled.")
    flow.transition(instance, "cancel", user, remark=reason)
    req.refresh_from_db()
    return req


# ---------------------------------------------------------------------------
# Flow effects (registered in accreditation.effects)
# ---------------------------------------------------------------------------
def on_submitted(ctx):
    req = ctx.subject
    req.submitted_at = timezone.now()
    req.submitted_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
    req.save(update_fields=["submitted_at", "submitted_by", "updated_at"])
    _sync_container(req, "submitted")
    count = QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE).count()
    timeline.record(req.institution, "qa_request.submitted", subject=req, actor=ctx.actor, visibility="public",
                    public_text=f"{req.qa_type.name} request {req.reference} submitted ({count} programme(s)).")
    dept = req.institution.handling_department

    def _send():
        users = inst_services.staff_in_department(dept, perm="accreditation.view_qa_case")
        inst_services.notify_role("qa_request.submitted", {
            "reference": req.reference, "institution": req.institution.name, "qa_type": req.qa_type.name,
            "programmes": count}, users, url=f"/qa/requests/{req.pk}/")
    transaction.on_commit(_send)


def on_clearance_opened(ctx):
    req = ctx.subject
    QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE, is_new_programme=True) \
        .exclude(clearance_status="cleared").update(clearance_status="pending", updated_at=timezone.now())
    scope = getattr(ctx.transition.to_stage, "actor_scope", "") or ""
    from staff.models import Department

    dept = Department.objects.filter(code=scope).first()

    def _send():
        users = inst_services.staff_in_department(dept, perm="accreditation.clear_qa_programmes")
        inst_services.notify_role("qa_request.clearance", {"reference": req.reference,
                                                           "institution": req.institution.name},
                                  users, url=f"/qa/requests/{req.pk}/")
    transaction.on_commit(_send)


def on_returned(ctx):
    req = ctx.subject
    QARequestProgramme.unscoped.filter(qa_request=req, clearance_status="pending").update(
        clearance_status="returned", clearance_remark=ctx.remark, updated_at=timezone.now())
    req.declaration_accepted = False
    req.save(update_fields=["declaration_accepted", "updated_at"])
    _sync_container(req, "draft")
    timeline.record(req.institution, "qa_request.returned", subject=req, actor=ctx.actor, visibility="public",
                    public_text=f"NBTE returned {req.reference}: {ctx.remark}")
    transaction.on_commit(lambda: inst_services.notify_institution(
        req.institution, "qa_request.returned", {"reference": req.reference, "remark": ctx.remark},
        url=f"/qa-requests/{req.pk}/"))


def fee_variables(req):
    """Phase 8: the request's programmes with the streams asked for."""
    from payments.services import stream_variables

    rows = QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE) \
        .select_related("programme")
    return stream_variables([(r.programme, r.requested_streams) for r in rows])


def issue_fee(req, actor=None):
    """Issue the QA fee. When the fee is not confirmed yet (or computes to
    zero) the request waits at `fee_due` with a note for Finance — the move
    itself never fails."""
    from payments import services as pay

    schedule = req.qa_type.fee_schedule
    code = schedule.code if schedule is not None else "qa_request_processing"
    count = fee_variables(req)["programmes"]
    try:
        with transaction.atomic():
            pay.issue_obligation(req.institution, code, source=req, actor=actor, variables=fee_variables(req),
                                 description=f"{req.qa_type.name} — {count} programme(s) ({req.reference})")
    except ValidationError as exc:
        req.fee_note = " ".join(exc.messages)[:255]
        req.save(update_fields=["fee_note", "updated_at"])
        timeline.record(req.institution, "qa_request.fee_waiting", subject=req, actor=actor, visibility="public",
                        public_text=f"The fee for {req.reference} will be issued once NBTE confirms the amount.",
                        internal_text=req.fee_note)
        return None
    if req.fee_note:
        req.fee_note = ""
        req.save(update_fields=["fee_note", "updated_at"])
    return True


def on_issue_fee(ctx):
    issue_fee(ctx.subject, ctx.actor)


def retry_fee(req, *, user):
    """Finance / the desk re-tries a fee that could not be issued."""
    if req.status != "fee_due":
        raise ValidationError("The request is not waiting for its fee.")
    if open_obligations(req):
        raise ValidationError("A bill is already open for this request.")
    if issue_fee(req, user) is None:
        raise ValidationError(req.fee_note or "The fee could not be issued.")


def open_obligations(req):
    from payments.models import PaymentObligation

    ct = ContentType.objects.get_for_model(QARequest)
    return list(PaymentObligation.unscoped.filter(source_type=ct, source_id=str(req.pk))
                .exclude(status__in=("cancelled", "expired")).order_by("-created_at"))


def _refresh_unpaid_fee(req, user):
    """After the programme count changes at `fee_due`, an unpaid bill is
    cancelled and re-issued for the new count (cancel and reissue, §10.6)."""
    from payments import services as pay

    if req.status != "fee_due":
        if req.status == "payment_pending_confirmation":
            for ob in open_obligations(req):
                pay.alert(ob, f"The programmes on {req.reference} changed while the payment awaits confirmation. "
                              "Check the amount before confirming; a difference is refunded or credited.")
        return
    for ob in open_obligations(req):
        if ob.status in ("issued", "awaiting_payment") and ob.amount_paid == 0:
            pay.cancel(ob, actor=user, reason=f"Programmes on {req.reference} changed; bill re-issued.")
    if not any(ob.status not in ("cancelled",) for ob in open_obligations(req)):
        issue_fee(req, user)


def on_cancelled(ctx):
    from payments import services as pay

    req = ctx.subject
    now = timezone.now()
    req.cancelled_at, req.cancel_reason = now, ctx.remark
    req.cancelled_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
    req.save(update_fields=["cancelled_at", "cancel_reason", "cancelled_by", "updated_at"])
    if req.institution_request_id:
        InstitutionRequest.unscoped.filter(pk=req.institution_request_id).exclude(status="cancelled").update(
            status="cancelled", cancelled_at=now, cancel_reason=ctx.remark, updated_at=now)
    for ob in open_obligations(req):
        if ob.status in ("issued", "awaiting_payment"):
            pay.cancel(ob, actor=ctx.actor, reason=f"QA request {req.reference} cancelled.")
    timeline.record(req.institution, "qa_request.cancelled", subject=req, actor=ctx.actor, visibility="public",
                    public_text=f"QA request {req.reference} was cancelled.", internal_text=ctx.remark)
    dept = req.institution.handling_department

    def _send():
        context = {"reference": req.reference, "remark": ctx.remark}
        inst_services.notify_institution(req.institution, "qa_request.cancelled", context, url="/qa-requests/")
        inst_services.notify_role("qa_request.cancelled", context,
                                  inst_services.staff_in_department(dept, perm="accreditation.view_qa_case"),
                                  url=f"/qa/requests/{req.pk}/")
    transaction.on_commit(_send)


# ---------------------------------------------------------------------------
# Programme clearance (new programmes)
# ---------------------------------------------------------------------------
def can_clear(req, user):
    if getattr(user, "user_type", "staff") != "staff" or not user.has_perm("accreditation.clear_qa_programmes"):
        return False, "You do not hold accreditation.clear_qa_programmes."
    instance = request_instance(req)
    stage = flow.stage(instance, "programme_clearance")
    scope = getattr(stage, "actor_scope", "") if stage else ""
    if scope and not user.is_superuser and not user.has_perm("qa_core.confirm_any_department") \
            and getattr(user.department, "code", "") != scope:
        return False, "Programme clearance belongs to another department."
    if req.status != "programme_clearance":
        return False, "The request is not waiting for programme clearance."
    return True, ""


@transaction.atomic
def clear_programmes(req, *, user, remark=""):
    ok, why = can_clear(req, user)
    if not ok:
        raise PermissionDenied(why)
    now = timezone.now()
    QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE,
                                       clearance_status="pending").update(
        clearance_status="cleared", cleared_by=user, cleared_at=now, clearance_remark=remark, updated_at=now)
    flow.transition(request_instance(req), "clear_programmes", user, remark=remark)
    timeline.record(req.institution, "qa_request.programmes_cleared", subject=req, actor=user, visibility="public",
                    public_text=f"NBTE cleared the new programmes on {req.reference}.")
    req.refresh_from_db()
    return req


@transaction.atomic
def return_programmes(req, *, user, remark):
    ok, why = can_clear(req, user)
    if not ok:
        raise PermissionDenied(why)
    flow.transition(request_instance(req), "return", user, remark=remark)
    req.refresh_from_db()
    return req


# ---------------------------------------------------------------------------
# Payments → request
# ---------------------------------------------------------------------------
def _req_for(obligation):
    if obligation.source_type_id is None or obligation.source_type.model_class() is not QARequest:
        return None
    return QARequest.unscoped.filter(pk=obligation.source_id).first()


def payment_claimed(obligation):
    req = _req_for(obligation)
    instance = request_instance(req) if req else None
    if instance is not None and flow.can(instance, "payment_claimed"):
        flow.transition(instance, "payment_claimed", None, metadata={"obligation": obligation.reference})


def payment_rejected(obligation):
    req = _req_for(obligation)
    instance = request_instance(req) if req else None
    if instance is not None and flow.can(instance, "payment_rejected"):
        flow.transition(instance, "payment_rejected", None, metadata={"obligation": obligation.reference})


def payment_settled(obligation):
    req = _req_for(obligation)
    instance = request_instance(req) if req else None
    if instance is not None and flow.can(instance, "payment_confirmed"):
        flow.transition(instance, "payment_confirmed", None, metadata={"obligation": obligation.reference})


# ===========================================================================
# SSQs
# ===========================================================================
def on_open_ssqs(ctx):
    """One SSQ (version 1) per active programme, prefilled from the record."""
    req = ctx.subject
    template = req.qa_type.ssq_template
    if template is None:
        raise ValidationError(f"No published SSQ template for {req.qa_type.name}.")
    for row in req.active_programmes():
        if current_ssq(row) is None:
            create_ssq(row, template, actor=ctx.actor)
    _sync_container(req, "in_progress")
    timeline.record(req.institution, "qa_request.ssq_open", subject=req, visibility="public",
                    public_text=f"Fee confirmed for {req.reference}. Complete the self-study questionnaires.")
    transaction.on_commit(lambda: inst_services.notify_institution(
        req.institution, "qa_request.ssq_open", {"reference": req.reference}, url=f"/qa-requests/{req.pk}/"))


def create_ssq(row, template, *, actor=None, version=1, supersedes=None):
    inst = row.qa_request.institution
    data, docs, prefilled = engine.compute_prefill(template, inst, row.programme)
    if supersedes is not None:
        data = dict(supersedes.data)
        prefilled = list((supersedes.meta or {}).get("prefilled", []))
    ssq = SSQ.unscoped.create(qa_request_programme=row, template=template, data=data, status="draft",
                              version=version, supersedes=supersedes,
                              meta={"prefilled": prefilled, "edited": [], "sections": {}})
    if supersedes is not None:
        for ev in Evidence.unscoped.filter(ssq=supersedes).exclude(status="removed"):
            Evidence.unscoped.create(ssq=ssq, field_key=ev.field_key, document_kind=ev.document_kind,
                                     reused_from=ev.reused_from, file=ev.file.name if ev.file else "",
                                     original_name=ev.original_name, size_bytes=ev.size_bytes, sha256=ev.sha256,
                                     uploaded_by=ev.uploaded_by)
    else:
        for key, doc in docs.items():
            Evidence.unscoped.create(ssq=ssq, field_key=key, document_kind=doc.kind, reused_from=doc,
                                     uploaded_by=actor if getattr(actor, "pk", None) else None)
    flow.start(SSQ_FLOW, ssq, actor=actor)
    return ssq


def evidence_counts(ssq):
    counts = {}
    for key in Evidence.unscoped.filter(ssq=ssq).exclude(status="removed").values_list("field_key", flat=True):
        counts[key] = counts.get(key, 0) + 1
    return counts


def submit_roles():
    return _json_list("qa.ssq.submit_roles", ["inst-chief-executive", "inst-admin"])


def editable_sections(ssq, user):
    """The section keys `user` may edit now (empty = read-only)."""
    if getattr(user, "user_type", "") != "institution" or user.institution_id != ssq.institution.pk:
        return []
    if not ssq.is_current:
        return []
    keys = [s["key"] for s in ssq.template.sections()]
    if ssq.status == "returned_to_department":
        if is_department_user(user) and ssq.assigned_department_user_id == user.pk:
            return [k for k in keys if k in (ssq.assigned_sections or [])]
        return []
    if ssq.status in ("draft", "submitted_by_department") and not is_department_user(user):
        return keys
    return []


def _assert_section(ssq, user, section_key):
    if section_key not in editable_sections(ssq, user):
        raise PermissionDenied("You cannot edit this section now.")


def _field(ssq, key):
    field = ssq.template.field_map().get(key)
    if field is None:
        raise ValidationError("Unknown field.")
    return field


def _section_of(ssq, key):
    for section in ssq.template.sections():
        if any(f["key"] == key for f in section.get("fields", [])):
            return section["key"]
    return None


@transaction.atomic
def save_fields(ssq, values, *, user):
    """Autosave: clean and store each posted field (draft rules — `required`
    waits for submission). Returns {field_key: error}; good fields are saved
    even when others fail."""
    ssq = SSQ.unscoped.select_for_update().get(pk=ssq.pk)
    errors = {}
    data = dict(ssq.data or {})
    meta = dict(ssq.meta or {})
    edited = set(meta.get("edited", []))
    prefilled = set(meta.get("prefilled", []))
    programme = ssq.qa_request_programme.programme
    touched_sections = set()
    for key, raw in values.items():
        try:
            field = _field(ssq, key)
        except ValidationError:
            continue
        section = _section_of(ssq, key)
        _assert_section(ssq, user, section)
        if field["type"] == "file":
            continue
        try:
            value = engine.clean_value(field, raw, programme=programme)
        except ValidationError as exc:
            errors[key] = " ".join(exc.messages)
            continue
        if data.get(key) != value:
            data[key] = value
            if key in prefilled:
                edited.add(key)
        touched_sections.add(section)
    now = timezone.now()
    meta["edited"] = sorted(edited)
    sections_meta = dict(meta.get("sections", {}))
    for section in touched_sections:
        sections_meta[section] = {"saved_at": now.isoformat(), "by": user.pk}
    meta["sections"] = sections_meta
    ssq.data, ssq.meta, ssq.last_saved_at, ssq.last_saved_by = data, meta, now, user
    ssq.save(update_fields=["data", "meta", "last_saved_at", "last_saved_by", "updated_at"])
    return errors


@transaction.atomic
def attach_upload(ssq, field_key, upload, *, user):
    from qa_core.files import validate_upload

    field = _field(ssq, field_key)
    if field["type"] != "file":
        raise ValidationError("This field does not take files.")
    _assert_section(ssq, user, _section_of(ssq, field_key))
    max_mb = _int("qa.documents.max_upload_mb", 10)
    kind = None
    exts = ["pdf", "jpg", "jpeg", "png", "docx", "xlsx"]
    if field.get("document_kind"):
        from institutions.models import DocumentKind

        kind = DocumentKind.objects.filter(code=field["document_kind"]).first()
        if kind is not None:
            exts = kind.extensions() or exts
            max_mb = min(max_mb, kind.max_mb or max_mb)
    validate_upload(upload, extensions=exts, max_mb=max_mb, label=field["label"])
    if not field.get("multiple"):
        Evidence.unscoped.filter(ssq=ssq, field_key=field_key).exclude(status="removed").update(
            status="removed", updated_at=timezone.now())
    ev = Evidence(ssq=ssq, field_key=field_key, document_kind=kind, uploaded_by=user)
    ev.file = upload
    ev.save()
    _touch(ssq, user, field_key)
    return ev


@transaction.atomic
def reuse_document(ssq, field_key, document, *, user):
    """Attach a document from the permanent record. Only ACCEPTED documents of
    this institution are ever re-used (never drafts or returned ones)."""
    field = _field(ssq, field_key)
    if field["type"] != "file":
        raise ValidationError("This field does not take files.")
    _assert_section(ssq, user, _section_of(ssq, field_key))
    if document.institution_id != ssq.institution.pk:
        raise PermissionDenied("That document belongs to another institution.")
    if document.status != InstitutionDocument.ACCEPTED:
        raise ValidationError("Only documents NBTE has accepted can be re-used.")
    if field.get("document_kind") and document.kind.code != field["document_kind"]:
        raise ValidationError(f"Choose a {field['document_kind']} document.")
    if not field.get("multiple"):
        Evidence.unscoped.filter(ssq=ssq, field_key=field_key).exclude(status="removed").update(
            status="removed", updated_at=timezone.now())
    ev = Evidence.unscoped.create(ssq=ssq, field_key=field_key, document_kind=document.kind, reused_from=document,
                                  uploaded_by=user)
    _touch(ssq, user, field_key)
    return ev


def reusable_documents(ssq, field):
    qs = InstitutionDocument.unscoped.filter(institution=ssq.institution, status=InstitutionDocument.ACCEPTED)
    if field.get("document_kind"):
        qs = qs.filter(kind__code=field["document_kind"])
    return list(qs.select_related("kind").order_by("kind__name", "-version"))


@transaction.atomic
def remove_evidence(evidence, *, user):
    ssq = evidence.ssq
    _assert_section(ssq, user, _section_of(ssq, evidence.field_key))
    evidence.status = "removed"
    evidence.save(update_fields=["status", "updated_at"])
    _touch(ssq, user, evidence.field_key)


def _touch(ssq, user, field_key):
    now = timezone.now()
    meta = dict(ssq.meta or {})
    sections_meta = dict(meta.get("sections", {}))
    sections_meta[_section_of(ssq, field_key)] = {"saved_at": now.isoformat(), "by": user.pk}
    meta["sections"] = sections_meta
    SSQ.unscoped.filter(pk=ssq.pk).update(meta=meta, last_saved_at=now, last_saved_by=user, updated_at=now)
    ssq.meta, ssq.last_saved_at = meta, now


# ---------------------------------------------------------------------------
# Department return path
# ---------------------------------------------------------------------------
def department_users(inst):
    from accounts.models import User

    return list(User.objects.filter(institution=inst, user_type="institution", is_active=True,
                                    role__code="inst-academic-dept").order_by("first_name"))


@transaction.atomic
def return_to_department(ssq, *, user, department_user, sections, remark):
    if not can_manage_requests(user, ssq.institution):
        raise PermissionDenied("Only the institution administrator sends sections to a department.")
    if department_user is None or department_user.institution_id != ssq.institution.pk \
            or not is_department_user(department_user):
        raise ValidationError("Choose one of your academic-department accounts.")
    allowed = [s["key"] for s in ssq.template.sections() if s.get("department", True)]
    sections = [s for s in sections if s in allowed]
    if not sections:
        raise ValidationError("Choose at least one section the department may complete.")
    ssq.assigned_department_user, ssq.assigned_sections, ssq.department_remark = department_user, sections, remark
    ssq.save(update_fields=["assigned_department_user", "assigned_sections", "department_remark", "updated_at"])
    flow.transition(ssq_instance(ssq), "return_to_department", user, remark=remark)
    titles = [s["title"] for s in ssq.template.sections() if s["key"] in sections]
    programme = str(ssq.qa_request_programme.programme)
    transaction.on_commit(lambda: inst_services.notify_role("ssq.department", {
        "programme": programme, "sections": ", ".join(titles), "remark": remark,
        "sender": user.get_full_name() or user.email}, [department_user],
        url=f"/qa-requests/{ssq.qa_request.pk}/ssq/{ssq.pk}/"))
    ssq.refresh_from_db()
    return ssq


@transaction.atomic
def recall_from_department(ssq, *, user):
    if not can_manage_requests(user, ssq.institution):
        raise PermissionDenied("Only the institution administrator can take the SSQ back.")
    flow.transition(ssq_instance(ssq), "recall", user)
    ssq.refresh_from_db()
    return ssq


@transaction.atomic
def department_submit(ssq, *, user):
    ssq = SSQ.unscoped.select_for_update().get(pk=ssq.pk)
    if not (is_department_user(user) and ssq.assigned_department_user_id == user.pk):
        raise PermissionDenied("Only the assigned department account sends the SSQ back.")
    problems = engine.required_problems(ssq.template, ssq.data or {}, evidence_counts(ssq),
                                        sections=ssq.assigned_sections or [])
    if problems:
        raise ValidationError(f"Complete the required fields first ({len(problems)} left).")
    flow.transition(ssq_instance(ssq), "department_submit", user)
    admins = [u for u in inst_services.institution_users(ssq.institution) if not is_department_user(u)]
    programme = str(ssq.qa_request_programme.programme)
    transaction.on_commit(lambda: inst_services.notify_role("ssq.department_done", {
        "programme": programme, "sender": user.get_full_name() or user.email}, admins,
        url=f"/qa-requests/{ssq.qa_request.pk}/ssq/{ssq.pk}/"))
    ssq.refresh_from_db()
    return ssq


# ---------------------------------------------------------------------------
# Submission (immutable snapshot) and review
# ---------------------------------------------------------------------------
def submission_problems(ssq):
    return engine.required_problems(ssq.template, ssq.data or {}, evidence_counts(ssq))


@transaction.atomic
def submit_ssq(ssq, *, user, declaration=False):
    ssq = SSQ.unscoped.select_for_update().get(pk=ssq.pk)
    if getattr(user, "user_type", "") != "institution" or user.institution_id != ssq.institution.pk:
        raise PermissionDenied("Only the institution submits its SSQ.")
    if _role_code(user) not in submit_roles():
        raise PermissionDenied("Your account cannot submit SSQs to NBTE (qa.ssq.submit_roles).")
    if not ssq.is_current or ssq.status not in ("draft", "submitted_by_department"):
        raise ValidationError("This SSQ cannot be submitted now.")
    if not declaration:
        raise ValidationError("Confirm the declaration first.")
    problems = submission_problems(ssq)
    if problems:
        raise ValidationError(f"Complete the required fields first ({len(problems)} left).")
    flow.transition(ssq_instance(ssq), "submit", user)
    ssq.refresh_from_db()
    return ssq


def on_ssq_submitted(ctx):
    """Take the immutable snapshot, then check whether the whole request is in."""
    ssq = ctx.subject
    now = timezone.now()
    programme = ssq.qa_request_programme.programme
    rendered = engine.render_snapshot(ssq.template, ssq.data or {}, programme=programme)
    evidence = [{"field": ev.field_key, "id": ev.pk, "name": ev.label, "sha256": ev.sha256 or
                 (ev.reused_from.sha256 if ev.reused_from_id else ""),
                 "reused_from": ev.reused_from_id}
                for ev in Evidence.unscoped.filter(ssq=ssq).exclude(status="removed").select_related(
                    "reused_from__kind")]
    version = Submission.unscoped.filter(ssq=ssq).count() + 1
    Submission.unscoped.create(ssq=ssq, version=version, template_version=ssq.template.version,
                               data_snapshot=dict(ssq.data or {}), rendered_snapshot=rendered,
                               evidence_snapshot=evidence, submitted_by=ctx.actor if getattr(ctx.actor, "pk", None)
                               else None, submitted_at=now)
    ssq.submitted_by = ctx.actor if getattr(ctx.actor, "pk", None) else None
    ssq.submitted_at = now
    # The engine mirrors the status after the effects run; the completeness
    # check below must already see this SSQ as submitted.
    ssq.status = "submitted"
    ssq.save(update_fields=["submitted_by", "submitted_at", "status", "updated_at"])
    req = ssq.qa_request
    timeline.record(req.institution, "ssq.submitted", subject=ssq, actor=ctx.actor, visibility="public",
                    public_text=f"SSQ for {programme} submitted to NBTE ({req.reference}, version {ssq.version}).")
    dept = req.institution.handling_department

    def _send():
        inst_services.notify_role("ssq.submitted", {
            "institution": req.institution.name, "programme": str(programme), "version": ssq.version,
            "reference": req.reference}, inst_services.staff_in_department(dept, perm="accreditation.review_ssq"),
            url=f"/qa/requests/ssq/{ssq.pk}/")
    transaction.on_commit(_send)
    advance_request_if_complete(req, ctx.actor)


def advance_request_if_complete(req, actor):
    req = QARequest.unscoped.get(pk=req.pk)
    instance = request_instance(req)
    if instance is None or not flow.can(instance, "submit_ssqs"):
        return False
    rows = req.active_programmes()
    if not rows:
        return False
    for row in rows:
        current = current_ssq(row)
        if current is None or current.status not in ("submitted", "accepted", "analysed"):
            return False
    flow.transition(instance, "submit_ssqs", actor)
    return True


def can_review(ssq, user):
    if getattr(user, "user_type", "staff") != "staff" or not user.has_perm("accreditation.review_ssq"):
        return False, "You do not hold accreditation.review_ssq."
    inst = ssq.institution
    if not (user.is_superuser or user.has_perm("qa_core.view_all_institutions")
            or user.has_perm("accounts.access_all_departments")
            or user.department_id == inst.handling_department_id):
        return False, "This SSQ belongs to another department."
    if ssq.status != "submitted" or not ssq.is_current:
        return False, "The SSQ is not waiting for review."
    return True, ""


@transaction.atomic
def accept_ssq(ssq, *, user, remark=""):
    ssq = SSQ.unscoped.select_for_update().get(pk=ssq.pk)
    ok, why = can_review(ssq, user)
    if not ok:
        raise PermissionDenied(why)
    ssq.reviewed_by, ssq.reviewed_at, ssq.review_remark = user, timezone.now(), remark
    ssq.save(update_fields=["reviewed_by", "reviewed_at", "review_remark", "updated_at"])
    flow.transition(ssq_instance(ssq), "accept", user, remark=remark)
    ssq.refresh_from_db()
    # Phase 5 (§9): the review completes and writes the reviewer's service row.
    from inspection.services import record_ssq_review

    record_ssq_review(ssq, reviewer=user, outcome="accepted")
    return ssq


@transaction.atomic
def return_ssq(ssq, *, user, remark):
    ssq = SSQ.unscoped.select_for_update().get(pk=ssq.pk)
    ok, why = can_review(ssq, user)
    if not ok:
        raise PermissionDenied(why)
    ssq.reviewed_by, ssq.reviewed_at, ssq.review_remark = user, timezone.now(), remark
    ssq.save(update_fields=["reviewed_by", "reviewed_at", "review_remark", "updated_at"])
    flow.transition(ssq_instance(ssq), "return", user, remark=remark)
    ssq.refresh_from_db()
    # Phase 5 (§9): the review completes and writes the reviewer's service row.
    from inspection.services import record_ssq_review

    record_ssq_review(ssq, reviewer=user, outcome="returned")
    return ssq


def on_ssq_accepted(ctx):
    ssq = ctx.subject
    Evidence.unscoped.filter(ssq=ssq, status="attached").update(status="accepted", updated_at=timezone.now())
    req = ssq.qa_request
    programme = str(ssq.qa_request_programme.programme)
    timeline.record(req.institution, "ssq.accepted", subject=ssq, actor=ctx.actor, visibility="public",
                    public_text=f"NBTE accepted the SSQ for {programme}.")
    transaction.on_commit(lambda: inst_services.notify_institution(
        req.institution, "ssq.accepted", {"programme": programme, "reference": req.reference},
        url=f"/qa-requests/{req.pk}/"))


def on_ssq_returned(ctx):
    ssq = ctx.subject
    req = ssq.qa_request
    programme = str(ssq.qa_request_programme.programme)
    timeline.record(req.institution, "ssq.returned", subject=ssq, actor=ctx.actor, visibility="public",
                    public_text=f"NBTE returned the SSQ for {programme}: {ctx.remark}")
    transaction.on_commit(lambda: inst_services.notify_institution(
        req.institution, "ssq.returned", {"programme": programme, "remark": ctx.remark, "reference": req.reference},
        url=f"/qa-requests/{req.pk}/ssq/{ssq.pk}/"))


@transaction.atomic
def revise_ssq(ssq, *, user):
    """A returned SSQ is never edited: a new version supersedes it, carrying
    its answers and evidence forward (Requirement 16)."""
    ssq = SSQ.unscoped.select_for_update().get(pk=ssq.pk)
    if getattr(user, "user_type", "") != "institution" or user.institution_id != ssq.institution.pk \
            or is_department_user(user):
        raise PermissionDenied("Only the institution administrator can start a revision.")
    if ssq.status != "returned" or not ssq.is_current:
        raise ValidationError("Only a returned SSQ can be revised.")
    SSQ.unscoped.filter(pk=ssq.pk).update(is_current=False, updated_at=timezone.now())
    template = ssq.template
    new = create_ssq(ssq.qa_request_programme, template, actor=user, version=ssq.version + 1, supersedes=ssq)
    timeline.record(ssq.institution, "ssq.revised", subject=new, actor=user, visibility="public",
                    public_text=f"Version {new.version} of the SSQ for {ssq.qa_request_programme.programme} "
                                "started (revision after NBTE's return).")
    return new


# ---------------------------------------------------------------------------
# QA case (Phase 3: created empty)
# ---------------------------------------------------------------------------
def on_spawn_case(ctx):
    req = ctx.subject
    if QACase.unscoped.filter(qa_request=req).exists():
        return
    dept = req.institution.handling_department
    code = getattr(dept, "code", "") or "qa"
    count = QARequestProgramme.unscoped.filter(qa_request=req, status=QARequestProgramme.ACTIVE).count()
    case = QACase.unscoped.create(reference=references.issue("case", dept=code.upper()), qa_request=req,
                                  institution=req.institution, owning_department=dept, programme_count=count)
    _sync_container(req, "in_progress")
    timeline.record(req.institution, "qa_case.created", subject=case, actor=ctx.actor, visibility="internal",
                    internal_text=f"QA case {case.reference} created for {req.reference} ({count} programme(s)).")
    # Phase 4: the SOP-03 flow starts at once; its first task goes to the
    # owning department's Director.
    from . import case_services

    case_services.start_case(case, actor=ctx.actor)
    # Phase 6: tell each mapped professional body (joint QA exercise).
    from institutions.professional_bodies import notify_bodies_for_case

    notify_bodies_for_case(case, actor=ctx.actor)


# ===========================================================================
# Queries
# ===========================================================================
def query_subject_label(subject):
    if isinstance(subject, SSQ):
        return f"SSQ — {subject.qa_request_programme.programme} (v{subject.version})"
    if isinstance(subject, Evidence):
        return f"Evidence — {subject.label}"
    if isinstance(subject, QARequest):
        return f"QA request {subject.reference}"
    return str(subject)


def _institution_of(subject):
    if isinstance(subject, (SSQ, Evidence)):
        return subject.institution
    return subject.institution


def _request_of(subject):
    if isinstance(subject, SSQ):
        return subject.qa_request
    if isinstance(subject, Evidence):
        return subject.ssq.qa_request
    if isinstance(subject, QARequest):
        return subject
    return None


@transaction.atomic
def raise_query(subject, *, user, title, body, due_days=None):
    if getattr(user, "user_type", "staff") != "staff" or not user.has_perm("accreditation.raise_query"):
        raise PermissionDenied("You do not hold accreditation.raise_query.")
    inst = _institution_of(subject)
    if not (user.is_superuser or user.has_perm("qa_core.view_all_institutions")
            or user.has_perm("accounts.access_all_departments") or user.department_id == inst.handling_department_id):
        raise PermissionDenied("This institution is not in your scope.")
    title, body = (title or "").strip(), (body or "").strip()
    if len(title) < 3 or len(body) < 5:
        raise ValidationError("Give the query a title and say what you need.")
    days = due_days if due_days not in (None, "") else _int("qa.query.default_due_days", 14)
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise ValidationError("The number of days must be a whole number.") from None
    if days < 1 or days > 120:
        raise ValidationError("Allow between 1 and 120 days.")
    query = Query.unscoped.create(
        reference=references.issue("query"), institution=inst, qa_request=_request_of(subject),
        content_type=ContentType.objects.get_for_model(subject, for_concrete_model=True), object_id=str(subject.pk),
        subject_label=query_subject_label(subject)[:255], title=title[:200], body=body, raised_by=user,
        due_at=timezone.now() + datetime.timedelta(days=days))
    timeline.record(inst, "query.raised", subject=query, actor=user, visibility="public",
                    public_text=f"NBTE raised query {query.reference}: {query.title}")
    due = timezone.localtime(query.due_at).strftime("%d %b %Y")
    transaction.on_commit(lambda: inst_services.notify_institution(
        inst, "query.raised", {"query": query.reference, "title": query.title, "body": query.body, "due": due,
                               "subject": query.subject_label}, url=f"/queries/{query.pk}/"))
    return query


@transaction.atomic
def respond(query, *, user, body, upload=None):
    query = Query.unscoped.select_for_update().get(pk=query.pk)
    if getattr(user, "user_type", "") != "institution" or user.institution_id != query.institution_id:
        raise PermissionDenied("Only the institution responds to its queries.")
    if query.status == Query.CLOSED:
        raise ValidationError("This query is closed.")
    body = (body or "").strip()
    if len(body) < 3:
        raise ValidationError("Write your response.")
    response = Response(query=query, body=body, responded_by=user)
    if upload is not None:
        from qa_core.files import validate_upload

        validate_upload(upload, extensions=["pdf", "jpg", "jpeg", "png", "docx", "xlsx"],
                        max_mb=_int("qa.documents.max_upload_mb", 10), label="attachment")
        response.file = upload
    response.save()
    query.status = Query.ANSWERED
    query.save(update_fields=["status", "updated_at"])
    timeline.record(query.institution, "query.answered", subject=query, actor=user, visibility="public",
                    public_text=f"Response sent to NBTE for query {query.reference}.")
    raiser = query.raised_by
    transaction.on_commit(lambda: inst_services.notify_role("query.answered", {
        "institution": query.institution.name, "query": query.reference, "body": body[:300]}, [raiser],
        url=f"/qa/requests/queries/{query.pk}/"))
    return response


@transaction.atomic
def close_query(query, *, user, remark=""):
    if getattr(user, "user_type", "staff") != "staff" or not user.has_perm("accreditation.raise_query"):
        raise PermissionDenied("You do not hold accreditation.raise_query.")
    if query.status == Query.CLOSED:
        raise ValidationError("Already closed.")
    query.status, query.closed_by, query.closed_at, query.close_remark = Query.CLOSED, user, timezone.now(), remark
    query.save(update_fields=["status", "closed_by", "closed_at", "close_remark", "updated_at"])
    timeline.record(query.institution, "query.closed", subject=query, actor=user, visibility="public",
                    public_text=f"NBTE closed query {query.reference}.")
    return query


def query_subject(query):
    model = query.content_type.model_class() if query.content_type_id else None
    if model is None:
        return None
    manager = getattr(model, "unscoped", None) or model._base_manager
    return manager.filter(pk=query.object_id).first()


# ===========================================================================
# Staff-side work lists (internal and the Staff Portal Dashboard)
# ===========================================================================
def staff_work(user, *, limit=50):
    """QA requests and SSQs this staff member can act on now."""
    if getattr(user, "user_type", "staff") != "staff":
        return {"requests": [], "ssqs": [], "queries": []}
    reqs = QARequest.objects.for_user(user).select_related("institution", "qa_type")
    items = []
    if user.has_perm("accreditation.clear_qa_programmes"):
        for req in reqs.filter(status="programme_clearance")[:limit]:
            if can_clear(req, user)[0]:
                items.append({"request": req, "action": "Clear new programmes"})
    if user.has_perm("accreditation.view_qa_case"):
        for req in reqs.filter(status="fee_due", fee_note__gt="")[:limit]:
            items.append({"request": req, "action": "Fee not issued yet"})
    ssqs = []
    if user.has_perm("accreditation.review_ssq"):
        ssqs = list(SSQ.objects.for_user(user).filter(status="submitted", is_current=True)
                    .select_related("qa_request_programme__qa_request__institution",
                                    "qa_request_programme__programme")[:limit])
    queries = []
    if user.has_perm("accreditation.raise_query"):
        queries = list(Query.objects.for_user(user).filter(status=Query.ANSWERED, raised_by=user)
                       .select_related("institution")[:limit])
    return {"requests": items, "ssqs": ssqs, "queries": queries}


def fee_estimate(inst, qa_type, pairs):
    """Live fee estimate for a QA request being prepared (Phase 8):
    ``pairs`` = [(programme, streams)]."""
    from payments.services import fee_estimate as estimate

    schedule = qa_type.fee_schedule if qa_type is not None else None
    return estimate(inst, schedule.code if schedule is not None else "qa_request_processing", pairs)
