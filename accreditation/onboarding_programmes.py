"""Onboarding programme submission and programme clearance (Phase 0: Q1, Q2).

    programme_selection --submit_programmes (Chief Executive, X12)--> programme_clearance
    programme_clearance: NTIRD (or the setting's department) clears or returns
        each programme (one OnboardingProgrammeReview per programme per round)
      all cleared  --clear_programmes (effect: programme_processing bill)--> programme_fee_due
      any returned --return--> programme_selection (only the returned programmes
                                reopen; cleared programmes stay locked)
    programme_fee_due --payment effects--> programme_fee_confirmed (accreditation.services)

The web views (portal and staff) and the API call these functions; the rules
live here.
"""
# scoped-by-caller
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config
from institutions import services as inst_services
from institutions.models import Programme
from qa_core import flow, timeline

from .models import Application, OnboardingProgrammeReview

FLOW_CODE = "onboarding"  # the flow these actions fire (also read by the dead-end test)
SETTING_DEPARTMENT = "qa.onboarding.programme_clearance_department"
CLEAR_PERM = "accreditation.clear_qa_programmes"
EXPECTED_TIME = "2–4 weeks"  # NBTE Service Charter (programme clearance)


def _instance(app):
    from .services import instance_of

    return instance_of(app)


# ---------------------------------------------------------------------------
# Who clears
# ---------------------------------------------------------------------------
def clearance_department(app):
    """The department that clears onboarding programmes for ``app``.

    ``qa.onboarding.programme_clearance_department``: a department code
    (default ``ntird``) or ``category`` (the institution category's routing
    department, falling back to NTIRD)."""
    from qa_core.departments import qa_department

    code = str(config.get(SETTING_DEPARTMENT, "ntird") or "ntird").strip()
    inst = app.institution
    if code == "category":
        dept = getattr(getattr(inst, "category", None), "routing_department", None)
        return dept or inst_services.proposed_department()
    return qa_department(code) or inst_services.proposed_department()


def can_clear(app, user):
    """(allowed, reason) for clearing or returning programmes of ``app``."""
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return False, "Only NBTE staff clear programmes."
    if not (user.is_superuser or user.has_perm(CLEAR_PERM)):
        return False, "You do not hold accreditation.clear_qa_programmes."
    dept = clearance_department(app)
    if not (user.is_superuser or user.has_perm("qa_core.confirm_any_department")) \
            and (dept is None or user.department_id != dept.pk):
        return False, f"Programme clearance belongs to {getattr(dept, 'name', 'another department')}."
    if app.status != "programme_clearance":
        return False, "The application is not waiting for programme clearance."
    from assignments import access
    if access.assignment_mode() and not access.can_see(user, app.institution, "summary"):
        return False, "You are not assigned to this institution."  # X4: server-side, forged POSTs included
    return True, ""


def clearers_for(app):
    """Who hears that programmes wait. Need-to-know (X1/X4): the clearing
    department's director plus anyone already working on the institution;
    department mode: every holder of the clear permission in the department."""
    dept = clearance_department(app)
    from assignments import access
    if not access.assignment_mode():
        return [u for u in inst_services.staff_in_department(dept) if u.has_perm(CLEAR_PERM)]
    import org
    out = [u for u in (org.director_of(dept),) if u is not None] if dept is not None else []
    from assignments.models import WorkAssignment
    for wa in (WorkAssignment.objects.filter(institution=app.institution, department=dept,
                                             status__in=WorkAssignment.WORKING).select_related("assignee")):
        if wa.assignee not in out and wa.assignee.has_perm(CLEAR_PERM):
            out.append(wa.assignee)
    return out


# ---------------------------------------------------------------------------
# Programme list and its state
# ---------------------------------------------------------------------------
def programmes_of(app):
    return list(Programme.unscoped.filter(institution=app.institution).exclude(status="dropped")
                .select_related("level", "category").order_by("name"))


def latest_reviews(app):
    """{programme_id: newest OnboardingProgrammeReview}."""
    out = {}
    for r in (OnboardingProgrammeReview.unscoped.filter(application=app)
              .exclude(status=OnboardingProgrammeReview.SUPERSEDED).select_related("decided_by")
              .order_by("programme_id", "-round", "-id")):
        out.setdefault(r.programme_id, r)
    return out


def is_locked(app, programme, reviews=None):
    """A programme NBTE cleared stays locked when the list is reopened."""
    reviews = latest_reviews(app) if reviews is None else reviews
    review = reviews.get(programme.pk)
    return review is not None and review.status == OnboardingProgrammeReview.CLEARED


