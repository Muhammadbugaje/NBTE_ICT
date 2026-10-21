"""Phase 5 services: resource persons (nominate, vet), QA personnel, mapping,
inspections (create, complete, attendance overrides), post-visit reports and
endorsements (SOP-03 stage 12–13), and the service history.

System code (``# scoped-by-caller``): callers pass objects they already
reached through a scoped query.
"""
# scoped-by-caller
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from qa_core import flow, references, task_bridge, timeline

from . import rules
from .models import (
    VISIT_DUTIES, Inspection, InspectionTeamMember, PostVisitPersonnel, PostVisitReport, QAMapping, QAPersonnel,
    ReportEndorsement, ResourcePerson, ResourcePersonDiscipline, ResourcePersonFile, ServiceRecord,
    SSQReviewAssignment,
)

LAYER_FOR_STAGE = {"submitted": "division_head", "division_endorsed": "deputy_director",
                   "deputy_verified": "director"}
LAYER_LABEL = {"division_head": "Division Head", "deputy_director": "Deputy Director", "director": "Director"}
OPEN_REPORT_STAGES = ("draft", "submitted", "division_endorsed", "deputy_verified", "returned")


def _name(user):
    return (user.get_full_name() or user.email) if user else "—"


def _role(user):
    return getattr(getattr(user, "role", None), "code", "")


def is_monitor(user):
    return _role(user) == "ict-qa-monitor"


def _staff(user):
    if user is None or getattr(user, "user_type", "") != "staff":
        raise PermissionDenied("Sign in as NBTE staff.")
    if is_monitor(user):
        raise PermissionDenied("The ICT monitor has read-only access (R-13).")


# ===========================================================================
# Resource persons: nominate → vet → active
# ===========================================================================
@transaction.atomic
def nominate(data, *, actor):
    """Create a resource person (status ``nominated``). Eligibility (rank /
    designation) is checked now; a vetter approves later."""
    from institutions.models import Discipline, Institution

    _staff(actor)
    if not (actor.is_superuser or actor.has_perm("inspection.manage_resource_person")):
        raise PermissionDenied("Nominating needs inspection.manage_resource_person.")
    email = (data.get("email") or "").strip().lower()
    name = (data.get("full_name") or "").strip()
    errors = {}
    if len(name) < 3:
        errors["full_name"] = "Enter the full name."
    if "@" not in email:
        errors["email"] = "Enter a valid e-mail address."
    elif ResourcePerson.objects.filter(email__iexact=email).exists():
        errors["email"] = "A resource person with this e-mail address already exists."
    source = data.get("source") or "academia"
    if source not in dict(ResourcePerson.SOURCE_CHOICES):
        errors["source"] = "Choose a source."
    discipline_ids = [int(x) for x in (data.get("disciplines") or []) if str(x).isdigit()]
    if not discipline_ids:
        errors["disciplines"] = "Choose at least one discipline."
    if errors:
        raise ValidationError(errors)
    rp = ResourcePerson(full_name=name, email=email, phone=(data.get("phone") or "").strip(), source=source,
                        professional_body=(data.get("professional_body") or "").strip(),
                        rank=(data.get("rank") or "").strip().lower(),
                        qualifications=(data.get("qualifications") or "").strip(), nominated_by=actor,
                        nominated_at=timezone.now())
    if data.get("home_institution"):
        rp.home_institution = Institution.unscoped.filter(pk=data["home_institution"]).first()
    if source == "nbte_staff" and data.get("staff"):
        from staff.models import Staff

        rp.staff = Staff.objects.filter(pk=data["staff"]).select_related("user__role", "designation").first()
    problems = rules.nomination_problems(rp)
    if problems:
        field = "rank" if not rp.is_staff_person else "staff"
        raise ValidationError({field: [str(p) for p in problems]})
    rp.save()
    primary = int(data.get("primary_discipline") or discipline_ids[0])
    for d in Discipline.objects.filter(pk__in=discipline_ids):
        ResourcePersonDiscipline.objects.create(resource_person=rp, discipline=d, is_primary=(d.pk == primary))
    _notify_vetters(rp, actor)
    return rp


def _notify_vetters(rp, actor):
    from accounts.models import User

    vetters = [u for u in User.objects.filter(is_active=True, user_type="staff").select_related("role")[:500]
               if u.pk != actor.pk and (u.has_perm("inspection.vet_resource_person"))][:20]
    task_bridge._notify(vetters, "rp.vetting_requested", {"name": rp.full_name, "who": _name(actor)},
                        url=f"/qa/inspection/resource-persons/{rp.pk}/")


def add_file(rp, *, actor, upload, kind="cv"):
    if not (actor.is_superuser or actor.has_perm("inspection.manage_resource_person")
            or (rp.user_id and rp.user_id == actor.pk)):
        raise PermissionDenied("You cannot add files for this person.")
    if upload is None:
        raise ValidationError({"file": "Choose a file."})
    max_mb = rules._int("qa.documents.max_upload_mb", 10)
    if upload.size > max_mb * 1024 * 1024:
        raise ValidationError({"file": f"The file is larger than {max_mb} MB."})
    name = (upload.name or "").lower()
    if not name.endswith((".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png")):
        raise ValidationError({"file": "Upload a PDF, Word document or image."})
    return ResourcePersonFile.objects.create(resource_person=rp, kind=kind if kind in dict(
        ResourcePersonFile.KIND_CHOICES) else "other", file=upload, uploaded_by=actor)


