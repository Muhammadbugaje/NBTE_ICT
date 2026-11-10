"""SOP-01 → SOP-02 as one linear path (onboarding self-studies to the QA case).

The steps follow each other on their own; each person sees one card with one
button for the step that is theirs:

1. The Chief Executive submits the programme self-studies
   (``onboarding_ssq.submit_application``) → ``route_on_submit``: every
   self-study gets an ``OnboardingSSQReview`` routed to the institution's
   handling department; its director (heads / the ES when the seat is empty)
   is told to assign an officer. The rotation board lists them under
   "Onboarding self-study" (``assignments.rotation_queue``).
2. ``assign`` → the officer assesses → ``send_to_director`` (recommend clear,
   or return with notes) → the director ``clear`` s the programme, or
   ``return_to_officer`` / ``return_to_institution`` (the institution corrects
   that one self-study and ``resubmit`` s it).
3. Every programme cleared → ``ready_for_approval(app)``: the handling director
   presses "Approve institution" (``approve_institution``), which initiates the
   two-officer action ``institutions.approve_institution`` (the Action menu on
   the institution page still works). The ES confirms (``confirm_approval``)
   or rejects it through the normal pending-action path.
4. On approval (``ApproveInstitutionHandler.apply`` → ``on_institution_approved``)
   the formal QA request is started for the cleared programmes
   (``qa.onboarding.auto_request_type``) and each onboarding self-study is
   carried over as the request's accepted SSQ (``carry_over``): the
   institution never fills an SSQ again for a programme already covered. The
   fee is billed as normal unless ``qa.onboarding.auto_request_fee_waived``;
   once every SSQ is in, the QA case (SOP-03) opens through ``on_spawn_case``.
5. ``next_actions(user)`` lists the step waiting for this person (director:
   assign / clear / approve; ES: confirm; officer: assess) — QA home and
   "Your work".
6. ``send_stage_reminders`` (Celery beat + ``manage.py qa_onboarding_reminders``)
   reminds the holder after ``qa.onboarding.stage_sla_days`` and escalates to
   the director, then the ES.

``review_stage(ssq)`` (re-exported as ``onboarding_ssq.review_stage``) is the
contract other screens read::

    {"code": awaiting_assignment | under_assessment | awaiting_director | cleared
             | returned_to_institution | approved   ("" while still a draft),
     "label": staff wording, "public": institution-safe wording (no staff names;
     the department only with qa.portal.show_department_names),
     "holder": who holds it now (name), "assigned_officer": User | None,
     "director": User | None, "days_at_stage": int}
"""
from __future__ import annotations

import datetime
import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

KIND = "ssq_review"     # the WorkAssignment kind (subject: the OnboardingSSQ)


def assessment_payable():
    """Setting ``honoraria.onboarding_ssq_payable`` (default on): an officer who
    assesses an onboarding programme self-study earns the internal SSQ-review
    honorarium (``honoraria.rate.internal_item``), as for QA-request SSQs."""
    import config as config_registry
    value = config_registry.get("honoraria.onboarding_ssq_payable", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)
APPROVE_POLICY = "institutions.approve_institution"

AWAITING_ASSIGNMENT = "awaiting_assignment"
UNDER_ASSESSMENT = "under_assessment"
AWAITING_DIRECTOR = "awaiting_director"
CLEARED = "cleared"
RETURNED = "returned_to_institution"
APPROVED = "approved"
OPEN_STAGES = (AWAITING_ASSIGNMENT, UNDER_ASSESSMENT, AWAITING_DIRECTOR)

LABELS = {
    AWAITING_ASSIGNMENT: "Awaiting officer assignment",
    UNDER_ASSESSMENT: "Under assessment",
    AWAITING_DIRECTOR: "Awaiting the director's clearance",
    CLEARED: "Cleared by the director",
    RETURNED: "Returned to the institution for correction",
    APPROVED: "Approved — institution approved by NBTE",
}
PUBLIC = {
    AWAITING_ASSIGNMENT: "Received by NBTE — being assigned for assessment",
    UNDER_ASSESSMENT: "Under assessment by NBTE",
    AWAITING_DIRECTOR: "Assessed — awaiting NBTE clearance",
    CLEARED: "Cleared by NBTE — awaiting institution approval",
    RETURNED: "Returned by NBTE for correction",
    APPROVED: "Approved by NBTE",
}
RECOMMENDATIONS = {"clear": "Clear the programme", "return": "Return to the institution for correction"}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _cfg(key, default):
    try:
        import config
        value = config.get(key, default)
    except Exception:  # pragma: no cover - settings table missing in odd setups
        return default
    return default if value in (None, "") else value


