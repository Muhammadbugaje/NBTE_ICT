import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _lock_institution(inst, user):
    from accreditation.models import Application

    return Application.unscoped.create(
        institution=inst, reference=f"APP-{inst.code}-LOCK", status="document_review",
        submitted_at=timezone.now(), submitted_by=user)


def test_locked_team_changes_require_approved_request(proposed_inst, inst_user, make_user, qa_seed):
    from accounts.models import Role, User
    from institutions import change_requests, profile, services
    from institutions.models import InstitutionChangeRequest, InstitutionUser, ManagementRole
    from staff.models import Department

    _lock_institution(proposed_inst, inst_user)
    assert services.institution_team_locked(proposed_inst)
    with pytest.raises(PermissionDenied):
        services.invite_user(proposed_inst, {"first_name": "A", "last_name": "User",
                                             "email": "locked@zit.edu.ng", "role": "inst-academic-dept"},
                             actor=inst_user)

    role, _ = ManagementRole.objects.get_or_create(
        code="registrar", defaults={"name": "Registrar", "is_active": True,
                                    "requires_email": True, "requires_phone": True})
    with pytest.raises(PermissionDenied):
        profile.set_holder(proposed_inst, role, {"full_name": "New Registrar", "email": "reg@zit.edu.ng",
                                                 "phone": "08030000000"}, actor=inst_user)

    ict = Department.objects.get(code="ict")
    ict.director = make_user("change.director@t.local", role="director", dept="ict")
    ict.save(update_fields=["director"])
    reviewer = make_user("change.reviewer@t.local", role="officer", dept="ict")
    lead = make_user("change.es@t.local", superuser=True)
    ict_head = User.objects.create(
        email="change.ict-head@zit.edu.ng", first_name="ICT", last_name="Head", user_type=User.INSTITUTION,
        institution=proposed_inst, role=Role.objects.get(code="inst-admin"), is_active=True)
    InstitutionUser.unscoped.create(institution=proposed_inst, user=ict_head,
                                    account_kind=InstitutionUser.ICT_HEAD)
    with pytest.raises(PermissionDenied):
        change_requests.submit(proposed_inst, actor=ict_head, change_type=InstitutionChangeRequest.USER_INVITE,
                               data={}, reason="Cannot submit this.")
    request = change_requests.submit(
        proposed_inst, actor=inst_user, change_type=InstitutionChangeRequest.USER_INVITE,
        data={"first_name": "Ada", "last_name": "Obi", "email": "ada.change@zit.edu.ng",
              "role": "inst-academic-dept", "job_title": "", "institution_department": "", "grade_level": None},
        reason="The institution needs an additional academic user.")
    assert not User.objects.filter(email="ada.change@zit.edu.ng").exists()
    with pytest.raises(PermissionDenied):
        change_requests.submit(proposed_inst, actor=reviewer, change_type=InstitutionChangeRequest.USER_INVITE,
                               data={}, reason="Not the Chief Executive.")

    request = change_requests.assign(request, actor=lead, assignee=reviewer)
    assert request.status == InstitutionChangeRequest.ASSIGNED
    change_requests.decide(request, actor=reviewer, decision="approve")
    request.refresh_from_db()
    assert request.status == InstitutionChangeRequest.APPROVED
    invited = User.objects.get(email="ada.change@zit.edu.ng")
    assert InstitutionUser.unscoped.filter(
        institution=proposed_inst, user=invited, is_active_link=True).exists()


def test_management_request_applies_only_after_assignment_and_approval(
        proposed_inst, inst_user, make_user, qa_seed):
    from institutions import change_requests
    from institutions.models import InstitutionChangeRequest, ManagementRole, ManagementStaff
    _lock_institution(proposed_inst, inst_user)
    role = ManagementRole.objects.create(code="registrar-change", name="Registrar", is_active=True,
                                         requires_email=True, requires_phone=True)
    lead = make_user("change.lead@t.local", superuser=True)
    reviewer = make_user("change.ict.reviewer@t.local", role="officer", dept="ict")
    row = change_requests.submit(
        proposed_inst, actor=inst_user, change_type=InstitutionChangeRequest.MANAGEMENT,
        data={"role_code": role.code, "title": "Dr", "full_name": "Amina Registrar",
              "email": "registrar@zit.edu.ng", "phone": "08030000000"},
        reason="The current registrar has left the institution.")
    assert not ManagementStaff.unscoped.filter(institution=proposed_inst, role=role, is_current=True).exists()
    row = change_requests.assign(row, actor=lead, assignee=reviewer)
    change_requests.decide(row, actor=reviewer, decision="approve", note="Verified against the records.")
    assert ManagementStaff.unscoped.filter(institution=proposed_inst, role=role, is_current=True,
                                           full_name="Amina Registrar").exists()