@transaction.atomic
def vet(rp, *, actor, approve, remark=""):
    """A vetter approves (→ active) or rejects a nominated / vetted person.
    The vetter cannot be the nominator; the rules are checked again."""
    _staff(actor)
    if not (actor.is_superuser or actor.has_perm("inspection.vet_resource_person")):
        raise PermissionDenied("Vetting needs inspection.vet_resource_person.")
    rp = ResourcePerson.objects.select_for_update().get(pk=rp.pk)
    if rp.status not in (ResourcePerson.NOMINATED, ResourcePerson.VETTED):
        raise ValidationError("Only a nominated person can be vetted.")
    if rp.nominated_by_id == actor.pk:
        raise PermissionDenied("The person who nominated a resource person cannot also vet them.")
    remark = (remark or "").strip()
    now = timezone.now()
    if approve:
        rules.raise_if(rules.nomination_problems(rp))
        if not rp.files.filter(kind="cv").exists():
            raise ValidationError("Upload the CV before approving.")
        rp.status, rp.vetted_by, rp.vetted_at, rp.activated_at = ResourcePerson.ACTIVE, actor, now, now
    else:
        if len(remark) < 3:
            raise ValidationError({"remark": "Give the reason for not approving."})
        rp.status, rp.vetted_by, rp.vetted_at = ResourcePerson.REJECTED, actor, now
    rp.vetting_remark = remark
    rp.save()
    task_bridge._notify([rp.nominated_by] if rp.nominated_by_id else [], "rp.vetted",
                        {"name": rp.full_name, "outcome": "approved" if approve else "not approved",
                         "who": _name(actor)}, url=f"/qa/inspection/resource-persons/{rp.pk}/")
    return rp


@transaction.atomic
def set_status(rp, *, actor, status, remark=""):
    """Suspend, retire or reactivate an approved person (manage permission)."""
    _staff(actor)
    if not (actor.is_superuser or actor.has_perm("inspection.manage_resource_person")):
        raise PermissionDenied("Needs inspection.manage_resource_person.")
    if status not in (ResourcePerson.ACTIVE, ResourcePerson.SUSPENDED, ResourcePerson.RETIRED):
        raise ValidationError("Choose active, suspended or retired.")
    if rp.status in (ResourcePerson.NOMINATED, ResourcePerson.REJECTED):
        raise ValidationError("This person has not been approved; vet them first.")
    if status == ResourcePerson.ACTIVE:
        rules.raise_if(rules.nomination_problems(rp))
    ResourcePerson.objects.filter(pk=rp.pk).update(status=status, vetting_remark=remark or rp.vetting_remark,
                                                   updated_at=timezone.now())
    rp.refresh_from_db()
    return rp


def invite_to_portal(rp, *, actor, request=None):
    """Create the person's portal account (user type ``resource_person``) and
    e-mail a set-password link."""
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from accounts.models import Role, User
    from qa_core import mail
    from qa_core.hosts import portal_absolute, portal_reverse
    from qa_core.portal_tokens import link_valid_hours, portal_token_generator

    _staff(actor)
    if rp.status != ResourcePerson.ACTIVE:
        raise ValidationError("Only an active resource person can be invited.")
    user = rp.user
    if user is None:
        if User.objects.filter(email__iexact=rp.email).exists():
            raise ValidationError(f"{rp.email} already has an account.")
        first, _, last = rp.full_name.partition(" ")
        user = User(email=rp.email, first_name=first[:150], last_name=last[:150] or "-", user_type="resource_person",
                    role=Role.objects.filter(code="resource-person").first(), is_active=True)
        user.set_unusable_password()
        user.save()
        ResourcePerson.objects.filter(pk=rp.pk).update(user=user)
        rp.user = user
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = portal_token_generator.make_token(user)
    link = portal_absolute(portal_reverse("qa_portal:password_set", uidb64=uid, token=token), request)
    mail.send("Your NBTE Quality Assurance Portal account", [user.email], "emails/qa_set_password.html",
              {"user": user, "set_url": link, "valid_hours": link_valid_hours(), "institution": None},
              category="reset")
    return user


# ===========================================================================
# QA personnel (Edit QA Personnel applies through here)
# ===========================================================================
@transaction.atomic
def register_qa_personnel(staff, *, actor, disciplines, is_verification_officer=True, is_team_lead_eligible=False,
                          active=True):
    if active:
        rules.raise_if(rules.staff_problems(staff))
    qp, _created = QAPersonnel.objects.get_or_create(staff=staff, defaults={"registered_by": actor})
    qp.is_verification_officer = bool(is_verification_officer)
    qp.is_team_lead_eligible = bool(is_team_lead_eligible)
    qp.active = bool(active)
    qp.save()
    qp.disciplines.set(disciplines)
    return qp


