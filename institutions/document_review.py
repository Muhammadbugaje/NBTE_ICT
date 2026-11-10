"""v2.1 F.16 (X16, X17, X18): per-document review (spec_v21 G3.d).

    open_reviews(app)            one DocumentReview per (document, reviewing department)
    assign(review, director, officer=None, accept_suggestion=False, reason="")
                                 the director assigns an analyst (rotation suggestion, X30)
    recommend(review, analyst, recommendation, remark)
                                 the analyst recommends accept / return (remark)
    decide(review, director, decision, remark="")
                                 approve · send_back (to the analyst) · return_to_institution
    on_document_reuploaded(doc, previous, user, application)
                                 a returned document's new version goes back to the same
                                 department, director and analyst (no resubmission)

Visibility: the director sees only his department's reviews; the analyst his
own. TODO(pp-access): switch to ``assignments.access.can_see`` once the
assignment-based visibility lands on master.
"""
from __future__ import annotations

import datetime
import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core import audit
from qa_core import timeline

from . import services as inst_services
from .models import DocumentReview, InstitutionDocument

logger = logging.getLogger(__name__)
KIND = "document_analysis"


# ---------------------------------------------------------------------------
# Who
# ---------------------------------------------------------------------------
def _org():
    import org

    return org


def is_director_for(user, dept):
    """Director of ``dept`` (or ES / super admin / the audited override permission)."""
    org = _org()
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if org.is_director(user, dept):
        return True
    return bool(org.is_super_admin(user) or org.is_es(user) or user.has_perm("institutions.review_documents_override"))


def director_of(dept):
    return _org().director_of(dept)


def can_assign_for(user, dept):
    """Who may assign a document of ``dept`` (X4): its director, a staff member
    the director delegated ``assignments.assign_work`` to, the ES or super admin."""
    from assignments import work

    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if work.can_assign(user, dept):
        return True
    # v2.2 (owner): the Director of ICT may also assign a document's officer.
    from assignments import access

    return bool(_org().is_director(user, access.ict_department_code()))


def visible_reviews(user):
    """Reviews ``user`` may see: his department's (director), his own as analyst
    while the work is open, or all for ES / super admin / Director of ICT."""
    org = _org()
    qs = DocumentReview.unscoped.select_related("document", "document__kind", "document__institution", "department",
                                                "analyst", "application")
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "") != "staff":
        return qs.none()
    if user.is_superuser or org.is_super_admin(user) or org.is_es(user):
        return qs
    # v2.2: the override permission comes with every director role, so it no
    # longer opens other departments' reviews; it still counts for non-directors
    # (a delegated QA officer) — limited to their own department below.
    import config

    ict = org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
    if ict is not None and org.is_director(user, ict):  # X2: the Director of ICT sees every journey
        return qs
    directed = org.directed_department(user)
    from django.db.models import Q

    # The analyst sees the review while the work is open (X5): assigned, sent
    # back, recommended, or returned and awaiting the re-upload. Not after.
    q = Q(analyst=user, status__in=DocumentReview.OPEN + (DocumentReview.RETURNED,))
    if directed is not None:
        q |= Q(department=directed)
    # v2.2: the ICT officer overseeing an institution follows its reviews (read-only).
    from assignments import access

    supervised = access.supervised_institution_ids(user)
    if supervised:
        q |= Q(document__institution_id__in=list(supervised))
    return qs.filter(q)


# ---------------------------------------------------------------------------
# Open
# ---------------------------------------------------------------------------
def _department_for(inst, kind):
    for req in inst_services.requirements_for(inst):
        if req.document_kind_id == kind.pk:
            return req.reviewing_department
    return None


def open_reviews(app):
    """One review per live document whose kind has a reviewing department; the
    departments' directors are told how many documents await assignment.
    Pools flagged ``auto_assign`` (X27b) get their officer picked by the
    rotation at once."""
    inst = app.institution
    created = {}
    docs = (InstitutionDocument.unscoped.filter(institution=inst, status__in=(InstitutionDocument.SUBMITTED,
                                                                            InstitutionDocument.ACCEPTED))
            .select_related("kind"))
    for doc in docs:
        dept = _department_for(inst, doc.kind)
        if dept is None:
            continue
        review, made = DocumentReview.unscoped.get_or_create(
            document=doc, department=dept, defaults={"application": app, "round": 1,
                                                     "status": DocumentReview.AWAITING})
        if made:
            created.setdefault(dept, []).append(review)
    for dept, rows in created.items():
        for review in rows:  # the director may always reassign an auto pick
            auto_assign_review(review)
        still = DocumentReview.unscoped.filter(pk__in=[r.pk for r in rows],
                                               status=DocumentReview.AWAITING).count()
        director = director_of(dept)
        if director is None:
            logger.warning("No director for %s: %s document(s) of %s wait", dept.code, len(rows), inst.code)
        elif still:
            ctx = {"reference": app.reference, "institution": inst.name, "count": still,
                   "department": dept.name}
            transaction.on_commit(lambda d=director, c=ctx: inst_services.notify_role(
                "document_review.awaiting_assignment", c, [d], url="/qa/applications/documents/"))
    return created


