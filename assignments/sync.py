"""Keep WorkAssignment in step with the assignment-like fields the existing
QA services still write (P.1/P.5). The back-fill (``assignments.backfill``)
covers rows that already exist; these receivers cover every new assignment,
so need-to-know visibility (and its grants) follows the work without
changing each service:

* SSQReviewAssignment created → ``ssq_review`` (completed when ``completed_at`` is set)
* Complaint.current_assignee changes → ``complaint_task`` (previous one reassigned; all closed with the complaint)
* QACase desk / supervising officers → ``case_desk`` / ``case_supervision_*``
* QACase.tracking_ict_officers added → ``oversight_ict`` on the institution (removed → cancelled)
* InspectionTeamMember (staff) created → ``visit_panel``; Inspection.team_lead → ``visit_lead``

Receivers never raise into the caller (an error is logged).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import m2m_changed, post_save

logger = logging.getLogger(__name__)


def _safe(fn):
    def wrapper(*args, **kwargs):
        if kwargs.get("raw"):
            return
        try:
            with transaction.atomic():
                fn(*args, **kwargs)
        except Exception:  # pragma: no cover - visibility sync must never break the work itself
            logger.exception("WorkAssignment sync failed in %s", fn.__name__)
    return wrapper


def _work():
    from . import work
    from .models import WorkAssignment
    return work, WorkAssignment


def visit_meta(insp):
    """Visit dates in the convention the rotation engine's availability reads
    (WorkAssignment.meta["from"] / ["to"], ISO dates)."""
    out = {}
    if getattr(insp, "scheduled_start", None):
        out["from"] = insp.scheduled_start.isoformat()
    if getattr(insp, "scheduled_end", None):
        out["to"] = insp.scheduled_end.isoformat()
    return out


def _sync_person(subject, institution, kind, user, *, department=None, role="", is_open=True, meta=None):
    """Exactly one open assignment of ``kind`` on ``subject`` for ``user``
    (others reassigned); none when ``user`` is None or the record closed."""
    work, WA = _work()
    if institution is None:
        return
    open_rows = WA.objects.for_subject(subject).filter(kind=kind, status__in=WA.OPEN)
    for wa in open_rows:
        if user is None or wa.assignee_id != user.pk or not is_open:
            if is_open:
                work.cancel(wa, None, "reassigned")
            else:
                work.complete(wa, None)
    if user is not None and is_open and getattr(user, "is_active", False):
        work.ensure(institution, kind, user, subject=subject, department=department or user.department,
                    role_on_work=role, meta=meta)


@_safe
def ssq_review_saved(sender, instance, created=False, **kwargs):
    work, WA = _work()
    ssq = instance.ssq
    inst = ssq.qa_request_programme.qa_request.institution
    if instance.completed_at is None:
        work.ensure(inst, WA.SSQ_REVIEW, instance.reviewer, subject=ssq, department=instance.reviewer.department,
                    role_on_work="reviewer", payable=True)  # X32: the reviewer earns the ₦10,000 item
    else:
        for wa in WA.objects.for_subject(ssq).filter(kind=WA.SSQ_REVIEW, assignee=instance.reviewer,
                                                       status__in=WA.OPEN):
            work.complete(wa, None)


@_safe
def complaint_saved(sender, instance, **kwargs):
    if instance.institution_id is None:
        return
    # v2.1 X10: the officer the director assigned holds the work (the stage
    # resolver in current_assignee may be the director himself).
    holder = instance.assigned_officer if getattr(instance, "assigned_officer_id", None) else instance.current_assignee
    _sync_person(instance, instance.institution, "complaint_task", holder,
                 department=instance.current_department, role="analyst", is_open=instance.closed_at is None)


CASE_FIELDS = (("desk_officer", "case_desk", "analyst", "owning_department"),
               ("supervising_audit_officer", "case_supervision_audit", "supervisor", None),
               ("supervising_finance_officer", "case_supervision_finance", "supervisor", None))
CASE_CLOSED = ("closed", "cancelled", "withdrawn")


@_safe
def case_saved(sender, instance, **kwargs):
    for field, kind, role, dept_attr in CASE_FIELDS:
        user = getattr(instance, field)
        dept = getattr(instance, dept_attr) if dept_attr else None
        _sync_person(instance, instance.institution, kind, user, department=dept, role=role,
                     is_open=instance.status not in CASE_CLOSED)


@_safe
def case_ict_changed(sender, instance, action, pk_set=None, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    from accounts.models import User
    work, WA = _work()
    inst = instance.institution
    if action == "post_add":
        for user in User.objects.filter(pk__in=pk_set or ()):
            work.ensure(inst, WA.OVERSIGHT_ICT, user, subject=inst, department=user.department,
                        role_on_work="supervisor", meta={"case": instance.pk})
        return
    keep = set(instance.tracking_ict_officers.values_list("pk", flat=True))
    for wa in WA.objects.filter(institution=inst, kind=WA.OVERSIGHT_ICT, status__in=WA.OPEN,
                                meta__case=instance.pk).exclude(assignee_id__in=keep):
        work.cancel(wa, None, "reassigned")


@_safe
def team_member_saved(sender, instance, created=False, **kwargs):
    if not created or not instance.staff_id or not instance.staff.user_id:
        return
    work, WA = _work()
    insp = instance.inspection
    user = instance.staff.user
    work.ensure(insp.case.institution, WA.VISIT_PANEL, user, subject=insp, department=user.department,
                role_on_work="member", meta=visit_meta(insp))


@_safe
def inspection_saved(sender, instance, **kwargs):
    _sync_person(instance, instance.case.institution, "visit_lead", instance.team_lead, role="lead",
                 is_open=instance.closed_at is None, meta=visit_meta(instance))
    if instance.closed_at is not None:
        work, WA = _work()
        for wa in WA.objects.for_subject(instance).filter(status__in=WA.OPEN):
            work.complete(wa, None)


def connect():
    from django.apps import apps
    pairs = (("inspection.SSQReviewAssignment", ssq_review_saved), ("complaints.Complaint", complaint_saved),
             ("accreditation.QACase", case_saved), ("inspection.InspectionTeamMember", team_member_saved),
             ("inspection.Inspection", inspection_saved))
    for label, fn in pairs:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        post_save.connect(fn, sender=model, dispatch_uid=f"asg_sync_{label}")
    try:
        QACase = apps.get_model("accreditation.QACase")
        m2m_changed.connect(case_ict_changed, sender=QACase.tracking_ict_officers.through,
                            dispatch_uid="asg_sync_case_ict")
    except LookupError:  # pragma: no cover
        pass
