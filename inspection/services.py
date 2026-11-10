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
    VISIT_DUTIES, Inspection, InspectionTeamMember, PostVisitMemberReport, PostVisitMemberReview,
    PostVisitPersonnel, PostVisitReport, QAMapping, QAPersonnel, ReportEndorsement, ResourcePerson,
    ResourcePersonDiscipline, ResourcePersonFile, ServiceRecord, SSQReviewAssignment,
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

    if actor is not None:
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


def mapping_approval_state(mapping):
    """Where a mapping sits in the v2.7 two-step approval (Director → ES)."""
    if mapping.es_approved_at:
        return "es_approved", "Approved by the ES"
    if mapping.director_approved_at:
        return "director_approved", "Director approved — waiting for the ES"
    return "pending", "Waiting for the Director"


def can_approve_mapping(mapping, user, layer):
    """Director layer: the handling department's Director (or the ES /
    superuser). ES layer: the ES (or a superuser)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False, "Sign in as NBTE staff."
    if is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    import org

    if user.is_superuser:
        return True, ""
    if layer == "es":
        return (org.is_es(user), "Only the Executive Secretary approves this step.")
    if org.is_es(user):
        return True, ""
    dept = mapping.case.owning_department or getattr(mapping.case.institution, "handling_department", None)
    if dept is not None and org.is_director(user, dept):
        return True, ""
    return False, ("Only the handling department Director approves the mapping "
                   "(the ES may also act).")


@transaction.atomic
def approve_mapping(mapping, *, actor, layer, approve=True, remark=""):
    """v2.7: the Director approves (or sends back) a mapping, then the ES
    confirms it. Approval is visibility, not eligibility: rules still decide
    who may be mapped, and every mapping is shown to the whole case team."""
    mapping = QAMapping.unscoped.select_for_update().get(pk=mapping.pk)
    ok, why = can_approve_mapping(mapping, actor, layer)
    if not ok:
        raise PermissionDenied(why)
    now = timezone.now()
    if layer == "director":
        if mapping.es_approved_at:
            raise ValidationError("The ES has already approved this mapping.")
        mapping.director_approved_by = actor if approve else None
        mapping.director_approved_at = now if approve else None
        if not approve:
            mapping.es_approved_by = None
            mapping.es_approved_at = None
    else:
        if not mapping.director_approved_at:
            raise ValidationError("The Director must approve the mapping first.")
        mapping.es_approved_by = actor if approve else None
        mapping.es_approved_at = now if approve else None
    mapping.save(update_fields=["director_approved_by", "director_approved_at", "es_approved_by",
                                "es_approved_at", "updated_at"])
    try:
        from qa_core import timeline

        timeline.record(mapping.case.institution, event_code="qa.mapping.approved", subject=mapping.case,
                        actor=actor,
                        internal_text=(f"{mapping.person} → {mapping.qa_request_programme.programme} "
                                       f"({mapping.get_duty_display()}): {layer} "
                                       f"{'approved' if approve else 'sent back'}"
                                       f"{(' — ' + remark) if remark else ''}"))
    except Exception:  # the approval itself must stand
        pass
    return mapping


def on_mapping_active(mapping):
    """An active visit-duty mapping joins the planned team of a scheduled inspection."""
    if mapping.duty not in VISIT_DUTIES:
        return
    inspection = Inspection.unscoped.filter(case=mapping.case, status="scheduled").order_by("-pk").first()
    if inspection is not None and not InspectionTeamMember.unscoped.filter(inspection=inspection,
                                                                            qa_mapping=mapping).exists():
        InspectionTeamMember.unscoped.create(inspection=inspection, qa_mapping=mapping,
                                             role_on_team=mapping.get_duty_display())
    if inspection is not None and mapping.resource_person_id:
        person = mapping.resource_person
        if person.user_id and person.user.is_active:
            from django.db import transaction

            from notifications.services import notify

            dates = f"{inspection.scheduled_start:%d %b %Y}"
            if inspection.scheduled_end and inspection.scheduled_end != inspection.scheduled_start:
                dates += f" to {inspection.scheduled_end:%d %b %Y}"
            transaction.on_commit(
                lambda user=person.user, dates=dates: notify(
                    user,
                    title="You have been assigned to a QA visit",
                    body=f"{inspection.case.institution.name}: {dates}. "
                         "Review the engagement and submit your findings in My engagements.",
                    url="/engagements/",
                )
            )


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
        _notify_bodies_of_visit(case, visit)  # v2.1 R.5 X43
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
    _sync_attendance_duty(inspection)
    _notify_team_of_visit(inspection)
    return inspection


def _sync_attendance_duty(inspection):
    """v2.1 G7: the visit's team is "On official duty" on the visit days
    (dates are written with .update(), which sends no signal)."""
    from attendance.duty import sync_visit_duty

    sync_visit_duty(inspection)


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
    if (inspection.scheduled_start and actual_start != inspection.scheduled_start) or (
            inspection.scheduled_end and actual_end != inspection.scheduled_end):
        # v2.7: the dates are the approved schedule. A move is a controlled
        # change (Director → ES), applied on the case page before recording.
        raise ValidationError("The visit dates are the approved schedule. Ask for a date change on the case "
                              "page; an approved change updates the schedule first.")
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
    _sync_attendance_duty(inspection)  # v2.1 G7: actual dates replace the scheduled ones
    _close_recording_task(inspection, actor=actor)
    return inspection


# ---------------------------------------------------------------------------
# The record-the-visit task, opened on its own (v2.6)
# ---------------------------------------------------------------------------
REC_RECORD_STAGE = "record_visit"


def auto_recording_task(inspection, *, now=None):
    """v2.6: the team lead's board gains the *record the visit* task by itself
    on the visit's last day (or the first run after it, if nobody recorded):
    the actual dates on the form come prefilled from the schedule, and the
    lead may only change or extend them. Idempotent — one open task at most."""
    from datetime import timedelta

    from django.contrib.contenttypes.models import ContentType
    from config import registry as config
    from notifications.models import Notification
    from notifications.services import notify
    from tasks import services as task_services
    from tasks.models import TaskCategory, TaskAssignee

    from qa_core.models import QATaskLink

    if inspection.team_lead_id is None:
        return None
    ct = ContentType.objects.get_for_model(Inspection, for_concrete_model=True)
    if (QATaskLink.objects.filter(content_type=ct, object_id=inspection.pk, stage_code=REC_RECORD_STAGE,
                                  closed_at__isnull=True).exists()):
        return None
    sys_user = task_bridge.system_user()
    lead = inspection.team_lead
    inst = inspection.case.institution.name if inspection.case.institution_id else "the institution"
    grace = int(config.get("qa.visit.recording_grace_days", 3) or 3)
    due_date = inspection.scheduled_end + timedelta(days=grace)
    title = f"[QA] Record the visit — {inst} ({inspection.reference})"
    description = (f"The visit to {inst} has reached its last day "
                   f"({inspection.scheduled_start:%d %b %Y} – {inspection.scheduled_end:%d %b %Y}).\n\n"
                   "Open it in QA and record the visit. The actual dates come prefilled from the schedule — "
                   "only change or extend them if the visit ran longer; then tick anyone who did not attend. "
                   "The task closes by itself when the visit is recorded.")
    with task_bridge.bridge_active():
        category = TaskCategory.objects.filter(code="qa-inspection").first() or \
            TaskCategory.objects.filter(code="qa-sop03").first()
        task = task_services.create_task(created_by=sys_user, title=title[:200], description=description,
                                         category=category, due_date=due_date, requires_review=False,
                                         priority=None)
        if not TaskAssignee.objects.filter(task=task, user=lead).exists():
            TaskAssignee.objects.create(task=task, user=lead, state=TaskAssignee.ASSIGNED, assigned_by=sys_user)
            task_services.log_activity(task, "assignee_added", actor=sys_user,
                                       message=f"Assigned by QA to {(lead.get_full_name() or lead.email)}",
                                       metadata={"assignee_id": lead.pk})
        QATaskLink.objects.create(task=task, content_type=ct, object_id=inspection.pk,
                                  stage_code=REC_RECORD_STAGE, kind="stage_action",
                                  due_at=timezone.now() + timedelta(days=grace))
        task_services.open_task(task, sys_user)
    timeline.record(inspection.case.institution, "inspection.record_task_opened", subject=inspection,
                    actor=sys_user, visibility="internal",
                    internal_text="Record-the-visit task opened for the team lead "
                    f"{(lead.get_full_name() or lead.email) or ''}.")
    notify(lead, title=f"Record the visit to {inst}",
           body=f"The visit ({inspection.reference}) reached its last day "
                f"{inspection.scheduled_end:%d %b %Y}. Record the attendance: the dates are pre-filled.",
           url=f"/qa/inspection/inspections/{inspection.pk}/", category=Notification.QA)
    return task


def visit_recording_sweep(now=None):
    """v2.6: open the record-the-visit task for every scheduled inspection
    whose last day has arrived or passed and whose visit nobody recorded yet.
    Returns counts (called from the nightly reminders run)."""
    today = timezone.localdate(now) if now else timezone.localdate()
    due = [i for i in Inspection.unscoped.filter(status="scheduled", scheduled_end__lte=today, team_lead__isnull=False)
           .select_related("case", "case__institution", "team_lead")]
    opened = 0
    for inspection in due:
        if auto_recording_task(inspection, now=now) is not None:
            opened += 1
    return {"due": len(due), "opened": opened}


def _close_recording_task(inspection, *, actor=None):
    """The visit is recorded — the auto task closes itself."""
    try:
        task_bridge.close_for_stage(inspection, REC_RECORD_STAGE, reason="recorded", actor=actor)
    except Exception:  # noqa: BLE001 — the visit recording never fails over the task bridge
        import logging

        logging.getLogger(__name__).exception("recording task close failed (inspection %s)", inspection.pk)


def on_inspection_closed(ctx):
    inspection = ctx.subject
    Inspection.unscoped.filter(pk=inspection.pk).update(closed_at=timezone.now())
    for pvp in attendance(inspection):
        if pvp.attended:
            write_service(pvp, source="inspection_closed")
    # Visit duties are done: release their mappings (R-11 / the load count).
    QAMapping.unscoped.filter(case=inspection.case, status="active", duty__in=VISIT_DUTIES).update(
        status="released", released_at=timezone.now(), updated_at=timezone.now())
    for report in PostVisitReport.unscoped.filter(inspection=inspection):
        ensure_member_reports(report)


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
    assignment.completed_at, assignment.outcome = now, outcome[:20]
    assignment.save(update_fields=["completed_at", "outcome", "updated_at"])
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
    if closed:
        for report in PostVisitReport.unscoped.filter(inspection=inspection):
            ensure_member_reports(report)


# ===========================================================================
# Post-visit reports (SOP-03 stage 12) and the stage-13 recommendation
# ===========================================================================
def reports_of(case):
    return list(PostVisitReport.unscoped.filter(inspection__case=case)
                .select_related("qa_request_programme__programme__level", "submitted_by", "inspection")
                .order_by("pk"))


def _member_programmes(inspection, attendee):
    if attendee.resource_person_id:
        identity = {"resource_person_id": attendee.resource_person_id}
    elif attendee.qa_personnel_id:
        identity = {"qa_personnel_id": attendee.qa_personnel_id}
    else:
        qa_personnel = QAPersonnel.objects.filter(staff_id=attendee.staff_id).first()
        if qa_personnel is None:
            return set(PostVisitReport.unscoped.filter(inspection=inspection).values_list(
                "qa_request_programme_id", flat=True))
        identity = {"qa_personnel_id": qa_personnel.pk}
    mappings = QAMapping.unscoped.filter(case=inspection.case, duty__in=VISIT_DUTIES, **identity)
    programme_ids = set(mappings.values_list("qa_request_programme_id", flat=True))
    if programme_ids:
        return programme_ids
    return set(PostVisitReport.unscoped.filter(inspection=inspection).values_list(
        "qa_request_programme_id", flat=True))


def _member_identity(attendee):
    user = attendee.user
    return ("user", user.pk) if user is not None else (attendee.person_type, attendee.person_id)


def _attendees_for_report(report):
    grouped = {}
    for attendee in attendance(report.inspection):
        if report.qa_request_programme_id not in _member_programmes(report.inspection, attendee):
            continue
        grouped.setdefault(_member_identity(attendee), []).append(attendee)
    return [next((item for item in candidates if item.attended), candidates[0])
            for candidates in grouped.values()]


def ensure_member_reports(report):
    """Create one owned contribution for each eligible, attended person."""
    if report.inspection.status != "closed":
        return []
    for attendee in _attendees_for_report(report):
        if not attendee.attended:
            continue
        user = attendee.user
        if user is not None:
            member_report, created = PostVisitMemberReport.unscoped.get_or_create(
                report=report, attendee=attendee, defaults={"author": user})
            if created:
                _notify_member_report_user(
                    user, report, title="Your post-visit report is ready",
                    body=f"Submit your individual findings for "
                         f"{report.qa_request_programme.programme}. The Team Lead will review them.")
    return list(PostVisitMemberReport.unscoped.filter(report=report).select_related(
        "attendee__staff__user", "attendee__qa_personnel__staff__user",
        "attendee__resource_person__user", "author", "reviewed_by"))


def member_report_for_user(report, user):
    if report is None or user is None:
        return None
    return next((row for row in ensure_member_reports(report) if row.author_id == user.pk), None)


def member_report_roster(report, *, user=None):
    """Return each relevant team member, including absentees and missing accounts."""
    from types import SimpleNamespace

    rows = []
    reports = {item.attendee_id: item for item in ensure_member_reports(report)}
    for attendee in _attendees_for_report(report):
        member_report = reports.get(attendee.pk)
        rows.append(SimpleNamespace(
            attendee=attendee, person=attendee.person, member_report=member_report,
            status=("Absent" if not attendee.attended else
                    "No portal account linked" if attendee.user is None else
                    member_report.get_status_display() if member_report is not None else "Report not assigned"),
            can_view=(getattr(attendee.user, "pk", None) == getattr(user, "pk", None)
                      or report.inspection.team_lead_id == getattr(user, "pk", None)
                      or report.submitted_at is not None),
        ))
    return rows


def member_report_counts(report):
    rows = member_report_roster(report, user=report.inspection.team_lead)
    counts = {"total": len(rows), "absent": 0, "pending": 0, "submitted": 0,
              "returned": 0, "accepted": 0, "skipped": 0, "unlinked": 0}
    for row in rows:
        if not row.attendee.attended:
            counts["absent"] += 1
        elif row.member_report is None:
            counts["unlinked"] += 1
        else:
            counts[row.member_report.status] = counts.get(row.member_report.status, 0) + 1
            if row.member_report.status in ("draft", "returned"):
                counts["pending"] += 1
    counts["awaiting_review"] = counts["submitted"]
    return counts


def member_reports_ready(report):
    roster = member_report_roster(report, user=report.inspection.team_lead)
    pending = []
    for row in roster:
        if not row.attendee.attended:
            continue
        contribution = row.member_report
        if contribution is None:
            pending.append(str(row.person) + " (no linked portal account)")
        elif contribution.author_id == report.inspection.team_lead_id and contribution.status == "submitted":
            continue
        elif contribution.status not in ("accepted", "skipped"):
            pending.append(str(row.person) + f" ({row.status.lower()})")
    return pending


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
    holders = ([r.user for r in link.task.assignee_rows.select_related("user").exclude(state="declined")]
               if link is not None else [])
    if holders:
        return holders, link
    if report.status in ("draft", "returned"):
        if report.status == "returned" and report.submitted_by_id:
            return [report.submitted_by], link
        from accreditation import case_services as cs

        fallback = cs.post_visit_fallback_holders(report.inspection.case)
        if fallback:
            return [fallback[0]], link
    return [], link


def ict_assignees(inspection):
    """The people allowed to complete the ICT verification (VVT/001):
    the tracking ICT officers of the case, plus anyone on the team whose duty
    is ICT. Superusers, the ES and the Director of ICT are added by the check."""
    from django.contrib.contenttypes.models import ContentType

    users = list(inspection.case.tracking_ict_officers.select_related("role").all())
    for member in InspectionTeamMember.unscoped.filter(inspection=inspection).select_related("staff__user"):
        if member.staff_id and member.staff.user_id and "ict" in (member.role_on_team or "").lower():
            users.append(member.staff.user)
    seen, out = set(), []
    for u in users:
        if u is not None and u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    return out


def can_edit_ict(inspection, user):
    """v2.7: the ICT verification is the assigned ICT officer's alone. The
    Director of ICT, the ES and a super administrator may open it; everyone
    else is locked out (even other visit-team members)."""
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "") != "staff":
        return False, "Sign in to complete the ICT verification."
    if is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    if user.is_superuser:
        return True, ""
    import org

    if org.is_es(user) or org.is_super_admin(user) or org.is_director(user, "ict"):
        return True, ""
    if user in ict_assignees(inspection):
        return True, ""
    names = ", ".join(_name(u) for u in ict_assignees(inspection)) or "nobody yet"
    return False, (f"The ICT verification is completed by the assigned ICT officer ({names}). "
                   "It is locked for everyone else.")


def can_review_cqac_evidence(case, user):
    """Active decision holders and the handling-department Director may review evidence.

    Reviewing the case record does not grant authority to submit a decision.
    """
    from accreditation import case_services as cs, decisions
    from accreditation.models import Decision

    if cs.is_monitor(user):
        return False
    current = set(cs.current_stages(case))
    decision_stages = (("cqac_recommendation", Decision.CQAC),
                       ("cqac_chair_approval", Decision.CHAIR),
                       ("es_final_approval", Decision.ES))
    for code, stage in decision_stages:
        if code in current and decisions.can_decide(case, user, stage)[0]:
            return True
    if current.intersection(code for code, _stage in decision_stages):
        from accreditation.case_changes import is_handling_director

        return is_handling_director(case, user)
    return False


def can_write(report, user):
    """Only the Team Lead compiles and submits the programme report."""
    if user is None or not user.is_authenticated or is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    if report.status not in ("draft", "returned"):
        return False, "The report is with the endorsers."
    if report.inspection.status != "closed":
        return False, "Record the visit first (the Team Lead marks it completed)."
    if not (user.is_superuser or report.inspection.team_lead_id == user.pk):
        return False, "Only the assigned Team Lead compiles and submits the programme report."
    return True, ""


def can_write_member_report(member_report, user):
    if user is None or not user.is_authenticated or is_monitor(user):
        return False, "The ICT monitor has read-only access (R-13)."
    if member_report.author_id != user.pk:
        return False, "This contribution belongs to another visit attendee."
    if member_report.attendee.inspection_id != member_report.report.inspection_id or \
            member_report.report.qa_request_programme_id not in _member_programmes(
                member_report.report.inspection, member_report.attendee):
        return False, "This attendee is not assigned to this programme's visit report."
    if not member_report.attendee.attended:
        return False, "You were recorded as absent and cannot submit a visit report."
    if member_report.report.inspection.status != "closed":
        return False, "Record the visit before submitting an individual report."
    if member_report.report.status not in ("draft", "returned"):
        return False, "The Team Lead has submitted the programme compilation."
    if member_report.status not in ("draft", "returned"):
        return False, "Your report is with the Team Lead for review."
    return True, ""


def _notify_member_report_user(user, report, *, title, body):
    if user is None or not user.is_active:
        return
    from notifications.models import Notification
    from notifications.services import notify

    url = ("/engagements/" if user.user_type == "resource_person" else
           f"/qa/inspection/reports/{report.pk}/")
    transaction.on_commit(lambda: notify(user, title=title, body=body, url=url, category=Notification.QA))


@transaction.atomic
def save_member_report(member_report, *, user, summary="", findings=None, recommendation="", submit=False):
    member_report = PostVisitMemberReport.unscoped.select_for_update().select_related(
        "report__inspection", "attendee").get(pk=member_report.pk)
    ok, why = can_write_member_report(member_report, user)
    if not ok:
        raise PermissionDenied(why)
    summary = (summary or "").strip()
    if submit:
        errors = {}
        if len(summary) < 20:
            errors["summary"] = "Write your findings summary (at least 20 characters)."
        if recommendation not in dict(PostVisitReport.RECOMMENDATION_CHOICES):
            errors["recommendation"] = "Choose your own recommendation."
        if errors:
            raise ValidationError(errors)
    member_report.summary = summary
    member_report.findings = {key: (value or "").strip() for key, value in (findings or {}).items()}
    member_report.recommendation = recommendation
    member_report.version += 1 if member_report.status == "returned" and submit else 0
    if submit:
        member_report.submitted_at = timezone.now()
        if member_report.author_id == member_report.report.inspection.team_lead_id:
            member_report.status = "accepted"
            member_report.reviewed_by = user
            member_report.reviewed_at = timezone.now()
            member_report.review_note = "Team Lead contribution included in the compilation."
        else:
            member_report.status = "submitted"
            member_report.reviewed_by = None
            member_report.reviewed_at = None
            member_report.review_note = ""
            lead = member_report.report.inspection.team_lead
            _notify_member_report_user(
                lead, member_report.report, title="Attendee report needs your review",
                body=f"{user.get_full_name() or user.email} submitted findings for "
                     f"{member_report.report.qa_request_programme.programme}.")
    member_report.save()
    return member_report


@transaction.atomic
def review_member_report(member_report, *, user, action, content=""):
    member_report = PostVisitMemberReport.unscoped.select_for_update().select_related(
        "report__inspection", "author").get(pk=member_report.pk)
    inspection = member_report.report.inspection
    if user is None or not user.is_authenticated or is_monitor(user):
        raise PermissionDenied("Sign in as the Team Lead to review attendee reports.")
    if not (user.is_superuser or inspection.team_lead_id == user.pk):
        raise PermissionDenied("Only the assigned Team Lead reviews individual visit reports.")
    content = (content or "").strip()
    if action not in ("accept", "return", "skip"):
        raise ValidationError("Choose accept, return, or skip.")
    if action in ("return", "skip") and len(content) < 3:
        raise ValidationError({"content": "Give a note explaining the return or skip decision."})
    if action == "skip":
        # The Team Lead may set aside any contribution, even one that was never
        # submitted or is still a draft. The note is the audit trail the CQAC sees.
        if member_report.status == "skipped" and member_report.review_note == content:
            return member_report
    else:
        if member_report.author_id == user.pk:
            raise PermissionDenied("You cannot review your own individual report.")
        if member_report.status != "submitted":
            raise ValidationError("Only a submitted attendee report can be reviewed.")
    member_report.status = {"accept": "accepted", "return": "returned", "skip": "skipped"}[action]
    member_report.reviewed_by = user
    member_report.reviewed_at = timezone.now()
    member_report.review_note = content
    member_report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
    PostVisitMemberReview.unscoped.create(member_report=member_report, action=action, content=content, actor=user)
    _notify_member_report_user(
        member_report.author, member_report.report,
        title={"return": "Team Lead returned your report", "skip": "Team Lead skipped your report",
               "accept": "Team Lead accepted your report"}[action],
        body=(content or "Your contribution was accepted for the programme compilation."))
    return member_report


@transaction.atomic
def skip_attendee_report(report, *, attendee, user, content=""):
    """Skip an attendee who has no report row yet (no portal account or never
    started). Keeps the decision and note visible to the Team Lead and CQAC."""
    report = PostVisitReport.unscoped.select_for_update().select_related(
        "inspection").get(pk=report.pk)
    inspection = report.inspection
    if user is None or not user.is_authenticated or is_monitor(user):
        raise PermissionDenied("Sign in as the Team Lead to review attendee reports.")
    if not (user.is_superuser or inspection.team_lead_id == user.pk):
        raise PermissionDenied("Only the assigned Team Lead can skip an attendee report.")
    content = (content or "").strip()
    if len(content) < 3:
        raise ValidationError({"content": "Give a note explaining the skip decision."})
    if attendee.inspection_id != inspection.pk:
        raise ValidationError("That attendee is not on this visit.")
    member_report, _created = PostVisitMemberReport.unscoped.get_or_create(
        report=report, attendee=attendee, defaults={"author": attendee.user})
    member_report.status = "skipped"
    member_report.reviewed_by = user
    member_report.reviewed_at = timezone.now()
    member_report.review_note = content
    member_report.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
    PostVisitMemberReview.unscoped.create(member_report=member_report, action="skip", content=content, actor=user)
    _notify_member_report_user(
        attendee.user, report, title="Team Lead skipped your report",
        body=content)
    return member_report


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


REPORT_ENDORSEMENT_STAGES = ("submitted", "division_endorsed", "deputy_verified")


def _report_stage_users(report, stage_code):
    from accreditation import case_services as cs

    instance = flow.instance_for(report, "post_visit_report")
    stage = flow.stage(instance, stage_code) if instance is not None else None
    if stage is None:
        return []
    users, _department, _title = cs.resolve_actors(report.inspection.case, stage, None)
    return [user for user in users if user is not None and user.is_active]


def _report_submission_stage(report):
    """Pick the first required endorsement stage after the Team Lead compiles."""
    lead = report.inspection.team_lead
    if lead is None:
        raise ValidationError("Assign the visit's Team Lead before submitting the compilation.")
    import org

    department = report.inspection.case.owning_department
    lead_is_deputy_or_director = org.is_director(lead, department) or \
        (department is not None and lead in org.deputies_of(department))
    candidates = ("deputy_verified",) if lead_is_deputy_or_director else REPORT_ENDORSEMENT_STAGES
    for stage_code in candidates:
        users = _report_stage_users(report, stage_code)
        if not users:
            continue
        if stage_code != "deputy_verified" and any(user.pk == lead.pk for user in users):
            continue
        return stage_code
    raise ValidationError(
        "No active Director is assigned to the handling department, so the report cannot be routed for approval.")


def _report_next_endorsement_stage(report):
    """Return the next staffed stage, skipping vacant intermediate roles."""
    current = report.status
    if current not in REPORT_ENDORSEMENT_STAGES:
        raise ValidationError("The report is not at an endorsement stage.")
    start = REPORT_ENDORSEMENT_STAGES.index(current) + 1
    for stage_code in REPORT_ENDORSEMENT_STAGES[start:]:
        if _report_stage_users(report, stage_code):
            return stage_code
    raise ValidationError(
        "No active Director is assigned to the handling department, so the report cannot advance.")


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
    pending = member_reports_ready(report)
    if pending:
        names = ", ".join(pending[:4])
        more = f" and {len(pending) - 4} more" if len(pending) > 4 else ""
        raise ValidationError(f"Every present attendee must submit a report and the Team Lead must accept it "
                              f"before compiling. Still pending: {names}{more}.")
    target_stage = _report_submission_stage(report)
    action_prefix = "submit" if report.status == "draft" else "resubmit"
    action = {
        "submitted": action_prefix,
        "division_endorsed": f"{action_prefix}_to_deputy",
        "deputy_verified": f"{action_prefix}_to_director",
    }[target_stage]
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
        transition_action = "endorse"
        if report.status == "submitted" and _report_next_endorsement_stage(report) == "deputy_verified":
            transition_action = "endorse_to_director"
        flow.transition(instance, transition_action, user, remark=content)
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
            if user is None:
                visit = getattr(case, "visit", None)
                user = getattr(getattr(visit, "team_lead", None), "user", None)
        elif stage.form_key == "submitted_by":
            user = report.submitted_by or report.inspection.team_lead
        if user is None or not user.is_active:
            user = next((holder for holder in cs.post_visit_fallback_holders(case)
                         if holder is not None and holder.is_active), None)
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
    inspection = live_inspection(mapping.case)
    if inspection is not None and inspection.status == "closed":
        attended = PostVisitPersonnel.unscoped.filter(
            inspection=inspection,
            **({"resource_person": mapping.resource_person} if mapping.resource_person_id else
               {"qa_personnel": mapping.qa_personnel}), attended=True).exists()
        if not attended:
            return False, "You were recorded as absent and cannot submit visit findings."
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
    inspection = Inspection.unscoped.filter(case=mapping.case, status__in=("scheduled", "closed")) \
        .select_related("team_lead").order_by("-pk").first()
    if inspection and inspection.team_lead_id:
        from django.db import transaction

        from notifications.services import notify

        transaction.on_commit(
            lambda recipient=inspection.team_lead: notify(
                recipient,
                title="Resource-person findings submitted",
                body=f"{mapping.resource_person.full_name} submitted findings for "
                     f"{mapping.qa_request_programme.programme}.",
                url=f"/qa/inspection/inspections/{inspection.pk}/",
            )
        )
    return mapping


def rp_findings_for(report):
    """Findings resource persons submitted for this report's programme —
    shown to the team on the report page."""
    rows = list(QAMapping.unscoped.filter(case=report.inspection.case,
                                          qa_request_programme=report.qa_request_programme)
                .exclude(findings="").select_related("resource_person", "qa_personnel__staff"))
    if report.inspection.status == "closed":
        present = set()
        for attendee in attendance(report.inspection):
            if attendee.attended:
                present.add(("rp", attendee.resource_person_id) if attendee.resource_person_id else
                            ("qa", attendee.qa_personnel_id) if attendee.qa_personnel_id else
                            ("staff", attendee.staff_id))
        rows = [mapping for mapping in rows
                if (("rp", mapping.resource_person_id) if mapping.resource_person_id else
                    ("qa", mapping.qa_personnel_id)) in present]
    return rows


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


def _notify_bodies_of_visit(case, visit):
    """v2.1 R.5 (X43): tell each programme's professional body the visit dates."""
    from django.db import transaction

    from accreditation.models import QARequestProgramme
    from institutions import pb_portal

    rows = (QARequestProgramme.unscoped.filter(qa_request=case.qa_request, status=QARequestProgramme.ACTIVE,
                                               programme__professional_body__isnull=False)
            .select_related("programme", "programme__institution", "programme__professional_body"))
    extra = f" ({visit.planned_start:%d %b %Y} to {visit.planned_end:%d %b %Y})"
    for row in rows:
        programme = row.programme
        transaction.on_commit(lambda p=programme: pb_portal.notify_body(p, "visit_scheduled", extra=extra))