def _auto_assign_pools():
    """Pool codes enabled for at-once assignment: ``qa.rotation.auto_assign_pools``."""
    import json

    import config

    codes = config.get("qa.rotation.auto_assign_pools", []) or []
    if isinstance(codes, str):
        try:
            codes = json.loads(codes)
        except ValueError:
            codes = []
    return set(codes or [])


def auto_assign_review(review):
    """X27b: a pool flagged ``auto_assign`` (or listed in
    ``qa.rotation.auto_assign_pools``) gives the review to the rotation's best
    officer at once — the system assigns (no director in the loop). Returns the
    assigned review, or None when the pool is not flagged."""
    from assignments import rotation

    try:
        pool = rotation.get_pool(KIND, review.department)
    except Exception:
        return None
    if not pool.auto_assign and pool.code not in _auto_assign_pools():
        return None
    s = suggestion(review, record=True)
    if s is None or s.suggested is None:
        return None
    return assign(review, director=None, officer=s.suggested.user, system=True)


# ---------------------------------------------------------------------------
# Assign (director, rotation suggestion)
# ---------------------------------------------------------------------------
def suggestion(review, *, record=False):
    """The rotation suggestion for this review (None when the department has no pool)."""
    from assignments import rotation
    from assignments.models_rotation import RotationPool

    try:
        return rotation.suggest(KIND, subject=review.document, institution=review.document.institution,
                                department=review.department, record=record)
    except RotationPool.DoesNotExist:
        return None


def candidates(review):
    """Officers the director may pick: the pool's eligible members first, then
    the rest of the department's active staff."""
    from accounts.models import User

    s = suggestion(review)
    out, seen = [], set()
    if s is not None:
        for c in s.eligible:
            out.append(c.user)
            seen.add(c.user.pk)
    director = director_of(review.department)
    for u in User.objects.filter(is_active=True, user_type="staff", department=review.department).order_by(
            "first_name", "last_name"):
        if u.pk not in seen and (director is None or u.pk != director.pk):
            out.append(u)
            seen.add(u.pk)
    return out


@transaction.atomic
def assign(review, *, director, officer=None, accept_suggestion=False, reason="", system=False):
    from assignments import rotation
    from assignments.models import WorkAssignment

    review = DocumentReview.unscoped.select_for_update().select_related("document", "department").get(pk=review.pk)
    if not system and not can_assign_for(director, review.department):
        raise PermissionDenied("Only the director of this department can assign the document.")
    if review.status != DocumentReview.AWAITING:
        raise ValidationError("This document already has an officer.")
    inst = review.document.institution
    s = suggestion(review, record=True)
    if accept_suggestion:
        if s is None or s.suggested is None:
            raise ValidationError("There is no rotation suggestion: choose an officer.")
        officer = s.suggested.user
    if officer is None:
        raise ValidationError("Choose an officer.")
    from assignments import work

    if not officer.is_active:
        raise ValidationError("Choose an active officer.")
    if not (system or work.is_top(director)) and not work.in_department(officer, review.department):
        raise ValidationError("Choose an officer of this department.")
    if s is not None and s.decision is not None:
        in_pool = any(c.get("user_id") == officer.pk for c in s.decision.candidates or [])
        if in_pool:
            rotation.record_decision(s.decision, by=director, chosen=officer, override_reason=reason,
                                     system=system)
    ct = ContentType.objects.get_for_model(review.document, for_concrete_model=False)
    wa = WorkAssignment.objects.create(
        institution=inst, kind=WorkAssignment.DOCUMENT_ANALYSIS, subject_ct=ct, subject_id=str(review.document_id),
        department=review.department, assignee=officer, role_on_work="analyst", assigned_by=director,
        status=WorkAssignment.IN_PROGRESS, accepted_at=timezone.now(), payable=True,
        due_at=timezone.now() + datetime.timedelta(days=3), meta={"document_review": review.pk})
    if s is not None and s.decision is not None and s.decision.work_assignment_id is None:
        rotation.link_work(s.decision, wa)
    work.open_grants(wa)  # X5: time-boxed sight of this document only, closed with the work
    work._bump(wa)
    review.assignment, review.analyst, review.status = wa, officer, DocumentReview.ASSIGNED
    review.assigned_by, review.assigned_at = director if getattr(director, "pk", None) else officer, timezone.now()
    review.save(update_fields=["assignment", "analyst", "status", "assigned_by", "assigned_at", "updated_at"])
    audit.log(audit._action("state_change"), review, actor=director, changes={"status": {"new": review.status}},
              note=f"analyst {officer.pk}" + (" (auto by rotation)" if system else ""))
    _trail(review, director, f"Assigned to {officer.get_full_name()}"
           + (" (next in rotation)" if accept_suggestion else "")
           + (" — picked automatically by rotation; the director may reassign" if system else ""))
    transaction.on_commit(lambda: inst_services.notify_role(
        "document_review.assigned", {"document": review.document.kind.name, "institution": inst.name},
        [officer], url=f"/qa/applications/documents/{review.pk}/"))
    if system:
        d = director_of(review.department)
        if d is not None:
            transaction.on_commit(lambda: inst_services.notify_role(
                "document_review.auto_assigned",
                {"document": review.document.kind.name, "institution": inst.name,
                 "officer": officer.get_full_name() or officer.email},
                [d], url=f"/qa/applications/documents/{review.pk}/"))
    return review