# ===========================================================================
# Mapping (Edit QA Mapping)
# ===========================================================================
def need_for(case, row, duty, start=None, end=None, *, exclude_mapping_id=None):
    from accreditation import case_services as cs

    if start is None or end is None:
        visit = cs.case_bundle(case)["visit"]
        if visit is None or not visit.planned_start:
            raise ValidationError("Enter the visit dates first (case → Visit tab); mappings use them.")
        start, end = visit.planned_start, visit.planned_end
    if end < start:
        raise ValidationError("The mapping cannot end before it starts.")
    if duty not in dict(QAMapping._meta.get_field("duty").choices):
        raise ValidationError("Choose a duty.")
    return rules.Need(case, row, duty, start, end, exclude_mapping_id=exclude_mapping_id)


def person_from_key(key):
    kind, _, pk = (key or "").partition(":")
    if not pk.isdigit():
        return None
    if kind == "resource_person":
        return ResourcePerson.objects.filter(pk=pk).first()
    if kind == "qa_personnel":
        return QAPersonnel.objects.filter(pk=pk).select_related("staff__user__role", "staff__designation").first()
    return None


@transaction.atomic
def propose_mapping(case, row, *, duty, person, actor, start=None, end=None, expected_version=None, reason=""):
    """Book ``person`` (a proposed mapping, under the guard) and open the
    *Edit QA Mapping* pending action; a second officer's confirmation makes it
    active. Without a second-officer policy it applies at once."""
    from qa_core import actions

    _staff(actor)
    if row.qa_request_id != case.qa_request_id or row.status != "active":
        raise ValidationError("Choose an active programme of this case.")
    need = need_for(case, row, duty, start, end)
    mapping = rules.book(person, need, actor=actor, expected_version=expected_version)
    reason = (reason or "").strip() or f"Map {person} to {row.programme} ({mapping.get_duty_display()})"
    pa = actions.initiate("inspection.edit_qa_mapping", actor=actor, target=case.institution,
                          payload={"mapping_id": mapping.pk, "reason": reason}, reason=reason)
    QAMapping.unscoped.filter(pk=mapping.pk).update(created_via=pa)
    mapping.refresh_from_db()
    return mapping, pa


def on_mapping_active(mapping):
    """An active visit-duty mapping joins the planned team of a scheduled inspection."""
    if mapping.duty not in VISIT_DUTIES:
        return
    inspection = Inspection.unscoped.filter(case=mapping.case, status="scheduled").order_by("-pk").first()
    if inspection is not None and not InspectionTeamMember.unscoped.filter(inspection=inspection,
                                                                            qa_mapping=mapping).exists():
        InspectionTeamMember.unscoped.create(inspection=inspection, qa_mapping=mapping,
                                             role_on_team=mapping.get_duty_display())


# ===========================================================================
# Inspections
# ===========================================================================
def live_inspection(case):
    return (Inspection.unscoped.filter(case=case).exclude(status="cancelled").order_by("-pk")
            .select_related("team_lead").first())


@transaction.atomic
def create_for_case(case, *, actor=None):
    """Called when the ES approves the visit (stage 10): the inspection from
    the visit details (dates, team lead), the personnel list and the case's
    visit-duty mappings. A later re-approval refreshes a scheduled one."""
    from accreditation import case_services as cs

    bundle = cs.case_bundle(case)
    visit = bundle["visit"]
    if visit is None or not visit.planned_start:
        return None
    lead_user = getattr(visit.team_lead, "user", None) if visit.team_lead_id else None
    inspection = live_inspection(case)
    if inspection is not None and inspection.status != "scheduled":
        return inspection
    if inspection is None:
        dept = getattr(case.owning_department, "code", "") or "qa"
        inspection = Inspection.unscoped.create(
            reference=references.issue("inspection", dept=dept.upper()), case=case,
            kind="accreditation_visit", team_lead=lead_user, scheduled_start=visit.planned_start,
            scheduled_end=visit.planned_end)
        flow.start("inspection", inspection, actor=actor)
        timeline.record(case.institution, "inspection.scheduled", subject=inspection, actor=actor,
                        visibility="public",
                        public_text=f"QA visit scheduled for {visit.planned_start:%d %b %Y} – "
                                    f"{visit.planned_end:%d %b %Y}.")
    else:
        Inspection.unscoped.filter(pk=inspection.pk).update(
            team_lead=lead_user, scheduled_start=visit.planned_start, scheduled_end=visit.planned_end,
            updated_at=timezone.now())
        InspectionTeamMember.unscoped.filter(inspection=inspection).delete()
    for entry in bundle["entries"]:
        if entry.status == "returned":
            continue
        InspectionTeamMember.unscoped.create(inspection=inspection, staff=entry.staff,
                                             role_on_team=entry.get_duty_display(),
                                             confirmed_by_division_director=entry.status == "approved")
    for mapping in QAMapping.unscoped.filter(case=case, status="active", duty__in=VISIT_DUTIES):
        on_mapping_active(mapping)
    inspection.refresh_from_db()
    return inspection


def team(inspection):
    return list(InspectionTeamMember.unscoped.filter(inspection=inspection)
                .select_related("staff__user", "qa_mapping__resource_person", "qa_mapping__qa_personnel__staff",
                                "qa_mapping__qa_request_programme__programme"))


def can_complete(inspection, user):
    if user is None or is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    if inspection.status != "scheduled":
        return False, "The visit is already recorded."
    if user.is_superuser or (inspection.team_lead_id and inspection.team_lead_id == user.pk):
        return True, ""
    return False, "Only the Team Lead records the visit."


