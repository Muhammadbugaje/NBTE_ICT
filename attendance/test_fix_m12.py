"""Regression tests for review finding M12 (attendance jobs and GPS checks)."""
import json
from datetime import date, timedelta

import pytest
from django.urls import reverse

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db

MONDAY = date(2026, 10, 5)


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def _holiday(**kw):
    from portal.models import Holiday
    data = {"name": "Public holiday", "date_start": MONDAY, "date_end": MONDAY}
    data.update(kw)
    return Holiday.objects.create(**data)


def test_auto_mark_absent_skips_public_holiday(ict, roles):
    from attendance.models import AttendanceRecord
    from attendance.services import auto_mark_absent
    u = mk("m12a@x.org", ict, roles["staff"])
    _holiday()
    auto_mark_absent(for_date=MONDAY)
    assert not AttendanceRecord.objects.filter(user=u, date=MONDAY).exists()


def test_auto_mark_absent_still_marks_on_normal_day(ict, roles):
    from attendance.models import AttendanceRecord
    from attendance.services import auto_mark_absent
    u = mk("m12b@x.org", ict, roles["staff"])
    auto_mark_absent(for_date=MONDAY)
    assert AttendanceRecord.objects.filter(user=u, date=MONDAY).exists()


def test_department_holiday_only_covers_that_department(ict, roles):
    from attendance.models import AttendanceRecord
    from attendance.services import auto_mark_absent
    from portal.models import Scope
    fin = Department.objects.create(name="Fin", code="fin-m12")
    in_ict = mk("m12c@x.org", ict, roles["staff"])
    in_fin = mk("m12d@x.org", fin, roles["staff"])
    _holiday(visibility=Scope.DEPARTMENT, department=ict, kind="departmental")
    auto_mark_absent(for_date=MONDAY)
    assert not AttendanceRecord.objects.filter(user=in_ict, date=MONDAY).exists()
    assert AttendanceRecord.objects.filter(user=in_fin, date=MONDAY).exists()


def _capture(monkeypatch):
    import core.email_utils
    sent = []
    monkeypatch.setattr(core.email_utils, "send_templated_email",
                        lambda *a, **kw: sent.extend(kw.get("recipients") or []))
    return sent


def test_weekly_summary_uses_department_head(ict, roles, monkeypatch):
    from attendance.models import AttendanceRecord
    from attendance.services import send_weekly_summary
    head = mk("m12-head@x.org", ict, roles["staff"])
    ict.head = head
    ict.save()
    staff = mk("m12e@x.org", ict, roles["staff"])
    AttendanceRecord.objects.create(user=staff, date=MONDAY, status="absent", source="auto")
    sent = _capture(monkeypatch)
    send_weekly_summary(for_date=MONDAY + timedelta(days=7))  # covers last week
    assert head.email in sent


def test_weekly_summary_falls_back_to_director(ict, roles, monkeypatch):
    from attendance.models import AttendanceRecord
    from attendance.services import send_weekly_summary
    director = mk("m12-dir@x.org", ict, roles["director"])
    staff = mk("m12f@x.org", ict, roles["staff"])
    AttendanceRecord.objects.create(user=staff, date=MONDAY, status="absent", source="auto")
    sent = _capture(monkeypatch)
    send_weekly_summary(for_date=MONDAY + timedelta(days=7))  # covers last week
    assert director.email in sent


def test_celery_schedule_has_no_duplicate_attendance_jobs():
    from nbte_ict.celery import app
    tasks = [e["task"] for e in app.conf.beat_schedule.values()]
    for name in ("attendance.tasks.auto_mark_absent", "attendance.tasks.send_weekly_summary"):
        assert tasks.count(name) <= 1, name


@pytest.mark.parametrize("payload", [
    {"lat": 0, "lng": 0, "accuracy": 10},
    {"lat": 95, "lng": 7, "accuracy": 10},
    {"lat": "nan", "lng": 7, "accuracy": 10},
    {"lat": 10.5, "lng": 7.4},                      # no accuracy
    {"lat": 10.5, "lng": 7.4, "accuracy": 5000},    # too imprecise
])
def test_check_in_api_rejects_impossible_or_imprecise(client, ict, roles, payload, monkeypatch):
    from types import SimpleNamespace
    from attendance import services
    # Even if the location service would accept it, the view must refuse.
    monkeypatch.setattr(services, "check_in_user", lambda *a: SimpleNamespace(
        ok=True, status="present", message="ok", distance_meters=0, location=None))
    client.force_login(mk("m12g@x.org", ict, roles["staff"]))
    resp = client.post(reverse("attendance:check_in_api"), json.dumps(payload),
                       content_type="application/json")
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_check_in_api_passes_sane_reading_to_service(client, ict, roles, monkeypatch):
    from attendance import services
    seen = {}

    def fake(user, lat, lng):
        seen["args"] = (lat, lng)
        from types import SimpleNamespace
        return SimpleNamespace(ok=False, status="outside", message="Outside",
                               distance_meters=None, location=None)

    monkeypatch.setattr(services, "check_in_user", fake)
    client.force_login(mk("m12h@x.org", ict, roles["staff"]))
    client.post(reverse("attendance:check_in_api"),
                json.dumps({"lat": 10.5, "lng": 7.4, "accuracy": 25}),
                content_type="application/json")
    assert seen["args"] == (10.5, 7.4)
