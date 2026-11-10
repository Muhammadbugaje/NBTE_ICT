"""v2.7 — controlled changes to a live SOP-03 case.

The visit dates are fixed once the ES approves them, and stages normally only
move forward. Two exceptions are allowed, both through one approval chain
(handling-department Director → ES):

* **visit dates** — the desk officer (or the Director) asks, the Director and
  then the ES approve, and only then may the desk officer apply the new dates.
  A request submitted by the handling Director records that step at submission
  and goes directly to the ES.
* **rollback** — move the case back to an earlier stage. The selected stage
  and every later stage are reopened and have to be completed again.

The request row is the audit record: who asked, who approved, and why. Nothing
is applied before the ES approves.
"""
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from qa_core import flow

from . import case_services as cs
from .models import CaseChangeRequest, CostSheet, PersonnelList, QACase, VisitDetail


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def requests_for(case):
    return list(CaseChangeRequest.unscoped.filter(case=case).select_related(
        "requested_by", "director_approved_by", "es_approved_by", "applied_by"))


def open_request(case, kind=None):
    qs = CaseChangeRequest.unscoped.filter(case=case, status__in=CaseChangeRequest.OPEN)
    if kind:
        qs = qs.filter(kind=kind)
    return qs.order_by("-requested_at").first()


def _stage_rows(case):
    inst = cs.instance_of(case)
    if inst is None:
        return []
    return list(inst.flow_version.stages.exclude(kind__in=("start", "end", "parallel_gate", "join"))
                .order_by("sort_order", "id"))


def earlier_stages(case):
    """Earlier action stages the case may be moved back to."""
    inst = cs.instance_of(case)
    if inst is None:
        return []
    rows = _stage_rows(case)
    current = {s.code: s for s in rows if s.code in inst.current_stages}
    if not current:
        return []
    low = min(s.sort_order for s in current.values())
    return [s for s in rows if s.sort_order < low and s.kind == "action"]


def stage_by_code(case, code):
    return next((s for s in _stage_rows(case) if s.code == code), None)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def is_desk_officer(case, user):
    return bool(user and case.desk_officer_id == user.pk)


def is_handling_director(case, user):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    import org

    if user.is_superuser or org.is_es(user):
        return True
    dept = case.owning_department or getattr(case.institution, "handling_department", None)
    return bool(dept is not None and org.is_director(user, dept))


def is_owning_department_director(case, user):
    if user is None or not getattr(user, "is_authenticated", False) or user.is_superuser:
        return False
    import org

    dept = case.owning_department or getattr(case.institution, "handling_department", None)
    return bool(dept is not None and org.is_director(user, dept))


def _director_step_for_request(case, user):
    """Record the Director step when the handling Director is the requester."""
    if not is_owning_department_director(case, user):
        return {}
    return {
        "status": "pending_es",
        "director_approved_by": user,
        "director_approved_at": timezone.now(),
        "director_remark": "Recorded at submission by the handling Director.",
    }


