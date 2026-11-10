"""Linear onboarding: self-study routing → officer → director clearance →
director approves → ES confirms → formal QA request with the SSQs carried over."""
import datetime
from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accreditation.tests.test_onboarding_ssq import (  # noqa: F401  (v3 is a fixture)
    _at_self_study, _complete_study, _ready_profile, v3,
)

pytestmark = pytest.mark.django_db


def _fresh(user):
    from accounts.models import User
    return User.objects.get(pk=user.pk)


@pytest.fixture
def w(v3, proposed_inst, inst_user, p2, make_user, django_capture_on_commit_callbacks):  # noqa: F811
    from accreditation import onboarding_ssq
    from staff.models import Department

    import importlib

    from django.apps import apps
    # --nomigrations: the onboarding self-study template is seeded by a data migration.
    importlib.import_module("accreditation.migrations.0017_onboarding_ssq_template").seed_onboarding_template(
        apps, None)
    ntird = Department.objects.get(code="ntird")
    ntird.director = p2.ntird_director
    ntird.save(update_fields=["director"])
    es = make_user("es.linear@t.local", role="executive-secretary")
    _ready_profile(proposed_inst, inst_user)
    app, programme = _at_self_study(proposed_inst, inst_user)
    studies = onboarding_ssq.open_for_application(app)
    study = studies[0]
    assert onboarding_ssq.save_fields(study, _complete_study(study), user=inst_user) == {}
    with django_capture_on_commit_callbacks(execute=True):
        app = onboarding_ssq.submit_application(app, user=inst_user, declaration=True)
    study.refresh_from_db()
    return SimpleNamespace(app=app, inst=proposed_inst, ce=inst_user, programme=programme, study=study,
                           director=_fresh(p2.ntird_director), officer=_fresh(p2.ntird_officer),
                           other_officer=_fresh(p2.officer), es=_fresh(es), ntird=ntird, p2=p2)


def _assign(w):
    from accreditation import onboarding_linear as linear
    return linear.assign(w.study, by=w.director, officer=w.officer)


def _cleared(w):
    from accreditation import onboarding_linear as linear
    _assign(w)
    linear.send_to_director(w.study, officer=w.officer, recommendation="clear")
    linear.clear(w.study, by=w.director)


def _approved(w, capture):
    from accreditation import onboarding_linear as linear
    _cleared(w)
    pa = linear.approve_institution(w.app, by=w.director)
    with capture(execute=True):
        pa = linear.confirm_approval(pa, by=w.es)
    return pa


def test_submit_routes_each_self_study_to_the_handling_director(w):
    from accreditation import onboarding_linear as linear
    from accreditation.models import OnboardingSSQReview
    from accreditation.onboarding_ssq import review_stage
    from assignments import rotation_queue as rq
    from notifications.models import Notification

    review = OnboardingSSQReview.objects.get(ssq=w.study)
    assert review.stage == "awaiting_assignment" and review.department == w.ntird
    assert review.director_id == w.director.pk
    assert Notification.objects.filter(recipient=w.director, title__startswith="Assign officers").exists()
    stage = review_stage(w.study)
    assert set(stage) == {"code", "label", "public", "holder", "assigned_officer", "director", "days_at_stage"}
    assert stage["code"] == "awaiting_assignment" and stage["assigned_officer"] is None
    assert stage["director"] == w.director and w.director.last_name not in stage["public"]
    # The rotation board lists it under "Onboarding self-study"; the director's next action is to assign.
    groups = {g.meta["code"]: g for g in rq.waiting([w.ntird])}
    assert rq.ONBOARDING in groups and groups[rq.ONBOARDING].jobs[0].obj.pk == w.study.pk
    kinds = [a["kind"] for a in linear.next_actions(w.director)]
    assert "assign" in kinds
    assert not linear.next_actions(w.officer)


def test_rotation_assigns_in_one_click_and_the_officer_sees_it(w):
    from accreditation import onboarding_linear as linear
    from accreditation.onboarding_ssq import review_stage
    from assignments import inbox
    from assignments import rotation_queue as rq
    from assignments.models import WorkAssignment

    job = rq.get_job(w.director, f"{rq.ONBOARDING}:{w.study.pk}")
    assert job is not None
    rows, suggested = rq.candidates(job)
    assert suggested is not None and all(r["user"].department_id == w.ntird.pk for r in rows)
    chosen = rq.assign(job, by=w.director, chosen=suggested["user_id"])
    stage = review_stage(w.study)
    assert stage["code"] == "under_assessment" and stage["assigned_officer"] == chosen
    assert rq.get_job(w.director, f"{rq.ONBOARDING}:{w.study.pk}") is None      # no longer waiting
    wa = linear.current_assignment(w.study)
    assert wa.assignee == chosen and wa.status == WorkAssignment.ACCEPTED
    assert inbox.task_url(wa)[0] == f"/qa/applications/self-study/{w.study.pk}/"
    assert [a["kind"] for a in linear.next_actions(_fresh(chosen))] == ["assess"]