# ---------------------------------------------------------------------------
# Recommend (analyst)
# ---------------------------------------------------------------------------
@transaction.atomic
def recommend(review, *, analyst, recommendation, remark=""):
    review = DocumentReview.unscoped.select_for_update().select_related("document", "department").get(pk=review.pk)
    if analyst is None or review.analyst_id != analyst.pk:
        raise PermissionDenied("Only the officer assigned to this document can recommend.")
    if review.status not in (DocumentReview.ASSIGNED, DocumentReview.SENT_BACK):
        raise ValidationError("This document is not waiting for your recommendation.")
    if recommendation not in ("accept", "return"):
        raise ValidationError("Choose accept or return.")
    remark = (remark or "").strip()
    if recommendation == "return" and len(remark) < 3:
        raise ValidationError("Say what the institution must fix.")
    review.recommendation, review.analyst_remark = recommendation, remark
    review.recommended_at, review.status = timezone.now(), DocumentReview.RECOMMENDED
    review.save(update_fields=["recommendation", "analyst_remark", "recommended_at", "status", "updated_at"])
    if review.assignment_id:
        from assignments.models import WorkAssignment

        _set_work(review, WorkAssignment.SUBMITTED, submitted_at=timezone.now())
    _trail(review, analyst, f"{analyst.get_full_name()} recommended "
           + ("accept" if recommendation == "accept" else "return with remark"))
    director = director_of(review.department)
    if director is not None:
        transaction.on_commit(lambda: inst_services.notify_role(
            "document_review.recommended", {"document": review.document.kind.name,
                                            "institution": review.document.institution.name},
            [director], url=f"/qa/applications/documents/{review.pk}/"))
    return review


# ---------------------------------------------------------------------------
# Decide (director)
# ---------------------------------------------------------------------------
@transaction.atomic
def decide(review, *, director, decision, remark=""):
    review = DocumentReview.unscoped.select_for_update().select_related("document", "department",
                                                                        "application").get(pk=review.pk)
    if not is_director_for(director, review.department):
        raise PermissionDenied("Only the director of this department decides on its documents.")
    if review.status != DocumentReview.RECOMMENDED:
        raise ValidationError("The officer has not recommended yet.")
    remark = (remark or "").strip()
    now = timezone.now()
    if decision == "approve":
        inst_services.review_document(review.document, "accept", user=director)
        review.status = DocumentReview.APPROVED
        review.locked_at = now
        _close_assignment(review, completed=True)
    elif decision == "send_back":
        if len(remark) < 3:
            raise ValidationError("Tell the officer what to look at again.")
        review.status = DocumentReview.SENT_BACK
        if review.assignment_id:
            from assignments.models import WorkAssignment

            _set_work(review, WorkAssignment.IN_PROGRESS)
        if review.analyst_id:
            analyst = review.analyst
            transaction.on_commit(lambda: inst_services.notify_role(
                "document_review.sent_back", {"document": review.document.kind.name, "remark": remark},
                [analyst], url=f"/qa/applications/documents/{review.pk}/"))
    elif decision == "return_to_institution":
        remark = remark or review.analyst_remark
        if len(remark) < 3:
            raise ValidationError("Write the remark the institution will see.")
        inst_services.review_document(review.document, "return", user=director, remark=remark)
        InstitutionDocument.unscoped.filter(pk=review.document_id).update(returned_round=review.round)
        review.status = DocumentReview.RETURNED
        review.locked_at = now
        # The assignment stays open ("returned"): the same analyst gets the re-upload.
        if review.assignment_id:
            from assignments.models import WorkAssignment

            _set_work(review, WorkAssignment.RETURNED)
    else:
        raise ValidationError("Choose approve, send back or return.")
    review.director_decision, review.director_remark = decision, remark
    review.decided_by, review.decided_at = director, now
    review.save(update_fields=["status", "locked_at", "director_decision", "director_remark", "decided_by",
                               "decided_at", "updated_at"])
    audit.log(audit._action("state_change"), review, actor=director, changes={"status": {"new": review.status}},
              note=f"document review: {decision}")
    _trail(review, director, {"approve": "Approved", "send_back": "Sent back to the officer",
                              "return_to_institution": "Returned to the institution"}[decision])
    if decision == "approve" and review.application_id:
        from accreditation import onboarding_v2

        app = review.application
        transaction.on_commit(lambda: onboarding_v2.maybe_clear(app))
    return review


