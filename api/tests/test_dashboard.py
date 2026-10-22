"""dashboard/executive/ (staff door) and the features object of home/."""
import pytest

from .conftest import BASE, bearer, err
from .work_fixtures import api_as, boss, grant, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db

URL = BASE + "dashboard/executive/"
TONES = {"good", "warn", "bad", "neutral"}


def _check_shape(body):
    assert {"title", "scope", "scope_label", "generated_at", "kpis", "series", "lists"} <= set(body)
    assert body["scope"] in ("organisation", "department")
    for k in body["kpis"]:
        assert set(k) == {"key", "label", "value", "display", "unit", "delta", "delta_label", "tone", "hint",
                          "target"}
        assert isinstance(k["value"], (int, float)) and k["tone"] in TONES
    for s in body["series"]:
        assert set(s) == {"key", "label", "kind", "unit", "points"} and s["kind"] in ("line", "bar")
        assert all(set(p) == {"label", "value"} for p in s["points"])
    for lst in body["lists"]:
        assert set(lst) == {"key", "label", "items"}
        for item in lst["items"]:
            assert set(item) == {"title", "subtitle", "value", "tone", "target"}


def test_not_signed_in_and_wrong_door(staff_api, portal_api, rp_user):
    r = staff_api.get(URL)
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"
    r = bearer(staff_api, rp_user).get(URL)
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"
    assert bearer(portal_api, rp_user).get(URL).status_code == 404  # staff-only route


def test_plain_staff_is_refused(worker):
    r = api_as(worker).get(URL)
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"


def test_password_change_required(work_seed, make_user):
    u = make_user("temp.dir@t.local", role="director", dept="ict", must_change=True)
    r = api_as(u).get(URL)
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


@pytest.mark.parametrize("role, board", [("director", "director"), ("deputy-director", "deputy"),
                                         ("executive-secretary", "es")])
def test_leadership_roles(work_seed, make_user, role, board):
    u = make_user(f"lead.{board}@t.local", role=role, dept="ict")
    r = api_as(u).get(URL)
    assert r.status_code == 200, r.content
    body = r.json()
    _check_shape(body)
    assert body["board"] == board
    keys = {k["key"] for k in body["kpis"]}
    assert {"approvals_pending", "approvals_aged", "requests_completed_30d"} <= keys
    assert any(lst["key"] == "aged_approvals" for lst in body["lists"])
    if board == "es":
        assert "es_signoff" in keys
    home = api_as(u).get(BASE + "home/").json()
    assert home["features"]["executive_dashboard"] is True and home["features"]["search"] is True


def test_chairman_permission_and_superuser(work_seed, make_user):
    u = grant(make_user("chair@t.local", role="officer", dept="ict"), "accounts.view_chairman_dashboard")
    body = api_as(u).get(URL).json()
    assert body["board"] == "chairman" and body["scope"] == "organisation"
    admin = make_user("root@t.local", superuser=True)
    body = api_as(admin).get(URL).json()
    _check_shape(body)
    assert body["board"] == "chairman" and body["scope"] == "organisation"


def test_department_scope_counts_only_visible_requests(work_seed, make_user, boss):
    body = api_as(boss).get(URL).json()
    assert body["scope"] == "department" and "ICT" in body["scope_label"]


def test_home_features_for_other_accounts(worker, rp_user, portal_api):
    f = api_as(worker).get(BASE + "home/").json()["features"]
    assert f == {"search": True, "executive_dashboard": False, "office_code_check_in": True}
    f = bearer(portal_api, rp_user).get(BASE + "home/").json()["features"]
    assert f == {"search": False, "executive_dashboard": False, "office_code_check_in": False}
