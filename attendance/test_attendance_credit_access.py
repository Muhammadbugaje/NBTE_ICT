"""Regression tests: no check-in / codes on non-working days, credited
statuses in reports and exports, and who may see which attendance."""
import io
from datetime import date, datetime, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db

SATURDAY = date(2026, 10, 17)
MONDAY = date(2026, 10, 5)


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def hr():
    return Department.objects.get_or_create(code="hr", defaults={"name": "Human Resources"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name=email.split("@")[0], department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def aware(d, hour=10):
    return timezone.make_aware(datetime(d.year, d.month, d.day, hour, 0))


def _location(dept):
    from attendance.models import AttendanceLocation
    loc = AttendanceLocation.objects.create(
        code="hq", name="HQ", latitude=10.5, longitude=7.4, radius_meters=500,
    )
    loc.departments.add(dept)
    return loc


# --------------------------------------------------------------------------
# Non-working days
# --------------------------------------------------------------------------
def test_gps_check_in_refused_on_weekend(ict, roles):
    from attendance.models import AttendanceRecord
    from attendance.services import check_in_user
    u = mk("w1@x.org", ict, roles["staff"])
    _location(ict)
    res = check_in_user(u, 10.5, 7.4, now=aware(SATURDAY))
    assert not res.ok and "not a working day" in res.message.lower()
    assert not AttendanceRecord.objects.filter(user=u).exists()


def test_gps_check_in_refused_on_public_holiday(ict, roles):
    from attendance.models import AttendanceRecord
    from attendance.services import check_in_user
    from portal.models import Holiday
    u = mk("w2@x.org", ict, roles["staff"])
    _location(ict)
    Holiday.objects.create(name="Independence Day", date_start=MONDAY, date_end=MONDAY)
    res = check_in_user(u, 10.5, 7.4, now=aware(MONDAY))
    assert not res.ok and "holiday" in res.message.lower()
    assert not AttendanceRecord.objects.filter(user=u).exists()


def test_codes_not_generated_on_weekend_or_holiday(ict):
    from attendance import codes
    from attendance.models import LocationCheckInCode
    from portal.models import Holiday
    loc = _location(ict)
    assert codes.get_current_code(loc, now=aware(SATURDAY)) is None
    Holiday.objects.create(name="Holiday", date_start=MONDAY, date_end=MONDAY)
    assert codes.get_current_code(loc, now=aware(MONDAY)) is None
    assert not LocationCheckInCode.objects.exists()
    assert codes.get_current_code(loc, now=aware(MONDAY + timedelta(days=1))) is not None


def test_manual_check_in_blocked_on_weekend(client, ict, roles, monkeypatch):
    from attendance.models import AttendanceRecord
    monkeypatch.setattr("django.utils.timezone.localdate", lambda *a, **k: SATURDAY)
    u = mk("w3@x.org", ict, roles["staff"])
    client.force_login(u)
    resp = client.post(reverse("attendance:check_in_manual"), {"code": "ABC234"})
    assert resp.status_code == 302
    assert not AttendanceRecord.objects.filter(user=u).exists()


def test_stale_code_rejected_on_weekend(ict, roles):
    from attendance import codes
    from attendance.models import LocationCheckInCode
    from django.core.exceptions import ValidationError
    u = mk("w4@x.org", ict, roles["staff"])
    loc = _location(ict)
    LocationCheckInCode.objects.create(
        location=loc, code="ABC234",
        window_start=aware(SATURDAY, 0), window_end=aware(SATURDAY + timedelta(days=1), 0),
    )
    with pytest.raises(ValidationError):
        codes.verify_check_in_code(u, "ABC234", now=aware(SATURDAY))


# --------------------------------------------------------------------------
# Credited statuses
# --------------------------------------------------------------------------
def _records(user):
    from attendance.models import AttendanceRecord as R
    base = date(2026, 9, 7)  # Monday, all in the past
    for i, st in enumerate([R.PRESENT, R.LATE, R.EXCUSED, R.ON_LEAVE, R.HOLIDAY]):
        R.objects.create(user=user, date=base + timedelta(days=i), status=st,
                         source=R.SOURCE_LEAVE if st == R.ON_LEAVE else R.SOURCE_MANUAL)
    R.objects.create(user=user, date=base + timedelta(days=7), status=R.ABSENT, source=R.SOURCE_MANUAL)


def test_summary_credits_excused_holiday_and_leave(ict, roles):
    from attendance.services import summarize_period
    u = mk("c1@x.org", ict, roles["staff"])
    _records(u)
    t = summarize_period([u], date(2026, 9, 1), date(2026, 9, 30))[u.pk]
    assert (t["present"], t["late"], t["excused"], t["on_leave"], t["holiday"], t["absent"]) == (1, 1, 1, 1, 1, 1)
    assert t["credited"] == 5 and t["expected"] == 6 and t["pct"] == 83


def test_department_report_and_export_show_credited_columns(client, ict, roles):
    director = mk("c2@x.org", ict, roles["director"])
    staff = mk("c3@x.org", ict, roles["staff"])
    _records(staff)
    client.force_login(director)

    resp = client.get(reverse("attendance:department"), {"year": 2026, "month": 9})
    assert resp.status_code == 200
    row = next(r for r in resp.context["rows"] if r["user"] == staff)
    # (the page also auto-marks the remaining past working days absent, so only
    # the credited count is asserted here; the percentage is covered above)
    assert row["credited"] == 5 and row["absent"] >= 1
    assert resp.context["dept_total"]["credited"] >= 5

    resp = client.get(reverse("attendance:export_excel"), {"year": 2026, "month": 9})
    assert resp.status_code == 200
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.content))
    ws = wb.worksheets[0]
    headers = [c.value for c in ws[3]]
    for h in ("Excused", "Holiday", "On approved leave", "Credited total", "Attendance %"):
        assert h in headers
    assert "Leave, excused & holiday" in wb.sheetnames

    resp = client.get(reverse("attendance:export_pdf"), {"year": 2026, "month": 9})
    assert resp.status_code == 200 and resp.content[:4] == b"%PDF"


