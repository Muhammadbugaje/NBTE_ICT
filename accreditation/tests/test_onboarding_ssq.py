import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import QueryDict


@pytest.fixture
def v3(qa_seed):
    from accreditation import onboarding_v2, onboarding_v3
    from accreditation.seed_sets import install_notifications

    onboarding_v2.install_flow_v2()
    onboarding_v3.install_flow_v3()
    install_notifications()


def _ready_profile(inst, ce):
    from accounts.models import Role, User
    from institutions import profile
    from institutions.models import InstitutionUser, ManagementRole

    InstitutionUser.unscoped.filter(user=ce).update(account_kind="chief_executive")
    ict = User.objects.create(
        email=f"ict.{inst.code.lower()}@t.local", first_name="ICT", last_name="Head",
        user_type="institution", institution=inst, role=Role.objects.get(code="inst-admin"), is_active=True,
    )
    InstitutionUser.unscoped.create(institution=inst, user=ict, account_kind="ict_head")
    for role in ManagementRole.objects.filter(is_mandatory=True, is_active=True):
        profile.set_holder(
            inst, role, {"full_name": f"{role.name} Person", "email": f"{role.code}@t.local", "phone": "0803"},
            actor=ce,
        )


def _at_self_study(proposed_inst, inst_user):
    from accreditation import services
    from accreditation.models import OnboardingProgrammeReview
    from institutions.models import Programme, ProgrammeLevel
    from qa_core import flow
    from qa_core.departments import qa_department

    app = services.start_application(proposed_inst, user=inst_user)
    instance = services.instance_of(app)
    flow.force_stage(instance, "ssq_in_progress", inst_user, remark="test fixture")
    app.refresh_from_db()
    app.programme_round = 1
    app.save(update_fields=["programme_round", "updated_at"])
    programme = Programme.unscoped.create(
        institution=proposed_inst, name="Computer Systems", level=ProgrammeLevel.objects.filter(
            is_active=True).exclude(code="admin").first(), source_application=app,
    )
    OnboardingProgrammeReview.unscoped.create(
        application=app, programme=programme, department=qa_department("ntird"), round=1,
        status=OnboardingProgrammeReview.CLEARED,
    )
    return app, programme


def _complete_study(study):
    data = QueryDict("", mutable=True)
    data["f__programme_name"] = study.programme.name
    data["f__programme_level"] = study.programme.level.name
    data["f__programme_objectives"] = "Prepare graduates for practical computing work."
    data["f__delivery_mode"] = "in_person"
    data["f__planned_intake"] = "25"
    data["f__staffing_summary"] = "The institution will appoint qualified programme staff."
    data["f__staffing_plan__0__name"] = "QA Example Lecturer"
    data["f__staffing_plan__0__qualification"] = "BSc Computing"
    data["f__staffing_plan__0__role"] = "Lecturer"
    data["f__facilities_summary"] = "Dedicated teaching rooms and practical labs are planned."
    data["f__learning_resources_summary"] = "Library and ICT resources will support the programme."
    data["f__readiness_declaration"] = "yes"
    return data


