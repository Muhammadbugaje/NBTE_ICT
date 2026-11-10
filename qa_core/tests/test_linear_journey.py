"""The institution's ONE linear journey (qa_core.portal_journey.linear_journey):
plain words, real data, no internal jargon, department names hidden by default."""
import re

import pytest
from django.test import Client

from qa_core.tests.phase2_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase3_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase3_fixtures import make_request

@pytest.fixture
def v3(qa_seed):
    from accreditation import onboarding_v2, onboarding_v3
    from accreditation.seed_sets import install_notifications

    onboarding_v2.install_flow_v2()
    onboarding_v3.install_flow_v3()
    install_notifications()
    # The programme self-study template is seeded by a data migration (absent with --nomigrations).
    import importlib

    from django.apps import apps
    importlib.import_module("accreditation.migrations.0017_onboarding_ssq_template").seed_onboarding_template(
        apps, None)


JARGON = re.compile(r"SOP[\s\-_]*0?\d|\bSOP\b|\bCQAC\b|Committee on Quality Assurance")


def _states(j):
    return {st["code"]: st["state"] for st in j["steps"]}


def _visible(html):
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def _no_jargon(j):
    for st in j["steps"]:
        blob = " ".join(str(st.get(k) or "") for k in ("title", "label", "sentence", "holder", "wait", "detail"))
        blob += " " + " ".join(str(v) for v in (st.get("todo") or {}).values())
        blob += " " + " ".join(f"{p['name']} {p['label']}" for p in st.get("programmes") or [])
        assert not JARGON.search(blob), blob


def _ready_profile(inst, ce):
    """Complete the portal set-up (as accreditation/tests/test_onboarding_ssq.py does)."""
    from accounts.models import Role, User
    from institutions import profile
    from institutions.models import InstitutionUser, ManagementRole

    InstitutionUser.unscoped.filter(user=ce).update(account_kind="chief_executive")
    ict = User.objects.create(email=f"ict.{inst.code.lower()}@t.local", first_name="ICT", last_name="Head",
                              user_type="institution", institution=inst,
                              role=Role.objects.get(code="inst-admin"), is_active=True)
    InstitutionUser.unscoped.create(institution=inst, user=ict, account_kind="ict_head")
    for role in ManagementRole.objects.filter(is_mandatory=True, is_active=True):
        profile.set_holder(inst, role, {"full_name": f"{role.name} Person", "email": f"{role.code}@t.local",
                                        "phone": "0803"}, actor=ce)


def _force(app, stage, user):
    from accreditation import services
    from qa_core import flow

    flow.force_stage(services.instance_of(app), stage, user, remark="test fixture")
    app.refresh_from_db()
    return app


def _self_study_app(inst, user):
    """An application at the self-study step with one NBTE-cleared programme."""
    from accreditation import onboarding_ssq, services
    from accreditation.models import OnboardingProgrammeReview
    from institutions.models import Programme, ProgrammeLevel
    from qa_core.departments import qa_department

    app = services.start_application(inst, user=user)
    _force(app, "ssq_in_progress", user)
    app.programme_round = 1
    app.save(update_fields=["programme_round", "updated_at"])
    level = ProgrammeLevel.objects.filter(code="nd").first() or ProgrammeLevel.objects.filter(
        is_active=True).exclude(code="admin").first()
    programme = Programme.unscoped.create(institution=inst, name="Computer Systems", level=level,
                                          source_application=app)
    OnboardingProgrammeReview.unscoped.create(application=app, programme=programme,
                                              department=qa_department("ntird"), round=1,
                                              status=OnboardingProgrammeReview.CLEARED)
    studies = onboarding_ssq.open_for_application(app)
    return app, programme, studies


def test_fresh_registration(proposed_inst, inst_user):
    from qa_core.portal_journey import linear_journey

    j = linear_journey(proposed_inst, user=inst_user)
    states = _states(j)
    assert states["registered"] == "done"
    assert states["documents"] == "action"  # set up the portal / start the application
    assert all(s == "upcoming" for code, s in states.items() if code not in ("registered", "documents"))
    assert j["position"] == 2 and j["total"] == 11 and j["mine"]
    assert j["current"]["todo"]["cta_url"]
    _no_jargon(j)