def test_import_preview_reports_aggregate(ict, roles):
    from attendance.services import parse_matrix
    from openpyxl import Workbook
    u = mk("c4@x.org", ict, roles["staff"])
    wb = Workbook(); ws = wb.active
    ws.append(["Email", 1, 2, 3, 4, 5])           # Oct 2026: Thu, Fri, Sat, Sun, Mon
    ws.append([u.email, "1", "E", "1", "1", "H"])  # Sat/Sun are not working days
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    parsed = parse_matrix(buf, month=10, year=2026)
    assert parsed.totals == {"present": 1, "excused": 1, "holiday": 1}
    assert parsed.total_credited == 3 and parsed.total_counted == 3


# --------------------------------------------------------------------------
# Who may see what
# --------------------------------------------------------------------------
def test_director_sees_only_own_department(client, ict, hr, roles):
    director = mk("a1@x.org", ict, roles["director"])
    client.force_login(director)
    resp = client.get(reverse("attendance:department"), {"department": hr.pk})
    assert resp.status_code == 200 and resp.context["department"] == ict
    assert client.get(reverse("attendance:all_departments")).status_code == 403
    assert client.get(reverse("attendance:export_excel"), {"department": hr.pk}).status_code == 200


def test_plain_staff_cannot_open_department_reports(client, ict, roles):
    client.force_login(mk("a2@x.org", ict, roles["staff"]))
    assert client.get(reverse("attendance:department")).status_code == 403
    assert client.get(reverse("attendance:all_departments")).status_code == 403


def test_executive_secretary_needs_the_permission(client, ict, roles):
    from django.contrib.auth.models import Permission
    es = mk("a3@x.org", ict, roles["executive-secretary"])
    client.force_login(es)
    assert client.get(reverse("attendance:all_departments")).status_code == 200
    perm = Permission.objects.get(content_type__app_label="attendance", codename="view_all_attendance")
    roles["executive-secretary"].permissions.remove(perm)
    client.force_login(User.objects.get(pk=es.pk))
    assert client.get(reverse("attendance:all_departments")).status_code == 403


def test_super_admin_role_sees_everything_automatically(client, ict, roles):
    client.force_login(mk("a4@x.org", ict, roles["super-admin"]))
    assert client.get(reverse("attendance:all_departments")).status_code == 200


def test_hr_director_sees_all_departments_but_hr_staff_does_not(client, ict, hr, roles):
    client.force_login(mk("a5@x.org", hr, roles["director"]))
    assert client.get(reverse("attendance:all_departments")).status_code == 200
    client.force_login(mk("a6@x.org", hr, roles["staff"]))
    assert client.get(reverse("attendance:all_departments")).status_code == 403