def submission_blockers(app):
    """Why the programme list cannot be submitted now ([] = it can)."""
    if app is None or app.status != "programme_selection":
        return ["Programme selection is not open."]
    programmes = programmes_of(app)
    if not programmes:
        return ["Add at least one programme."]
    out = []
    for p in programmes:
        if not p.requested_streams or p.requested_streams < 1:
            out.append(f"Set the number of streams for {p.name}.")
    if inst_services.active_hold(app):
        out.append("The application is on hold.")
    return out


def summary(app, user=None):
    """What the portal shows about programme submission (bar + sheet)."""
    programmes = programmes_of(app) if app is not None else []
    reviews = latest_reviews(app) if app is not None else {}
    rows = []
    for p in programmes:
        r = reviews.get(p.pk)
        rows.append({"programme": p, "review": r, "locked": r is not None and r.status == "cleared",
                     "state": r.status if r is not None else "draft"})
    blockers = submission_blockers(app) if app is not None and app.status == "programme_selection" else []
    is_ce = inst_services.is_chief_executive(user) if user is not None else False
    return {"rows": rows, "blockers": blockers, "is_ce": is_ce,
            "can_submit": bool(app is not None and app.status == "programme_selection" and not blockers and is_ce),
            "count": len(programmes), "streams": sum(max(1, p.requested_streams or 1) for p in programmes),
            "department": clearance_department(app) if app is not None else None,
            "returned": [r for r in rows if r["state"] == "returned"],
            "pending": [r for r in rows if r["state"] == "pending"],
            "cleared": [r for r in rows if r["state"] == "cleared"],
            "expected_time": EXPECTED_TIME}


# ---------------------------------------------------------------------------
# Q1: the Chief Executive submits the list
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_programmes(app, *, user, impersonating=False):
    if impersonating:
        raise PermissionDenied("You cannot submit while viewing as the institution.")
    inst_services.require_chief_executive(user, "the programme list")  # X12
    app = Application.unscoped.select_for_update().select_related("institution").get(pk=app.pk)
    if getattr(user, "institution_id", None) != app.institution_id:
        raise PermissionDenied("Only the institution submits its programmes.")
    blockers = submission_blockers(app)
    if blockers:
        raise ValidationError(blockers)
    dept = clearance_department(app)
    if dept is None:
        raise ValidationError("The department that clears programmes does not exist. Contact NBTE.")
    reviews = latest_reviews(app)
    programmes = programmes_of(app)
    app.programme_round = (app.programme_round or 0) + 1
    app.programmes_submitted_at = timezone.now()
    app.programmes_submitted_by = user
    app.save(update_fields=["programme_round", "programmes_submitted_at", "programmes_submitted_by", "updated_at"])
    sent = []
    for p in programmes:
        if is_locked(app, p, reviews):
            continue
        OnboardingProgrammeReview.unscoped.create(application=app, programme=p, department=dept,
                                                  round=app.programme_round)
        sent.append(p)
    flow.transition(_instance(app), "submit_programmes", user)
    app.refresh_from_db()
    if not sent:  # every programme left was cleared earlier: go straight to the fee
        _maybe_finish(app, user)
        app.refresh_from_db()
        return app
    names = ", ".join(p.name for p in sent)
    timeline.record(app.institution, "application.programmes_submitted", subject=app, actor=user,
                    visibility="public",
                    public_text=f"Programmes submitted to NBTE for clearance: {names}. Expected time: "
                                f"{EXPECTED_TIME}.",
                    internal_text=f"Programme clearance round {app.programme_round} with {dept.name}.")
    staff = clearers_for(app)
    ctx = {"reference": app.reference, "institution": app.institution.name, "count": len(sent),
           "department": dept.name, "expected_time": EXPECTED_TIME}

    def _send():
        inst_services.notify_role("onboarding.programmes_submitted", ctx, staff, url="/qa/applications/programmes/")
        inst_services.notify_institution(app.institution, "onboarding.programmes_submitted_institution", ctx,
                                         url="/application/")
    transaction.on_commit(_send)
    return app


