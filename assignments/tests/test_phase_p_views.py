"""Phase P web pages and API for rotation (X27–X29) and review chains (X30)."""
import pytest
from django.test import Client
from rest_framework.test import APIClient

from assignments import chain, rotation
from assignments.models import WorkAssignment
from assignments.models_chain import ReviewChainPolicy
from assignments.models_rotation import RotationDecision, RotationMember, RotationPool


@pytest.fixture
def setup(people, depts):
    from institutions.models import Institution
    inst = Institution.unscoped.create(code="RV", name="Board Demo Poly", status="approved",
                                       handling_department=depts["ict"])
    pool = RotationPool.objects.create(code="document_analysis:ict", kind="document_analysis",
                                       department=depts["ict"], name="Document analysis · ICT")
    analysts = [people["officer"], people["officer2"], people["staff"]]
    for u in analysts:
        RotationMember.objects.create(pool=pool, user=u)
    for i in range(2):
        WorkAssignment.objects.create(institution=inst, kind="document_analysis", department=depts["ict"],
                                      assignee=people["officer"], status="in_progress", subject_id=f"x{i}")
    ReviewChainPolicy.objects.create(code="document_analysis", name="Document analysis", steps=[
        {"key": "work", "actor": "assignee"}, {"key": "director", "actor": "director_of_department"}],
        return_targets={"director": ["work"]})
    return {"inst": inst, "pool": pool, "analysts": analysts, **people}


def web(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def api(user):
    from api.tokens import refresh_for
    c = APIClient(HTTP_HOST="localhost")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_for(user).access_token}")
    return c


def test_x29_board_counts(setup):
    r = web(setup["director"]).get("/qa/department/rotation/")
    assert r.status_code == 200
    ctx = r.context
    assert ctx["dept"] == setup["pool"].department
    names = [row["user"] for row in ctx["rows"]]
    assert setup["officer"] in names and setup["director"] not in names, "the director assigns, does not take turns"
    officer_row = next(row for row in ctx["rows"] if row["user"] == setup["officer"])
    assert officer_row["open"] == 2
    assert sum(len(c["chips"]) for c in officer_row["cells"]) == 2
    assert ctx["groups"] == [] and ctx["waiting_total"] == 0, "no real work waiting: nothing to assign"
    assert b"No work waiting for assignment in" in r.content
    assert not RotationDecision.objects.exists(), "viewing the board logs nothing"
    # HTMX swap renders the panel only
    r = web(setup["director"]).get("/qa/department/rotation/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="rot-panel")
    assert r.status_code == 200 and b"<html" not in r.content


def test_x29_only_directors_and_es_open_rotation(setup):
    for who in ("officer", "officer2", "staff", "deputy", "head", "hr_officer"):
        assert web(setup[who]).get("/qa/department/rotation/").status_code == 403, who
    assert web(setup["officer"]).get("/qa/department/rotation/assign/?job=document_analysis:1").status_code == 403
    assert web(setup["officer"]).get("/qa/department/rotation/settings/").status_code == 403
    assert web(setup["es"]).get("/qa/department/rotation/").status_code == 200


def test_x29_pool_settings_director_only(setup):
    c = web(setup["director"])
    assert c.get("/qa/department/rotation/settings/?pool=document_analysis:ict").status_code == 200
    m = RotationMember.objects.get(pool=setup["pool"], user=setup["staff"])
    r = c.post(f"/qa/department/rotation/settings/member/{m.pk}/",
               {"weight": "0.5", "max_concurrent": "2", "is_active": "1", "paused_until": "", "pause_reason": ""})
    assert r.status_code == 200
    m.refresh_from_db()
    assert str(m.weight) == "0.50" and m.max_concurrent == 2
    r = c.post("/qa/department/rotation/settings/?pool=document_analysis:ict",
               {"action": "rules", "cap": "4", "w_load": "50", "w_fairness": "30", "w_recency": "20",
                "rule_availability": "off"})
    assert r.status_code == 200
    cfg = rotation.settings_for(RotationPool.objects.get(pk=setup["pool"].pk))
    assert cfg.cap == 4 and cfg.weights["load"] == 0.5 and not cfg.rule_on("availability")
    assert web(setup["officer2"]).post(f"/qa/department/rotation/settings/member/{m.pk}/",
                                       {"weight": "3"}).status_code == 403


def test_x30_chain_panel_web(setup):
    ch = chain.open_chain("document_analysis", setup["inst"], department=setup["pool"].department,
                          assignee=setup["officer"])
    o = web(setup["officer"])
    assert o.get(f"/qa/chains/{ch.pk}/").status_code == 200
    r = o.post(f"/qa/chains/{ch.pk}/act/", {"action": "submit", "remark": "Recommend accept"})
    assert r.status_code == 200
    r = o.post(f"/qa/chains/{ch.pk}/act/", {"action": "approve"})
    assert r.status_code == 422          # the officer can't confirm their own work
    r = web(setup["director"]).post(f"/qa/chains/{ch.pk}/act/", {"action": "approve", "remark": "OK"})
    assert r.status_code == 200
    ch.refresh_from_db()
    assert ch.state == "approved"
    assert web(setup["hr_officer"]).get(f"/qa/chains/{ch.pk}/").status_code == 404


def test_x27_x30_api_mirror(setup):
    a = api(setup["director"])
    r = a.get("/api/v1/rotation/pools/")
    assert r.status_code == 200 and [p["code"] for p in r.json()["results"]] == ["document_analysis:ict"]
    assert api(setup["officer"]).get("/api/v1/rotation/suggest/?pool=document_analysis:ict").status_code == 403
    assert api(setup["hr_officer"]).get("/api/v1/rotation/suggest/?pool=document_analysis:ict").status_code == 404
    r = a.get(f"/api/v1/rotation/suggest/?pool=document_analysis:ict&institution={setup['inst'].pk}")
    assert r.status_code == 200
    body = r.json()
    r = a.post(f"/api/v1/rotation/decisions/{body['decision']}/", {"chosen": body["suggested"]}, format="json")
    assert r.status_code == 200 and r.json()["overridden"] is False
    ch = chain.open_chain("document_analysis", setup["inst"], department=setup["pool"].department,
                          assignee=setup["director"])
    r = api(setup["director"]).post(f"/api/v1/chains/{ch.pk}/act/", {"action": "submit"}, format="json")
    assert r.status_code == 200 and r.json()["current_step"] == "director"
    r = api(setup["director"]).post(f"/api/v1/chains/{ch.pk}/act/", {"action": "approve"}, format="json")
    assert r.status_code == 403
    assert api(setup["hr_officer"]).get(f"/api/v1/chains/{ch.pk}/").status_code == 404