@pytest.mark.django_db
def test_sop01_self_study_submission_is_immutable_and_requires_ce(
        v3, proposed_inst, inst_user, make_user, django_capture_on_commit_callbacks):
    from accounts.models import User
    from accreditation import onboarding_ssq
    from accreditation.models import OnboardingSSQ
    from institutions.handlers import ApproveInstitutionHandler
    from staff.models import Department

    _ready_profile(proposed_inst, inst_user)
    app, _programme = _at_self_study(proposed_inst, inst_user)
    ict_director = make_user("ict.director@t.local", role="director", dept="ict")
    ict_department = Department.objects.get(code="ict")
    ict_department.director = ict_director
    ict_department.save(update_fields=["director"])
    with pytest.raises(ValidationError, match="Complete and submit every programme self-study"):
        ApproveInstitutionHandler().validate(proposed_inst, {})

    studies = onboarding_ssq.open_for_application(app)
    assert len(studies) == 1 and studies[0].status == OnboardingSSQ.DRAFT
    assert studies[0].template.name.startswith("SOP-01 Programme Self-Study")
    assert len(onboarding_ssq.submission_problems(studies[0])) == 8

    ict_user = User.objects.create(
        email="onboarding-ict@t.local", first_name="ICT", last_name="Admin",
        user_type="institution", institution=proposed_inst, is_active=True,
    )
    with pytest.raises(PermissionDenied):
        onboarding_ssq.submit_application(app, user=ict_user, declaration=True)
    with pytest.raises(ValidationError, match="Confirm the self-study declaration"):
        onboarding_ssq.submit_application(app, user=inst_user, declaration=False)

    assert onboarding_ssq.save_fields(studies[0], _complete_study(studies[0]), user=inst_user) == {}
    with django_capture_on_commit_callbacks(execute=True):
        app = onboarding_ssq.submit_application(app, user=inst_user, declaration=True)
    assert app.status == "ssq_submitted"
    assert proposed_inst.status != "approved"
    from accreditation.views import visible_applications
    from qa_core.permissions import can_view_applications

    assert can_view_applications(ict_director)
    assert app.pk in visible_applications(ict_director)
    study = OnboardingSSQ.unscoped.get(application=app)
    assert study.status == OnboardingSSQ.SUBMITTED and study.verify_submission()
    with pytest.raises(PermissionError, match="immutable"):
        study.data = {"tampered": True}
        study.save()
    with pytest.raises(ValidationError, match="read-only"):
        onboarding_ssq.save_fields(study, _complete_study(study), user=inst_user)

    ApproveInstitutionHandler().validate(proposed_inst, {})


@pytest.mark.django_db
def test_sop01_self_study_portal_requires_approval_before_qa_request(
        v3, proposed_inst, inst_user, client_for, portal_host):
    from accreditation import onboarding_ssq, services
    from accreditation.models import OnboardingSSQ

    _ready_profile(proposed_inst, inst_user)
    app, _programme = _at_self_study(proposed_inst, inst_user)
    client = client_for(inst_user, host=portal_host)
    assert b"Programme self-study" in client.get("/application/").content
    assert client.post("/application/ssq/open/").status_code == 302
    study = OnboardingSSQ.unscoped.get(application=app)
    response = client.get(f"/application/ssq/{study.pk}/")
    assert response.status_code == 200 and b"Save self-study" in response.content

    response = client.post(f"/application/ssq/{study.pk}/save/", data=_complete_study(study))
    assert response.status_code == 302
    response = client.post("/application/ssq/submit/", {"declaration": "on"})
    assert response.status_code == 200
    app.refresh_from_db()
    assert app.status == "ssq_submitted" and proposed_inst.status != "approved"

    # The institution remains under review until an authorised NBTE action is taken.
    instance = services.instance_of(app)
    assert instance.current_stages == ["ssq_submitted"]


@pytest.mark.django_db
def test_sop01_self_studies_open_for_programmes_cleared_in_an_earlier_round(v3, proposed_inst, inst_user):
    """A programme cleared in round 1 is not re-sent when another is returned:
    the self-studies must still open (and submit) for every programme."""
    from accreditation import onboarding_ssq, onboarding_v4
    from accreditation.models import OnboardingProgrammeReview
    from institutions.models import Programme
    from qa_core.departments import qa_department

    _ready_profile(proposed_inst, inst_user)
    app, first = _at_self_study(proposed_inst, inst_user)
    second = Programme.unscoped.create(institution=proposed_inst, name="Data Networks", level=first.level,
                                       source_application=app)
    OnboardingProgrammeReview.unscoped.create(application=app, programme=second, department=qa_department("ntird"),
                                              round=1, status=OnboardingProgrammeReview.RETURNED)
    OnboardingProgrammeReview.unscoped.create(application=app, programme=second, department=qa_department("ntird"),
                                              round=2, status=OnboardingProgrammeReview.CLEARED)
    app.programme_round = 2
    app.save(update_fields=["programme_round", "updated_at"])

    studies = onboarding_ssq.open_for_application(app)
    assert {s.programme_id for s in studies} == {first.pk, second.pk}
    assert onboarding_v4.stalled_step(app) == ""
    for study in studies:
        assert onboarding_ssq.save_fields(study, _complete_study(study), user=inst_user) == {}
    app = onboarding_ssq.submit_application(app, user=inst_user, declaration=True)
    assert app.status == "ssq_submitted"