@transaction.atomic
def complete(inspection, *, actor, actual_start=None, actual_end=None, absent=()):
    """The Team Lead records the visit: actual dates and who did not attend.
    Writes PostVisitPersonnel from the planned team and closes the inspection
    (which writes the service history)."""
    inspection = Inspection.unscoped.select_for_update().get(pk=inspection.pk)
    ok, why = can_complete(inspection, actor)
    if not ok:
        raise PermissionDenied(why)
    actual_start = actual_start or inspection.scheduled_start
    actual_end = actual_end or inspection.scheduled_end
    today = timezone.localdate()
    if actual_end < actual_start:
        raise ValidationError({"actual_end": "The visit cannot end before it starts."})
    if actual_end > today:
        raise ValidationError({"actual_end": "The visit has not ended yet; record it when it has."})
    absent = {str(a) for a in absent}
    PostVisitPersonnel.unscoped.filter(inspection=inspection).delete()
    for member in team(inspection):
        mapping = member.qa_mapping
        kwargs = {}
        if mapping is not None:
            kwargs = ({"resource_person": mapping.resource_person} if mapping.resource_person_id
                      else {"qa_personnel": mapping.qa_personnel})
            duty = mapping.duty
        else:
            qp = QAPersonnel.objects.filter(staff=member.staff).first() if member.staff_id else None
            kwargs = {"qa_personnel": qp} if qp is not None else {"staff": member.staff}
            duty = "physical_verification"
        PostVisitPersonnel.unscoped.create(inspection=inspection, role_on_team=member.role_on_team, duty=duty,
                                           planned=True, attended=str(member.pk) not in absent, **kwargs)
    if not PostVisitPersonnel.unscoped.filter(inspection=inspection, attended=True).exists():
        raise ValidationError("At least one person must have attended.")
    Inspection.unscoped.filter(pk=inspection.pk).update(actual_start=actual_start, actual_end=actual_end,
                                                        completed_by=actor, updated_at=timezone.now())
    inspection.refresh_from_db()
    instance = flow.instance_for(inspection, "inspection")
    flow.transition(instance, "complete", actor)
    inspection.refresh_from_db()
    timeline.record(inspection.case.institution, "inspection.completed", subject=inspection, actor=actor,
                    visibility="public", public_text="The QA visit has taken place.")
    return inspection


def on_inspection_closed(ctx):
    inspection = ctx.subject
    Inspection.unscoped.filter(pk=inspection.pk).update(closed_at=timezone.now())
    for pvp in attendance(inspection):
        if pvp.attended:
            write_service(pvp, source="inspection_closed")
    # Visit duties are done: release their mappings (R-11 / the load count).
    QAMapping.unscoped.filter(case=inspection.case, status="active", duty__in=VISIT_DUTIES).update(
        status="released", released_at=timezone.now(), updated_at=timezone.now())


def attendance(inspection):
    return list(PostVisitPersonnel.unscoped.filter(inspection=inspection)
                .select_related("staff__user", "qa_personnel__staff__user", "resource_person__user"))


# ---------------------------------------------------------------------------
# Service history (append-only)
# ---------------------------------------------------------------------------
def _programme_for(pvp):
    """(programme, discipline) for a mapped assessor; (None, None) for the team."""
    if pvp.resource_person_id or pvp.qa_personnel_id:
        kind = {"resource_person": pvp.resource_person_id} if pvp.resource_person_id else {
            "qa_personnel": pvp.qa_personnel_id}
        mapping = (QAMapping.unscoped.filter(case=pvp.inspection.case, duty__in=VISIT_DUTIES, **{f"{k}_id": v
                   for k, v in kind.items()}).select_related("qa_request_programme__programme__category")
                   .order_by("-pk").first())
        if mapping is not None:
            programme = mapping.qa_request_programme.programme
            return programme, getattr(programme.category, "discipline", None)
    return None, None


def write_service(pvp, *, source, reason=""):
    inspection = pvp.inspection
    programme, discipline = _programme_for(pvp)
    return ServiceRecord.unscoped.create(
        person_type=pvp.person_type, person_id=pvp.person_id, person_name=str(pvp.person)[:150],
        resource_person=pvp.resource_person, qa_personnel=pvp.qa_personnel, staff=pvp.staff,
        case=inspection.case, inspection=inspection, institution=inspection.case.institution,
        programme=programme, discipline=discipline, duty=pvp.duty, role_on_team=pvp.role_on_team[:60],
        served_from=inspection.actual_start or inspection.scheduled_start,
        served_to=inspection.actual_end or inspection.scheduled_end, visit_count=1, source=source,
        reason=reason[:255])


def void_service(pvp, *, reason):
    """Compensating rows for every live positive row of this attendance."""
    rows = ServiceRecord.unscoped.filter(inspection=pvp.inspection, person_type=pvp.person_type,
                                         person_id=pvp.person_id, visit_count__gt=0, voided_by__isnull=True)
    out = []
    for row in rows:
        out.append(ServiceRecord.unscoped.create(
            person_type=row.person_type, person_id=row.person_id, person_name=row.person_name,
            resource_person=row.resource_person, qa_personnel=row.qa_personnel, staff=row.staff, case=row.case,
            inspection=row.inspection, institution=row.institution, programme=row.programme,
            discipline=row.discipline, duty=row.duty, role_on_team=row.role_on_team, served_from=row.served_from,
            served_to=row.served_to, visit_count=-row.visit_count, voids=row, source="attendance_override",
            reason=reason[:255]))
    return out


