"""v2.6: a professional body's visit report — the service layer.

Chain (like the SSQs, but on the body's side):

    representative (draft) —submit→ registrar  —approve→ with NBTE
     corrected ←—return (remark needed) ↗            │
                                NBTE reads it on the case's Reports tab ←-┘

The registrar decides what NBTE receives; NBTE still makes the approval
decision itself and the body still uploads the official letter per programme
(institutions/pb_portal.py:upload_letter).
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

REMARK_MIN = 3


def one_line(user):
    return (user.get_full_name() or user.email).strip() if user is not None else ""


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def can_review(user, report):
    """Only the body's administrator (the registrar or their stand-in)."""
    from institutions.pb_portal import is_pb_admin

    return is_pb_admin(user) and user.professional_body_id == report.body_id


def can_edit(user, report):
    """The author, while it is still theirs (draft or returned)."""
    return bool(user and user.is_authenticated and user.pk == report.author_id and report.is_editable())


def my_reports(user, body=None):
    """Everything the user is involved in: authored reports and (for the
    registrar) the body's other reports to review."""
    from .models import BodyVisitReport

    if not getattr(user, "is_authenticated", False):
        return BodyVisitReport.unscoped.none()
    qs = BodyVisitReport.unscoped.filter(author=user)
    body = body or getattr(user, "professional_body", None)
    if body is not None:
        from django.db.models import Q

        qs = BodyVisitReport.unscoped.filter(Q(author=user) | Q(body=body)).select_related(
            "case", "case__institution", "body", "programme", "author").order_by("-created_at")
    return qs


def to_review(user):
    """The registrar's inbox: reports still sitting with them."""
    from .models import BodyVisitReport

    if not getattr(user, "professional_body_id", None):
        return BodyVisitReport.unscoped.none()
    return BodyVisitReport.unscoped.filter(body_id=user.professional_body_id).exclude(
        status__in=(BodyVisitReport.WITH_NBTE,)).exclude(status=BodyVisitReport.DRAFT).order_by("-created_at")


def cases_awaiting_a_report(user):
    """Cases where one of the body's people is the representative and nobody
    has written the body's report yet — the drafts come from here."""
    from .models import BodyVisitReport, VisitExternalMember

    if not getattr(user, "professional_body_id", None):
        return []
    body = user.professional_body
    with_report = set(BodyVisitReport.unscoped.filter(body=body).values_list("case_id", flat=True))
    rows = (VisitExternalMember.unscoped.filter(body=body)
            .exclude(case_id__in=with_report)
            .select_related("case", "case__institution").order_by("-case__id"))
    return [(r.case, r) for r in rows]


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def create_report(user, case, *, title="", summary="", content="", recommendation=None,
                  programme=None, member=None, remark="", upload=None):
    """A representative starts their report on an assigned case."""
    from qa_core.files import validate_upload

    from .models import BodyVisitReport

    if user.professional_body_id is None:
        raise PermissionDenied("Only a person on a professional body's list writes its reports.")
    body = user.professional_body
    assigned = _assignment_for(case, user)
    if assigned is None:
        raise PermissionDenied("The registrar has not put you on this visit yet.")
    report = BodyVisitReport(case=case, body=body, author=user, member=assigned, programme=programme,
                             title=(title or "Visit report").strip()[:200],
                             summary=(summary or "")[:200],
                             content=content or "", recommendation=recommendation or BodyVisitReport.SATISFACTORY,
                             remark=remark or "", status=BodyVisitReport.DRAFT)
    if upload is not None:
        validate_upload(upload, extensions=("pdf", "jpg", "jpeg", "png"), max_mb=10, label="report")
        report.file = upload
    report.save()
    return report


def update_report(user, report, *, title=None, summary=None, content=None, recommendation=None, remark=None,
                  upload=None):
    """The author corrects their output (draft or returned)."""
    from qa_core.files import validate_upload

    from .models import BodyVisitReport

    if not can_edit(user, report):
        raise PermissionDenied("Only the author edits a report while it is theirs to write.")
    if title is not None:
        report.title = title.strip()[:200] or "Visit report"
    if summary is not None:
        report.summary = summary.strip()[:200]
    if content is not None:
        report.content = content
    if recommendation is not None:
        if recommendation not in dict(BodyVisitReport.RECOMMENDATION_CHOICES):
            raise ValidationError("Choose a valid recommendation.")
        report.recommendation = recommendation
    if remark is not None:
        report.remark = remark.strip()[:255]
    if upload is not None:
        validate_upload(upload, extensions=("pdf", "jpg", "jpeg", "png"), max_mb=10, label="report")
        report.file = upload
    if report.status == BodyVisitReport.RETURNED:  # corrections bring it back to the registrar
        report.status = BodyVisitReport.SUBMITTED
        report.review_remark = ""
    report.save()
    if report.status == BodyVisitReport.SUBMITTED:
        _notify_submission(report)
    return report


def submit_report(user, report):
    """The author sends it to the registrar (their No.1 reviews first)."""
    from .models import BodyVisitReport

    if not can_edit(user, report):
        raise PermissionDenied("Only the author submits a report while it is theirs to write.")
    if not (report.content or "").strip():
        raise ValidationError("Write the report before you submit it.")
    report.status = BodyVisitReport.SUBMITTED
    if report.member_id:
        report.member.status = "confirmed"
        report.member.save(update_fields=["status", "updated_at"])
    report.save(update_fields=["status", "updated_at"])
    _notify_submission(report)
    return report