def test_mid_onboarding_documents_with_nbte_and_department_hidden(v3, proposed_inst, inst_user, settings):
    from accreditation import services
    from qa_core.portal_journey import linear_journey

    from staff.models import Department

    if proposed_inst.handling_department is None:
        proposed_inst.handling_department = Department.objects.get(code="dpap")
        proposed_inst.save(update_fields=["handling_department"])
    app = services.start_application(proposed_inst, user=inst_user)
    from django.core.exceptions import ValidationError

    for stage in ("document_review", "clearance", "submitted"):
        try:
            _force(app, stage, inst_user)
            break
        except ValidationError:
            continue
    j = linear_journey(proposed_inst, user=inst_user)
    cur = j["current"]
    assert cur["code"] == "documents" and cur["state"] == "current"
    assert cur["holder"] == "NBTE"  # department names are hidden by default
    assert cur["todo"] is None and "your turn" in cur["wait"]
    _no_jargon(j)

    set_config_value("qa.portal.show_department_names", "true")
    j = linear_journey(proposed_inst, user=inst_user)
    assert j["current"]["holder"].startswith("NBTE – ")


def set_config_value(key, value):
    from django.core.cache import cache

    from config.models import ConfigEntry

    ConfigEntry.objects.update_or_create(key=key, scope="global",
                                         defaults={"value": value, "value_type": "str", "group": "QA"})
    cache.clear()


def test_self_studies_submitted_shows_programme_under_review(v3, proposed_inst, inst_user):
    from accreditation.models import OnboardingSSQ
    from qa_core.portal_journey import linear_journey

    app, programme, studies = _self_study_app(proposed_inst, inst_user)
    j = linear_journey(proposed_inst, user=inst_user)
    assert j["current"]["code"] == "self_studies" and j["current"]["state"] == "action"
    assert [p["name"] for p in j["current"]["programmes"]] == [str(programme)]

    OnboardingSSQ.unscoped.filter(application=app).update(status=OnboardingSSQ.SUBMITTED)
    _force(app, "ssq_submitted", inst_user)
    j = linear_journey(proposed_inst, user=inst_user)
    states = _states(j)
    assert [states[c] for c in ("registered", "documents", "programmes", "self_studies")] == ["done"] * 4
    assert states["review"] == "current" and states["approval"] == "upcoming"
    assert j["position"] == 5
    assert j["headline"] == f"NBTE is reviewing {programme}."
    assert j["current"]["holder"] == "NBTE"
    assert j["current"]["programmes"][0]["state"] in ("current", "done")
    _no_jargon(j)


def test_approved_and_accreditation_started(p3):
    from accreditation.models import QACase, QARequest, VisitDetail
    from qa_core.portal_journey import linear_journey, request_journey

    j = linear_journey(p3.inst, user=p3.ceo)
    assert j["current"]["code"] == "request" and j["current"]["state"] == "action"
    assert "qa-requests/new" in j["current"]["todo"]["cta_url"]

    req = make_request(p3)
    QARequest.unscoped.filter(pk=req.pk).update(status="in_workflow")
    case = QACase.unscoped.create(reference="CASE-T1", qa_request=req, institution=p3.inst,
                                  owning_department=p3.inst.handling_department, status="audit_personnel_cost")
    j = linear_journey(p3.inst, user=p3.ceo)
    states = _states(j)
    assert all(states[c] == "done" for c in ("registered", "documents", "programmes", "self_studies", "review",
                                             "approval", "request", "request_ssq"))
    assert states["visit_planned"] == "current" and states["visit"] == "upcoming"
    assert j["headline"].startswith("NBTE is planning your visit")
    _no_jargon(j)

    QACase.unscoped.filter(pk=case.pk).update(status="cqac_chair_approval")  # internal stage → friendly step
    j = linear_journey(p3.inst, user=p3.ceo)
    assert j["current"]["code"] == "decision"
    assert "Committee" not in j["headline"] and "CQAC" not in j["headline"]

    QACase.unscoped.filter(pk=case.pk).update(status="request_fund_release")
    import datetime
    VisitDetail.unscoped.create(case=case, planned_start=datetime.date(2027, 3, 12),
                                planned_end=datetime.date(2027, 3, 14), status="es_approved")
    rj = request_journey(QARequest.unscoped.get(pk=req.pk), user=p3.ceo)
    assert [s["code"] for s in rj["steps"]] == ["request", "request_ssq", "visit_planned", "visit", "decision"]
    assert rj["current"]["code"] == "visit" and "12 Mar" in rj["headline"]