def record_ssq_review(ssq, *, reviewer, outcome):
    """Phase 3's SSQ decision completes a review assignment and writes the
    service row (duty ``ssq_review``)."""
    now = timezone.now()
    assignment = (SSQReviewAssignment.unscoped.filter(ssq=ssq, reviewer=reviewer, completed_at__isnull=True)
                  .order_by("-assigned_at").first())
    if assignment is None:
        assignment = SSQReviewAssignment.unscoped.create(ssq=ssq, reviewer=reviewer,
                                                         assigned_at=ssq.submitted_at or now)
    SSQReviewAssignment.unscoped.filter(pk=assignment.pk).update(completed_at=now, outcome=outcome[:20],
                                                                 updated_at=now)
    row = ssq.qa_request_programme
    programme = row.programme
    qp = QAPersonnel.objects.filter(staff__user=reviewer).first()
    staff = getattr(reviewer, "staff_profile", None)
    if qp is not None:
        kind, pid, extra = "qa_personnel", qp.pk, {"qa_personnel": qp}
    elif staff is not None:
        kind, pid, extra = "staff", staff.pk, {"staff": staff}
    else:
        return assignment
    ServiceRecord.unscoped.create(
        person_type=kind, person_id=pid, person_name=_name(reviewer)[:150], case=None, inspection=None,
        institution=row.qa_request.institution, programme=programme,
        discipline=getattr(programme.category, "discipline", None) if programme.category_id else None,
        duty="ssq_review", served_from=timezone.localdate(), served_to=timezone.localdate(), ssq_count=1,
        source="ssq_review", **extra)
    return assignment


# ===========================================================================
# Override Post-Visit Personnel (applied by the two-officer handler)
# ===========================================================================
@transaction.atomic
def apply_attendance(inspection, changes, *, actor, pending_action=None):
    """changes: [{"pvp_id": int, "attended": bool, "reason": str}] or
    [{"staff_id": int, "attended": True, "reason": str}] for an unplanned
    attendee. Updates PostVisitPersonnel; after closure, the service history
    gets compensating / new rows (append-only)."""
    from staff.models import Staff

    closed = inspection.status == "closed"
    for change in changes:
        reason = (change.get("reason") or "").strip()
        attended = bool(change.get("attended"))
        if change.get("pvp_id"):
            pvp = PostVisitPersonnel.unscoped.select_for_update().filter(pk=change["pvp_id"],
                                                                         inspection=inspection).first()
            if pvp is None or pvp.attended == attended:
                continue
            pvp.attended, pvp.override_reason, pvp.overridden_via = attended, reason, pending_action
            pvp.save(update_fields=["attended", "override_reason", "overridden_via", "updated_at"])
        elif change.get("staff_id"):
            staff = Staff.objects.filter(pk=change["staff_id"]).first()
            if staff is None or PostVisitPersonnel.unscoped.filter(inspection=inspection, staff=staff).exists():
                continue
            pvp = PostVisitPersonnel.unscoped.create(inspection=inspection, staff=staff, planned=False,
                                                     attended=True, role_on_team="Unplanned attendee",
                                                     override_reason=reason, overridden_via=pending_action)
        else:
            continue
        if closed:
            if pvp.attended:
                write_service(pvp, source="attendance_override", reason=reason)
            else:
                void_service(pvp, reason=reason or "Did not attend (override)")
    timeline.record(inspection.case.institution, "inspection.attendance_overridden", subject=inspection,
                    actor=actor, visibility="internal", internal_text="Post-visit personnel corrected.")


# ===========================================================================
# Post-visit reports (SOP-03 stage 12) and the stage-13 recommendation
# ===========================================================================
def reports_of(case):
    return list(PostVisitReport.unscoped.filter(inspection__case=case)
                .select_related("qa_request_programme__programme__level", "submitted_by", "inspection")
                .order_by("pk"))


def report_variables(case, *, endorsed_now=None):
    rows = reports_of(case)
    return {"reports_total": len(rows),
            "reports_open": sum(1 for r in rows if r.status != "director_endorsed" and r.pk != endorsed_now)}


@transaction.atomic
def ensure_reports(case, *, actor=None, returned_remark=""):
    """Stage 12 entered: one report per active programme (draft, task for the
    Team Lead). Re-entered after a stage-13 return: endorsed reports go back
    to *returned* with the remark."""
    from accreditation.models import QARequestProgramme

    inspection = live_inspection(case) or create_for_case(case, actor=actor)
    if inspection is None:
        return []
    out = []
    for row in QARequestProgramme.unscoped.filter(qa_request=case.qa_request, status="active").select_related(
            "programme"):
        report = PostVisitReport.unscoped.filter(inspection=inspection, qa_request_programme=row).first()
        if report is None:
            report = PostVisitReport.unscoped.create(inspection=inspection, qa_request_programme=row)
            flow.start("post_visit_report", report, actor=actor)
            report.refresh_from_db()
        elif returned_remark and report.status == "director_endorsed":
            instance = flow.instance_for(report, "post_visit_report")
            flow.force_stage(instance, "returned", actor, remark=returned_remark)
            ReportEndorsement.unscoped.create(report=report, layer="director", action="return",
                                              content=returned_remark, actor=actor if getattr(actor, "pk", None)
                                              else None)
            report.refresh_from_db()
        out.append(report)
    return out