def decide(user, report, decision, *, remark=""):
    """The registrar approves or returns it (a return carries a remark ≥3 chars)."""
    from .models import BodyVisitReport

    if not can_review(user, report):
        raise PermissionDenied("Only the body's administrator reviews its reports.")
    if not report.is_reviewable():
        raise ValidationError("Only a report waiting for the registrar can be decided.")
    remark = (remark or "").strip()
    if decision == "return":
        if len(remark) < REMARK_MIN:
            raise ValidationError("Say what the representative must correct (at least a sentence).")
        report.status = BodyVisitReport.RETURNED
        report.review_remark = remark
        report.reviewed_by, report.reviewed_at = user, timezone.now()
        report.save(update_fields=["status", "review_remark", "reviewed_by", "reviewed_at", "updated_at"])
        notify_representative(report, title=f"Report returned: {report.case.reference}",
                              text=f"The registrar returned your report with a remark: “{remark}”. Correct it and "
                                   "submit it again.")
        return report
    report.status = BodyVisitReport.APPROVED
    report.review_remark = remark
    report.reviewed_by, report.reviewed_at = user, timezone.now()
    report.save(update_fields=["status", "review_remark", "reviewed_by", "reviewed_at", "updated_at"])
    notify_representative(report, title=f"Report approved: {report.case.reference}",
                          text="The registrar approved your visit report.")
    return report


def send_to_nbte(user, report, *, final_upload=None):
    """The registrar is happy: their approved report goes on NBTE's desk
    (visible on the case's Reports tab). An optional final PDF is attached."""
    from qa_core.files import validate_upload

    from .models import BodyVisitReport

    if not can_review(user, report):
        raise PermissionDenied("Only the body's administrator sends its reports on.")
    if report.status not in (BodyVisitReport.APPROVED, BodyVisitReport.WITH_NBTE):
        raise ValidationError("The registrar must approve the report before it reaches NBTE.")
    if final_upload is not None:
        validate_upload(final_upload, extensions=("pdf", "jpg", "jpeg", "png"), max_mb=10, label="final report")
        report.file = final_upload
    report.status = BodyVisitReport.WITH_NBTE
    report.sent_at = timezone.now()
    report.save(update_fields=["status", "sent_at", "file", "updated_at"])
    notify_nbte(report)
    notify_representative(report, title=f"Report sent to NBTE: {report.case.reference}",
                          text="Your approved visit report is now with NBTE.")
    return report


# ---------------------------------------------------------------------------
# notification helpers (internal)
# ---------------------------------------------------------------------------
def _assignment_for(case, user):
    from .models import VisitExternalMember

    return (VisitExternalMember.unscoped.filter(case=case, body=user.professional_body,
                                                user=user).select_related("case").first())


def notify_representative(report, *, title, text):
    try:
        from notifications.services import notify_many

        return notify_many([report.author], title=title, body=text, url=f"/pb/reports/{report.pk}/")
    except Exception:
        return []


def _notify_submission(report):
    """Tag the registrar (No.1 + administrators) that a report waits for them."""
    from institutions.pb_people import notify_body_people

    name = one_line(report.author)
    notify_body_people(report.body, title=f"Visit report to review: {report.case.reference}",
                       text=f"{name} sent the body's visit report for {report.case.institution.name} "
                            f"({report.case.reference}). Review it, then send it to NBTE.",
                       url=f"/pb/reports/{report.pk}/", admins_only=True)


def notify_nbte(report):
    """The case's working people — desk officer, supervisors, tracking ICT,
    the handling department — get the approved report on their desk."""
    from django.contrib.auth import get_user_model

    from notifications.services import notify_many

    User = get_user_model()
    case = report.case
    body_label = report.body.acronym or report.body.name
    title = f"{body_label} visit report: {case.reference}"
    text = (f"{body_label}'s approved visit report for {case.institution.name} "
            f"({case.reference}): {report.get_recommendation_display().lower()}.")
    people = _case_staff(case)
    notify_many(people, title=title, body=text, url=f"/qa/cases/{case.pk}/?tab=reports",
                metadata={"body_id": report.body_id})


def _case_staff(case):
    """The case's working people: desk officer, supervisors, tracking ICT and
    the handling department's director/officers."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    people = []
    for pk in {case.desk_officer_id, case.supervising_audit_officer_id, case.supervising_finance_officer_id}:
        if pk and pk not in {p.pk for p in people}:
            person = User.objects.filter(pk=pk, is_active=True).first()
            if person is not None:
                people.append(person)
    for officer in case.tracking_ict_officers.all():
        if officer.is_active and officer.pk not in {p.pk for p in people}:
            people.append(officer)
    inst = case.institution
    dept = getattr(inst, "handling_department_id", None)
    if dept:
        for person in User.objects.filter(is_active=True, user_type="staff", department_id=dept,
                                          role__code__in=("director", "officer")).order_by("first_name"):
            if person.pk not in {p.pk for p in people}:
                people.append(person)
    return people
