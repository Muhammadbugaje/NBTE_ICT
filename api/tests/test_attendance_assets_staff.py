"""attendance/, assets/, staff/ and workspace/ (staff door)."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from .conftest import BASE, bearer, err
from .work_fixtures import PDF, api_as, boss, grant, outsider, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db

HQ = (9.0765, 7.3986)


@pytest.fixture
def hq(work_seed):
    from attendance.models import AttendanceLocation

    return AttendanceLocation.objects.create(code="hq", name="NBTE HQ", latitude=HQ[0], longitude=HQ[1],
                                             radius_meters=200, gps_offset_meters=50)


@pytest.fixture
def working_day(monkeypatch):
    # Every weekday counts (patch work_week, not non_working_reason: attendance.codes
    # binds non_working_reason at import time and would keep a patched copy).
    monkeypatch.setattr("attendance.services.work_week", lambda: {0, 1, 2, 3, 4, 5, 6})


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
def test_attendance_auth_and_door(staff_api, rp_user):
    assert staff_api.get(BASE + "attendance/today/").status_code == 401
    r = bearer(staff_api, rp_user).post(BASE + "attendance/check-in/", {}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_today_and_check_in(worker, hq, working_day):
    c = api_as(worker)
    t = c.get(BASE + "attendance/today/").json()
    assert t["can_check_in"] and t["reason"] == "" and not t["checked_in"]
    assert t["locations"][0]["name"] == "NBTE HQ" and t["locations"][0]["radius_meters"] == 250
    assert t["check_out_supported"] is True and t["can_check_out"] is False  # v2.1 G1: check-out exists
    r = c.post(BASE + "attendance/check-in/", {"latitude": HQ[0], "longitude": HQ[1]}, format="json")
    assert r.status_code == 400 and "accuracy" in err(r)["fields"]
    r = c.post(BASE + "attendance/check-in/", {"latitude": HQ[0], "longitude": HQ[1], "accuracy": 900},
               format="json")
    assert r.status_code == 400 and "accurate" in err(r)["message"]
    r = c.post(BASE + "attendance/check-in/", {"latitude": HQ[0], "longitude": HQ[1], "accuracy": 10,
                                               "is_mocked": True}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "mock_location"
    r = c.post(BASE + "attendance/check-in/", {"latitude": 6.5, "longitude": 3.3, "accuracy": 10}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "check_in_refused" and "outside" in err(r)["message"]
    r = c.post(BASE + "attendance/check-in/", {"latitude": HQ[0], "longitude": HQ[1], "accuracy": 10},
               format="json")
    assert r.status_code == 201, r.content
    assert r.json()["status"] in ("present", "late") and r.json()["location"] == "NBTE HQ"
    t = c.get(BASE + "attendance/today/").json()
    assert t["checked_in"] and not t["can_check_in"] and "already" in t["reason"]
    r = c.post(BASE + "attendance/check-in/", {"latitude": HQ[0], "longitude": HQ[1], "accuracy": 10},
               format="json")
    assert r.status_code == 400 and err(r)["code"] == "check_in_refused"
    # v2.1 G1/G2 (was a 501 stub): check out by GPS at the office
    t = c.get(BASE + "attendance/today/").json()
    assert t["can_check_out"] and t["check_out_reason"] == ""
    r = c.post(BASE + "attendance/check-out/", {"method": "gps", "latitude": 6.5, "longitude": 3.3,
                                                "accuracy": 10}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "check_out_refused"
    r = c.post(BASE + "attendance/check-out/", {"method": "gps", "latitude": HQ[0], "longitude": HQ[1],
                                                "accuracy": 10}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["record"]["check_out_at"] and r.json()["record"]["check_out_source"] == "gps"
    r = c.post(BASE + "attendance/check-out/", {"method": "gps", "latitude": HQ[0], "longitude": HQ[1],
                                                "accuracy": 10}, format="json")
    assert r.status_code == 400 and "already" in err(r)["message"]
    hist = c.get(BASE + "attendance/history/").json()
    assert set(hist) == {"results", "next", "previous", "count"}
    assert any(rec["status"] in ("present", "late") for rec in hist["results"])
    r = c.get(BASE + "attendance/history/?month=13")
    assert r.status_code == 400 and "month" in err(r)["fields"]
    s = c.get(BASE + "attendance/summary/").json()
    assert len(s["days"]) == 7 and s["week"]["expected"] >= 1


def test_no_department_cannot_check_in(work_seed, make_user, hq, working_day):
    u = make_user("nodept@t.local", role="officer")
    t = api_as(u).get(BASE + "attendance/today/").json()
    assert not t["can_check_in"] and "department" in t["reason"]


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
@pytest.fixture
def laptop(work_seed, boss, worker):
    from assets import services
    from assets.models import AssetCategory
    from staff.models import Department

    cat = AssetCategory.objects.create(name="Laptop", code="laptop")
    asset = services.create_asset(category=cat, created_by=boss, owner_department=Department.objects.get(code="ict"),
                                  brand="Dell", model="Latitude 5420", state="in_service")
    services.assign_asset(asset=asset, to_user=worker, by_user=boss)
    asset.refresh_from_db()
    return asset


def test_my_assets_scan_and_detail(worker, outsider, laptop):
    c = api_as(worker)
    mine = c.get(BASE + "assets/mine/").json()
    assert mine["count"] == 1 and mine["results"][0]["asset"]["tag"] == laptop.asset_tag
    url = f"https://nbte.example/assets/{laptop.asset_tag}/"
    r = c.get(BASE + f"assets/by-code/{url}")
    assert r.status_code == 200 and r.json()["tag"] == laptop.asset_tag and r.json()["is_mine"]
    assert c.get(BASE + f"assets/by-code/{laptop.asset_tag.lower()}/").status_code == 200
    # an asset I may not see is "not found", by tag or by scan
    o = api_as(outsider)
    assert o.get(BASE + f"assets/{laptop.asset_tag}/").status_code == 404
    assert o.get(BASE + f"assets/by-code/{laptop.asset_tag}/").status_code == 404


def test_report_issue(worker, boss, outsider, laptop, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    c = api_as(worker)
    r = c.post(BASE + f"assets/{laptop.asset_tag}/issues/", {"summary": "bad"}, format="json")
    assert r.status_code == 400 and "summary" in err(r)["fields"]
    photo = SimpleUploadedFile("crack.pdf", PDF, content_type="application/pdf")
    r = c.post(BASE + f"assets/{laptop.asset_tag}/issues/", {"summary": "Screen cracked", "photo": photo},
               format="multipart")
    assert r.status_code == 403  # photos need edit rights, as on the website
    r = c.post(BASE + f"assets/{laptop.asset_tag}/issues/", {"summary": "Screen cracked",
                                                             "description": "Dropped it"}, format="json")
    assert r.status_code == 201 and r.json()["state"] == "damaged" and r.json()["issues"][0]["summary"]
    assert api_as(outsider).post(BASE + f"assets/{laptop.asset_tag}/issues/", {"summary": "Not mine at all"},
                                 format="json").status_code == 404
    manager = grant(boss, "assets.change_asset", "assets.view_department_assets")
    photo = SimpleUploadedFile("crack.pdf", PDF, content_type="application/pdf")
    r = api_as(manager).post(BASE + f"assets/{laptop.asset_tag}/issues/",
                             {"summary": "Hinge loose too", "photo": photo}, format="multipart")
    assert r.status_code == 201, r.content
    att = r.json()["attachments"][0]
    assert c.get(BASE + f"assets/{laptop.asset_tag}/attachments/{att['id']}/").status_code == 200


# ---------------------------------------------------------------------------
# Staff directory
# ---------------------------------------------------------------------------
def test_directory_scoping(worker, boss, outsider):
    c = api_as(worker)
    only_me = c.get(BASE + "staff/").json()
    assert [s["user_id"] for s in only_me["results"]] == [worker.pk]
    colleague = grant(worker, "staff.view_own_department_staff")
    c = api_as(colleague)
    ids = {s["user_id"] for s in c.get(BASE + "staff/").json()["results"]}
    assert ids == {worker.pk, boss.pk}
    boss_staff = c.get(BASE + "staff/?q=boss").json()["results"][0]
    prof = c.get(BASE + f"staff/{boss_staff['id']}/").json()
    assert prof["level"] == "colleague" and "duties" in prof
    out_id = outsider.staff_profile.pk
    assert c.get(BASE + f"staff/{out_id}/").status_code == 404
    depts = c.get(BASE + "staff/departments/").json()["results"]
    assert [d["code"] for d in depts] == ["ict"]


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
def test_workspace(worker, laptop, staff_api, rp_user):
    d = api_as(worker).get(BASE + "workspace/").json()
    assert {w["key"] for w in d["widgets"]} == {"tickets", "tasks", "requests", "assets"}
    assets = next(w for w in d["widgets"] if w["key"] == "assets")
    assert assets["count"] == 1 and assets["items"][0]["route"] == f"/assets/{laptop.asset_tag}"
    assert d["profile"]["staff_id"] and d["leave"]["staff_linked"]
    r = bearer(staff_api, rp_user).get(BASE + "workspace/")
    assert r.status_code == 403