def _on_case_stages_changed(instance, entered, left, transition, actor, remark, subject, automatic):
    from accreditation.models import QACase

    if not isinstance(subject, QACase) or "post_visit_report" not in entered:
        return
    action = getattr(transition, "action_code", "")
    ensure_reports(subject, actor=actor, returned_remark=remark if action == "return" else "")


flow.on_stages_changed(_on_case_stages_changed)


def attended_users(inspection):
    return {p.user.pk for p in attendance(inspection) if p.attended and p.user is not None}


def report_holders(report):
    from qa_core.models import QATaskLink
    from django.contrib.contenttypes.models import ContentType

    link = QATaskLink.objects.filter(content_type=ContentType.objects.get_for_model(PostVisitReport),
                                     object_id=report.pk, stage_code=report.status, closed_at__isnull=True
                                     ).select_related("task").first()
    if link is None:
        return [], None
    return [r.user for r in link.task.assignee_rows.select_related("user").exclude(state="declined")], link


def can_write(report, user):
    """B17: only someone who actually attended writes / submits the report."""
    if user is None or not user.is_authenticated or is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    if report.status not in ("draft", "returned"):
        return False, "The report is with the endorsers."
    if report.inspection.status != "closed":
        return False, "Record the visit first (the Team Lead marks it completed)."
    if user.pk not in attended_users(report.inspection):
        return False, "Only someone who attended the visit can write and submit the report (B17)."
    return True, ""


def can_endorse(report, user):
    layer = LAYER_FOR_STAGE.get(report.status)
    if layer is None:
        return False, "Nothing to endorse at this stage."
    if user is None or not user.is_authenticated or is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    if not (user.is_superuser or user.has_perm("inspection.endorse_post_visit_report")):
        return False, "Endorsing needs inspection.endorse_post_visit_report."
    holders, _link = report_holders(report)
    if user.is_superuser or user in holders:
        return True, ""
    names = ", ".join(_name(u) for u in holders) or "nobody yet (department queue)"
    return False, f"This layer ({LAYER_LABEL[layer]}) is with {names}."


@transaction.atomic
def save_report(report, *, user, summary=None, findings=None, recommendation=None):
    report = PostVisitReport.unscoped.select_for_update().get(pk=report.pk)
    ok, why = can_write(report, user)
    if not ok:
        raise PermissionDenied(why)
    fields = ["updated_at"]
    if summary is not None:
        report.summary = summary.strip()
        fields.append("summary")
    if findings is not None:
        report.findings = {k: (v or "").strip() for k, v in findings.items()}
        fields.append("findings")
    if recommendation is not None:
        if recommendation and recommendation not in dict(PostVisitReport.RECOMMENDATION_CHOICES):
            raise ValidationError({"recommendation": "Choose a recommendation."})
        report.recommendation = recommendation
        fields.append("recommendation")
    report.save(update_fields=fields)
    return report


@transaction.atomic
def submit_report(report, *, user):
    report = PostVisitReport.unscoped.select_for_update().get(pk=report.pk)
    ok, why = can_write(report, user)
    if not ok:
        raise PermissionDenied(why)
    errors = {}
    if len((report.summary or "").strip()) < 20:
        errors["summary"] = "Write the summary of findings (at least 20 characters)."
    if not report.recommendation:
        errors["recommendation"] = "Choose the team's recommendation."
    if errors:
        raise ValidationError(errors)
    action = "submit" if report.status == "draft" else "resubmit"
    report.submitted_by, report.submitted_at = user, timezone.now()
    if action == "resubmit":
        report.version += 1
    report.save(update_fields=["submitted_by", "submitted_at", "version", "updated_at"])
    ReportEndorsement.unscoped.create(report=report, layer="team", action="submit", actor=user,
                                      content="Re-submitted" if action == "resubmit" else "")
    flow.transition(flow.instance_for(report, "post_visit_report"), action, user)
    report.refresh_from_db()
    return report


@transaction.atomic
def endorse(report, *, user, action, content=""):
    """``endorse`` (move on), ``add_content`` (an addendum, the report stays)
    or ``return`` (remark required; back to the submitter)."""
    report = PostVisitReport.unscoped.select_for_update().get(pk=report.pk)
    ok, why = can_endorse(report, user)
    if not ok:
        raise PermissionDenied(why)
    layer = LAYER_FOR_STAGE[report.status]
    content = (content or "").strip()
    instance = flow.instance_for(report, "post_visit_report")
    if action == "add_content":
        if len(content) < 3:
            raise ValidationError({"content": "Write the content to add."})
        ReportEndorsement.unscoped.create(report=report, layer=layer, action="add_content", content=content,
                                          actor=user)
        return report
    if action == "return":
        if len(content) < 3:
            raise ValidationError({"content": "A return needs a remark (it is pinned on the returned task)."})
        ReportEndorsement.unscoped.create(report=report, layer=layer, action="return", content=content, actor=user)
        flow.transition(instance, "return", user, remark=content)
    elif action == "endorse":
        ReportEndorsement.unscoped.create(report=report, layer=layer, action="endorse", content=content, actor=user)
        flow.transition(instance, "endorse", user, remark=content)
    else:
        raise ValidationError("Unknown action.")
    # The bridge's pins: the person who acted holds this layer for the case's later reports.
    from accreditation import case_services as cs

    stage_code = {"division_head": "submitted", "deputy_director": "division_endorsed",
                  "director": "deputy_verified"}[layer]
    stage = instance.flow_version.stages.filter(code=stage_code).first()
    if stage is not None and not user.is_superuser:
        cs.pin(report.inspection.case, stage, user)
    report.refresh_from_db()
    return report


