"""Strict SSQ assignment (v2.1 Part 2.2; X1/X4, X27, R3).

    can_assign(user, ssq)          director of the involved (handling) department, a delegate
                                   (assignments.assign_work granted by the director), ES or super admin
    suggestion(ssq)                the rotation suggestion from that department's ssq_review pool
    candidates(ssq)                the department's active staff, pool order first (others after)
    assign(ssq, by=, officer=, accept_suggestion=, reason=)
                                   one officer per SSQ, as a WorkAssignment (offered) on the SSQ;
                                   cap qa.rotation.ssq_review.cap open reviews per officer (default 3)
    return_work(wa, officer, reason)
                                   the officer hands it back (before or after accepting); the director is told
    send_reminders(now=None)       offers not accepted after qa.ssq.assignment_reminder_hours, and
                                   reviews past their due date; at most one reminder a day per assignment

Unassigned staff never see the SSQ (assignments.access: the assignment opens it,
only once accepted). Every check runs on the server; the website and the API
call these functions.
"""
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

KIND = "ssq_review"


def _int(key, default):
    import config
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def department_of(ssq):
    return ssq.qa_request_programme.qa_request.institution.handling_department


def can_assign(user, ssq):
    from assignments import work
    dept = department_of(ssq)
    return bool(getattr(user, "is_authenticated", False) and user.is_active and dept is not None
                and work.can_assign(user, dept))


def cap():
    from assignments.rotation import settings_for
    value = settings_for(KIND).cap
    return value if value is not None else _int("qa.rotation.ssq_review.cap", 3)


def open_reviews(user):
    from assignments.models import WorkAssignment
    return WorkAssignment.objects.filter(assignee=user, kind=KIND, status__in=WorkAssignment.OPEN).count()


def current_assignment(ssq):
    from assignments.models import WorkAssignment
    return WorkAssignment.objects.for_subject(ssq).filter(kind=KIND, status__in=WorkAssignment.OPEN) \
        .select_related("assignee").order_by("-assigned_at").first()


def suggestion(ssq, *, record=False):
    from assignments import rotation
    from assignments.models_rotation import RotationPool
    inst = ssq.qa_request_programme.qa_request.institution
    try:
        return rotation.suggest(KIND, subject=ssq, institution=inst, department=department_of(ssq), record=record)
    except RotationPool.DoesNotExist:
        return None


def candidates(ssq):
    """[(user, open_count, eligible, reasons)] — pool members in rotation order, then the
    rest of the department's active staff (never the director, never another department)."""
    from accounts.models import User
    import org
    dept = department_of(ssq)
    s = suggestion(ssq)
    out, seen = [], set()
    if s is not None:
        for c in list(s.eligible) + list(s.blocked):
            if c.user.department_id == getattr(dept, "pk", None):
                out.append((c.user, open_reviews(c.user), c.eligible, list(c.reasons)))
                seen.add(c.user.pk)
    director = org.director_of(dept) if dept is not None else None
    from accounts.audience import is_nbte_staff, nbte_staff_users_q
    out = [row for row in out if is_nbte_staff(row[0])]
    seen = {row[0].pk for row in out}
    for u in User.objects.filter(nbte_staff_users_q(), department=dept).order_by("first_name", "last_name"):
        if u.pk in seen or (director is not None and u.pk == director.pk):
            continue
        n = open_reviews(u)
        out.append((u, n, n < cap(), [] if n < cap() else [f"At the cap: {n} open SSQ reviews"]))
    return out


@transaction.atomic
def assign(ssq, *, by, officer=None, accept_suggestion=False, reason="", due_days=None):
    from accreditation.models import SSQ
    from assignments import rotation, work
    from assignments.models import WorkAssignment

    ssq = SSQ.unscoped.select_for_update().select_related("qa_request_programme__qa_request__institution").get(pk=ssq.pk)
    if not can_assign(by, ssq):
        raise PermissionDenied("Only the director of the department handling this institution (or the ES) assigns "
                               "its SSQs.")
    if ssq.status != "submitted" or not ssq.is_current:
        raise ValidationError("This SSQ is not waiting for review.")
    existing = current_assignment(ssq)
    if existing is not None:
        raise ValidationError(f"{existing.assignee.get_full_name() or existing.assignee.email} already has this SSQ. "
                              "Reassign it from their work instead.")
    dept = department_of(ssq)
    s = suggestion(ssq, record=True)
    if accept_suggestion:
        if s is None or s.suggested is None:
            raise ValidationError("There is no rotation suggestion: choose an officer.")
        officer = s.suggested.user
    if officer is None:
        raise ValidationError("Choose an officer.")
    work.check_assignee(by, dept, officer)          # own department only (ES / super admin: anyone)
    reason = (reason or "").strip()
    n, limit = open_reviews(officer), cap()
    if limit is not None and n >= limit:
        if not (work.is_top(by) and len(reason) >= 5):
            raise ValidationError(f"{officer.get_full_name() or officer.email} already has {n} open SSQ review(s) "
                                  f"(the cap is {limit}). Choose someone else"
                                  + (", or give a reason to go over the cap." if work.is_top(by) else "."))
    if s is not None and s.decision is not None and any(c.get("user_id") == officer.pk
                                                        for c in s.decision.candidates or []):
        rotation.record_decision(s.decision, by=by, chosen=officer, override_reason=reason)
    due = timezone.now() + datetime.timedelta(days=due_days or _int("qa.ssq.review_due_days", 5))
    wa = work.assign(ssq.qa_request_programme.qa_request.institution, WorkAssignment.SSQ_REVIEW, officer,
                     subject=ssq, department=dept, assigned_by=by, due_at=due, role_on_work="reviewer",
                     payable=True, meta={"ssq": ssq.pk, "over_cap": n >= (limit or n + 1)})
    if s is not None and s.decision is not None and s.decision.work_assignment_id is None:
        rotation.link_work(s.decision, wa)
    return wa