def test_officer_sends_to_director_who_clears(w):
    from accreditation import onboarding_linear as linear
    from accreditation.onboarding_ssq import review_stage

    _assign(w)
    with pytest.raises(PermissionDenied):          # only the assigned officer assesses
        linear.send_to_director(w.study, officer=w.other_officer, recommendation="clear")
    with pytest.raises(ValidationError):           # a return needs notes
        linear.send_to_director(w.study, officer=w.officer, recommendation="return")
    linear.send_to_director(w.study, officer=w.officer, recommendation="clear", remark="Complete.")
    assert review_stage(w.study)["code"] == "awaiting_director"
    assert [a["kind"] for a in linear.next_actions(w.director)] == ["clear"]
    # Another department's officer, and the assessing officer, cannot clear.
    for user in (w.other_officer, w.officer):
        with pytest.raises(PermissionDenied):
            linear.clear(w.study, by=user)
    linear.return_to_officer(w.study, by=w.director, remark="Check the staffing plan.")
    assert review_stage(w.study)["code"] == "under_assessment"
    linear.send_to_director(w.study, officer=w.officer, recommendation="clear")
    linear.clear(w.study, by=w.director)
    stage = review_stage(w.study)
    assert stage["code"] == "cleared" and "Cleared by NBTE" in stage["public"]
    assert linear.ready_for_approval(w.app)


def test_ready_for_approval_card_and_director_initiates_the_action(w, client_for):
    from accreditation import onboarding_linear as linear
    from qa_core.models import PendingAction

    _cleared(w)
    actions = linear.next_actions(w.director)
    approve = [a for a in actions if a["kind"] == "approve"]
    assert approve and "has cleared everything" in approve[0]["title"]
    assert approve[0]["button_label"] == "Approve institution"
    # The QA home and the application page show the card.
    client = client_for(w.director)
    assert b"has cleared everything" in client.get("/qa/").content
    assert b"Approve institution" in client.get(f"/qa/applications/{w.app.pk}/").content
    # One click (POST) initiates institutions.approve_institution with the director as initiator.
    r = client.post(approve[0]["post_url"], {"next": "/qa/"})
    assert r.status_code == 302
    pa = PendingAction.objects.get(policy_id="institutions.approve_institution", status=PendingAction.PENDING)
    assert pa.initiated_by_id == w.director.pk
    assert not [a for a in linear.next_actions(w.director) if a["kind"] == "approve"]
    confirm = [a for a in linear.next_actions(w.es) if a["kind"] == "confirm"]
    assert confirm and confirm[0]["pa_id"] == pa.pk and confirm[0]["reject_url"]
    # The ES rejects with a reason → the director's card comes back.
    linear.reject_approval(pa, by=w.es, remark="Recheck the category.")
    assert [a for a in linear.next_actions(w.director) if a["kind"] == "approve"]


def test_es_confirm_approves_and_starts_the_qa_request_fee_billed(w, django_capture_on_commit_callbacks):
    from accreditation import onboarding_linear as linear, qa_services
    from accreditation.models import SSQ, OnboardingQAStart, QACase, QARequest, Submission
    from accreditation.onboarding_ssq import review_stage
    from notifications.models import Notification
    from qa_core import flow

    pa = _approved(w, django_capture_on_commit_callbacks)
    assert pa.status == pa.APPLIED, pa.failure_detail
    w.inst.refresh_from_db()
    assert w.inst.status == "approved"
    assert review_stage(w.study)["code"] == "approved"
    start = OnboardingQAStart.objects.get(application=w.app)
    assert start.error == "" and start.qa_request is not None and not start.fee_waived
    req = QARequest.unscoped.get(pk=start.qa_request_id)
    assert req.qa_type.code == "accreditation" and req.status == "fee_due"
    assert [r.programme_id for r in req.active_programmes()] == [w.programme.pk]
    assert Notification.objects.filter(recipient=w.ce, title__contains=req.reference).exists()
    assert not SSQ.unscoped.filter(qa_request_programme__qa_request=req).exists()   # opens after the fee
    # The fee is confirmed → the SSQ is carried over (accepted) and the QA case opens by itself.
    with django_capture_on_commit_callbacks(execute=True):
        flow.transition(qa_services.request_instance(req), "payment_confirmed", None)
    ssq = SSQ.unscoped.get(qa_request_programme__qa_request=req)
    assert ssq.status == "accepted" and ssq.meta["carried_over"]["onboarding_ssq"] == w.study.pk
    assert ssq.data == w.study.submitted_data and Submission.unscoped.filter(ssq=ssq).count() == 1
    req.refresh_from_db()
    assert req.status == "in_workflow" and QACase.unscoped.filter(qa_request=req).exists()