def on_report_director_endorsed(ctx):
    """When the Director endorses the last open report, stage 12 moves on."""
    from accreditation import case_services as cs

    report = ctx.subject
    case = report.inspection.case
    # The effect runs before the report's own status is synced: count it as endorsed.
    variables = report_variables(case, endorsed_now=report.pk)
    if variables["reports_open"] == 0 and "post_visit_report" in cs.current_stages(case):
        instance = cs.instance_of(case)
        flow.transition(instance, "reports_endorsed", ctx.actor, variables=variables,
                        from_stage="post_visit_report")
    timeline.record(case.institution, "post_visit_report.endorsed", subject=report, actor=ctx.actor,
                    visibility="internal", internal_text=f"Post-visit report for {report.qa_request_programme.programme} "
                                                         "endorsed by the Director.")


def on_report_submitted(ctx):
    report = ctx.subject
    timeline.record(report.inspection.case.institution, "post_visit_report.submitted", subject=report,
                    actor=ctx.actor, visibility="internal",
                    internal_text=f"Post-visit report for {report.qa_request_programme.programme} submitted.")


def on_report_returned(ctx):
    report = ctx.subject
    timeline.record(report.inspection.case.institution, "post_visit_report.returned", subject=report,
                    actor=ctx.actor, visibility="internal",
                    internal_text=f"Post-visit report for {report.qa_request_programme.programme} returned: "
                                  f"{ctx.remark}")


@transaction.atomic
def recommend(report, *, user, recommendation, note=""):
    """Stage 13: the Director's recommendation for one programme."""
    from accreditation import case_services as cs

    case = report.inspection.case
    ok, why = cs.can_act(case, user, "post_visit_recommendation")
    if not ok:
        raise PermissionDenied(why)
    if recommendation not in dict(PostVisitReport.RECOMMENDATION_CHOICES):
        raise ValidationError({"recommendation": "Choose a recommendation."})
    PostVisitReport.unscoped.filter(pk=report.pk).update(recommendation=recommendation,
                                                         recommendation_note=(note or "").strip(),
                                                         recommended_by=user, updated_at=timezone.now())
    report.refresh_from_db()
    return report


def recommendation_problems(case):
    rows = reports_of(case)
    if not rows:
        return ["There are no post-visit reports."]
    missing = [str(r.qa_request_programme.programme) for r in rows if not r.recommended_by_id]
    return [f"Confirm the recommendation for: {', '.join(missing)}."] if missing else []


# ===========================================================================
# Task routing for the report sub-flow (bridge resolver)
# ===========================================================================
def _report_resolver(report, stage, prefer_user):
    from accreditation import case_services as cs

    case = report.inspection.case
    title = f"{case.institution.code} · {report.qa_request_programme.programme}"
    dept = case.owning_department
    if prefer_user is not None and prefer_user.is_active:
        return [prefer_user], prefer_user.department or dept, title
    if stage.actor_kind == "specific_user":
        user = None
        if stage.form_key == "team_lead":
            user = report.inspection.team_lead
        elif stage.form_key == "submitted_by":
            user = report.submitted_by or report.inspection.team_lead
        return ([user] if user is not None else []), dept, title
    users, dept, _t = cs.resolve_actors(case, stage, None)
    return users, dept, title


task_bridge.register_subject(PostVisitReport, _report_resolver)


# ===========================================================================
# Service-history queries (§9)
# ===========================================================================
def person_totals(person_type, person_id):
    rows = ServiceRecord.unscoped.filter(person_type=person_type, person_id=person_id)
    agg = rows.aggregate(visits=Sum("visit_count"), ssqs=Sum("ssq_count"))
    year = rows.filter(served_to__year=timezone.localdate().year).aggregate(v=Sum("visit_count"))["v"] or 0
    last = rows.filter(visit_count__gt=0, voided_by__isnull=True).order_by("-served_to").values_list(
        "served_to", flat=True).first()
    return {"visits": agg["visits"] or 0, "ssqs": agg["ssqs"] or 0, "visits_this_year": year, "last_served": last}


def engaged_institution_ids(user):
    """Scoping resolver: a resource person sees the institutions of their live
    or recent engagements."""
    rp = ResourcePerson.objects.filter(user=user).first()
    if rp is None:
        return []
    since = timezone.localdate() - datetime.timedelta(days=365)
    return list(QAMapping.unscoped.filter(resource_person=rp, to_date__gte=since)
                .values_list("case__institution_id", flat=True).distinct())


# ===========================================================================
# Visibility for the screens (one rule, here): what a staff user may open
# ===========================================================================
def _scoped(qs):
    qs._mark_scoped()
    return qs