def _bool(key, default):
    value = _cfg(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def sla_days():
    try:
        return max(1, int(_cfg("qa.onboarding.stage_sla_days", 5)))
    except (TypeError, ValueError):
        return 5


def auto_request_type_code():
    return str(_cfg("qa.onboarding.auto_request_type", "accreditation")).strip()


def auto_fee_waived():
    return _bool("qa.onboarding.auto_request_fee_waived", False)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _name(user):
    if user is None:
        return ""
    return user.get_full_name() or user.email


def _staff(user):
    return bool(user is not None and getattr(user, "is_authenticated", False) and user.is_active
                and getattr(user, "user_type", "staff") == "staff")


def _is_top(user):
    import org
    return bool(_staff(user) and (user.is_superuser or org.is_es(user) or org.is_super_admin(user)))


def _es_users():
    import org
    return list(org.users_with_role("executive-secretary", staff_only=True).order_by("pk")[:3])


def _notify(users, *, title, body, url, actor=None, kind="onboarding", level=None):
    from notifications.models import Notification
    from notifications.services import notify
    seen = set()
    for u in users:
        if u is None or u.pk in seen or (actor is not None and u.pk == getattr(actor, "pk", None)):
            continue
        seen.add(u.pk)
        try:
            kw = {"level": level} if level else {}
            notify(u, title=title, body=body, url=url, category=Notification.QA, actor=actor,
                   metadata={"kind": f"onboarding.{kind}"}, **kw)
        except Exception:  # pragma: no cover - a notification never blocks the step
            logger.exception("onboarding notification failed")


def _later(fn):
    transaction.on_commit(fn)


def _institution(ssq):
    return ssq.application.institution


def study_url(ssq):
    return f"/qa/applications/self-study/{ssq.pk}/"


def application_url(app):
    return f"/qa/applications/{app.pk}/"


def department_for(inst):
    if inst.handling_department_id:
        return inst.handling_department
    try:
        return inst.derive_handling_department()
    except Exception:  # pragma: no cover
        return None


def assigners(dept):
    """Who assigns and clears: the director; the heads when the seat is empty; the ES last."""
    import org
    if dept is not None:
        d = org.director_of(dept)
        if d is not None:
            return [d]
        heads = org.heads_of(dept)
        if heads:
            return list(heads[:3])
    return _es_users()


def review_of(ssq):
    from .models import OnboardingSSQReview
    return OnboardingSSQReview.objects.filter(ssq_id=ssq.pk).select_related(
        "department", "director", "officer", "cleared_by").first()


def _department_of_study(ssq, review=None):
    review = review if review is not None else review_of(ssq)
    if review is not None and review.department_id:
        return review.department
    return department_for(_institution(ssq))


def current_assignment(ssq):
    from assignments.models import WorkAssignment
    return (WorkAssignment.objects.for_subject(ssq).filter(kind=KIND, status__in=WorkAssignment.OPEN)
            .select_related("assignee").order_by("-assigned_at").first())


def _history(review, event, actor=None, **extra):
    row = {"at": timezone.now().isoformat(), "event": event, "by": getattr(actor, "pk", None),
           "by_name": _name(actor), "stage": review.stage}
    row.update(extra)
    review.history = (list(review.history or []) + [row])[-100:]


def _move(review, stage, actor, event, **extra):
    review.stage = stage
    review.stage_since = timezone.now()
    review.reminder = {}
    _history(review, event, actor, **extra)


# ---------------------------------------------------------------------------
# Routing on submit
# ---------------------------------------------------------------------------
def route_on_submit(app, actor=None):
    """Called from ``submit_application`` (inside its transaction): one review per
    submitted self-study, routed to the handling department; its director is
    told to assign officers. Returns the reviews."""
    from .models import OnboardingSSQ, OnboardingSSQReview
    inst = app.institution
    dept = department_for(inst)
    people = assigners(dept)
    lead = people[0] if people else None
    out = []
    for study in OnboardingSSQ.unscoped.filter(application=app, status=OnboardingSSQ.SUBMITTED) \
            .select_related("programme"):
        review, created = OnboardingSSQReview.objects.get_or_create(
            ssq=study, defaults={"department": dept, "director": lead, "stage": AWAITING_ASSIGNMENT})
        if not created and review.stage in (CLEARED,):
            out.append(review)
            continue
        if not created:
            review.department, review.director = dept, lead
            _move(review, AWAITING_ASSIGNMENT, actor, "routed")
            review.save()
        else:
            _history(review, "routed", actor, department=getattr(dept, "name", ""))
            review.save(update_fields=["history", "updated_at"])
        out.append(review)
    from qa_core import timeline
    who = ", ".join(_name(u) for u in people) or "nobody (no director, heads or ES found)"
    timeline.record(inst, "onboarding.ssq_routed", subject=app, actor=actor if getattr(actor, "pk", None) else None,
                    visibility="internal", department=dept,
                    internal_text=f"{len(out)} programme self-stud{'y' if len(out) == 1 else 'ies'} routed to "
                                  f"{getattr(dept, 'name', 'no department')}; to assign: {who}.")
    if people and out:
        n = len(out)
        body = (f"{inst.name} submitted {n} programme self-stud{'y' if n == 1 else 'ies'} "
                f"({app.reference}). Assign an officer to each — one click from the rotation board.")
        if dept is not None and lead is not None and not _is_director_of(lead, dept):
            body += " The director's seat is empty, so this came to you."
        url = study_url(out[0].ssq) if n == 1 else application_url(app)
        _later(lambda: _notify(people, title=f"Assign officers: onboarding self-studies from {inst.name}",
                               body=body, url=url, kind="routed"))
    return out


def _is_director_of(user, dept):
    import org
    return bool(user is not None and dept is not None and org.is_director(user, dept))


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------
def can_direct(user, ssq, review=None):
    """Assign, clear or return: the director (or delegate / heads when vacant), ES, super admin."""
    if not _staff(user):
        return False
    if _is_top(user):
        return True
    from assignments import work
    dept = _department_of_study(ssq, review)
    if dept is None:
        return False
    if work.can_assign(user, dept):
        return True
    return user.pk in {u.pk for u in assigners(dept)}


def can_assess(user, ssq, review=None):
    review = review if review is not None else review_of(ssq)
    return bool(_staff(user) and review is not None and review.stage == UNDER_ASSESSMENT
                and review.officer_id == user.pk)


def can_view(user, ssq, review=None):
    """The self-study page: the people on the path, full viewers and whoever may see the application."""
    if not _staff(user):
        return False
    review = review if review is not None else review_of(ssq)
    if _is_top(user) or can_direct(user, ssq, review):
        return True
    if review is not None and review.officer_id == user.pk:
        return True
    from assignments import access
    if access.full_viewer_everywhere(user):
        return True
    try:
        from .views import visible_applications
        return ssq.application_id in visible_applications(user)
    except Exception:  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
# The stage (contract)
# ---------------------------------------------------------------------------
def _state(ssq, review=None, *, now=None):
    """Everything about where the self-study is (internal)."""
    from .models import OnboardingSSQ
    now = now or timezone.now()
    review = review if review is not None else review_of(ssq)
    inst = _institution(ssq)
    dept = _department_of_study(ssq, review)
    director = (review.director if review is not None and review.director_id else None)
    import org
    seat = org.director_of(dept) if dept is not None else None
    director = seat or director
    officer = review.officer if review is not None and review.officer_id else None
    if ssq.status != OnboardingSSQ.SUBMITTED and (review is None or review.stage != RETURNED):
        return {"code": "", "review": review, "department": dept, "director": director, "officer": officer,
                "since": ssq.last_saved_at or ssq.created_at, "holder": inst.name, "holder_user": None}
    if inst.status == "approved" and (review is None or review.stage in (CLEARED,)):
        code = APPROVED
        since = inst.approved_at or (review.cleared_at if review is not None else None) or ssq.submitted_at
    elif review is None:
        code, since = AWAITING_ASSIGNMENT, ssq.submitted_at
    else:
        code, since = review.stage, review.stage_since
    holder_user = None
    if code == AWAITING_ASSIGNMENT:
        people = assigners(dept)
        holder_user = people[0] if people else None
    elif code == UNDER_ASSESSMENT:
        holder_user = officer
    elif code == AWAITING_DIRECTOR:
        holder_user = director or (assigners(dept) or [None])[0]
    if code in OPEN_STAGES:
        holder = _name(holder_user) or getattr(dept, "name", "") or "NBTE"
    elif code == RETURNED:
        holder = inst.name
    elif code == CLEARED:
        holder = "Awaiting institution approval"
    else:
        holder = "—"
    days = max(0, (now - since).days) if since else 0
    due = since + datetime.timedelta(days=sla_days()) if since and code in OPEN_STAGES else None
    return {"code": code, "review": review, "department": dept, "director": director, "officer": officer,
            "since": since, "holder": holder, "holder_user": holder_user, "days": days, "due": due,
            "overdue": bool(due is not None and due <= now)}


def review_stage(ssq):
    """The contract read by the journey UI (see the module docstring)."""
    s = _state(ssq)
    code = s["code"]
    if not code:
        return {"code": "", "label": "Not submitted", "public": "", "holder": s["holder"],
                "assigned_officer": None, "director": s["director"], "days_at_stage": 0}
    label = LABELS[code]
    if code == UNDER_ASSESSMENT and s["officer"] is not None:
        label = f"{label} — {_name(s['officer'])}"
    elif code == AWAITING_DIRECTOR and s["director"] is not None:
        label = f"{label} — {_name(s['director'])}"
    elif code == AWAITING_ASSIGNMENT and s["department"] is not None:
        label = f"{label} — {s['department'].name}"
    public = PUBLIC[code]
    review = s["review"]
    if code == RETURNED and review is not None and review.director_remark:
        public = f"{public}: {review.director_remark}"
    from qa_core import portal_journey
    if code in OPEN_STAGES and s["department"] is not None and portal_journey.show_department_names():
        public = f"{public} — {s['department'].name}"
    return {"code": code, "label": label, "public": public, "holder": s["holder"],
            "assigned_officer": s["officer"], "director": s["director"], "days_at_stage": s.get("days", 0)}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def _lock(ssq):
    from .models import OnboardingSSQ, OnboardingSSQReview
    ssq = OnboardingSSQ.unscoped.select_related("application", "application__institution", "programme").get(
        pk=ssq.pk)
    review = OnboardingSSQReview.objects.select_for_update().filter(ssq=ssq).first()
    if review is None:
        if ssq.status != OnboardingSSQ.SUBMITTED:
            raise ValidationError("This self-study has not been submitted.")
        dept = department_for(ssq.application.institution)
        people = assigners(dept)
        review = OnboardingSSQReview.objects.create(ssq=ssq, department=dept, director=people[0] if people else None,
                                                    stage=AWAITING_ASSIGNMENT, stage_since=ssq.submitted_at
                                                    or timezone.now())
        review = OnboardingSSQReview.objects.select_for_update().get(pk=review.pk)
    return ssq, review


def _open_application(ssq):
    app = ssq.application
    if app.status != "ssq_submitted" or app.institution.status == "approved":
        raise ValidationError("This application is not waiting for self-study review.")


def suggestion(ssq):
    """The rotation's suggestion for this self-study (None without a pool)."""
    from assignments import rotation
    try:
        return rotation.suggest(KIND, subject=ssq, institution=_institution(ssq),
                                department=_department_of_study(ssq), record=False)
    except Exception:
        return None


@transaction.atomic
def assign(ssq, *, by, officer, reason=""):
    """The director gives the self-study to one officer of the department (accepted at once)."""
    from assignments import work
    from assignments.models import WorkAssignment
    ssq, review = _lock(ssq)
    _open_application(ssq)
    if not can_direct(by, ssq, review):
        raise PermissionDenied("Only the director of the handling department (or the ES) assigns this self-study.")
    if review.stage != AWAITING_ASSIGNMENT:
        raise ValidationError("This self-study already has an officer.")
    if officer is None:
        raise ValidationError("Choose an officer.")
    dept = review.department or department_for(ssq.application.institution)
    work.check_assignee(by, dept, officer)
    due = timezone.now() + datetime.timedelta(days=sla_days())
    wa = work.assign(ssq.application.institution, WorkAssignment.SSQ_REVIEW, officer, subject=ssq,
                     department=dept, assigned_by=by, due_at=due, role_on_work="reviewer",
                     meta={"onboarding_ssq": ssq.pk, "reason": (reason or "")[:255]}, accept_now=True,
                     notify_assignee=False, payable=assessment_payable())
    review.officer, review.assigned_by, review.assigned_at = officer, by, timezone.now()
    review.recommendation, review.officer_remark = "", ""
    _move(review, UNDER_ASSESSMENT, by, "assigned", officer=officer.pk, officer_name=_name(officer))
    review.save()
    inst = ssq.application.institution
    from qa_core import timeline
    timeline.record(inst, "onboarding.ssq_assigned", subject=ssq.application, actor=by, visibility="internal",
                    department=dept, internal_text=f"{_name(by)} assigned the self-study for {ssq.programme} to "
                                                   f"{_name(officer)}.")
    _later(lambda: _notify([officer], title=f"Assess: onboarding self-study from {inst.name}",
                           body=f"{ssq.programme}. Assess it and send it to your director. Due "
                                f"{timezone.localtime(due):%d %b %Y}.", url=study_url(ssq), actor=by,
                           kind="assigned"))
    return wa


@transaction.atomic
def send_to_director(ssq, *, officer, recommendation, remark="", override=False, override_reason=""):
    """The assigned officer hands the assessment to the director.

    A ``clear`` recommendation on a self-study that does not reach the bare
    minimum (``onboarding_check``) needs an explicit ``override`` and a
    written reason; both are recorded in the review history and shown to the
    director on the clearance card. A ``return`` never needs it — that does
    not approve anything.
    """
    from assignments import work
    from . import onboarding_check
    ssq, review = _lock(ssq)
    _open_application(ssq)
    if not can_assess(officer, ssq, review):
        raise PermissionDenied("Only the officer assigned to this self-study can send it to the director.")
    if recommendation not in RECOMMENDATIONS:
        raise ValidationError("Recommend clear, or return with notes.")
    remark = (remark or "").strip()
    if recommendation == "return" and len(remark) < 3:
        raise ValidationError("Say what the institution must correct.")
    verdict = onboarding_check.check(ssq)
    if recommendation == "clear" and not verdict["meets"]:
        if not override:
            failing = ", ".join(c["label"] for c in verdict["failed_checks"]) or "warnings on every check"
            raise ValidationError(
                f"{verdict['verdict']} (score {verdict['score']}/100: {failing}). Tick the override box and say "
                "why to send it to the director anyway, or recommend returning it to the institution.")
        override_reason = (override_reason or "").strip()
        if len(override_reason) < onboarding_check.OVERRIDE_MIN_LENGTH:
            raise ValidationError(
                f"Write at least {onboarding_check.OVERRIDE_MIN_LENGTH} characters explaining why you are "
                "sending this self-study to the director below the bare minimum.")
    else:
        override, override_reason = False, ""
    wa = current_assignment(ssq)
    if wa is not None:
        work.submit(wa, officer)
    review.recommendation, review.officer_remark = recommendation, remark[:4000]
    _move(review, AWAITING_DIRECTOR, officer, "assessed", recommendation=recommendation,
          check_score=verdict["score"], check_meets=verdict["meets"],
          **({"override": True, "override_reason": override_reason[:500]} if override else {}))
    review.save()
    people = assigners(review.department)
    inst = ssq.application.institution
    body = f"{_name(officer)} recommends: {RECOMMENDATIONS[recommendation]}."
    if override:
        body += (f" Sent to you below the bare minimum (score {verdict['score']}/100): "
                 f"{override_reason[:300]}")
    elif not verdict["meets"]:
        body += f" Bare-minimum check: {verdict['score']}/100."
    if remark:
        body += f" Notes: {remark[:300]}"
    _later(lambda: _notify(people, title=f"Clear programme: {ssq.programme} ({inst.name})",
                           body=body, url=study_url(ssq), actor=officer, kind="assessed"))
    return review


@transaction.atomic
def return_to_officer(ssq, *, by, remark):
    from assignments import work
    ssq, review = _lock(ssq)
    _open_application(ssq)
    if not can_direct(by, ssq, review):
        raise PermissionDenied("Only the director can send the assessment back.")
    if review.stage != AWAITING_DIRECTOR:
        raise ValidationError("The self-study is not waiting for the director.")
    remark = (remark or "").strip()
    if len(remark) < 3:
        raise ValidationError("Say what the officer should look at again.")
    wa = current_assignment(ssq)
    if wa is not None:
        work.return_to_assignee(wa, by, remark)
    review.director_remark = remark[:4000]
    _move(review, UNDER_ASSESSMENT, by, "returned_to_officer", remark=remark[:500])
    review.save()
    officer = review.officer
    _later(lambda: _notify([officer], title=f"Assessment sent back: {ssq.programme}", body=remark,
                           url=study_url(ssq), actor=by, kind="sent_back"))
    return review


@transaction.atomic
def clear(ssq, *, by, remark=""):
    """The director clears the programme. When it is the last one, the
    institution is ready for approval (the director is told)."""
    from assignments import work
    ssq, review = _lock(ssq)
    _open_application(ssq)
    if not can_direct(by, ssq, review):
        raise PermissionDenied("Only the director of the handling department (or the ES) clears a programme.")
    if review.stage != AWAITING_DIRECTOR:
        raise ValidationError("The officer has not sent this self-study to the director yet.")
    wa = current_assignment(ssq)
    if wa is not None:
        work.complete(wa, by)
    now = timezone.now()
    review.cleared_by, review.cleared_at = by, now
    review.director_remark = (remark or "").strip()[:4000]
    _move(review, CLEARED, by, "cleared")
    review.save()
    app = ssq.application
    inst = app.institution
    from qa_core import timeline
    timeline.record(inst, "onboarding.programme_cleared", subject=app, actor=by, visibility="public",
                    public_text=f"NBTE cleared the self-study for {ssq.programme}.",
                    internal_text=f"{_name(by)} cleared {ssq.programme}.")
    if ready_for_approval(app):
        timeline.record(inst, "onboarding.ready_for_approval", subject=app, actor=by, visibility="internal",
                        internal_text=f"Every programme self-study of {app.reference} is cleared — ready for "
                                      "institution approval.")
        people = approvers(app)
        _later(lambda: _notify(people, title=f"{inst.name} has cleared everything — ready for approval",
                               body="Every programme self-study is cleared. Press “Approve institution”; the ES "
                                    "confirms it.", url=application_url(app), kind="ready"))
    return review


@transaction.atomic
def return_to_institution(ssq, *, by, remark):
    """The director returns one self-study to the institution for correction;
    it opens for editing again and comes back to the same officer."""
    from assignments import work
    from .models import OnboardingSSQ
    ssq, review = _lock(ssq)
    _open_application(ssq)
    if not can_direct(by, ssq, review):
        raise PermissionDenied("Only the director of the handling department (or the ES) returns a self-study.")
    if review.stage != AWAITING_DIRECTOR:
        raise ValidationError("The officer has not sent this self-study to the director yet.")
    remark = (remark or "").strip()
    if len(remark) < 3:
        raise ValidationError("Say what the institution must correct.")
    wa = current_assignment(ssq)
    if wa is not None:
        work.complete(wa, by)
    review.director_remark = remark[:4000]
    _move(review, RETURNED, by, "returned_to_institution", remark=remark[:500], checksum=ssq.checksum,
          submitted_at=ssq.submitted_at.isoformat() if ssq.submitted_at else "")
    review.save()
    # The submitted record is immutable through save(); reopening it is an
    # explicit, recorded state change (the old checksum is kept in the history).
    OnboardingSSQ.unscoped.filter(pk=ssq.pk).update(status=OnboardingSSQ.DRAFT, updated_at=timezone.now())
    inst = ssq.application.institution
    from qa_core import timeline
    timeline.record(inst, "onboarding.ssq_returned", subject=ssq.application, actor=by, visibility="public",
                    public_text=f"NBTE returned the self-study for {ssq.programme} for correction: {remark}",
                    internal_text=f"{_name(by)} returned {ssq.programme} to the institution.")

    def _tell():
        from institutions import services as inst_services
        _notify(inst_services.institution_users(inst), title=f"Correct the self-study for {ssq.programme}",
                body=f"NBTE returned it for correction: {remark}", url=f"/application/ssq/{ssq.pk}/",
                kind="returned")
    _later(_tell)
    return review


def is_returned(ssq):
    review = review_of(ssq)
    return bool(review is not None and review.stage == RETURNED)


@transaction.atomic
def resubmit(ssq, *, user, declaration=False):
    """The Chief Executive resubmits one returned self-study."""
    from institutions import services as inst_services
    from . import onboarding_ssq
    from .models import OnboardingSSQ
    inst_services.require_chief_executive(user, "the corrected self-study")
    ssq = OnboardingSSQ.unscoped.select_for_update().select_related(
        "application", "application__institution", "programme", "template").get(pk=ssq.pk)
    from .models import OnboardingSSQReview
    review = OnboardingSSQReview.objects.select_for_update().filter(ssq=ssq).first()
    if user.institution_id != ssq.application.institution_id:
        raise PermissionDenied("Only this institution can resubmit its self-study.")
    if review is None or review.stage != RETURNED or ssq.status != OnboardingSSQ.DRAFT:
        raise ValidationError("This self-study is not waiting for correction.")
    if not declaration:
        raise ValidationError("Confirm the self-study declaration before submitting.")
    problems = onboarding_ssq.submission_problems(ssq)
    if problems:
        raise ValidationError(f"Complete {len(problems)} required item(s) first.")
    snap = onboarding_ssq.snapshot(ssq)
    OnboardingSSQ.unscoped.filter(pk=ssq.pk).update(
        status=OnboardingSSQ.SUBMITTED, submitted_by=user, submitted_at=timezone.now(), **snap,
        updated_at=timezone.now())
    ssq.refresh_from_db()
    review.round += 1
    officer = review.officer
    dept = review.department
    from assignments import work
    if officer is not None and officer.is_active and (dept is None or work.in_department(officer, dept)):
        from assignments.models import WorkAssignment
        work.assign(ssq.application.institution, WorkAssignment.SSQ_REVIEW, officer, subject=ssq, department=dept,
                    assigned_by=review.assigned_by, due_at=timezone.now() + datetime.timedelta(days=sla_days()),
                    role_on_work="reviewer", meta={"onboarding_ssq": ssq.pk, "round": review.round},
                    accept_now=True, notify_assignee=False, payable=assessment_payable())
        review.recommendation = ""
        _move(review, UNDER_ASSESSMENT, user, "resubmitted")
        review.save()
        _later(lambda: _notify([officer], title=f"Corrected self-study: {ssq.programme}",
                               body=f"{ssq.application.institution.name} resubmitted it. Assess it again.",
                               url=study_url(ssq), kind="resubmitted"))
    else:
        review.officer = None
        _move(review, AWAITING_ASSIGNMENT, user, "resubmitted")
        review.save()
        people = assigners(dept)
        _later(lambda: _notify(people, title=f"Assign an officer: corrected self-study ({ssq.programme})",
                               body="The institution resubmitted it.", url=study_url(ssq), kind="resubmitted"))
    from qa_core import timeline
    timeline.record(ssq.application.institution, "onboarding.ssq_resubmitted", subject=ssq.application, actor=user,
                    visibility="public", public_text=f"The corrected self-study for {ssq.programme} was submitted.")
    return ssq


# ---------------------------------------------------------------------------
# Ready for approval → Director approves → ES confirms
# ---------------------------------------------------------------------------
def _studies(app):
    from .models import OnboardingSSQ
    return list(OnboardingSSQ.unscoped.filter(application=app).select_related("programme").order_by(
        "programme__name"))


def cleared_count(app):
    from .models import OnboardingSSQReview
    return OnboardingSSQReview.objects.filter(ssq__application=app, stage=CLEARED).count()


def ready_for_approval(app):
    """Every programme self-study of the application is cleared (and the
    institution is not approved yet)."""
    from .models import OnboardingSSQ, OnboardingSSQReview
    from .onboarding_ssq import cleared_reviews
    inst = app.institution
    if app.status != "ssq_submitted" or inst.status not in ("proposed", "under_review"):
        return False
    try:
        expected = {r.programme_id for r in cleared_reviews(app)}
    except ValidationError:
        return False
    cleared = set(OnboardingSSQReview.objects.filter(
        ssq__application=app, ssq__status=OnboardingSSQ.SUBMITTED, stage=CLEARED).values_list(
        "ssq__programme_id", flat=True))
    return bool(expected) and expected <= cleared


def onboarding_application(inst):
    """The institution's live onboarding application (newest, not withdrawn), or
    ``None`` for a legacy/imported institution that never applied online."""
    from .models import Application
    return (Application.unscoped.filter(institution=inst).exclude(status="withdrawn")
            .order_by("-created_at", "-id").first())


def approval_blocker(inst):
    """Why the institution cannot be approved yet, or ``""`` when it can.

    Same rule as the "Approve institution" button: an institution that came in
    through online onboarding needs every programme self-study submitted and
    cleared by NBTE. Institutions with no onboarding application (legacy /
    imported / added through Add TVET) are not blocked here."""
    from .models import OnboardingSSQ, OnboardingSSQReview
    from .onboarding_ssq import cleared_reviews
    from . import onboarding_v3
    app = onboarding_application(inst)
    if app is None:
        return ""
    has_studies = OnboardingSSQ.unscoped.filter(application=app).exists()
    if not (onboarding_v3.is_v3(app) or has_studies):
        return ""
    try:
        expected = {r.programme_id for r in cleared_reviews(app)}
    except ValidationError:
        expected = set()
    if not expected:
        return (f"{app.reference}: NBTE has not cleared the programme list yet, so no self-study can be "
                "cleared. Approve the institution once every programme self-study is cleared.")
    submitted = set(OnboardingSSQ.unscoped.filter(application=app, status=OnboardingSSQ.SUBMITTED)
                    .values_list("programme_id", flat=True))
    cleared = set(OnboardingSSQReview.objects.filter(
        ssq__application=app, ssq__status=OnboardingSSQ.SUBMITTED, stage=CLEARED).values_list(
        "ssq__programme_id", flat=True))
    done = len(expected & cleared)
    if app.status != "ssq_submitted" or not expected <= submitted:
        return (f"{app.reference}: not every programme self-study has been submitted yet "
                f"({len(expected & submitted)} of {len(expected)} submitted, {done} cleared). Every "
                "programme self-study must be submitted and cleared before the institution can be approved.")
    if not expected <= cleared:
        return (f"{app.reference}: only {done} of {len(expected)} programme self-studies are cleared. Every "
                "programme self-study must be cleared before the institution can be approved.")
    return ""


def pending_approval(inst):
    from django.contrib.contenttypes.models import ContentType
    from qa_core.models import PendingAction
    ct = ContentType.objects.get_for_model(type(inst))
    return (PendingAction.objects.filter(policy_id=APPROVE_POLICY, content_type=ct, object_id=str(inst.pk),
                                         status=PendingAction.PENDING)
            .select_related("initiated_by", "policy").order_by("-initiated_at").first())


def approvers(app):
    """Who presses "Approve institution": the handling department's director
    (heads when the seat is empty)."""
    import org
    dept = department_for(app.institution)
    d = org.director_of(dept) if dept is not None else None
    if d is not None:
        return [d]
    return list(org.heads_of(dept)[:3]) if dept is not None else []


def can_approve(user, app):
    if not _staff(user) or _is_top(user):
        # The ES confirms; the ES starting it would leave nobody to confirm.
        return False
    return user.pk in {u.pk for u in approvers(app)}


def approve_institution(app, *, by, remark=""):
    """The handling director initiates ``institutions.approve_institution`` directly."""
    from qa_core import actions
    from .models import Application
    app = Application.unscoped.select_related("institution").get(pk=app.pk)
    if not can_approve(by, app):
        raise PermissionDenied("Only the director of the handling department approves this institution.")
    if not ready_for_approval(app):
        raise ValidationError("Every programme self-study must be cleared first.")
    inst = app.institution
    if pending_approval(inst) is not None:
        raise ValidationError("The approval is already waiting for the ES.")
    reason = (remark or "").strip() or f"Every programme self-study of {app.reference} is cleared."
    pa = actions.initiate(APPROVE_POLICY, actor=by, target=inst, payload={"reason": reason}, reason=reason)
    if pa.status == pa.PENDING:
        es = _es_users()
        _notify(es, title=f"Confirm institution approval: {inst.name}",
                body=f"{_name(by)} approved {inst.name} after every programme self-study was cleared. Confirm or "
                     "reject.", url="/qa/", actor=by, kind="confirm")
    return pa


def confirm_approval(pa, *, by, remark=""):
    from qa_core import actions
    _check_pa(pa)
    return actions.confirm(pa, by, remark=remark)


def reject_approval(pa, *, by, remark):
    from qa_core import actions
    _check_pa(pa)
    pa = actions.reject(pa, by, remark=remark)
    app = _application_of(pa)
    if app is not None:
        people = approvers(app)
        _notify(people, title=f"Approval rejected: {app.institution.name}", body=remark,
                url=application_url(app), actor=by, kind="rejected")
    return pa


def _check_pa(pa):
    if pa is None or pa.policy_id != APPROVE_POLICY:
        raise ValidationError("This is not an institution approval.")


def _application_of(pa):
    from .models import Application
    from institutions.models import Institution
    inst = Institution.unscoped.filter(pk=pa.object_id).first()
    if inst is None:
        return None
    return (Application.unscoped.filter(institution=inst).exclude(status="withdrawn")
            .order_by("-created_at", "-id").first())


# ---------------------------------------------------------------------------
# Approved → the formal QA request starts on its own
# ---------------------------------------------------------------------------
def on_institution_approved(inst, actor=None):
    """Called by ``ApproveInstitutionHandler.apply``. Never blocks the approval:
    a failure is logged on ``OnboardingQAStart.error``."""
    from .models import Application, OnboardingQAStart
    app = (Application.unscoped.filter(institution=inst, status="ssq_submitted")
           .order_by("-created_at", "-id").first())
    if app is None:
        return None
    from .models import OnboardingSSQReview
    if not OnboardingSSQReview.objects.filter(ssq__application=app, stage=CLEARED).exists():
        return None  # approved outside the linear path (e.g. legacy): nothing was cleared here
    if OnboardingQAStart.objects.filter(application=app, qa_request__isnull=False).exists():
        return None
    try:
        with transaction.atomic():
            return start_formal_qa(app, actor=actor)
    except Exception as exc:  # the approval stands; NBTE can start the request by hand
        logger.exception("Could not start the formal QA request for %s", app.reference)
        msg = " ".join(getattr(exc, "messages", [str(exc)]))[:2000]
        OnboardingQAStart.objects.create(application=app, started_by=actor if getattr(actor, "pk", None) else None,
                                         error=msg or type(exc).__name__)
        return None


def start_formal_qa(app, *, actor=None):
    """Create, submit and move the QA request for the cleared programmes."""
    from django.contrib.contenttypes.models import ContentType

    from institutions.models import Institution, InstitutionRequest, InstitutionRequestType
    from qa_core import flow, references, timeline

    from . import qa_services
    from .models import OnboardingQAStart, OnboardingSSQReview, QARequest, QARequestProgramme, QAType

    inst = Institution.unscoped.get(pk=app.institution_id)
    code = auto_request_type_code()
    if code.lower() in ("none", "off", "-"):
        return None
    qa_type = QAType.objects.filter(code=code).first()
    if qa_type is None:
        raise ValidationError(f"QA type '{code}' (qa.onboarding.auto_request_type) does not exist.")
    if not qa_type.is_active:
        raise ValidationError(f"{qa_type.name} is switched off (Settings → QA types).")
    # No published template is needed: every programme here carries its
    # onboarding self-study over (a programme added later needs the template).
    if inst.status != Institution.APPROVED:
        raise ValidationError("The institution is not approved.")
    reviews = list(OnboardingSSQReview.objects.filter(ssq__application=app, stage=CLEARED)
                   .select_related("ssq__programme", "cleared_by"))
    rows = []
    for review in reviews:
        programme = review.ssq.programme
        if programme.status == "dropped" or programme.is_institutional_admin:
            continue
        busy = QARequestProgramme.unscoped.filter(programme=programme, status=QARequestProgramme.ACTIVE,
                                                  qa_request__status__in=qa_services.OPEN_STATUSES).exists()
        if not busy:
            rows.append((programme, review))
    if not rows:
        raise ValidationError("No cleared programme is free for a QA request.")
    # Onboarding already vetted these programmes: the type's programme-status
    # rule (e.g. Accreditation needs approval to commence) does not hold back
    # the automatic request.
    _accepting, cycle = qa_services.cycle_for(qa_type)
    req = QARequest.unscoped.create(reference=references.issue("qa_request", inst=inst.code), institution=inst,
                                    qa_type=qa_type, cycle=cycle, status="draft", created_by=None)
    for programme, review in rows:
        new = programme.status == "proposed"
        QARequestProgramme.unscoped.create(
            qa_request=req, programme=programme, is_new_programme=new,
            clearance_status="cleared" if new else "", cleared_by=review.cleared_by if new else None,
            cleared_at=review.cleared_at if new else None,
            clearance_remark="Cleared through the onboarding self-study." if new else "",
            requested_streams=qa_services.default_streams(programme, qa_type))
    container_type = InstitutionRequestType.objects.filter(code="qa").first()
    if container_type is not None:
        container = InstitutionRequest.unscoped.create(
            institution=inst, request_type=container_type, reference=req.reference, status="draft",
            title=f"{qa_type.name} — {len(rows)} programme(s)",
            content_type=ContentType.objects.get_for_model(QARequest), object_id=str(req.pk))
        req.institution_request = container
        req.save(update_fields=["institution_request", "updated_at"])
    start = OnboardingQAStart.objects.create(application=app, qa_request=req, fee_waived=auto_fee_waived(),
                                             started_by=actor if getattr(actor, "pk", None) else None)
    flow.start(req.flow_code, req, actor=None)
    qa_services._refresh_joint_qa(req)
    timeline.record(inst, "qa_request.created", subject=req, visibility="public",
                    public_text=f"NBTE started {qa_type.name} request {req.reference} for "
                                f"{', '.join(str(p) for p, _r in rows)} from your onboarding self-studies.")
    req.declaration_accepted = True  # the Chief Executive declared the self-studies at onboarding
    req.save(update_fields=["declaration_accepted", "updated_at"])
    instance = qa_services.request_instance(req)
    flow.transition(instance, "submit", None)
    flow.transition(instance, "route_to_fee", None)
    req.refresh_from_db()
    if start.fee_waived and req.status == "fee_due":
        flow.transition(qa_services.request_instance(req), "payment_confirmed", None,
                        remark="Fee waived (qa.onboarding.auto_request_fee_waived).")
        req.refresh_from_db()
    qa_services.advance_request_if_complete(req, None)
    req.refresh_from_db()

    def _tell():
        from institutions import services as inst_services
        _notify(inst_services.institution_users(inst), title=f"{qa_type.name} request {req.reference} started",
                body=("NBTE approved your institution and started the request for your cleared programmes. "
                      "Your onboarding self-studies are used — you do not fill them again.")
                + ("" if start.fee_waived else " Pay the QA fee when the bill appears."),
                url=f"/qa-requests/{req.pk}/", kind="qa_started")
    _later(_tell)
    return req


def fee_waived(req):
    """The automatic request whose fee NBTE waived (``qa_services.issue_fee`` skips the bill)."""
    from .models import OnboardingQAStart
    return OnboardingQAStart.objects.filter(qa_request_id=req.pk, fee_waived=True).exists()


def _source_study(programme):
    """The cleared onboarding self-study of ``programme`` not yet carried into an SSQ."""
    from .models import SSQ, OnboardingSSQ, OnboardingSSQReview
    review = (OnboardingSSQReview.objects.filter(ssq__programme=programme, ssq__status=OnboardingSSQ.SUBMITTED,
                                                 stage=CLEARED)
              .select_related("ssq", "ssq__template", "cleared_by").order_by("-cleared_at").first())
    if review is None:
        return None
    used = SSQ.unscoped.filter(meta__carried_over__onboarding_ssq=review.ssq_id).exclude(
        qa_request_programme__qa_request__status="cancelled").exists()
    return None if used else review


def carry_over(row, *, actor=None):
    """Use the cleared onboarding self-study as the accepted SSQ of ``row``
    (``qa_services.on_open_ssqs``). Returns the SSQ, or None when the programme
    has no unused cleared self-study (it then gets a normal, empty SSQ)."""
    from qa_core import flow, timeline

    from . import qa_services
    from .models import SSQ, Evidence, Submission
    from institutions.models import InstitutionDocument
    review = _source_study(row.programme)
    if review is None:
        return None
    study = review.ssq
    now = timezone.now()
    ssq = SSQ.unscoped.create(
        qa_request_programme=row, template=study.template, data=dict(study.submitted_data or {}),
        status="draft", version=1, submitted_by=study.submitted_by, submitted_at=study.submitted_at,
        reviewed_by=review.cleared_by, reviewed_at=review.cleared_at or now,
        review_remark="Carried over from the cleared onboarding self-study.",
        meta={"prefilled": [], "edited": [], "sections": {},
              "carried_over": {"onboarding_ssq": study.pk, "application": study.application.reference,
                               "checksum": study.checksum, "at": now.isoformat()}})
    for item in study.evidence_snapshot or []:
        doc = InstitutionDocument.unscoped.filter(pk=item.get("document_id")).select_related("kind").first()
        if doc is not None:
            Evidence.unscoped.create(ssq=ssq, field_key=item.get("field", ""), document_kind=doc.kind,
                                     reused_from=doc, status="accepted", uploaded_by=None)
    Submission.unscoped.create(
        ssq=ssq, version=1, template_version=study.template.version, data_snapshot=dict(study.submitted_data or {}),
        rendered_snapshot=study.rendered_snapshot or [], evidence_snapshot=study.evidence_snapshot or [],
        submitted_by=study.submitted_by, submitted_at=study.submitted_at or now)
    instance = flow.start(qa_services.SSQ_FLOW, ssq, actor=None)
    _carry_stage(instance, ssq, actor)
    timeline.record(row.qa_request.institution, "ssq.carried_over", subject=ssq, visibility="public",
                    public_text=f"The onboarding self-study for {row.programme} is used as its SSQ "
                                f"({row.qa_request.reference}) — nothing to fill again.")
    ssq.refresh_from_db()
    return ssq


def _carry_stage(instance, ssq, actor):
    """Put the carried SSQ straight at "accepted" (a recorded system move)."""
    from qa_core import flow
    from qa_core.models import FlowEvent, FlowInstance
    instance = FlowInstance.objects.select_for_update().get(pk=instance.pk)
    before = ",".join(instance.current_stages)
    instance.current_stages = ["accepted"]
    instance.save(update_fields=["current_stages", "updated_at"])
    FlowEvent.objects.create(instance=instance, from_stage=before, to_stage="accepted", action_code="carry_over",
                             actor=None, actor_role_label="System",
                             remark="Carried over from the cleared onboarding self-study.",
                             metadata={"automatic": True, "carried_over": True})
    flow._sync_status(instance, ssq)


def after_ssqs_opened(req):
    """``on_open_ssqs`` ran inside a transition: once it commits, move the
    request on if every SSQ is already in (all carried over)."""
    from . import qa_services

    def _go():
        try:
            with transaction.atomic():
                qa_services.advance_request_if_complete(req, None)
        except Exception:  # pragma: no cover
            logger.exception("Could not advance %s after carrying SSQs over", req.reference)
    _later(_go)


def covered_programme_ids(inst):
    """Programmes whose cleared onboarding self-study will stand in for their SSQ."""
    from .models import OnboardingSSQ, OnboardingSSQReview
    ids = set(OnboardingSSQReview.objects.filter(ssq__application__institution=inst, stage=CLEARED,
                                                 ssq__status=OnboardingSSQ.SUBMITTED)
              .values_list("ssq__programme_id", flat=True))
    return {pk for pk in ids if _source_study_pk(pk)}


def _source_study_pk(programme_id):
    from institutions.models import Programme
    p = Programme.unscoped.filter(pk=programme_id).first()
    return p is not None and _source_study(p) is not None


# ---------------------------------------------------------------------------
# Next actions (staff dashboards)
# ---------------------------------------------------------------------------
def _open_studies():
    from .models import OnboardingSSQ
    return (OnboardingSSQ.unscoped.filter(application__status__in=("ssq_submitted",),
                                          application__institution__status__in=("proposed", "under_review"))
            .exclude(status=OnboardingSSQ.DRAFT, linear_review__isnull=True)
            .select_related("application", "application__institution",
                            "application__institution__handling_department", "programme"))


def next_actions(user):
    """[{title, detail, url, button_label, institution, due, overdue, kind, method,
    post_url, reject_url, stage, ssq_id, application_id, pa_id}] — the onboarding
    steps waiting for ``user``, most urgent first."""
    if not _staff(user):
        return []
    from django.urls import reverse
    now = timezone.now()
    out = []
    top = _is_top(user)
    apps = {}
    for study in _open_studies():
        review = review_of(study)
        s = _state(study, review, now=now)
        code = s["code"]
        if code in ("", RETURNED, APPROVED):
            continue
        inst = study.application.institution
        apps.setdefault(study.application_id, study.application)
        base = {"institution": inst, "due": s.get("due"), "overdue": s.get("overdue", False), "stage": code,
                "ssq_id": study.pk, "application_id": study.application_id, "method": "get", "post_url": "",
                "reject_url": "", "pa_id": None, "url": study_url(study), "since": s.get("since")}
        if code == UNDER_ASSESSMENT:
            if review is not None and review.officer_id == user.pk:
                out.append({**base, "kind": "assess", "title": f"Assess the self-study — {study.programme}",
                            "detail": f"{inst.name} · onboarding programme self-study",
                            "button_label": "Assess"})
            continue
        if code == CLEARED:
            continue
        # Assign / clear: the people who direct (not the ES, unless the seat sends it to them).
        people = assigners(s["department"])
        mine = user.pk in {u.pk for u in people} or (
            not top and s["department"] is not None and _is_director_of(user, s["department"]))
        if not mine:
            continue
        if code == AWAITING_ASSIGNMENT:
            out.append({**base, "kind": "assign", "title": f"Assign an officer — {study.programme}",
                        "detail": f"{inst.name} · onboarding self-study waiting {s['days']} day(s)",
                        "button_label": "Assign officer"})
        elif code == AWAITING_DIRECTOR:
            rec = RECOMMENDATIONS.get(review.recommendation, "") if review is not None else ""
            out.append({**base, "kind": "clear", "title": f"Clear programme — {study.programme}",
                        "detail": f"{inst.name} · {_name(s['officer']) or 'The officer'} recommends: "
                                  f"{rec.lower() or 'see notes'}",
                        "button_label": "Review and clear"})
    # Ready for approval (handling director)
    from .models import Application
    for app in Application.unscoped.filter(status="ssq_submitted",
                                           institution__status__in=("proposed", "under_review")) \
            .select_related("institution"):
        if not can_approve(user, app) or not ready_for_approval(app) or pending_approval(app.institution):
            continue
        since = _ready_since(app)
        due = since + datetime.timedelta(days=sla_days()) if since else None
        out.append({"kind": "approve", "title": f"{app.institution.name} has cleared everything — ready for approval",
                    "detail": f"Every programme self-study of {app.reference} is cleared.",
                    "url": application_url(app), "button_label": "Approve institution", "method": "post",
                    "post_url": reverse("qa_app:onboarding_approve", args=[app.pk]), "reject_url": "",
                    "institution": app.institution, "due": due, "overdue": bool(due and due <= now),
                    "stage": "ready_for_approval", "ssq_id": None, "application_id": app.pk, "pa_id": None,
                    "since": since})
    # ES: confirm the approval
    from qa_core import actions
    from qa_core.models import PendingAction
    for pa in PendingAction.objects.filter(policy_id=APPROVE_POLICY, status=PendingAction.PENDING) \
            .select_related("policy", "initiated_by"):
        if not actions.can_decide(pa, user)[0]:
            continue
        app = _application_of(pa)
        if app is None or not top:
            continue
        due = pa.initiated_at + datetime.timedelta(days=sla_days()) if pa.initiated_at else None
        out.append({"kind": "confirm", "title": f"Confirm institution approval — {app.institution.name}",
                    "detail": f"{_name(pa.initiated_by)} approved it after every programme self-study was cleared.",
                    "url": application_url(app), "button_label": "Confirm", "method": "post",
                    "post_url": reverse("qa_app:onboarding_confirm", args=[pa.pk]),
                    "reject_url": reverse("qa_app:onboarding_reject", args=[pa.pk]),
                    "institution": app.institution, "due": due, "overdue": bool(due and due <= now),
                    "stage": "approval_pending", "ssq_id": None, "application_id": app.pk, "pa_id": pa.pk,
                    "since": pa.initiated_at})
    order = {"confirm": 0, "approve": 1, "clear": 2, "assess": 3, "assign": 4}
    far = now + datetime.timedelta(days=3650)
    out.sort(key=lambda a: (not a["overdue"], order.get(a["kind"], 9), a["due"] or far))
    return out


def _ready_since(app):
    from .models import OnboardingSSQReview
    last = OnboardingSSQReview.objects.filter(ssq__application=app, stage=CLEARED).order_by("-cleared_at").first()
    return last.cleared_at if last is not None else None


def approval_card(user, app):
    """The application-page card for ``user`` (None when nothing is theirs)."""
    for a in next_actions(user):
        if a["application_id"] == app.pk and a["kind"] in ("approve", "confirm"):
            return a
    return None


def application_summary(app):
    """Counts per stage for the application page."""
    rows = []
    for study in _studies(app):
        rows.append({"study": study, "stage": review_stage(study)})
    counts = {}
    for r in rows:
        counts[r["stage"]["code"]] = counts.get(r["stage"]["code"], 0) + 1
    return {"rows": rows, "counts": counts, "total": len(rows), "cleared": counts.get(CLEARED, 0)
            + counts.get(APPROVED, 0), "ready": ready_for_approval(app)}


# ---------------------------------------------------------------------------
# Rotation queue
# ---------------------------------------------------------------------------
def waiting_for_assignment(dept_ids=None, pk=None):
    """Submitted self-studies waiting for an officer (rotation queue)."""
    from .models import OnboardingSSQ
    qs = (OnboardingSSQ.unscoped.filter(status=OnboardingSSQ.SUBMITTED, application__status="ssq_submitted",
                                        application__institution__status__in=("proposed", "under_review"))
          .exclude(linear_review__stage__in=(UNDER_ASSESSMENT, AWAITING_DIRECTOR, CLEARED, RETURNED))
          .select_related("application", "application__institution", "programme", "linear_review",
                          "application__institution__handling_department")
          .order_by("submitted_at", "pk"))
    if pk is not None:
        qs = qs.filter(pk=pk)
    out = []
    for study in qs[:200]:
        dept = _department_of_study(study, getattr(study, "linear_review", None))
        if dept is None or (dept_ids is not None and dept.pk not in dept_ids):
            continue
        out.append((study, dept))
    return out


# ---------------------------------------------------------------------------
# SLA reminders and escalation
# ---------------------------------------------------------------------------
def send_stage_reminders(now=None):
    """Remind whoever holds an open self-study once it is past
    ``qa.onboarding.stage_sla_days``; after two periods tell the director
    (the ES when the director holds it), after three the ES. Each level is
    sent once per stage. Returns the number of reminders sent."""
    from .models import OnboardingSSQReview
    now = now or timezone.now()
    sla = datetime.timedelta(days=sla_days())
    sent = 0
    es = _es_users()
    for review in (OnboardingSSQReview.objects.filter(stage__in=OPEN_STAGES,
                                                      ssq__application__status="ssq_submitted")
                   .select_related("ssq", "ssq__application__institution", "ssq__programme", "officer",
                                   "department")):
        age = now - review.stage_since
        if age < sla:
            continue
        level = min(3, int(age / sla))
        done = review.reminder or {}
        if done.get("stage") == review.stage and int(done.get("level", 0)) >= level:
            continue
        ssq = review.ssq
        s = _state(ssq, review, now=now)
        holder = s["holder_user"]
        directors = assigners(review.department)
        inst = ssq.application.institution
        days = age.days
        what = {AWAITING_ASSIGNMENT: "assign an officer", UNDER_ASSESSMENT: "finish the assessment and send it "
                                                                             "to the director",
                AWAITING_DIRECTOR: "clear it or return it"}[review.stage]
        body = f"{ssq.programme} ({inst.name}) — {LABELS[review.stage].lower()} for {days} day(s). Please {what}."
        to = [holder] if holder is not None else list(directors)
        if level >= 2:
            if review.stage == UNDER_ASSESSMENT:
                to += directors
            else:
                to += es
        if level >= 3:
            to += es
        title = ("Overdue: onboarding self-study" if level == 1 else
                 "Escalated: onboarding self-study overdue") + f" — {inst.name}"
        _notify(to, title=title, body=body, url=study_url(ssq), kind="reminder")
        review.reminder = {"stage": review.stage, "level": level, "at": now.isoformat()}
        _history(review, "reminder", None, level=level)
        review.save(update_fields=["reminder", "history", "updated_at"])
        sent += 1
    return sent