def test_fee_waived_goes_straight_to_the_case_and_new_programmes_still_need_an_ssq(
        w, django_capture_on_commit_callbacks):
    from accreditation import onboarding_linear as linear, qa_services
    from accreditation.models import SSQ, OnboardingQAStart, QACase, QARequest, QAType, QARequestProgramme
    from institutions.models import Programme

    from config.models import ConfigEntry
    from config.registry import invalidate
    ConfigEntry.objects.update_or_create(key="qa.onboarding.auto_request_fee_waived", scope="global",
                                         defaults={"value": "true", "value_type": "bool"})
    invalidate("qa.onboarding.auto_request_fee_waived")
    assert linear.auto_fee_waived()
    _approved(w, django_capture_on_commit_callbacks)
    req = OnboardingQAStart.objects.get(application=w.app).qa_request
    req.refresh_from_db()
    assert req.status == "in_workflow" and QACase.unscoped.filter(qa_request=req).exists()
    assert not qa_services.open_obligations(req)
    assert SSQ.unscoped.get(qa_request_programme__qa_request=req).status == "accepted"
    assert not SSQ.unscoped.filter(qa_request_programme__qa_request=req, status="draft").exists()

    # A programme added later needs its own SSQ; the covered one is never carried twice.
    new = Programme.unscoped.create(institution=w.inst, name="Welding", level=w.programme.level,
                                    status="approval_to_commence")
    w.inst.refresh_from_db()
    later = QARequest.unscoped.create(reference="T/QA/LATER", institution=w.inst, status="draft",
                                      qa_type=QAType.objects.get(code="reaccreditation"))
    QARequestProgramme.unscoped.create(qa_request=later, programme=new)
    QARequestProgramme.unscoped.create(qa_request=later, programme=w.programme)
    qa_services.on_open_ssqs(SimpleNamespace(subject=later, actor=None))
    by_programme = {s.qa_request_programme.programme_id: s
                    for s in SSQ.unscoped.filter(qa_request_programme__qa_request=later)}
    assert by_programme[new.pk].status == "draft" and "carried_over" not in by_programme[new.pk].meta
    assert "carried_over" not in by_programme[w.programme.pk].meta


def test_return_to_institution_and_resubmit_goes_back_to_the_officer(w):
    from accreditation import onboarding_linear as linear, onboarding_ssq
    from accreditation.models import OnboardingSSQ
    from accreditation.onboarding_ssq import review_stage

    _assign(w)
    linear.send_to_director(w.study, officer=w.officer, recommendation="return", remark="Add a lab plan.")
    linear.return_to_institution(w.study, by=w.director, remark="Add a lab plan.")
    study = OnboardingSSQ.unscoped.get(pk=w.study.pk)
    stage = review_stage(study)
    assert stage["code"] == "returned_to_institution" and "Add a lab plan." in stage["public"]
    assert study.status == OnboardingSSQ.DRAFT
    assert onboarding_ssq.workspace(study, w.ce)["editable"]
    assert onboarding_ssq.save_fields(study, _complete_study(study), user=w.ce) == {}
    linear.resubmit(study, user=w.ce, declaration=True)
    study.refresh_from_db()
    assert study.status == OnboardingSSQ.SUBMITTED and study.verify_submission()
    stage = review_stage(study)
    assert stage["code"] == "under_assessment" and stage["assigned_officer"] == w.officer


def test_overdue_stage_reminds_then_escalates(w):
    from accreditation import onboarding_linear as linear
    from accreditation.models import OnboardingSSQReview
    from notifications.models import Notification

    _assign(w)
    OnboardingSSQReview.objects.filter(ssq=w.study).update(stage_since=timezone.now() - datetime.timedelta(days=6))
    assert linear.send_stage_reminders() == 1
    assert Notification.objects.filter(recipient=w.officer, title__startswith="Overdue").count() == 1
    assert linear.send_stage_reminders() == 0                      # once per level
    assert linear.next_actions(w.officer)[0]["overdue"]
    OnboardingSSQReview.objects.filter(ssq=w.study).update(stage_since=timezone.now() - datetime.timedelta(days=11))
    assert linear.send_stage_reminders() == 1
    assert Notification.objects.filter(recipient=w.director, title__startswith="Escalated").exists()