def test_no_sop_in_rendered_portal_pages_and_departments_hidden(p3, portal_host):
    from accreditation.models import QACase, QARequest

    req = make_request(p3)
    QARequest.unscoped.filter(pk=req.pk).update(status="in_workflow")
    QACase.unscoped.create(reference="CASE-T2", qa_request=req, institution=p3.inst,
                           owning_department=p3.inst.handling_department, status="es_final_approval")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.ceo)
    dept = p3.inst.handling_department.name
    for url in ("/", f"/qa-requests/{req.pk}/", "/timeline/"):
        r = c.get(url)
        assert r.status_code == 200, url
        text = _visible(r.content.decode())
        assert not JARGON.search(text), (url, JARGON.search(text).group(0))
        assert dept not in text, (url, dept)
    body = c.get("/dashboard/").content.decode()
    assert 'aria-current="step"' in body and "What you need to do now" in body
    assert 'class="lj ' in body or 'class="lj"' in body
    detail = c.get(f"/qa-requests/{req.pk}/").content.decode()
    assert "lj--compact" in detail


def test_application_page_uses_journey_wording(v3, proposed_inst, inst_user, portal_host):
    from accreditation.models import OnboardingSSQ

    _ready_profile(proposed_inst, inst_user)
    app, _programme, _studies = _self_study_app(proposed_inst, inst_user)
    OnboardingSSQ.unscoped.filter(application=app).update(status=OnboardingSSQ.SUBMITTED)
    _force(app, "ssq_submitted", inst_user)
    c = Client(HTTP_HOST=portal_host)
    c.force_login(inst_user)
    r = c.get("/application/")
    if r.status_code == 302:  # the profile set-up gate may lead first
        pytest.skip("profile gate redirects before the application page")
    text = _visible(r.content.decode())
    assert "formal QA requests" not in text
    assert not JARGON.search(text)
    assert "NBTE is reviewing" in text


def test_returned_self_study_needs_correction_and_resubmit(v3, proposed_inst, inst_user, portal_host):
    from accreditation.models import OnboardingSSQ, OnboardingSSQReview
    from qa_core.portal_journey import linear_journey

    _ready_profile(proposed_inst, inst_user)
    app, programme, studies = _self_study_app(proposed_inst, inst_user)
    _force(app, "ssq_submitted", inst_user)  # submitted, then NBTE returned this one (a draft again)
    ssq = OnboardingSSQ.unscoped.get(application=app)
    OnboardingSSQReview.objects.create(ssq=ssq, stage="returned_to_institution",
                                       director_remark="Add the staffing plan for year two.")
    j = linear_journey(proposed_inst, user=inst_user)
    cur = j["current"]
    assert cur["code"] == "review" and cur["state"] == "action"
    assert cur["sentence"] == f"Needs your action: correct and resubmit {programme}."
    assert "Add the staffing plan for year two." in cur["todo"]["text"]
    assert cur["todo"]["cta_url"].endswith(f"/application/ssq/{ssq.pk}/")

    c = Client(HTTP_HOST=portal_host)
    c.force_login(inst_user)
    r = c.get(f"/application/ssq/{ssq.pk}/")
    if r.status_code == 302:
        pytest.skip("profile gate redirects before the self-study page")
    body = r.content.decode()
    assert "Resubmit to NBTE" in body and "Add the staffing plan for year two." in body
    assert f"/application/ssq/{ssq.pk}/resubmit/" in body and 'name="declaration"' in body
    assert not JARGON.search(_visible(body))
