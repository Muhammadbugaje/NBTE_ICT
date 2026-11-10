"""Who is handling this SSQ? — routing, holder, tracker and nudges (QA-request SSQs).

The SSQ flow (``qa_core/seed_data_phase3.SSQ_FLOW``) only knows draft → submitted →
accepted | returned. Between "submitted" and the decision the work moves through
people (X1/X4, ``ssq_assign``): the director of the handling department assigns one
officer, the officer accepts and assesses, and the director (the approver) decides.
This module turns that into one answer, used by the NBTE queue, the SSQ page, the
institution portal and the API::

    route_on_submit(ssq, actor)   on submit: record the handling department, its director
                                  (or the heads / the ES when the seat is empty) in
                                  ``ssq.meta["routing"]`` and notify them to assign an officer
    handling_department(ssq)      the department that handles the institution (routing record first)
    assigners(ssq)                who assigns an officer (director; heads if vacant; the ES last)
    approver(ssq)                 who approves after the assessment (the director; the ES if vacant)
    holder(ssq)                   {stage, label, holder, department, director, assignee, approver,
                                   ict_supervisors, since, days, overdue, …} — internal (staff) view
    public_stage(ssq)             the institution-facing label (never staff names; the department
                                  name only when ``qa.portal.show_department_names`` is on)
    submit_assessment(ssq, officer, recommendation, remark)
                                  the assigned officer hands the assessment to the approver
    send_back_to_officer(ssq, by, remark)
                                  the approver sends the assessment back to the officer
    close_review_work(ssq, actor) every open SSQ-review assignment completes when the SSQ is decided
    can_track(user, ssq) / tracked_queryset(user) / can_track_any(user)
                                  who may see the routing panel (not the SSQ content): ES, super
                                  admin, Director of ICT, the ICT supervisor of the institution, the
                                  QA case's tracking ICT officers, the handling department's leads
                                  (every member with ``qa.ssq.tracker_department_wide``), the assignee
    can_nudge(user, ssq) / nudge(ssq, by=, message=)
                                  "Send reminder": notify the current holder (cool-down
                                  ``qa.ssq.nudge_cooldown_minutes``, default 60)
    rotation_context(ssq)         kwargs for ``assignments.rotation.suggest`` (the rotation queue)

Reading the SSQ answers still goes through ``SSQ.objects.for_user`` (strict X1 list);
the tracker only shows routing metadata.
"""
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

KIND = "ssq_review"

# Internal stage codes (holder()["stage"]).
WITH_INSTITUTION = "with_institution"
AWAITING_ASSIGNMENT = "awaiting_assignment"
OFFERED = "offered"
UNDER_ASSESSMENT = "under_assessment"
AWAITING_APPROVAL = "awaiting_approval"
APPROVED = "approved"
RETURNED = "returned"
SUPERSEDED = "superseded"

STAGE_LABELS = {
    WITH_INSTITUTION: "With the institution",
    AWAITING_ASSIGNMENT: "Awaiting officer assignment",
    OFFERED: "Offered to an officer",
    UNDER_ASSESSMENT: "Under assessment",
    AWAITING_APPROVAL: "Awaiting approval",
    APPROVED: "Approved",
    RETURNED: "Returned to the institution",
    SUPERSEDED: "Superseded by a newer version",
}
STAGE_TONES = {
    WITH_INSTITUTION: "qa-tone-neutral", AWAITING_ASSIGNMENT: "qa-tone-warn", OFFERED: "qa-tone-info",
    UNDER_ASSESSMENT: "qa-tone-info", AWAITING_APPROVAL: "qa-tone-warn", APPROVED: "qa-tone-success",
    RETURNED: "qa-tone-danger", SUPERSEDED: "qa-tone-neutral",
}
OPEN_NBTE_STAGES = (AWAITING_ASSIGNMENT, OFFERED, UNDER_ASSESSMENT, AWAITING_APPROVAL)
RECOMMENDATIONS = {"accept": "Accept", "return": "Return to the institution"}


def _cfg(key, default):
    try:
        import config
        value = config.get(key, default)
    except Exception:  # pragma: no cover - settings table missing in odd test setups
        return default
    return default if value in (None, "") else value


def _int(key, default):
    try:
        return int(_cfg(key, default))
    except (TypeError, ValueError):
        return default