def test_bare_minimum_counter_reads_the_institution_and_programme(w):
    """The counter on the self-study page scores the submission against what NBTE
    already holds, and is honest when this is the programme's first self-study."""
    from accreditation import onboarding_check

    verdict = onboarding_check.check(w.study)
    assert verdict["checks"] and 0 <= verdict["score"] <= 100
    assert verdict["failed_checks"] == [] and verdict["meets"]
    assert verdict["verdict"] == "Meets the bare minimum" and verdict["band"] in ("good", "fair")
    # Pulled from the institution and the programme records, not just the form.
    assert {label for label, _ in verdict["facts"]["institution"]} >= {"Code", "Handling department"}
    assert {label for label, _ in verdict["facts"]["programme"]} >= {"Award level", "Programme status"}
    # First self-study for this programme: nothing earlier to compare with, and it says so.
    assert verdict["compare"]["first"] and "First self-study" in verdict["compare"]["note"]
    # Read-only: scoring one writes nothing (and there is no override to show the director).
    assert onboarding_check.latest_override(None) is None


def test_self_study_page_reads_first_then_offers_the_one_step(w, client_for):
    """The counter and the submission come first on the page; the action cards
    (assign / assess / clear) sit below them."""
    _assign(w)
    html = client_for(w.officer).get(f"/qa/applications/self-study/{w.study.pk}/").content.decode()
    assert "Does this self-study reach the bare minimum?" in html
    assert "What the institution submitted" in html
    snap, check, assess = (html.index("What the institution submitted"),
                           html.index("Does this self-study reach the bare minimum?"),
                           html.index("Send your assessment to the director"))
    assert snap < check < assess
    assert 'id="ob-clear-h"' not in html                      # the director's step is not the officer's
    assert "Send to Director" in html


def test_below_the_bare_minimum_clears_only_with_a_recorded_override(
        w, django_capture_on_commit_callbacks):
    from accreditation import onboarding_check, onboarding_linear as linear
    from institutions.models import Programme
    from notifications.models import Notification

    _assign(w)
    # Expired accreditation is a failing check, so the submission falls below the minimum.
    Programme.unscoped.filter(pk=w.programme.pk).update(
        expiry_date=timezone.localdate() - datetime.timedelta(days=30))
    verdict = onboarding_check.check(w.study)
    assert not verdict["meets"] and verdict["failed_checks"]
    with pytest.raises(ValidationError):                       # blocked until it is overridden
        linear.send_to_director(w.study, officer=w.officer, recommendation="clear")
    with pytest.raises(ValidationError):                       # …and the reason is written, not empty
        linear.send_to_director(w.study, officer=w.officer, recommendation="clear",
                                override=True, override_reason="short")
    with django_capture_on_commit_callbacks(execute=True):
        review = linear.send_to_director(
            w.study, officer=w.officer, recommendation="clear", override=True,
            override_reason="Accreditation renewal is filed; the receipt is on record.")
    override = onboarding_check.latest_override(review)
    assert override["score"] == verdict["score"] and "renewal" in override["reason"] and override["by"]
    row = [h for h in review.history if h.get("override")][-1]
    assert row["check_meets"] is False and row["event"] == "assessed"
    # The director is told the score and the officer's reason before clearing.
    notice = Notification.objects.filter(recipient=w.director,
                                         title__startswith="Clear programme").latest("created_at")
    assert f"{verdict['score']}/100" in notice.body and "Accreditation renewal" in notice.body
    # A return never needs an override — it approves nothing, and the card shows no override.
    linear.return_to_officer(w.study, by=w.director, remark="Check the renewal receipt.")
    linear.send_to_director(w.study, officer=w.officer, recommendation="return", remark="Receipt missing.")
    assert onboarding_check.latest_override(linear.review_of(w.study)) is None


def test_action_menu_approval_needs_every_self_study_cleared(w):
    """Owner decision: the old Action-menu approval follows the same rule as the
    Approve button — every onboarding self-study must be cleared first."""
    from accreditation import onboarding_linear as linear
    from qa_core import actions
    from qa_core.actions import get_handler

    handler = get_handler("institutions.approve_institution")
    ok, reason = handler.available_for(w.inst)
    assert not ok and "0 of 1" in reason and "cleared" in reason
    with pytest.raises(ValidationError, match="must be cleared"):
        actions.initiate("institutions.approve_institution", actor=w.director, target=w.inst,
                         payload={"reason": "x"}, reason="x")
    _cleared(w)
    assert linear.approval_blocker(w.inst) == ""
    assert handler.available_for(w.inst) == (True, "")
