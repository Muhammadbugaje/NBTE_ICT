"""Programme picker by institution type, the ND/HND rule (onboarding and
approved institutions) and director-only programme clearance (onboarding,
bulk, and QA requests of approved institutions)."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accreditation.tests.test_qa_requests import resource_type  # noqa: F401
from accreditation.tests.test_v21_phase0 import selecting_app  # noqa: F401
from qa_core.tests.conftest import hx
from qa_core.tests.phase3_fixtures import make_request, p3, set_config  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _gate_off(qa_seed):
    set_config("qa.onboarding.profile_gate_enabled", "false")


@pytest.fixture
def cat(qa_seed):
    from institutions.models import Discipline, InstitutionType, ProgrammeLevel, ProgrammeMaster

    class C:
        pass

    c = C()
    c.nd, c.hnd = ProgrammeLevel.objects.get(code="nd"), ProgrammeLevel.objects.get(code="hnd")
    c.poly = InstitutionType.objects.get_or_create(code="t-poly", defaults={"name": "Test Polytechnic"})[0]
    c.health_type = InstitutionType.objects.get_or_create(code="t-health", defaults={"name": "Test Health"})[0]
    c.eng = Discipline.objects.get_or_create(code="t-eng", defaults={"name": "Test Engineering"})[0]
    c.med = Discipline.objects.get_or_create(code="t-med", defaults={"name": "Test Health Sciences"})[0]
    c.eng.institution_types.set([c.poly])
    c.med.institution_types.set([c.health_type])
    c.weld_nd = ProgrammeMaster.objects.create(name="Welding Technology", level=c.nd, discipline=c.eng)
    c.weld_hnd = ProgrammeMaster.objects.create(name="Welding Technology", level=c.hnd, discipline=c.eng)
    c.foundry_nd = ProgrammeMaster.objects.create(name="Foundry Technology", level=c.nd, discipline=c.eng)
    c.foundry_hnd = ProgrammeMaster.objects.create(name="Foundry Technology", level=c.hnd, discipline=c.eng)
    c.nursing = ProgrammeMaster.objects.create(name="Community Nursing", level=c.nd, discipline=c.med)
    return c


# ---------------------------------------------------------------------------
# 1. The picker shows the programmes relevant to the institution's type
# ---------------------------------------------------------------------------
def test_relevant_masters_follow_the_institution_type(cat, make_institution):
    from institutions.programme_master import picker, relevant_masters

    poly, health, untyped = make_institution("RT1"), make_institution("RT2"), make_institution("RT3")
    for inst, t in ((poly, cat.poly), (health, cat.health_type)):
        inst.institution_type = t
        inst.save(update_fields=["institution_type"])
    assert {cat.weld_nd, cat.weld_hnd} <= set(relevant_masters(poly)) and cat.nursing not in relevant_masters(poly)
    assert cat.nursing in relevant_masters(health) and cat.weld_nd not in relevant_masters(health)
    assert {cat.weld_nd, cat.nursing} <= set(relevant_masters(untyped))  # no type: everything
    groups = {g["name"]: g for g in picker(poly)["groups"]}
    assert "Community Nursing" not in groups
    assert [o["code"] for o in groups["Welding Technology"]["options"]] == ["nd", "hnd"]
    # An unmapped discipline is offered to every type.
    cat.med.institution_types.clear()
    assert cat.nursing in relevant_masters(poly)


def test_portal_add_sheet_lists_every_relevant_programme(cat, selecting_app, inst_user, client_for, portal_host):
    inst = selecting_app.institution
    inst.institution_type = cat.poly
    inst.save(update_fields=["institution_type"])
    page = client_for(inst_user, host=portal_host).get("/application/")
    assert page.status_code == 200
    assert b"data-programme-picker" in page.content and b"Foundry Technology" in page.content
    assert b"Community Nursing" not in page.content
    assert b"data-needs-nd" in page.content  # HND greyed until its ND is ticked


# ---------------------------------------------------------------------------
# 2. ND/HND during onboarding
# ---------------------------------------------------------------------------
def test_onboarding_hnd_needs_its_nd_and_goes_with_it(cat, selecting_app, inst_user):
    from accreditation import onboarding_programmes as op
    from institutions import services as inst_services
    from institutions.models import Programme
    from institutions.programme_master import add_programme, picker, remove_programme

    inst = selecting_app.institution
    inst.institution_type = cat.poly
    inst.save(update_fields=["institution_type"])
    with pytest.raises(ValidationError, match="never an HND without its ND"):
        add_programme(inst, master=cat.weld_hnd, application=selecting_app)
    g = {g["name"]: g for g in picker(inst)["groups"]}["Welding Technology"]
    assert [o["blocked"] for o in g["options"]] == [False, True]
    nd = add_programme(inst, master=cat.weld_nd, application=selecting_app)
    hnd = add_programme(inst, master=cat.weld_hnd, application=selecting_app)
    g = {g["name"]: g for g in picker(inst)["groups"]}["Welding Technology"]
    assert all(o["added"] for o in g["options"])
    # Removing the ND takes its HND with it.
    ids = [nd.pk, hnd.pk]
    removed = remove_programme(nd, user=inst_user)
    assert len(removed) == 2
    assert not Programme.unscoped.filter(pk__in=ids).exists()
    # An orphan HND that slipped in another way blocks submission.
    inst_services.add_proposed_programme(inst, "Foundry Technology", cat.hnd, application=selecting_app)
    assert any("HND Foundry Technology needs ND" in b for b in op.submission_blockers(selecting_app))


def test_onboarding_portal_adds_nd_and_hnd_together(cat, selecting_app, inst_user, client_for, portal_host):
    from institutions.models import Programme

    c = client_for(inst_user, host=portal_host)
    r = c.post("/application/programmes/", {"masters": [cat.foundry_hnd.pk], "streams": 1}, **hx("qa-prog-card"))
    assert r.status_code == 422 and b"never an HND without its ND" in r.content
    assert not Programme.unscoped.filter(institution=selecting_app.institution, name="Foundry Technology").exists()
    r = c.post("/application/programmes/", {"masters": [cat.foundry_hnd.pk, cat.foundry_nd.pk], "streams": 2},
               **hx("qa-prog-card"))
    assert r.status_code == 200, r.content[:300]
    added = Programme.unscoped.filter(institution=selecting_app.institution, name="Foundry Technology")
    assert sorted(p.level.code for p in added) == ["hnd", "nd"]
    nd = added.get(level=cat.nd)
    r = c.post(f"/application/programmes/{nd.pk}/remove/", **hx("qa-prog-card"))
    assert r.status_code == 200
    assert not Programme.unscoped.filter(institution=selecting_app.institution, name="Foundry Technology").exists()


# ---------------------------------------------------------------------------
# 2b. ND/HND for approved institutions (propose + QA request)
# ---------------------------------------------------------------------------
def test_approved_propose_routes_through_add_programme(cat, p3, client_for, portal_host):
    from institutions.models import Programme
    from institutions.programme_master import add_programme

    with pytest.raises(ValidationError, match="needs the ND"):
        add_programme(p3.inst, master=cat.weld_hnd)
    c = client_for(p3.admin, host=portal_host)
    r = c.post("/qa-requests/new-programme/", {"masters": [cat.weld_hnd.pk], "streams": 1})
    assert r.status_code == 200 and b"needs the ND" in r.content
    r = c.post("/qa-requests/new-programme/", {"masters": [cat.weld_nd.pk, cat.weld_hnd.pk], "streams": 1})
    assert r.status_code == 302, r.content[:300]
    rows = Programme.unscoped.filter(institution=p3.inst, name="Welding Technology")
    assert rows.count() == 2 and all(p.status == "proposed" and p.master_id and p.body_source == "master"
                                     for p in rows)
    assert all(f"chosen={p.pk}" in r["Location"] for p in rows)


def test_portal_request_hnd_needs_its_nd_on_the_request(cat, p3, resource_type):
    from accreditation import qa_services as svc
    from accreditation.models import QAType
    from institutions.programme_master import add_programme

    resource_type = QAType.objects.get(pk=resource_type.pk)  # fresh: the published template
    nd = add_programme(p3.inst, master=cat.foundry_nd)
    hnd = add_programme(p3.inst, master=cat.foundry_hnd)  # allowed: the ND is being proposed
    with pytest.raises(ValidationError, match="HND"):
        svc.create_request(p3.inst, resource_type, [hnd], user=p3.admin, justifications={})
    req = svc.create_request(p3.inst, resource_type, [nd, hnd], user=p3.admin, justifications={})
    assert len(req.active_programmes()) == 2


# ---------------------------------------------------------------------------
# 3. Onboarding clearance: the handling department's Director only, in bulk
# ---------------------------------------------------------------------------
def test_onboarding_clearance_is_director_only_and_bulk(selecting_app, inst_user, p2, client_for,
                                                        django_capture_on_commit_callbacks):
    from django.contrib.auth.models import Permission

    from accounts.models import User
    from accreditation import onboarding_programmes as op
    from accreditation.models import OnboardingProgrammeReview

    op.submit_programmes(selecting_app, user=inst_user)
    assert [u.pk for u in op.clearers_for(selecting_app)] == [p2.ntird_director.pk]
    officer = p2.ntird_officer
    officer.user_permissions.add(Permission.objects.get(codename="clear_qa_programmes"))
    officer = User.objects.get(pk=officer.pk)
    ok, why = op.can_clear(selecting_app, officer)
    assert not ok and "Director" in why
    assert not op.can_clear(selecting_app, p2.director)[0]  # another department's Director
    reviews = list(OnboardingProgrammeReview.unscoped.filter(application=selecting_app))
    ids = [r.pk for r in reviews]
    with pytest.raises(PermissionDenied):
        op.decide_many(selecting_app, ids, user=officer, decision="clear")
    with pytest.raises(ValidationError, match="remark"):
        op.decide_many(selecting_app, ids, user=p2.ntird_director, decision="return", remark="")
    c = client_for(p2.ntird_director)
    page = c.get("/qa/applications/programmes/")
    assert b"Select all waiting" in page.content
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post(f"/qa/applications/programmes/{selecting_app.pk}/decide/",
                   {"reviews": ids, "decision": "clear", "remark": "All in order"}, **hx("progq"))
    assert r.status_code == 200, r.content[:300]
    selecting_app.refresh_from_db()
    assert selecting_app.status == "programme_fee_due"
    assert set(OnboardingProgrammeReview.unscoped.filter(pk__in=ids).values_list("status", flat=True)) == {"cleared"}


# ---------------------------------------------------------------------------
# 4. QA request clearance of an approved institution: its handling Director
# ---------------------------------------------------------------------------
def test_approved_new_programme_clearance_goes_to_handling_director(p3, resource_type,
                                                                    django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from notifications.models import Notification

    req = make_request(p3, [p3.proposed], type_code="resource_inspection")
    with django_capture_on_commit_callbacks(execute=True):
        req = svc.submit(req, user=p3.ceo, declaration=True)
    assert req.status == "programme_clearance"
    assert svc.clearance_department(req).code == "dpap"
    url = f"/qa/requests/{req.pk}/"
    assert Notification.objects.filter(recipient=p3.p2.director, url__endswith=url).exists()
    assert not Notification.objects.filter(recipient=p3.p2.ntird_director, url__endswith=url).exists()
    assert req in svc.clearance_queue(p3.p2.director)
    assert req not in svc.clearance_queue(p3.p2.ntird_director)
    with pytest.raises(PermissionDenied):
        svc.clear_programmes(req, user=p3.p2.ntird_director)
    with pytest.raises(ValidationError):
        svc.return_programmes(req, user=p3.p2.director, remark="")
    svc.clear_programmes(req, user=p3.p2.director, remark="Fine")
    req.refresh_from_db()
    assert req.status == "fee_due"