def _notify_request_owner(req, *, actor):
    import org

    from notifications.models import Notification
    from notifications.services import notify_many

    department = req.case.owning_department or getattr(req.case.institution, "handling_department", None)
    director = org.director_of(department) if department else None
    directors = [director] if director else []
    executive_secretaries = list(org.users_with_role(org.ES_ROLE))
    participants = [req.requested_by, req.director_approved_by, req.es_approved_by, req.case.desk_officer]
    participants.extend(directors)
    participants.extend(executive_secretaries)

    if req.status == "pending_director":
        recipients = executive_secretaries
        title = f"New change request: {req.case.reference}"
        body = f"{req.get_kind_display()} is with the handling department Director; you will be asked to approve it next."
    elif req.status == "pending_es":
        recipients = [req.requested_by, req.director_approved_by]
        title = f"Change request sent for ES approval: {req.case.reference}"
        body = f"The Director has approved {req.get_kind_display()}; it is now with the Executive Secretary."
    elif req.status == "approved":
        recipients = [recipient for recipient in participants
                      if recipient is None or recipient.pk != req.case.desk_officer_id]
        title = f"Approved change ready to apply: {req.case.reference}"
        body = f"The Executive Secretary approved {req.get_kind_display()}. The assigned desk officer must apply it."
    elif req.status in {"applied", "rejected", "returned", "cancelled"}:
        recipients = ([recipient for recipient in participants
                      if recipient is None or recipient.pk != req.requested_by_id]
                     if req.status == "returned" else participants)
        if req.status == "applied":
            title = f"Change completed: {req.case.reference}"
            body = f"The approved {req.get_kind_display().lower()} has been applied."
        elif req.status == "returned":
            title = f"Change request returned: {req.case.reference}"
            body = f"The {req.get_kind_display().lower()} request was returned for revision."
        elif req.status == "cancelled":
            title = f"Change request cancelled: {req.case.reference}"
            body = f"The {req.get_kind_display().lower()} request was cancelled."
        else:
            title = f"Change request declined: {req.case.reference}"
            body = f"The {req.get_kind_display().lower()} request was declined."
    else:
        return

    recipients = list({recipient.pk: recipient for recipient in recipients if recipient is not None}.values())
    tab = "visit" if req.kind == CaseChangeRequest.VISIT_DATE else "overview"
    url = f"/qa/cases/{req.case_id}/?tab={tab}#change-request-{req.pk}"
    notification_data = {
        "title": title,
        "body": body,
        "url": url,
        "category": Notification.REQUEST,
        "level": (Notification.SUCCESS if req.status == "applied" else
                  Notification.ERROR if req.status == "rejected" else
                  Notification.WARNING if req.status in {"pending_director", "pending_es", "returned"}
                  else Notification.INFO),
        "actor": actor,
        "metadata": {"case_change_request_id": req.pk, "status": req.status},
    }
    transaction.on_commit(lambda: notify_many(recipients, **notification_data))


def _sync_request_task(req, *, actor):
    """Keep the request's current approval/apply step in My Tasks."""
    from django.contrib.contenttypes.models import ContentType

    import org
    from qa_core.models import QATaskLink
    from qa_core.task_bridge import close_for_stage, system_user
    from tasks import services as task_services
    from tasks.models import Task, TaskAssignee, TaskCategory

    case = req.case
    department = case.owning_department or getattr(case.institution, "handling_department", None)
    if req.status == "pending_director":
        director = org.director_of(department) if department else None
        assignees = [director] if director else []
        step = "director"
        action = "Review and approve or return this request from the QA case workspace."
    elif req.status == "pending_es":
        assignees = list(org.users_with_role(org.ES_ROLE))
        step = "executive_secretary"
        action = "Review and approve or return this request from the QA case workspace."
    elif req.status == "approved":
        assignees = [case.desk_officer] if case.desk_officer_id else []
        step = "apply"
        action = "Apply the approved change from the QA case workspace."
    elif req.status == "returned":
        assignees = [req.requested_by] if req.requested_by_id else []
        step = "revise"
        action = "Review the return remark and submit a revised request from the QA case workspace."
    else:
        assignees = []
        step = ""
        action = ""

    if req.status == "pending_director" and not assignees:
        raise ValidationError("Assign an active handling department Director before submitting this request.")
    if req.status == "pending_es" and not assignees:
        raise ValidationError("Assign an active Executive Secretary before submitting this request.")
    if req.status == "approved" and not assignees:
        raise ValidationError("Assign a desk officer before approving this change; they must apply it.")

    prefix = f"case_change_{req.pk}_"
    ct = ContentType.objects.get_for_model(QACase, for_concrete_model=True)
    open_links = QATaskLink.objects.filter(content_type=ct, object_id=case.pk, stage_code__startswith=prefix,
                                           kind="assignment", closed_at__isnull=True)
    for link in open_links:
        close_for_stage(case, link.stage_code, reason=req.status, actor=actor)

    if not assignees:
        return None

    instance = cs.instance_of(case)
    if instance is None:
        raise ValidationError("This case has no active workflow to attach the request task to.")

    system = system_user()
    assignees = list({assignee.pk: assignee for assignee in assignees}.values())
    requester = (req.requested_by.get_full_name() or req.requested_by.email) if req.requested_by else "staff"
    title = f"[QA] {req.get_kind_display()} — {case.reference} — {step.replace('_', ' ').title()}"
    task = task_services.create_task(
        created_by=system,
        title=title[:200],
        description=(
            f"{req.get_kind_display()} requested by {requester}.\n\n"
            f"Reason: {req.reason}\n\n{action}"
        ),
        category=TaskCategory.objects.filter(code="qa-sop03").first(),
        owner_department=department,
        priority=Task.HIGH,
        due_date=timezone.localdate() + datetime.timedelta(days=3),
        requires_review=False,
    )
    for assignee in assignees:
        TaskAssignee.objects.create(
            task=task,
            user=assignee,
            state=TaskAssignee.ASSIGNED,
            assigned_by=system,
        )
    QATaskLink.objects.create(
        task=task,
        content_type=ct,
        object_id=case.pk,
        flow_instance=instance,
        stage_code=f"{prefix}{step}",
        kind="assignment",
        due_at=timezone.now() + datetime.timedelta(days=3),
    )
    task_services.open_task(task, system)
    return task