def _bool(key, default):
    value = _cfg(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.email


def _institution(ssq):
    return ssq.qa_request_programme.qa_request.institution


def _meta(ssq):
    return dict(ssq.meta or {})


def _save_meta(ssq, meta):
    from .models import SSQ
    SSQ.unscoped.filter(pk=ssq.pk).update(meta=meta)
    ssq.meta = meta


# ---------------------------------------------------------------------------
# Department and people
# ---------------------------------------------------------------------------
def handling_department(ssq):
    """The department handling the SSQ: the one recorded when it was routed,
    otherwise the institution's handling department (Master Plan §5.2: NTIRD
    while the institution is proposed / under review, then its category's
    department)."""
    from staff.models import Department
    routing = _meta(ssq).get("routing") or {}
    if routing.get("department_id"):
        dept = Department.objects.filter(pk=routing["department_id"]).first()
        if dept is not None:
            return dept
    inst = _institution(ssq)
    if inst.handling_department_id:
        return inst.handling_department
    try:
        return inst.derive_handling_department()
    except Exception:  # pragma: no cover
        return None


def _es_users():
    import org
    return list(org.users_with_role("executive-secretary", staff_only=True).order_by("pk")[:3])


def director(ssq):
    import org
    dept = handling_department(ssq)
    return org.director_of(dept) if dept is not None else None


def assigners(ssq):
    """Who assigns an officer: the director; the department's heads when the
    seat is empty; the ES when nobody leads the department."""
    import org
    dept = handling_department(ssq)
    if dept is not None:
        d = org.director_of(dept)
        if d is not None:
            return [d]
        heads = org.heads_of(dept)
        if heads:
            return heads[:3]
    return _es_users()


def approver(ssq):
    """Who approves (accepts / returns) after the officer's assessment."""
    d = director(ssq)
    if d is not None:
        return d
    es = _es_users()
    return es[0] if es else None


def ict_director():
    import org
    from assignments import access
    return org.director_of(access.ict_department_code())


def ict_supervisors(ssq):
    """The ICT supervisor(s) of the institution (open ``oversight_ict`` work) and
    the QA case's tracking ICT officers."""
    from assignments.models import WorkAssignment
    from .models import QACase
    inst = _institution(ssq)
    out, seen = [], set()
    for wa in (WorkAssignment.objects.filter(institution=inst, kind__in=WorkAssignment.FULL_JOURNEY_KINDS,
                                             status__in=WorkAssignment.WORKING).select_related("assignee")):
        if wa.assignee_id not in seen:
            seen.add(wa.assignee_id)
            out.append(wa.assignee)
    case = QACase.unscoped.filter(qa_request_id=ssq.qa_request_programme.qa_request_id).first()
    if case is not None:
        for u in case.tracking_ict_officers.all():
            if u.pk not in seen:
                seen.add(u.pk)
                out.append(u)
    return out


def current_assignment(ssq):
    from . import ssq_assign
    return ssq_assign.current_assignment(ssq)


def rotation_context(ssq):
    """Keyword arguments for ``assignments.rotation.suggest`` so the rotation
    queue can offer this SSQ: ``rotation.suggest(**rotation_context(ssq))``."""
    return {"kind": KIND, "subject": ssq, "institution": _institution(ssq),
            "department": handling_department(ssq)}


# ---------------------------------------------------------------------------
# Routing on submit
# ---------------------------------------------------------------------------
def route_on_submit(ssq, actor=None, *, notify=True):
    """Record where a just-submitted SSQ went and tell the people who assign it.

    Called from ``qa_services.on_ssq_submitted`` (inside the submit transaction).
    Returns the routing record."""
    import org
    inst = _institution(ssq)
    # Route on the institution's department *now* (a new version re-routes).
    dept = inst.handling_department if inst.handling_department_id else None
    if dept is None:
        try:
            dept = inst.derive_handling_department()
        except Exception:  # pragma: no cover
            dept = None
    d = org.director_of(dept) if dept is not None else None
    if d is not None:
        people = [d]
    else:
        people = (org.heads_of(dept)[:3] if dept is not None else []) or _es_users()
    now = timezone.now()
    routing = {
        "version": ssq.version, "routed_at": now.isoformat(),
        "department_id": getattr(dept, "pk", None), "department": getattr(dept, "name", "") or "",
        "director_id": getattr(d, "pk", None), "director": _name(d),
        "assigner_ids": [u.pk for u in people], "director_vacant": d is None,
    }
    meta = _meta(ssq)
    meta["routing"] = routing
    meta.pop("assessment", None)
    _save_meta(ssq, meta)
    from qa_core import timeline
    who = ", ".join(_name(u) for u in people) or "nobody (no director, heads or ES found)"
    timeline.record(inst, "ssq.routed", subject=ssq, actor=actor if getattr(actor, "pk", None) else None,
                    visibility="internal", department=dept,
                    internal_text=f"SSQ for {ssq.qa_request_programme.programme} (v{ssq.version}) routed to "
                                  f"{routing['department'] or 'no department'}; to assign: {who}.")
    if notify and people:
        url = f"/qa/requests/ssq/{ssq.pk}/"
        title = f"Assign an officer: SSQ from {inst.name}"
        body = (f"{ssq.qa_request_programme.programme} (version {ssq.version}) was submitted and routed to "
                f"{routing['department'] or 'your department'}. Choose the officer who will assess it.")
        if d is None:
            body += " The director's seat is empty, so this came to you."

        def _send():
            from notifications.models import Notification
            from notifications.services import notify as _notify
            for u in people:
                _notify(u, title=title, body=body, url=url, category=Notification.QA,
                        metadata={"ssq": ssq.pk, "kind": "ssq.routed"})
        transaction.on_commit(_send)
    return routing


# ---------------------------------------------------------------------------
# The holder
# ---------------------------------------------------------------------------
def _parse(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt)


def holder(ssq, *, now=None, assignment=None, with_supervisors=True):
    """Everything the "Who is handling this SSQ" panel shows (internal)."""
    from assignments.models import WorkAssignment
    now = now or timezone.now()
    dept = handling_department(ssq)
    d = director(ssq)
    meta = _meta(ssq)
    routing = meta.get("routing") or {}
    assessment = meta.get("assessment") or {}
    wa = assignment if assignment is not None else (current_assignment(ssq) if ssq.status == "submitted" else None)
    inst = _institution(ssq)
    out = {
        "department": dept, "director": d, "director_vacant": d is None, "assigners": [],
        "assignee": getattr(wa, "assignee", None), "assignment": wa, "approver": approver(ssq),
        "assessment": assessment, "routing": routing, "institution": inst,
        "ict_director": ict_director(), "ict_supervisors": ict_supervisors(ssq) if with_supervisors else [],
        "holder": None, "holder_label": "", "holder_role": "", "next_step": "", "since": None,
        "decided_by": ssq.reviewed_by if ssq.status in ("accepted", "returned", "analysed") else None,
    }
    if not ssq.is_current and ssq.status not in ("submitted",):
        stage = SUPERSEDED
        out.update(holder_label="—", next_step="A newer version replaced this one.", since=ssq.updated_at)
    elif ssq.status in ("draft", "returned_to_department", "submitted_by_department"):
        stage = WITH_INSTITUTION
        out.update(holder_label=inst.name, holder_role="Institution",
                   next_step="The institution completes and submits the SSQ.", since=ssq.last_saved_at or ssq.created_at)
    elif ssq.status == "returned":
        stage = RETURNED
        out.update(holder_label=inst.name, holder_role="Institution",
                   next_step="The institution starts a new version and resubmits.", since=ssq.reviewed_at)
    elif ssq.status in ("accepted", "analysed"):
        stage = APPROVED
        out.update(holder_label="—", next_step="Done: the SSQ feeds the QA case.", since=ssq.reviewed_at)
    elif wa is None:
        stage = AWAITING_ASSIGNMENT
        people = assigners(ssq)
        out["assigners"] = people
        lead = people[0] if people else None
        out.update(holder=lead, holder_label=_name(lead) or "Nobody — no director in the department",
                   holder_role=("Director" if d is not None and lead is not None and lead.pk == d.pk
                                else ("Department head" if lead is not None else "")),
                   next_step="Assign one officer of the department to assess it.",
                   since=_parse(routing.get("routed_at")) or ssq.submitted_at)
    elif wa.status in (WorkAssignment.SUGGESTED, WorkAssignment.OFFERED):
        stage = OFFERED
        out.update(holder=wa.assignee, holder_label=_name(wa.assignee), holder_role="Assessing officer",
                   next_step="The officer accepts (or returns) the assignment.", since=wa.assigned_at)
    elif wa.status == WorkAssignment.SUBMITTED:
        stage = AWAITING_APPROVAL
        a = out["approver"]
        out.update(holder=a, holder_label=_name(a) or "—", holder_role="Approver",
                   next_step="Approve (accept) or return the SSQ, or send the assessment back to the officer.",
                   since=wa.submitted_at or _parse(assessment.get("at")) or wa.assigned_at)
    else:
        stage = UNDER_ASSESSMENT
        out.update(holder=wa.assignee, holder_label=_name(wa.assignee), holder_role="Assessing officer",
                   next_step="The officer assesses the SSQ and submits the assessment for approval.",
                   since=wa.accepted_at or wa.assigned_at)
    since = out["since"] or ssq.submitted_at
    out["since"] = since
    out["days"] = (now - since).days if since else None
    sla_days = _int("qa.ssq.stage_sla_days", 5)
    out["sla_days"] = sla_days
    due = getattr(wa, "due_at", None) if stage in (OFFERED, UNDER_ASSESSMENT) else None
    out["due_at"] = due
    out["overdue"] = bool(stage in OPEN_NBTE_STAGES and (
        (due is not None and due <= now) or (out["days"] is not None and out["days"] >= sla_days)))
    out["stage"], out["label"], out["tone"] = stage, STAGE_LABELS[stage], STAGE_TONES[stage]
    if stage in (OFFERED, UNDER_ASSESSMENT):
        out["label"] = f"{STAGE_LABELS[stage]} — {_name(wa.assignee)}"
    elif stage == AWAITING_APPROVAL and out["approver"] is not None:
        out["label"] = f"Awaiting approval — {_name(out['approver'])}"
    elif stage == AWAITING_ASSIGNMENT and dept is not None:
        out["label"] = f"Awaiting officer assignment — {dept.name}"
    out["is_open"] = stage in OPEN_NBTE_STAGES
    out["nudges"] = list(meta.get("nudges") or [])[-5:]
    return out


def public_stage(ssq):
    """The plain status the institution sees. Never staff names; the
    department only when ``qa.portal.show_department_names`` is on."""
    from qa_core import portal_journey
    if ssq.status not in ("submitted", "accepted", "analysed"):
        return ""
    if ssq.status in ("accepted", "analysed"):
        return "Approved by NBTE"
    dept = handling_department(ssq)
    suffix = f" — {dept.name}" if dept is not None and portal_journey.show_department_names() else ""
    wa = current_assignment(ssq)
    from assignments.models import WorkAssignment
    if wa is None or wa.status in (WorkAssignment.SUGGESTED, WorkAssignment.OFFERED):
        return f"Received by NBTE — being assigned for assessment{suffix}"
    if wa.status == WorkAssignment.SUBMITTED:
        return f"Assessed — awaiting NBTE approval{suffix}"
    return f"Under assessment by NBTE{suffix}"


# ---------------------------------------------------------------------------
# Assessment hand-over (officer → approver)
# ---------------------------------------------------------------------------
def _is_approver(user, ssq):
    import org
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or org.is_super_admin(user) or org.is_es(user):
        return True
    dept = handling_department(ssq)
    return dept is not None and org.is_director(user, dept)


def can_submit_assessment(user, ssq):
    from assignments.models import WorkAssignment
    wa = current_assignment(ssq)
    return bool(ssq.status == "submitted" and ssq.is_current and wa is not None and user is not None
                and wa.assignee_id == user.pk
                and wa.status in (WorkAssignment.ACCEPTED, WorkAssignment.IN_PROGRESS, WorkAssignment.RETURNED))


def can_send_back(user, ssq):
    from assignments.models import WorkAssignment
    wa = current_assignment(ssq)
    return bool(ssq.status == "submitted" and wa is not None and wa.status == WorkAssignment.SUBMITTED
                and _is_approver(user, ssq))


@transaction.atomic
def submit_assessment(ssq, *, officer, recommendation, remark=""):
    from assignments import work
    if not can_submit_assessment(officer, ssq):
        raise PermissionDenied("Only the officer who accepted this SSQ can submit its assessment.")
    if recommendation not in RECOMMENDATIONS:
        raise ValidationError("Recommend accept or return.")
    remark = (remark or "").strip()
    if recommendation == "return" and len(remark) < 3:
        raise ValidationError("Say what the institution must change.")
    wa = current_assignment(ssq)
    work.submit(wa, officer)
    meta = _meta(ssq)
    meta["assessment"] = {"by": officer.pk, "by_name": _name(officer), "recommendation": recommendation,
                          "remark": remark[:2000], "at": timezone.now().isoformat()}
    _save_meta(ssq, meta)
    a = approver(ssq)
    inst = _institution(ssq)
    from qa_core import timeline
    timeline.record(inst, "ssq.assessed", subject=ssq, actor=officer, visibility="internal",
                    internal_text=f"{_name(officer)} assessed the SSQ for {ssq.qa_request_programme.programme}: "
                                  f"recommends {RECOMMENDATIONS[recommendation].lower()}.")
    if a is not None:
        def _send():
            from notifications.models import Notification
            from notifications.services import notify
            notify(a, title=f"SSQ ready for your approval: {inst.name}",
                   body=f"{_name(officer)} recommends: {RECOMMENDATIONS[recommendation]}. "
                        f"{ssq.qa_request_programme.programme}.", url=f"/qa/requests/ssq/{ssq.pk}/",
                   category=Notification.QA, actor=officer)
        transaction.on_commit(_send)
    return wa


@transaction.atomic
def send_back_to_officer(ssq, *, by, remark):
    from assignments import work
    if not can_send_back(by, ssq):
        raise PermissionDenied("Only the approver can send the assessment back.")
    remark = (remark or "").strip()
    if len(remark) < 3:
        raise ValidationError("Say what the officer should look at again.")
    wa = current_assignment(ssq)
    work.return_to_assignee(wa, by, remark)
    meta = _meta(ssq)
    meta.pop("assessment", None)
    _save_meta(ssq, meta)

    def _send():
        from notifications.models import Notification
        from notifications.services import notify
        notify(wa.assignee, title=f"SSQ assessment sent back: {_institution(ssq).name}", body=remark,
               url=f"/qa/requests/ssq/{ssq.pk}/", category=Notification.QA, actor=by)
    transaction.on_commit(_send)
    return wa


def close_review_work(ssq, actor=None):
    """The SSQ is decided: complete every open SSQ-review assignment on it, so
    the officer's open-review count (the cap) and "my work" are right."""
    from assignments import work
    try:
        return work.close_for_subject(ssq, kind=KIND, actor=actor, reason="completed")
    except Exception:  # pragma: no cover - never block the decision on bookkeeping
        import logging
        logging.getLogger(__name__).exception("Could not close SSQ review work for SSQ %s", ssq.pk)
        return 0


def officer_final_decision():
    """``qa.ssq.officer_final_decision`` (default on): the assigned officer may
    accept / return directly; off, the officer only submits the assessment and
    the approver decides."""
    return _bool("qa.ssq.officer_final_decision", True)


# ---------------------------------------------------------------------------
# Who sees the routing panel (tracker) and who may nudge
# ---------------------------------------------------------------------------
def _staff(user):
    return bool(user is not None and getattr(user, "is_authenticated", False) and user.is_active
                and getattr(user, "user_type", "staff") == "staff")


def tracks_everything(user):
    import org
    from assignments import access
    if not _staff(user):
        return False
    return bool(user.is_superuser or org.is_super_admin(user) or org.is_es(user)
                or access.full_viewer_everywhere(user))


def _lead_departments(user):
    import org
    from staff.models import Department
    ids = set(Department.objects.filter(director_id=user.pk).values_list("pk", flat=True))
    if user.department_id and (org.is_department_lead(user, user.department_id)
                               or user.has_perm("assignments.view_department_qa")
                               or _bool("qa.ssq.tracker_department_wide", False)):
        ids.add(user.department_id)
    return ids


def tracker_q(user):
    """Q on SSQ for the rows the user may track; None means every SSQ."""
    if not _staff(user):
        return Q(pk__in=[])
    if tracks_everything(user):
        return None
    from assignments import access
    from assignments.models import WorkAssignment
    from django.contrib.contenttypes.models import ContentType
    from .models import SSQ
    path = "qa_request_programme__qa_request__institution"
    q = Q(pk__in=[])
    depts = _lead_departments(user)
    if depts:
        q |= Q(**{f"{path}__handling_department_id__in": depts})
    supervised = access.supervised_institution_ids(user)
    if supervised:
        q |= Q(**{f"{path}_id__in": supervised})
    q |= Q(qa_request_programme__qa_request__case__tracking_ict_officers=user)
    ct = ContentType.objects.get_for_model(SSQ, for_concrete_model=False)
    assigned = list(WorkAssignment.objects.filter(
        assignee=user, kind=KIND, subject_ct=ct,
        status__in=WorkAssignment.OPEN + (WorkAssignment.COMPLETED,)).values_list("subject_id", flat=True))
    ids = []
    for pk in assigned:
        try:
            ids.append(int(pk))
        except (TypeError, ValueError):
            continue
    if ids:
        q |= Q(pk__in=ids)
    return q


def tracked_queryset(user):
    from .models import SSQ
    qs = SSQ.unscoped.all()
    q = tracker_q(user)
    return qs if q is None else qs.filter(q).distinct()


def can_track(user, ssq):
    if tracks_everything(user):
        return True
    return tracked_queryset(user).filter(pk=ssq.pk).exists()


def can_track_any(user):
    if not _staff(user):
        return False
    return tracks_everything(user) or tracked_queryset(user).exists()


def can_open(user, ssq):
    """May the user open the SSQ itself (its answers)? The strict X1 list."""
    from .models import SSQ
    return SSQ.objects.for_user(user).filter(pk=ssq.pk).exists()


def can_nudge(user, ssq):
    """ES / super admin / Director of ICT, the handling department's leads and
    the institution's ICT supervisor may remind whoever holds an open SSQ."""
    import org
    from assignments import access
    if not _staff(user) or ssq.status != "submitted" or not ssq.is_current:
        return False
    if tracks_everything(user):
        return True
    dept = handling_department(ssq)
    if dept is not None and (org.is_director(user, dept) or (user.department_id == dept.pk
                                                             and org.is_department_lead(user, dept))):
        return True
    return access.is_ict_supervisor(user, _institution(ssq))


def nudge_recipients(ssq, info=None):
    info = info or holder(ssq, with_supervisors=False)
    if info["stage"] == AWAITING_ASSIGNMENT:
        return list(info["assigners"]) or assigners(ssq)
    if info["stage"] in (OFFERED, UNDER_ASSESSMENT) and info["assignee"] is not None:
        return [info["assignee"]]
    if info["stage"] == AWAITING_APPROVAL and info["approver"] is not None:
        return [info["approver"]]
    return []


def nudge(ssq, *, by, message=""):
    """Send a reminder to the current holder. Returns the people reminded."""
    if not can_nudge(by, ssq):
        raise PermissionDenied("You cannot send reminders on this SSQ.")
    info = holder(ssq, with_supervisors=False)
    people = [u for u in nudge_recipients(ssq, info) if u is not None and u.pk != by.pk]
    if not people:
        raise ValidationError("Nobody is holding this SSQ at NBTE right now — there is no one to remind.")
    meta = _meta(ssq)
    nudges = list(meta.get("nudges") or [])
    cooldown = datetime.timedelta(minutes=_int("qa.ssq.nudge_cooldown_minutes", 60))
    now = timezone.now()
    last = _parse(nudges[-1].get("at")) if nudges else None
    if last is not None and now - last < cooldown and nudges[-1].get("stage") == info["stage"]:
        mins = max(1, int((cooldown - (now - last)).total_seconds() // 60))
        raise ValidationError(f"A reminder was sent {int((now - last).total_seconds() // 60)} minute(s) ago. "
                              f"Try again in {mins} minute(s).")
    message = (message or "").strip()[:500]
    inst = _institution(ssq)
    what = {AWAITING_ASSIGNMENT: "Please assign an officer to assess it.",
            OFFERED: "Please accept the assignment (or return it) so the assessment can start.",
            UNDER_ASSESSMENT: "Please finish the assessment and submit it for approval.",
            AWAITING_APPROVAL: "Please approve or return it."}[info["stage"]]
    days = info["days"] or 0
    body = (f"{ssq.qa_request_programme.programme} — {info['label']} for {days} day{'s' if days != 1 else ''}. "
            f"{what}" + (f" Note from {_name(by)}: {message}" if message else ""))
    from notifications.models import Notification
    from notifications.services import notify
    for u in people:
        notify(u, title=f"Reminder: SSQ from {inst.name}", body=body, url=f"/qa/requests/ssq/{ssq.pk}/",
               category=Notification.QA, level=getattr(Notification, "WARNING", Notification.INFO), actor=by, metadata={"ssq": ssq.pk, "kind": "ssq.nudge"})
    nudges.append({"at": now.isoformat(), "by": by.pk, "by_name": _name(by), "stage": info["stage"],
                   "to": [_name(u) for u in people], "message": message})
    meta["nudges"] = nudges[-20:]
    _save_meta(ssq, meta)
    from qa_core import timeline
    timeline.record(inst, "ssq.nudged", subject=ssq, actor=by, visibility="internal",
                    internal_text=f"{_name(by)} sent a reminder to {', '.join(_name(u) for u in people)} "
                                  f"({info['label']}).")
    return people