def team_members_of(inspection):
    """[(user, role)] on the planned team: the lead, mapped personnel and
    mapped resource people (with a portal account)."""
    from .models import InspectionTeamMember

    out = []
    seen = set()
    lead = inspection.team_lead
    if getattr(lead, "pk", None):
        out.append((lead, "Team lead"))
        seen.add(lead.pk)
    for tm in InspectionTeamMember.unscoped.filter(inspection=inspection).select_related("qa_mapping", "staff"):
        user = role = None
        if tm.qa_mapping_id:
            role = tm.role_on_team or "Visitor"
            person = tm.qa_mapping
            if person.qa_personnel_id:
                user = person.qa_personnel.user
            elif person.resource_person_id and person.resource_person.user_id:
                user = person.resource_person.user
        elif tm.staff_id and getattr(tm.staff, "user_id", None):
            user, role = tm.staff.user, tm.role_on_team or "Visitor"
        if user is not None and getattr(user, "is_active", False) and user.pk not in seen:
            out.append((user, role))
            seen.add(user.pk)
    return out


def _notify_team_of_visit(inspection):
    """Tell every planned team member the visit dates (staff and external
    resource people with a portal account)."""
    from institutions import services as inst_services

    if inspection.scheduled_start is None:
        return
    members = team_members_of(inspection)
    if not members:
        return
    ctx = {"institution": inspection.case.institution.name if inspection.case.institution_id else "",
           "reference": inspection.reference,
           "dates": f"{inspection.scheduled_start:%d %b %Y} to "
                    f"{inspection.scheduled_end or inspection.scheduled_start:%d %b %Y}"}
    inst_services.notify_role("inspection.visit_scheduled", ctx, [u for u, _role in members],
                              url=f"/qa/inspection/inspections/{inspection.pk}/")