# ---------------------------------------------------------------------------
# Q2: programme clearance (per programme)
# ---------------------------------------------------------------------------
@transaction.atomic
def decide(review, *, user, decision, remark="", impersonating=False):
    if impersonating:
        raise PermissionDenied("You cannot decide while impersonating.")
    review = (OnboardingProgrammeReview.unscoped.select_for_update()
              .select_related("application", "application__institution", "programme").get(pk=review.pk))
    app = Application.unscoped.select_for_update().select_related("institution").get(pk=review.application_id)
    ok, why = can_clear(app, user)
    if not ok:
        raise PermissionDenied(why)
    if review.status != OnboardingProgrammeReview.PENDING or review.round != app.programme_round:
        raise ValidationError(f"{review.programme.name} is already {review.get_status_display().lower()}.")
    remark = (remark or "").strip()
    if decision == "clear":
        review.status = OnboardingProgrammeReview.CLEARED
    elif decision == "return":
        if len(remark) < 3:
            raise ValidationError("Write the remark the institution will see.")
        review.status = OnboardingProgrammeReview.RETURNED
    else:
        raise ValidationError("Choose clear or return.")
    review.remark, review.decided_by, review.decided_at = remark, user, timezone.now()
    review.save(update_fields=["status", "remark", "decided_by", "decided_at", "updated_at"])
    inst = app.institution
    label = f"{review.programme.name} ({review.programme.level.name})" if review.programme.level_id \
        else review.programme.name
    if decision == "clear":
        public = f"NBTE cleared {label}."
    else:
        public = f"NBTE returned {label}: {remark}"
    timeline.record(inst, f"application.programme_{'cleared' if decision == 'clear' else 'returned'}",
                    subject=app, actor=user, visibility="public", public_text=public)
    transaction.on_commit(lambda: inst_services.notify_institution(
        inst, "onboarding.programme_decided",
        {"reference": app.reference, "programme": label, "decision": "cleared" if decision == "clear" else "returned",
         "remark": remark}, url="/application/"))
    _maybe_finish(app, user)
    app.refresh_from_db()
    return review


def _maybe_finish(app, user):
    current = list(OnboardingProgrammeReview.unscoped.filter(application=app, round=app.programme_round))
    if any(r.status == OnboardingProgrammeReview.PENDING for r in current):
        return
    reviews = latest_reviews(app)
    returned = [r for r in reviews.values() if r.status == OnboardingProgrammeReview.RETURNED]
    inst = app.institution
    if returned:
        remark = "; ".join(f"{r.programme.name}: {r.remark}" for r in returned)
        flow.transition(_instance(app), "return", user, remark=remark)
        timeline.record(inst, "application.programmes_returned", subject=app, actor=user, visibility="public",
                        public_text=f"{len(returned)} programme(s) returned. Fix them (or remove them) and submit "
                                    "the list again; cleared programmes stay cleared.")
        transaction.on_commit(lambda: inst_services.notify_institution(
            inst, "onboarding.programmes_returned", {"reference": app.reference, "count": len(returned),
                                                     "remark": remark}, url="/application/"))
        return
    # All cleared: the effect create_payment_obligation:programme_processing issues the bill.
    from documents.issue import approval_recorded  # v2.1 X24/X25: provisional clearance letter

    approval_recorded("onboarding.programmes_cleared", app, actor=user, institution=inst, context={
        "application_reference": app.reference,
        "programmes": "\n".join(f"• {r.programme.name}" for r in reviews.values())})
    flow.transition(_instance(app), "clear_programmes", user)
    timeline.record(inst, "application.programmes_cleared", subject=app, actor=user, visibility="public",
                    public_text="NBTE cleared all your programmes. Your programme processing fee is on the "
                                "Payments page.")
    transaction.on_commit(lambda: inst_services.notify_institution(
        inst, "onboarding.programmes_cleared", {"reference": app.reference}, url="/payments/"))


# ---------------------------------------------------------------------------
# Staff queue
# ---------------------------------------------------------------------------
def queue(user, *, status="pending"):
    """Applications for the user's programme-clearance department.
    Returns [{"app", "reviews", "pending", "can_clear", "why"}]."""
    qs = (Application.unscoped.filter(programme_round__gt=0)
          .select_related("institution", "institution__ownership", "institution__category")
          .order_by("programmes_submitted_at", "id"))
    if status == "pending":
        qs = qs.filter(status="programme_clearance")
    from assignments import access
    need_to_know = access.assignment_mode()
    if need_to_know:
        # X1/X4: full viewers everything; otherwise only institutions the user
        # may see (the department's lead: its part; staff: what they work on).
        see_all = access.full_viewer_everywhere(user)
        visible = access.visible_institution_ids(user)
    else:
        see_all = user.is_superuser or user.has_perm("qa_core.view_all_institutions") \
            or user.has_perm("qa_core.confirm_any_department")
    out = []
    for app in qs:
        dept = clearance_department(app)
        if not see_all and (dept is None or dept.pk != user.department_id):
            continue
        if need_to_know and not see_all and app.institution_id not in visible:
            continue
        reviews = list(OnboardingProgrammeReview.unscoped.filter(application=app, round=app.programme_round)
                       .select_related("programme", "programme__level", "programme__category", "decided_by")
                       .order_by("programme__name"))
        ok, why = can_clear(app, user)
        out.append({"app": app, "reviews": reviews, "department": dept, "can_clear": ok, "why": why,
                    "pending": sum(1 for r in reviews if r.status == "pending"),
                    "waiting_days": (timezone.now() - app.programmes_submitted_at).days
                    if app.programmes_submitted_at else 0})
    return out


def load_review(pk):
    return (OnboardingProgrammeReview.unscoped.select_related("application", "application__institution",
                                                               "programme").filter(pk=pk).first())
