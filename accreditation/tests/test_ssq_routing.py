"""Who is handling this SSQ: routing on submit, the stage advancing through
assignment → assessment → approval (NBTE side and institution side), the
"who is handling" panel and its visibility, reminders (nudges), and the
onboarding self-study stage after the institution approval."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from qa_core.tests.phase3_fixtures import complete_ssq

pytestmark = pytest.mark.django_db


def _fresh(u):
    from accounts.models import User
    return User.objects.get(pk=u.pk)


def _web(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def _director(p3):
    import org
    return org.director_of(p3.inst.handling_department)


def _submitted(p3, paid_request, capture):
    from accreditation import qa_services as svc
    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    with capture(execute=True):
        svc.submit_ssq(ssq, user=p3.ceo, declaration=True)
    ssq.refresh_from_db()
    return ssq


def _staff(email, dept_code, role="officer"):
    from accounts.models import Role, User
    from qa_core.departments import qa_department
    return User.objects.create(email=email, first_name=email.split("@")[0].title(), last_name="T",
                               user_type="staff", role=Role.objects.get_or_create(
                                   code=role, defaults={"name": role.title(), "rank": 20})[0],
                               department=qa_department(dept_code), must_change_password=False)


def test_submit_routes_to_the_handling_department_and_tells_its_director(
        p3, paid_request, django_capture_on_commit_callbacks):
    from accreditation import ssq_routing
    from notifications.models import Notification

    director = _director(p3)
    assert director is not None
    ssq = _submitted(p3, paid_request, django_capture_on_commit_callbacks)
    routing = ssq.meta["routing"]
    assert routing["department_id"] == p3.inst.handling_department_id
    assert routing["director_id"] == director.pk and routing["assigner_ids"] == [director.pk]
    assert routing["version"] == ssq.version and routing["routed_at"]
    note = Notification.objects.filter(recipient=director, title__startswith="Assign an officer").first()
    assert note is not None and f"/qa/requests/ssq/{ssq.pk}/" in note.url
    who = ssq_routing.holder(ssq)
    assert who["stage"] == ssq_routing.AWAITING_ASSIGNMENT
    assert who["holder"] == director and who["approver"] == director
    assert who["department"] == p3.inst.handling_department


def test_vacant_director_routes_to_the_heads_or_the_es(p3, paid_request, django_capture_on_commit_callbacks):
    from staff.models import Department

    Department.objects.filter(pk=p3.inst.handling_department_id).update(director=None)
    es = _staff("es.routing@t.local", "ntird", role="executive-secretary")
    ssq = _submitted(p3, paid_request, django_capture_on_commit_callbacks)
    routing = ssq.meta["routing"]
    assert routing["director_vacant"] is True and routing["assigner_ids"]
    if not routing["assigner_ids"] == [es.pk]:      # department heads stand in before the ES
        from accounts.models import User
        assert all(User.objects.get(pk=pk).department_id == p3.inst.handling_department_id
                   for pk in routing["assigner_ids"])


def test_status_advances_from_with_nbte_to_approved(p3, paid_request, django_capture_on_commit_callbacks):
    """Problem 2: the label used to stay "Submitted to NBTE" until the very end
    and never named who held it. Now every hand-over moves it."""
    from accreditation import qa_services as svc, ssq_assign, ssq_routing
    from assignments import work
    from assignments.models import WorkAssignment

    director = _director(p3)
    officer = p3.p2.officer2
    ssq = _submitted(p3, paid_request, django_capture_on_commit_callbacks)
    assert svc.ssq_public_status(ssq).startswith("Received by NBTE")

    wa = ssq_assign.assign(ssq, by=director, officer=officer)
    who = ssq_routing.holder(ssq)
    assert who["stage"] == ssq_routing.OFFERED and who["holder"] == officer

    work.accept(wa, _fresh(officer))
    assert svc.ssq_public_status(ssq).startswith("Under assessment by NBTE")
    assert ssq_routing.holder(ssq)["stage"] == ssq_routing.UNDER_ASSESSMENT

    with django_capture_on_commit_callbacks(execute=True):
        ssq_routing.submit_assessment(ssq, officer=_fresh(officer), recommendation="accept", remark="Complete.")
    ssq.refresh_from_db()
    who = ssq_routing.holder(ssq)
    assert who["stage"] == ssq_routing.AWAITING_APPROVAL and who["holder"] == director
    assert who["assessment"]["recommendation"] == "accept"
    assert svc.ssq_public_status(ssq).startswith("Assessed")

    svc.accept_ssq(ssq, user=_fresh(director), remark="Approved on the officer's assessment.")
    ssq.refresh_from_db()
    assert ssq.status == "accepted"
    assert svc.ssq_public_status(ssq) == "Approved by NBTE"
    who = ssq_routing.holder(ssq)
    assert who["stage"] == ssq_routing.APPROVED and who["decided_by"] == director
    wa.refresh_from_db()
    assert wa.status == WorkAssignment.COMPLETED                 # the officer's review work is closed
    assert ssq_assign.open_reviews(officer) == 0


def test_institution_portal_shows_the_real_stage(p3, paid_request, django_capture_on_commit_callbacks, portal_host):
    from accreditation import qa_services as svc, ssq_assign

    ssq = _submitted(p3, paid_request, django_capture_on_commit_callbacks)
    ssq_assign.assign(ssq, by=_director(p3), officer=p3.p2.officer2)
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.admin)
    page = c.get(f"/qa-requests/{paid_request.pk}/").content.decode()
    assert "Received by NBTE" in page
    assert p3.p2.officer2.get_full_name() not in page            # never staff names on the portal
    # The blank fixture SSQ scores as a major shortfall (X36), which needs a remark to accept.
    svc.accept_ssq(ssq, user=_fresh(_director(p3)), remark="Accepted for the routing test.")
    page = c.get(f"/qa-requests/{paid_request.pk}/").content.decode()
    assert "Approved by NBTE" in page


def test_officer_final_decision_switch_makes_the_director_the_approver(
        p3, paid_request, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc, ssq_assign
    from assignments import work
    from qa_core.tests.phase3_fixtures import set_config

    ssq = _submitted(p3, paid_request, django_capture_on_commit_callbacks)
    officer = p3.p2.officer2
    work.accept(ssq_assign.assign(ssq, by=_director(p3), officer=officer), _fresh(officer))
    assert svc.can_review(ssq, _fresh(officer))[0]
    from config.models import ConfigEntry
    if ConfigEntry.objects.filter(key="qa.ssq.officer_final_decision").exists():
        set_config("qa.ssq.officer_final_decision", "false")
    else:
        ConfigEntry.objects.create(key="qa.ssq.officer_final_decision", scope="global", value="false")
        import config
        getattr(config, "clear_cache", lambda: None)()
    ok, why = svc.can_review(ssq, _fresh(officer))
    assert not ok and "assessment" in why
    assert svc.can_review(ssq, _fresh(_director(p3)))[0]


def test_who_is_handling_panel_visibility_and_reminders(p3, paid_request, django_capture_on_commit_callbacks):
    import org
    from accreditation import ssq_assign, ssq_routing
    from assignments import access, work
    from assignments.models import WorkAssignment
    from notifications.models import Notification
    from staff.models import Department

    ssq = _submitted(p3, paid_request, django_capture_on_commit_callbacks)
    director = _director(p3)
    officer = p3.p2.officer2
    work.accept(ssq_assign.assign(ssq, by=director, officer=officer), _fresh(officer))

    # Director of ICT: tracks every SSQ (not the answers) and may remind.
    ict_dept = org.department(access.ict_department_code())
    ict_director = _staff("ict.director@t.local", access.ict_department_code(), role="director")
    Department.objects.filter(pk=ict_dept.pk).update(director=ict_director)
    ict_director = _fresh(ict_director)
    # ICT supervisor of the institution: an open oversight assignment.
    supervisor = _staff("ict.supervisor@t.local", access.ict_department_code())
    work.assign(p3.inst, WorkAssignment.OVERSIGHT_ICT, supervisor, department=ict_dept,
                assigned_by=ict_director, accept_now=True)
    supervisor = _fresh(supervisor)
    outsider = _staff("vtsd.officer@t.local", "vtsd")

    for user in (director, ict_director, supervisor, _fresh(officer)):
        assert ssq_routing.can_track(_fresh(user), ssq), user
    assert not ssq_routing.can_track(outsider, ssq)
    assert supervisor in ssq_routing.holder(ssq)["ict_supervisors"]

    page = _web(ict_director).get("/qa/requests/ssq/?status=assessing").content.decode()
    assert p3.inst.name in page and officer.get_full_name() in page and "Who is handling it" in page
    page = _web(director).get(f"/qa/requests/ssq/{ssq.pk}/").content.decode()
    assert "Who is handling this SSQ" in page and officer.get_full_name() in page
    assert _web(outsider).get(f"/qa/requests/ssq/{ssq.pk}/").status_code in (403, 404)

    assert ssq_routing.can_nudge(ict_director, ssq) and ssq_routing.can_nudge(supervisor, ssq)
    assert not ssq_routing.can_nudge(_fresh(officer), ssq)
    with pytest.raises(PermissionDenied):
        ssq_routing.nudge(ssq, by=outsider)
    response = _web(ict_director).post(f"/qa/requests/ssq/{ssq.pk}/remind/", {"message": "Please finish today"},
                                       HTTP_HX_REQUEST="true")
    assert response.status_code == 200 and "Reminder sent" in response.headers.get("HX-Trigger", "")
    note = Notification.objects.filter(recipient=officer, title__startswith="Reminder: SSQ").first()
    assert note is not None and "Please finish today" in note.body
    ssq.refresh_from_db()
    assert ssq.meta["nudges"][-1]["by"] == ict_director.pk
    with pytest.raises(ValidationError, match="minute"):           # cool-down
        ssq_routing.nudge(ssq, by=ict_director)


def test_onboarding_self_study_stage_follows_the_institution_approval(qa_seed, proposed_inst, inst_user, make_user):
    from django.contrib.contenttypes.models import ContentType

    from accreditation import onboarding_ssq, services
    from qa_core import flow
    from qa_core.models import PendingAction

    app = services.start_application(proposed_inst, user=inst_user)
    flow.force_stage(services.instance_of(app), "ssq_submitted", inst_user, remark="test fixture")
    app.refresh_from_db()
    assert app.status == "ssq_submitted"
    assert onboarding_ssq.approval_stage(app)["code"] == "under_review"
    assert "under review" in services.public_status(app).lower()

    initiator = make_user("ict.dir.onb@t.local", role="director")
    pa = PendingAction.objects.create(
        reference="T/PA/ONB-1", policy_id="institutions.approve_institution",
        content_type=ContentType.objects.get_for_model(type(proposed_inst)), object_id=str(proposed_inst.pk),
        target_label=proposed_inst.name, initiated_by=initiator)
    stage = onboarding_ssq.approval_stage(app)
    assert stage["code"] == "approval_pending" and stage["initiated_by"] == initiator
    assert services.public_status(app) == "Approval in progress at NBTE"

    type(proposed_inst).unscoped.filter(pk=proposed_inst.pk).update(status="approved")
    PendingAction.objects.filter(pk=pa.pk).update(status=PendingAction.APPLIED)
    app.institution.refresh_from_db()
    assert onboarding_ssq.approval_stage(app)["code"] == "approved"
    assert services.public_status(app) == "Approved by NBTE"