def test_profile_request_applies_only_after_approval(proposed_inst, inst_user, make_user, qa_seed):
    from institutions import change_requests
    from institutions.models import InstitutionChangeRequest
    from institutions.portal_views import _profile_initial

    _lock_institution(proposed_inst, inst_user)
    profile_data = _profile_initial(proposed_inst)
    profile_data["website"] = "https://updated.example.test"
    row = change_requests.submit(
        proposed_inst, actor=inst_user, change_type=InstitutionChangeRequest.PROFILE,
        data={"profile": profile_data}, reason="The institution has moved to a new official website.")
    proposed_inst.refresh_from_db()
    assert proposed_inst.website != "https://updated.example.test"

    lead = make_user("profile.change.lead@t.local", superuser=True)
    reviewer = make_user("profile.change.reviewer@t.local", role="officer", dept="ict")
    row = change_requests.assign(row, actor=lead, assignee=reviewer)
    row = change_requests.decide(row, actor=reviewer, decision="review", note="Verified the requested update.")
    assert row.status == InstitutionChangeRequest.REVIEWED
    with pytest.raises(PermissionDenied):
        change_requests.decide(row, actor=reviewer, decision="approve")
    proposed_inst.refresh_from_db()
    assert proposed_inst.website != "https://updated.example.test"
    change_requests.decide(row, actor=lead, decision="approve", note="Senior approval granted.")
    proposed_inst.refresh_from_db()
    assert proposed_inst.website == "https://updated.example.test"


def test_inactive_reviewer_and_lead_cannot_act_on_change_requests(
        proposed_inst, inst_user, make_user, qa_seed):
    from institutions import change_requests
    from institutions.models import InstitutionChangeRequest, ManagementRole

    _lock_institution(proposed_inst, inst_user)
    role = ManagementRole.objects.create(code="registrar-inactive-check", name="Registrar", is_active=True)
    lead = make_user("inactive.check.lead@t.local", superuser=True)
    reviewer = make_user("inactive.check.reviewer@t.local", role="officer", dept="ict")
    row = change_requests.submit(
        proposed_inst, actor=inst_user, change_type=InstitutionChangeRequest.MANAGEMENT,
        data={"role_code": role.code, "title": "", "full_name": "Amina Registrar", "email": "", "phone": ""},
        reason="The current registrar has left the institution.")
    row = change_requests.assign(row, actor=lead, assignee=reviewer)

    reviewer.is_active = False
    reviewer.save(update_fields=["is_active"])
    with pytest.raises(PermissionDenied):
        change_requests.decide(row, actor=reviewer, decision="approve")

    inactive_lead = make_user("inactive.check.superadmin@t.local", superuser=True)
    inactive_lead.is_active = False
    inactive_lead.save(update_fields=["is_active"])
    assert not change_requests.is_lead(inactive_lead)


def test_institution_portal_surfaces_locked_request_path(
        proposed_inst, inst_user, client_for, portal_host, qa_seed):
    _lock_institution(proposed_inst, inst_user)
    client = client_for(inst_user, host=portal_host)

    profile_page = client.get("/institution/")
    assert profile_page.status_code == 200 and not profile_page.context["editable"]
    response = client.post("/institution/", {"website": "https://bypass.example.test"})
    assert response.status_code == 302 and response["Location"] == \
        "/institution/change-requests/new/?type=profile"
    page = client.get("/institution/management/")
    assert page.status_code == 200 and not page.context["can_edit"] and page.context["team_locked"]
    response = client.post("/institution/management/", {"full_name": "Bypass Attempt"})
    assert response.status_code == 302 and response["Location"] == \
        "/institution/change-requests/new/?type=management"
    response = client.post("/institution/users/", {"first_name": "Bypass", "last_name": "Attempt",
                                                   "email": "bypass@zit.edu.ng",
                                                   "role": "inst-academic-dept"})
    assert response.status_code == 302 and response["Location"] == \
        "/institution/change-requests/new/?type=user_invite"
    assert client.get("/institution/change-requests/new/?type=management").status_code == 200