def visible_inspections(user):
    """Inspections of the cases the user may open (SOP-03 visibility), plus
    the ones they lead."""
    from django.db.models import Q

    from accreditation import case_services as cs

    if not cs.can_view_cases(user):
        return Inspection.objects.for_user(user).none()
    return _scoped(Inspection.unscoped.filter(Q(case__in=cs.visible_cases(user).values("pk")) | Q(team_lead=user))
                   .distinct())


def visible_reports(user):
    return _scoped(PostVisitReport.unscoped.filter(inspection__in=visible_inspections(user).values("pk")))


def visible_mappings(user):
    from accreditation import case_services as cs

    if not cs.can_view_cases(user):
        return QAMapping.objects.for_user(user).none()
    return _scoped(QAMapping.unscoped.filter(case__in=cs.visible_cases(user).values("pk")))


def can_view_service(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "") == "staff"
                and (user.is_superuser or user.has_perm("inspection.view_service_history")))


def visible_service(user):
    """The service history is NBTE-wide for holders of view_service_history
    (it is about people, not institution records)."""
    if not can_view_service(user):
        return ServiceRecord.objects.for_user(user).none()
    return _scoped(ServiceRecord.unscoped.all())


def can_view_people(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "") == "staff"
                and (user.is_superuser or user.has_perm("inspection.view_resource_person")))


def endorsements_of(report):
    return list(ReportEndorsement.unscoped.filter(report=report).select_related("actor").order_by("at", "pk"))


def case_rows(case):
    from accreditation.models import QARequestProgramme

    return list(QARequestProgramme.unscoped.filter(qa_request=case.qa_request, status="active")
                .select_related("programme__category__discipline"))


def endorsement_count(user):
    """Reports waiting on ``user`` at an endorsement layer (sidebar badge)."""
    if not (user and user.is_authenticated and (user.is_superuser
                                                 or user.has_perm("inspection.endorse_post_visit_report"))):
        return 0
    rows = visible_reports(user).filter(status__in=tuple(LAYER_FOR_STAGE))[:100]
    return sum(1 for r in rows if user in report_holders(r)[0])


# ===========================================================================
# Portal (Phase 5): resource persons and institutions
# ===========================================================================
def rp_of(user):
    """The resource-person record behind a portal account (or None)."""
    if not (user and user.is_authenticated and getattr(user, "user_type", "") == "resource_person"):
        return None
    return ResourcePerson.objects.filter(user=user).first()


def rp_engagements(rp):
    return (QAMapping.unscoped.filter(resource_person=rp)
            .select_related("case__institution", "qa_request_programme__programme")
            .order_by("-from_date", "-pk"))


def rp_service(rp):
    return (ServiceRecord.unscoped.filter(resource_person=rp)
            .select_related("institution", "programme").order_by("-served_from", "-pk"))


def rp_files(rp):
    return list(ResourcePersonFile.objects.filter(resource_person=rp).order_by("-pk"))


def findings_open(mapping):
    """A resource person writes findings for a visit they are on, until the
    team submits the programme's report."""
    if mapping.duty not in VISIT_DUTIES or mapping.status not in ("active", "released"):
        return False, "Findings open once your engagement is confirmed."
    report = PostVisitReport.unscoped.filter(inspection__case=mapping.case,
                                             qa_request_programme=mapping.qa_request_programme).first()
    if report is not None and report.status not in ("draft", "returned"):
        return False, "The team has submitted this programme's report."
    return True, ""


@transaction.atomic
def submit_findings(mapping, *, user, text):
    mapping = QAMapping.unscoped.select_for_update().get(pk=mapping.pk)
    rp = rp_of(user)
    if rp is None or mapping.resource_person_id != rp.pk:
        raise PermissionDenied("This engagement is not yours.")
    ok, why = findings_open(mapping)
    if not ok:
        raise ValidationError(why)
    text = (text or "").strip()
    if len(text) < 20:
        raise ValidationError({"findings": "Write your findings (at least 20 characters)."})
    QAMapping.unscoped.filter(pk=mapping.pk).update(findings=text)
    mapping.findings = text
    return mapping


def rp_findings_for(report):
    """Findings resource persons submitted for this report's programme —
    shown to the team on the report page."""
    return list(QAMapping.unscoped.filter(case=report.inspection.case,
                                          qa_request_programme=report.qa_request_programme)
                .exclude(findings="").select_related("resource_person", "qa_personnel__staff"))


def institution_inspections(user):
    """Read-only list for the institution's portal (its own visits)."""
    return Inspection.objects.for_user(user).select_related("case").order_by("-scheduled_start")


def lapsed_people_report():
    """Nightly reconciliation (§9 "falling below the threshold later"): every
    active person who no longer qualifies — existing work continues, new work
    is blocked. Listed in the drift report for the department heads."""
    out = []
    for rp in ResourcePerson.objects.filter(status=ResourcePerson.ACTIVE):
        if rules.no_longer_eligible(rp):
            out.append({"kind": "no longer eligible", "subject": f"resource person {rp.full_name}", "stage": "",
                        "why": "; ".join(str(p) for p in rules.nomination_problems(rp))})
    for qp in QAPersonnel.objects.filter(active=True).select_related("staff"):
        if rules.no_longer_eligible(qp):
            out.append({"kind": "no longer eligible", "subject": f"QA personnel {qp.staff}", "stage": "",
                        "why": "; ".join(str(p) for p in rules.staff_problems(qp.staff))})
    return out
