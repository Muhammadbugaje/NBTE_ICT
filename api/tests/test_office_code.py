"""attendance/check-in/code/ (staff door): office-code check-in, plus the
office_code_* fields of attendance/today/."""
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from .conftest import BASE, bearer, err
from .work_fixtures import api_as, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db

URL = BASE + "attendance/check-in/code/"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def working_day(monkeypatch):
    monkeypatch.setattr("attendance.services.work_week", lambda: {0, 1, 2, 3, 4, 5, 6})


@pytest.fixture
def live_code(work_seed):
    from attendance.models import AttendanceLocation, LocationCheckInCode

    loc = AttendanceLocation.objects.create(code="hq", name="NBTE HQ", latitude=9.07, longitude=7.39)
    now = timezone.now()
    return LocationCheckInCode.objects.create(location=loc, code="ABC234", window_start=now - timedelta(minutes=5),
                                              window_end=now + timedelta(minutes=25))


def test_not_signed_in_and_wrong_door(staff_api, rp_user):
    r = staff_api.post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"
    r = bearer(staff_api, rp_user).post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_password_change_required(work_seed, make_user, live_code):
    u = make_user("temp.pw@t.local", role="officer", dept="ict", must_change=True)
    r = api_as(u).post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


def test_today_reports_office_code(worker, live_code, working_day):
    t = api_as(worker).get(BASE + "attendance/today/").json()
    assert t["office_code_enabled"] is True and t["office_code_reason"] == ""
    assert t["manual_check_in_enabled"] is True


def test_invalid_input(worker, live_code, working_day):
    c = api_as(worker)
    r = c.post(URL, {}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "validation_error" and "code" in err(r)["fields"]
    r = c.post(URL, {"code": "X" * 13}, format="json")
    assert r.status_code == 400 and "code" in err(r)["fields"]


def test_wrong_code_then_right_code(worker, live_code, working_day):
    from attendance.models import AttendanceRecord

    c = api_as(worker)
    r = c.post(URL, {"code": "ZZZ999"}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"
    assert err(r)["fields"]["code"] == ["That code isn't valid right now. Ask the office contact for the current "
                                        "code and try again."]
    r = c.post(URL, {"code": " abc 234 "}, format="json")  # case and spaces ignored, like the website
    assert r.status_code == 201, r.content
    body = r.json()
    assert set(body) == {"status", "status_display", "message", "distance_meters", "location", "record"}
    assert body["status"] in ("present", "late") and body["location"] == "NBTE HQ"
    assert body["message"].startswith("Checked in via NBTE HQ.")
    assert body["record"]["source"] == "manual" and body["distance_meters"] is None
    rec = AttendanceRecord.objects.get(user=worker)
    assert rec.source == "manual" and rec.recorded_by_id == worker.pk and "location code" in rec.notes
    # second time: refused, as on the website
    r = c.post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "check_in_refused"
    assert err(r)["message"] == "You already have an attendance record for today."
    t = c.get(BASE + "attendance/today/").json()
    assert t["checked_in"] and t["office_code_reason"]


def test_lateness_follows_location_start(worker, live_code, working_day):
    loc = live_code.location
    loc.work_start_time = (timezone.localtime() - timedelta(hours=3)).time()
    loc.grace_minutes = 0
    loc.save()
    r = api_as(worker).post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 201 and r.json()["status"] == "late"
    assert r.json()["message"] == "Checked in via NBTE HQ. Recorded as late."


def test_attempt_limit(worker, live_code, working_day, monkeypatch):
    monkeypatch.setattr("attendance.codes.max_attempts_per_hour", lambda: 2)
    c = api_as(worker)
    for _ in range(2):
        assert c.post(URL, {"code": "WRONG1"}, format="json").status_code == 400
    r = c.post(URL, {"code": "ABC234"}, format="json")  # even the right code is refused now
    assert r.status_code == 400 and "Too many incorrect codes" in err(r)["fields"]["code"][0]


def test_code_of_another_department_is_refused(worker, live_code, working_day):
    from staff.models import Department

    live_code.location.departments.add(Department.objects.get(code="finance"))
    r = api_as(worker).post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 400 and "code" in err(r)["fields"]


def test_switched_off_and_non_working_day(worker, live_code, monkeypatch):
    monkeypatch.setattr("attendance.services.work_week", lambda: set())
    r = api_as(worker).post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "check_in_refused"
    monkeypatch.setattr("attendance.services.work_week", lambda: {0, 1, 2, 3, 4, 5, 6})
    monkeypatch.setattr("attendance.codes.manual_checkin_enabled", lambda: False)
    r = api_as(worker).post(URL, {"code": "ABC234"}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "check_in_refused" and "disabled" in err(r)["message"]
    assert api_as(worker).get(BASE + "attendance/today/").json()["office_code_enabled"] is False


def test_website_page_still_works(client, worker, live_code, working_day):
    from django.urls import reverse

    from attendance.models import AttendanceRecord

    client.force_login(worker)
    r = client.post(reverse("attendance:check_in_manual"), {"code": "nope00"})
    assert r.status_code == 200 and not AttendanceRecord.objects.filter(user=worker).exists()
    r = client.post(reverse("attendance:check_in_manual"), {"code": "ABC234"})
    assert r.status_code == 302 and AttendanceRecord.objects.get(user=worker).source == "manual"