def test_explicit_permission_grants_all_departments(client, ict, roles):
    from django.contrib.auth.models import Permission
    perm = Permission.objects.get(content_type__app_label="attendance", codename="view_all_attendance")
    u = mk("a7@x.org", ict, roles["staff"])
    u.user_permissions.add(perm)
    client.force_login(u)
    assert client.get(reverse("attendance:all_departments")).status_code == 200


# --------------------------------------------------------------------------
# Dashboard: real, scoped, headcount based
# --------------------------------------------------------------------------
def test_dashboard_is_scoped_and_uses_headcount(ict, hr, roles):
    from attendance.models import AttendanceRecord as R
    from dashboards.services import _attendance_dashboard
    director = mk("d1@x.org", ict, roles["director"])
    ict_staff = [mk(f"d{i}@x.org", ict, roles["staff"]) for i in range(2, 5)]
    hr_staff = mk("d9@x.org", hr, roles["staff"])
    for d in range(3):                       # three completed working days
        day = timezone.localdate() - timedelta(days=30 + d)
        while day.weekday() > 4:
            day -= timedelta(days=1)
        for u in [director] + ict_staff:
            R.objects.update_or_create(user=u, date=day, defaults={"status": R.PRESENT})
        R.objects.update_or_create(user=hr_staff, date=day, defaults={"status": R.ABSENT})

    mine = _attendance_dashboard(director, days=60)
    assert mine["attendance_scope"] == "department"
    assert [r["department"] for r in mine["attendance_depts"]] == [ict]
    assert mine["attendance_kpis"]["total_staff"] == 4

    es = mk("d10@x.org", ict, roles["executive-secretary"])
    allv = _attendance_dashboard(es, days=60)
    assert allv["attendance_scope"] == "all"
    assert {r["department"] for r in allv["attendance_depts"]} == {ict, hr}

    plain = mk("d11@x.org", ict, roles["staff"])
    assert _attendance_dashboard(plain)["attendance_visible"] is False


# --------------------------------------------------------------------------
# Only recorded days count; consecutive leave days are one row
# --------------------------------------------------------------------------
def test_days_without_a_record_are_not_counted(ict, roles):
    from attendance.services import summarize_period
    u = mk("n1@x.org", ict, roles["staff"])
    t = summarize_period([u], date(2026, 9, 1), date(2026, 9, 30))[u.pk]
    assert t["expected"] == 0 and t["credited"] == 0 and t["pct"] == 0


def test_backfill_never_goes_before_the_first_recorded_day(client, ict, roles):
    from attendance.models import AttendanceRecord as R
    director = mk("n2@x.org", ict, roles["director"])
    staff = mk("n3@x.org", ict, roles["staff"])
    User.objects.filter(pk__in=[director.pk, staff.pk]).update(
        date_joined=timezone.make_aware(datetime(2026, 1, 1)))
    first = date(2026, 9, 21)  # Monday
    R.objects.create(user=staff, date=first, status="present")
    client.force_login(director)
    assert client.get(reverse("attendance:department"), {"year": 2026, "month": 9}).status_code == 200
    assert not R.objects.filter(date__lt=first).exists()
    assert R.objects.filter(user=staff, date__gt=first, status="absent").exists()


def test_same_approver_consecutive_days_are_one_row(ict, roles):
    from attendance.models import AttendanceRecord as R
    from attendance.services import group_credited_days
    staff = mk("g1@x.org", ict, roles["staff"])
    boss = mk("g2@x.org", ict, roles["director"])
    other = mk("g3@x.org", ict, roles["director"])
    for d, who in [(14, boss), (15, boss), (16, boss), (19, boss), (20, other)]:
        R.objects.create(user=staff, date=date(2026, 10, d), status=R.EXCUSED,
                         source=R.SOURCE_MANUAL, recorded_by=who)
    recs = list(R.objects.select_related("user", "recorded_by"))
    groups = group_credited_days(recs)
    assert [(g["dates"], g["days"]) for g in groups] == [
        ("Wed 14 Oct – Mon 19 Oct 2026", 4),   # Fri -> Mon is still back-to-back
        ("Tue 20 Oct 2026", 1),                # different person recorded it
    ]
    one = group_credited_days([r for r in recs if r.date.day in (14, 15)])
    assert [(g["dates"], g["days"]) for g in one] == [("Wed 14 Oct – Thu 15 Oct 2026", 2)]