def _set_work(review, status, **fields):
    """Move the review's WorkAssignment and keep its grants and the access
    cache in step (X5): open work holds the grant, closed work loses it now."""
    from assignments import work
    from assignments.models import WorkAssignment

    wa = WorkAssignment.objects.filter(pk=review.assignment_id).first()
    if wa is None:
        return
    WorkAssignment.objects.filter(pk=wa.pk).update(status=status, **fields)
    wa.refresh_from_db()
    if wa.is_open:
        work.open_grants(wa)
    work._bump(wa)


def _close_assignment(review, *, completed):
    if not review.assignment_id:
        return
    from assignments import work
    from assignments.models import WorkAssignment

    wa = WorkAssignment.objects.filter(pk=review.assignment_id).first()
    if wa is None or not wa.is_open:
        return
    if completed:
        work.complete(wa)
    else:
        work.cancel(wa, reason="withdrawn")


# ---------------------------------------------------------------------------
# Re-upload (X17)
# ---------------------------------------------------------------------------
def on_document_reuploaded(doc, previous, *, user, application):
    """Route the new version to the same department, director and analyst.
    Returns the department (the portal toast says where it went)."""
    prev = (DocumentReview.unscoped.select_for_update().filter(document=previous, status=DocumentReview.RETURNED)
            .select_related("department", "analyst").order_by("-id").first())
    if prev is None:
        return None
    if doc.application_id != application.pk:
        doc.application = application
        doc.save(update_fields=["application", "updated_at"])
    DocumentReview.unscoped.filter(pk=prev.pk).update(status=DocumentReview.SUPERSEDED, updated_at=timezone.now())
    has_analyst = prev.analyst_id is not None and prev.analyst.is_active
    new = DocumentReview.unscoped.create(
        document=doc, department=prev.department, application=application, round=prev.round + 1,
        analyst=prev.analyst if has_analyst else None, assignment=prev.assignment if has_analyst else None,
        assigned_by=prev.assigned_by, assigned_at=timezone.now() if has_analyst else None,
        status=DocumentReview.ASSIGNED if has_analyst else DocumentReview.AWAITING)
    if has_analyst and prev.assignment_id:
        from assignments.models import WorkAssignment

        _set_work(new, WorkAssignment.IN_PROGRESS)
    inst = doc.institution
    timeline.record(inst, "document.resubmitted", subject=doc, actor=user, visibility="public",
                    public_text=f"{doc.kind.name} re-uploaded. Sent back to NBTE for review. "
                                "You do not need to resubmit your application.",
                    internal_text=f"Round {new.round}: back with {prev.department.name}.")
    recipients = [u for u in (director_of(prev.department), prev.analyst if has_analyst else None) if u is not None]
    ctx = {"reference": application.reference, "institution": inst.name, "document": doc.kind.name,
           "department": prev.department.name, "version": doc.version}
    transaction.on_commit(lambda: inst_services.notify_role("onboarding.document_resubmitted", ctx, recipients,
                                                            url=f"/qa/applications/documents/{new.pk}/"))
    return prev.department


# ---------------------------------------------------------------------------
# Trail (internal timeline of one review)
# ---------------------------------------------------------------------------
def _trail(review, actor, text):
    timeline.record(review.document.institution, "document_review.step", subject=review.document, actor=actor,
                    visibility="internal", internal_text=f"{review.document.kind.name}: {text}")


def trail(review):
    """[(when, text)] for the review panel."""
    out = [(review.document.created_at, "Uploaded by the institution"
            + (f" (version {review.document.version})" if review.document.version > 1 else ""))]
    if review.assigned_at and review.analyst_id:
        out.append((review.assigned_at, f"Assigned to {review.analyst.get_full_name()}"))
    if review.recommended_at:
        out.append((review.recommended_at, f"{review.analyst.get_full_name() if review.analyst_id else 'Officer'} "
                                           f"recommended {review.get_recommendation_display().lower()}"))
    if review.decided_at:
        out.append((review.decided_at, {"approve": "Approved", "send_back": "Sent back to the officer",
                                        "return_to_institution": "Returned to the institution"}.get(
            review.director_decision, review.director_decision)))
    return out