def _close_returned_tasks(case, kind, *, actor):
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import QATaskLink
    from qa_core.task_bridge import close_for_stage

    ct = ContentType.objects.get_for_model(QACase, for_concrete_model=True)
    returned = CaseChangeRequest.unscoped.filter(case=case, kind=kind, status="returned").values_list("pk", flat=True)
    for request_id in returned:
        prefix = f"case_change_{request_id}_"
        stages = QATaskLink.objects.filter(
            content_type=ct, object_id=case.pk, stage_code__startswith=prefix,
            kind="assignment", closed_at__isnull=True,
        ).values_list("stage_code", flat=True)
        for stage_code in stages:
            close_for_stage(case, stage_code, reason="resubmitted", actor=actor)


def is_es(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    import org

    return bool(user.is_superuser or org.is_es(user))


def can_request(case, user):
    if user is None or not getattr(user, "is_authenticated", False):
        return False, "Sign in to request a change."
    if getattr(getattr(user, "role", None), "code", "") == "ict-qa-monitor":
        return False, "The ICT monitor has read-only access (R-13)."
    if user.is_superuser or is_desk_officer(case, user) or is_handling_director(case, user):
        return True, ""
    return False, "Only the desk officer or the handling department Director can request this change."


def can_director_approve(case, user):
    is_superuser = bool(user and getattr(user, "is_superuser", False))
    return (is_owning_department_director(case, user) or is_superuser,
            "Only the handling department Director approves this step.")


def can_es_approve(user):
    return is_es(user), "Only the Executive Secretary approves this step."


def can_apply(case, req, user):
    if req.status != "approved":
        return False, "This change has not been approved by the ES yet."
    if req.kind == CaseChangeRequest.VISIT_DATE:
        if user.is_superuser or is_desk_officer(case, user):
            return True, ""
        return False, "Only the desk officer applies an approved date change."
    return is_desk_officer(case, user) or user.is_superuser, "Only the desk officer applies a rollback."


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------
@transaction.atomic
def request_visit_date(case, *, user, start, end, reason):
    ok, why = can_request(case, user)
    if not ok:
        raise PermissionDenied(why)
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValidationError("Say why the dates must change (at least 10 characters).")
    if not start or not end:
        raise ValidationError("Choose the new first and last day.")
    if end < start:
        raise ValidationError("The visit cannot end before it starts.")
    if start < timezone.localdate():
        raise ValidationError("The new visit cannot start in the past.")
    if open_request(case, CaseChangeRequest.VISIT_DATE):
        raise ValidationError("A date-change request is already open for this case.")
    _close_returned_tasks(case, CaseChangeRequest.VISIT_DATE, actor=user)
    approval = _director_step_for_request(case, user)
    req = CaseChangeRequest.unscoped.create(
        case=case, kind=CaseChangeRequest.VISIT_DATE, requested_by=user, reason=reason,
        proposed_start=start, proposed_end=end, **approval)
    _sync_request_task(req, actor=user)
    _notify_request_owner(req, actor=user)
    return req


@transaction.atomic
def request_rollback(case, *, user, target_stage, reason):
    ok, why = can_request(case, user)
    if not ok:
        raise PermissionDenied(why)
    case = QACase.unscoped.select_for_update().get(pk=case.pk)
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValidationError("Say why the case must move back (at least 10 characters).")
    stage = stage_by_code(case, target_stage)
    if stage is None:
        raise ValidationError("Choose a stage from this case's workflow.")
    allowed = {s.code for s in earlier_stages(case)}
    if stage.code not in allowed:
        raise ValidationError("You can only move back to a stage before the case's current step.")
    if open_request(case, CaseChangeRequest.ROLLBACK):
        raise ValidationError("A rollback request is already open for this case.")
    _close_returned_tasks(case, CaseChangeRequest.ROLLBACK, actor=user)
    approval = _director_step_for_request(case, user)
    req = CaseChangeRequest.unscoped.create(
        case=case, kind=CaseChangeRequest.ROLLBACK, requested_by=user, reason=reason,
        target_stage=stage.code, **approval)
    _sync_request_task(req, actor=user)
    _notify_request_owner(req, actor=user)
    return req


@transaction.atomic
def cancel(req, *, user):
    if not req.is_open:
        raise ValidationError("This request is already closed.")
    if not (user.is_superuser or req.requested_by_id == user.pk or is_handling_director(req.case, user)):
        raise PermissionDenied("Only the person who asked, or the Director, can cancel it.")
    req.status, req.applied_at = "cancelled", None
    req.save(update_fields=["status", "applied_at", "updated_at"])
    _sync_request_task(req, actor=user)
    _notify_request_owner(req, actor=user)
    return req


# ---------------------------------------------------------------------------
# Approving
# ---------------------------------------------------------------------------
@transaction.atomic
def decide(req, *, user, decision, remark=""):
    """Director first (unless they requested it), then ES."""
    req = CaseChangeRequest.unscoped.select_for_update().get(pk=req.pk)
    case = req.case
    now = timezone.now()
    if decision not in {"approve", "reject", "return"}:
        raise ValidationError("Choose approve, reject, or return.")
    if decision == "return" and not (remark or "").strip():
        raise ValidationError("Add a remark explaining what must be revised before returning this request.")
    approve = decision == "approve"
    status = "returned" if decision == "return" else "rejected"
    if req.status == "pending_director":
        ok, why = can_director_approve(case, user)
        if not ok:
            raise PermissionDenied(why)
        req.director_approved_by = user if approve else None
        req.director_approved_at = now if approve else None
        req.director_remark = (remark or "").strip()
        req.status = "pending_es" if approve else status
    elif req.status == "pending_es":
        ok, why = can_es_approve(user)
        if not ok:
            raise PermissionDenied(why)
        if approve and not case.desk_officer_id:
            raise ValidationError("Assign a desk officer before approving this change; they must apply it.")
        req.es_approved_by = user if approve else None
        req.es_approved_at = now if approve else None
        req.es_remark = (remark or "").strip()
        req.status = "approved" if approve else status
    else:
        raise ValidationError("This request is no longer waiting for an approval.")
    req.save()
    _sync_request_task(req, actor=user)
    _notify_request_owner(req, actor=user)
    return req


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
@transaction.atomic
def apply(req, *, user):
    req = CaseChangeRequest.unscoped.select_for_update().get(pk=req.pk)
    case = QACase.unscoped.select_for_update().get(pk=req.case_id)
    req.case = case
    ok, why = can_apply(case, req, user)
    if not ok:
        raise PermissionDenied(why)
    if req.kind == CaseChangeRequest.VISIT_DATE:
        _apply_dates(req, user)
    elif req.kind == CaseChangeRequest.ROLLBACK:
        if req.target_stage not in {stage.code for stage in earlier_stages(case)}:
            raise ValidationError("The case has moved; this rollback target is no longer earlier than its current step.")
        _apply_rollback(req, user)
    req.status, req.applied_by, req.applied_at = "applied", user, timezone.now()
    req.save(update_fields=["status", "applied_by", "applied_at", "updated_at"])
    _sync_request_task(req, actor=user)
    _notify_request_owner(req, actor=user)
    return req


def _apply_dates(req, user):
    case = req.case
    visit, _ = VisitDetail.unscoped.get_or_create(case=case)
    old = (visit.planned_start, visit.planned_end)
    visit.planned_start, visit.planned_end = req.proposed_start, req.proposed_end
    # The ES already approved the visit itself; a date change keeps that
    # approval but the dates must be re-confirmed by the Director/ES chain
    # only if the whole visit moves (which this request is the record of).
    visit.save(update_fields=["planned_start", "planned_end", "status", "updated_at"])
    _sync_visit_dates(case, req.proposed_start, req.proposed_end)
    from qa_core import timeline

    timeline.record(case.institution, event_code="qa.case.visit_dates_changed", subject=case, actor=user,
                    internal_text=(f"Visit dates changed from {old[0]}–{old[1]} to "
                                   f"{req.proposed_start}–{req.proposed_end}: {req.reason}"))


def _sync_visit_dates(case, start, end):
    """Keep the inspection and the issued invitations on the same dates."""
    from inspection.models import Inspection

    Inspection.unscoped.filter(case=case).exclude(status="closed").update(
        scheduled_start=start, scheduled_end=end, updated_at=timezone.now())
    try:
        from .visit_invitation_models import VisitInvitation

        VisitInvitation.unscoped.filter(case=case).exclude(status=VisitInvitation.WITHDRAWN).update(
            visit_start=start, visit_end=end, updated_at=timezone.now())
    except Exception:  # invitations are best-effort here
        import logging

        logging.getLogger(__name__).exception("Could not sync invitation dates for %s", case.reference)


def _apply_rollback(req, user):
    case = req.case
    inst = cs.instance_of(case)
    stage = stage_by_code(case, req.target_stage)
    if inst is None or stage is None:
        raise ValidationError("This case can no longer be moved to that stage.")
    _reset_after(case, stage, req=req, actor=user)
    flow.force_stage(inst, stage.code, user, remark=req.reason,
                     metadata={"change_request": req.pk, "approved_by": getattr(req.es_approved_by, "pk", None)})
    from qa_core import timeline

    timeline.record(case.institution, event_code="qa.case.stage_rolled_back", subject=case, actor=user,
                    internal_text=f"Moved back to {stage.label} (request #{req.pk}): {req.reason}")


def _reset_after(case, target, *, req, actor):
    """Reset work at `target` and later stages so it must be completed again."""
    order = {s.code: s.sort_order for s in _stage_rows(case)}
    cut = order.get(target.code, 0)

    def affected(code):
        return order.get(code, -1) >= cut

    if any(affected(code) for code in ("generate_cost", "audit_personnel_cost", "es_approve_cost")):
        CostSheet.unscoped.filter(case=case, is_current=True).update(status="draft", updated_at=timezone.now())
    if any(affected(code) for code in ("add_personnel", "confirm_personnel")):
        plist = PersonnelList.unscoped.filter(case=case).first()
        if plist is not None:
            plist.status = "draft"
            plist.save(update_fields=["status", "updated_at"])
            plist.entries.update(status="proposed", updated_at=timezone.now())
    if any(affected(code) for code in ("add_visit_details", "confirm_visit_details", "es_approve_visit")):
        visit = VisitDetail.unscoped.filter(case=case).first()
        if visit is not None:
            visit.status = "draft"
            visit.approved_by = None
            visit.approved_at = None
            visit.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    if affected("request_fund_release"):
        from .models import FundRelease

        FundRelease.unscoped.filter(case=case).exclude(status="returned").update(
            status="returned", remark="Rolled back — the fund release must be requested again.")
        if target.code == "request_fund_release":
            sheet = CostSheet.unscoped.filter(case=case, is_current=True).first()
            if sheet is not None:
                FundRelease.unscoped.create(case=case, cost_sheet=sheet, amount=sheet.total, status="es_approved")

    if affected("post_visit_report"):
        _reset_post_visit_work(case, req=req, actor=actor)
    elif affected("post_visit_recommendation"):
        from inspection.models import PostVisitReport

        PostVisitReport.unscoped.filter(inspection__case=case).update(
            recommendation_note="", recommended_by=None, updated_at=timezone.now())

    from .models import CapacityAssessment, Decision, QARequestProgramme

    decision_stages = {
        Decision.CQAC: "cqac_recommendation",
        Decision.CHAIR: "cqac_chair_approval",
        Decision.ES: "es_final_approval",
    }
    invalid_decisions = [stage for stage, flow_code in decision_stages.items() if affected(flow_code)]
    if invalid_decisions:
        Decision.unscoped.filter(case=case, stage__in=invalid_decisions, applied_at__isnull=True).delete()
    if affected("cqac_recommendation"):
        CapacityAssessment.unscoped.filter(
            qa_request_programme__in=QARequestProgramme.unscoped.filter(qa_request=case.qa_request),
            is_current=True,
        ).update(is_current=False, updated_at=timezone.now())


def _reset_post_visit_work(case, *, req, actor):
    """Reopen report work without deleting its endorsement or flow history."""
    from inspection import services as inspection_services
    from inspection.models import Inspection, PostVisitMemberReport, PostVisitReport, PostVisitPersonnel

    from .models import VisitReportForm

    inspections = list(Inspection.unscoped.filter(case=case).order_by("pk"))
    reports = list(PostVisitReport.unscoped.filter(inspection__in=inspections).select_related("inspection"))
    report_ids = [report.pk for report in reports]
    for report in reports:
        instance = flow.instance_for(report, "post_visit_report")
        if instance is None:
            flow.start("post_visit_report", report, actor=actor)
        elif report.status != "draft":
            flow.force_stage(instance, "draft", actor,
                             remark=f"Case rollback request #{req.pk}: {req.reason}",
                             metadata={"change_request": req.pk})
    VisitReportForm.unscoped.filter(inspection__in=inspections).update(
        data={}, score={}, computed_outcome="", status="draft", rendered_snapshot=[],
        submitted_by=None, submitted_at=None, updated_at=timezone.now())
    PostVisitReport.unscoped.filter(pk__in=report_ids).update(
        status="draft", summary="", findings={}, recommendation="", recommendation_note="", recommended_by=None,
        submitted_by=None, submitted_at=None, updated_at=timezone.now())
    PostVisitMemberReport.unscoped.filter(report_id__in=report_ids).update(
        status="draft", summary="", findings={}, recommendation="", submitted_at=None,
        reviewed_by=None, reviewed_at=None, review_note="", updated_at=timezone.now())

    if not affected_visit_stage(case, req.target_stage):
        return

    for inspection in inspections:
        if inspection.status == "closed":
            for person in PostVisitPersonnel.unscoped.filter(inspection=inspection, attended=True):
                inspection_services.void_service(person, reason=f"Case rollback request #{req.pk}")
        instance = flow.instance_for(inspection, "inspection")
        if instance is None:
            flow.start("inspection", inspection, actor=actor)
        elif inspection.status != "scheduled":
            flow.force_stage(instance, "scheduled", actor,
                             remark=f"Case rollback request #{req.pk}: visit must be recorded again",
                             metadata={"change_request": req.pk})
        Inspection.unscoped.filter(pk=inspection.pk).update(
            status="scheduled", actual_start=None, actual_end=None, completed_by=None, closed_at=None,
            updated_at=timezone.now())
        PostVisitPersonnel.unscoped.filter(inspection=inspection).delete()


def affected_visit_stage(case, target_stage):
    """Whether rolling back to this stage requires the visit to be recorded again."""
    order = {s.code: s.sort_order for s in _stage_rows(case)}
    return order.get(target_stage, 10**6) <= order.get("es_approve_visit", -1)