@transaction.atomic
def return_work(wa, officer, reason):
    """The officer hands an SSQ back (offered or already accepted). Reason required."""
    from assignments import work
    from assignments.models import WorkAssignment
    reason = (reason or "").strip()
    if wa.kind != KIND:
        raise ValidationError("This is not an SSQ review.")
    if officer is None or wa.assignee_id != officer.pk:
        raise PermissionDenied("Only the assigned officer can return this SSQ.")
    if len(reason) < 3:
        raise ValidationError("Say why you are returning it.")
    if wa.status in (WorkAssignment.SUGGESTED, WorkAssignment.OFFERED):
        return work.decline(wa, officer, reason)
    if not wa.is_open:
        raise ValidationError("This work is already closed.")
    WorkAssignment.objects.filter(pk=wa.pk).update(meta={**wa.meta, "return_reason": reason[:255]})
    wa.refresh_from_db()
    work.cancel(wa, officer, "withdrawn")
    _tell_director(wa, f"{officer.get_full_name() or officer.email} returned the SSQ review: {reason}")
    return wa


def _tell_director(wa, body):
    try:
        import org
        from notifications.services import notify
        director = wa.assigned_by or org.director_of(wa.department_id)
        if director is not None:
            notify(director, title=f"SSQ returned: {wa.institution.name}", body=body,
                   url=f"/qa/requests/ssq/{wa.subject_id}/", actor=wa.assignee)
    except Exception:  # pragma: no cover
        pass


def send_reminders(now=None):
    """Remind officers of offers they have not accepted and reviews past due.
    Returns the number of reminders sent."""
    from assignments.models import WorkAssignment
    from notifications.services import notify
    now = now or timezone.now()
    wait = datetime.timedelta(hours=_int("qa.ssq.assignment_reminder_hours", 24))
    sent = 0
    from django.contrib.contenttypes.models import ContentType

    from accreditation.models import SSQ
    # QA-request SSQs only: onboarding self-studies (same kind) have their own
    # reminders (onboarding_linear.send_stage_reminders).
    ssq_ct = ContentType.objects.get_for_model(SSQ, for_concrete_model=False)
    for wa in WorkAssignment.objects.filter(kind=KIND, status__in=WorkAssignment.OPEN, subject_ct=ssq_ct).select_related(
            "assignee", "institution", "assigned_by"):
        last = wa.meta.get("reminded_at")
        if last and now - datetime.datetime.fromisoformat(last) < datetime.timedelta(hours=24):
            continue
        offered = wa.status in (WorkAssignment.SUGGESTED, WorkAssignment.OFFERED) and wa.assigned_at \
            and now - wa.assigned_at >= wait
        overdue = wa.due_at is not None and wa.due_at <= now and wa.status not in (WorkAssignment.SUBMITTED,)
        if not (offered or overdue):
            continue
        what = "Please accept or return this SSQ review." if offered else "This SSQ review is past its due date."
        notify(wa.assignee, title=f"Reminder: SSQ review for {wa.institution.name}", body=what,
               url="/qa/my-work/" if offered else f"/qa/requests/ssq/{wa.subject_id}/", actor=None)
        if overdue and wa.assigned_by_id:
            notify(wa.assigned_by, title=f"Overdue SSQ review: {wa.institution.name}",
                   body=f"{wa.assignee.get_full_name() or wa.assignee.email} has not finished it.",
                   url=f"/qa/requests/ssq/{wa.subject_id}/", actor=None)
        WorkAssignment.objects.filter(pk=wa.pk).update(meta={**wa.meta, "reminded_at": now.isoformat()})
        sent += 1
    return sent
