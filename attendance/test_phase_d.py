"""v2.1 Phase D tests: check-out, office QR, missing check-out, visit duty,
availability, exports/imports and analytics (G1-G9)."""
import io
from datetime import date, datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department


pytestmark = pytest.mark.django_db

HQ = (9.0765, 7.3986)
MONDAY = date(2026, 10, 5)


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def finance():
    return Department.objects.get_or_create(code="finance", defaults={"name": "Finance"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


@pytest.fixture
def hq(ict):
    from attendance.models import AttendanceLocation
    loc = AttendanceLocation.objects.create(code="hq", name="NBTE HQ", latitude=HQ[0], longitude=HQ[1],
                                            radius_meters=200, gps_offset_meters=50, work_start_time=time(8, 0),
                                            work_end_time=time(16, 0), grace_minutes=15)
    return loc


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F", last_name=email[:4],
                                 department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def at(day, hh, mm=0):
    return timezone.make_aware(datetime.combine(day, time(hh, mm)))


def _present(u, day, hh=8, mm=0, loc=None, out=None):
    from attendance.models import AttendanceRecord
    return AttendanceRecord.objects.create(user=u, date=day, status="present" if (hh, mm) <= (8, 15) else "late",
                                           source="gps", check_in_at=at(day, hh, mm), location=loc,
                                           check_out_at=at(day, *out) if out else None)


# ---------------------------------------------------------------------------
# G1 / G2 check-out
# ---------------------------------------------------------------------------
def test_g1_checkout(ict, roles, hq):
    from attendance import checkout, codes
    from attendance.models import AttendanceRecord

    u = mk("g1@x.org", ict, roles["staff"])
    now = at(MONDAY, 16, 30)
    out = checkout.check_out(u, method="gps", lat=HQ[0], lng=HQ[1], accuracy=10, now=now)
    assert not out.ok and "not checked in" in out.message
    _present(u, MONDAY, 8, 5, hq)
    out = checkout.check_out(u, method="gps", lat=6.5, lng=3.3, accuracy=10, now=now)
    assert not out.ok and out.error == "gps_outside"
    out = checkout.check_out(u, method="gps", lat=HQ[0], lng=HQ[1], accuracy=10, now=now)
    assert out.ok
    rec = AttendanceRecord.objects.get(user=u, date=MONDAY)
    assert rec.check_out_at == now and rec.check_out_source == "gps" and rec.status == "present"
    assert not checkout.check_out(u, method="gps", lat=HQ[0], lng=HQ[1], accuracy=10, now=now).ok
    # by the current office code
    u2 = mk("g1b@x.org", ict, roles["staff"])
    _present(u2, MONDAY, 8, 0, hq)
    code = codes.get_current_code(hq, now=at(MONDAY, 15, 0))
    out = checkout.check_out(u2, method="code", code="WRONG1", now=at(MONDAY, 15, 1))
    assert not out.ok and out.error == "code"
    out = checkout.check_out(u2, method="code", code=code.code, now=at(MONDAY, 15, 1))
    assert out.ok and AttendanceRecord.objects.get(user=u2, date=MONDAY).check_out_source == "code"


def test_g1_checkout_web_button(ict, roles, hq, client):
    from attendance.models import AttendanceRecord

    u = mk("g1w@x.org", ict, roles["staff"])
    today = timezone.localdate()
    AttendanceRecord.objects.create(user=u, date=today, status="present", source="gps",
                                    check_in_at=timezone.now(), location=hq)
    client.force_login(u)
    r = client.post(reverse("attendance:check_out_api"), {"method": "gps", "lat": 6.5, "lng": 3.3, "accuracy": 5},
                    content_type="application/json")
    assert r.status_code == 400 and r.json()["error"] == "gps_outside"
    r = client.post(reverse("attendance:check_out_api"), {"method": "gps", "lat": HQ[0], "lng": HQ[1],
                                                          "accuracy": 5}, content_type="application/json")
    assert r.status_code == 200 and r.json()["ok"]


# ---------------------------------------------------------------------------
# G4 office QR
# ---------------------------------------------------------------------------
def test_g4_qr_token_window(ict, roles, hq, client):
    from attendance import checkout
    from attendance.models import AttendanceRecord

    u = mk("g4@x.org", ict, roles["staff"])
    t0 = at(MONDAY, 7, 55)
    token = checkout.make_token(hq, now=t0)
    assert checkout.verify_token(u, token, now=t0 + timedelta(seconds=30)) == hq
    # expired: older than its slot + grace
    rot, grace = checkout.qr_rotation_seconds(), checkout.qr_grace_seconds()
    with pytest.raises(checkout.QRError, match="expired"):
        checkout.verify_token(u, token, now=t0 + timedelta(seconds=rot + grace + 1))
    # forged
    with pytest.raises(checkout.QRError, match="not a valid"):
        checkout.verify_token(u, token[:-3] + "abc", now=t0)
    # scan: check in, then check out
    res = checkout.scan(u, token, now=t0 + timedelta(seconds=5))
    assert res.ok and res.action == "check_in"
    rec = AttendanceRecord.objects.get(user=u, date=MONDAY)
    assert rec.source == "qr" and rec.status == "present"
    later = at(MONDAY, 16, 10)
    res = checkout.scan(u, checkout.make_token(hq, now=later), now=later)
    assert res.ok and res.action == "check_out"
    rec.refresh_from_db()
    assert rec.check_out_source == "qr"
    # another department's office is refused
    other = mk("g4o@x.org", Department.objects.create(code="ppd", name="PPD"), roles["staff"])
    hq.departments.add(ict)
    with pytest.raises(checkout.QRError, match="not an attendance location"):
        checkout.verify_token(other, checkout.make_token(hq, now=t0), now=t0)


def test_g4_office_screen_and_scan_page(ict, roles, hq, client):
    from django.contrib.auth.models import Permission

    viewer = mk("g4v@x.org", ict, roles["staff"])
    client.force_login(viewer)
    assert client.get(reverse("attendance:office_screen", args=[hq.pk])).status_code == 403
    viewer.user_permissions.add(Permission.objects.get(codename="view_location_codes"))
    viewer = User.objects.get(pk=viewer.pk)
    client.force_login(viewer)
    assert client.get(reverse("attendance:office_screen", args=[hq.pk])).status_code == 200
    assert client.get(reverse("attendance:office_qr", args=[hq.pk])).status_code == 200
    r = client.get(reverse("attendance:scan", args=["bad-token"]))
    assert r.status_code == 200 and b"not a valid office QR" in r.content


# ---------------------------------------------------------------------------
# G3 missing check-out
# ---------------------------------------------------------------------------
def test_g3_missing_checkout_flag(ict, roles, hq):
    from attendance import checkout, services
    from attendance.models import AttendanceRecord

    a = mk("g3a@x.org", ict, roles["staff"])
    b = mk("g3b@x.org", ict, roles["staff"])
    _present(a, MONDAY, 8, 0, hq)
    _present(b, MONDAY, 8, 0, hq, out=(16, 30))
    assert checkout.flag_missing_checkouts(before=MONDAY + timedelta(days=1)) == 1
    ra = AttendanceRecord.objects.get(user=a, date=MONDAY)
    assert ra.missing_checkout and ra.status == "present"
    tally = services.summarize_period([a], MONDAY, MONDAY, today=MONDAY)[a.pk]
    assert tally["present"] == 1 and tally["missing_checkout"] == 1 and tally["pct"] == 100


# ---------------------------------------------------------------------------
# G9 import / export
# ---------------------------------------------------------------------------
def test_g9_export_columns_and_import_times(ict, roles, hq):
    from attendance import services
    from attendance.models import AttendanceRecord

    assert services.parse_cell("1") == ("present", None, None)
    assert services.parse_cell("OD")[0] == "on_duty"
    st, tin, tout = services.parse_cell("L 09:20-17:05")
    assert st == "late" and tin == time(9, 20) and tout == time(17, 5)
    assert services.parse_cell("1 08:00-")[2] is None
    assert services.parse_cell("X 08:00")[0] is None

    u = mk("g9@x.org", ict, roles["staff"])
    rec = _present(u, MONDAY, 8, 5, hq, out=(16, 40))
    assert services.cell_for(rec) == "1 08:05-16:40"
    matrix = services.to_matrix([rec], MONDAY.month, MONDAY.year)
    assert matrix[1][MONDAY.day] == "1 08:05-16:40"

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Email", str(MONDAY.day), str(MONDAY.day + 1)])
    ws.append([u.email, "1 08:05-16:40", "L 09:30-"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    AttendanceRecord.objects.filter(user=u).delete()
    parsed = services.parse_matrix(buf, MONDAY.month, MONDAY.year)
    res = services.apply_matrix(parsed, actor=None)
    assert res.created == 2
    r1 = AttendanceRecord.objects.get(user=u, date=MONDAY)
    assert timezone.localtime(r1.check_in_at).time() == time(8, 5)
    assert timezone.localtime(r1.check_out_at).time() == time(16, 40) and r1.check_out_source == "excel"
    r2 = AttendanceRecord.objects.get(user=u, date=MONDAY + timedelta(days=1))
    assert r2.status == "late" and r2.check_out_at is None


def test_g6_department_export_has_new_columns(ict, roles, hq, client):
    from openpyxl import load_workbook

    boss = mk("g6@x.org", ict, roles["director"])
    import org
    try:
        org.assign_director(ict, boss, actor=None)
    except Exception:
        pass
    client.force_login(boss)
    r = client.get(reverse("attendance:export_excel") + f"?year={MONDAY.year}&month={MONDAY.month}")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    headers = [c.value for c in wb.worksheets[0][3]]
    assert "On official duty" in headers and "Missing check-out" in headers
    assert "Daily register" in wb.sheetnames
    reg = [c.value for c in wb["Daily register"][1]]
    assert reg[:7] == ["Name", "Email", "Date", "Code", "Status", "Check-in", "Check-out"]


# ---------------------------------------------------------------------------
# G7 official duty and availability
# ---------------------------------------------------------------------------
def test_g7_checkin_allowed_while_on_duty(ict, roles, hq):
    from attendance import services
    from attendance.models import AttendanceRecord

    u = mk("g7c@x.org", ict, roles["staff"])
    today = timezone.localdate()
    AttendanceRecord.objects.create(user=u, date=today, status="on_duty", source="visit")
    assert "already" not in services.check_in_block_reason(u, today)


# ---------------------------------------------------------------------------
# G5 / G8 analytics
# ---------------------------------------------------------------------------
def test_g8_analytics_scoping(ict, finance, roles, client):
    import org

    es = mk("g8es@x.org", Department.objects.create(code="es-office", name="ES"), roles["executive-secretary"])
    director = mk("g8d@x.org", ict, roles["director"])
    try:
        org.assign_director(ict, director, actor=None)
    except Exception:
        pass
    officer = mk("g8o@x.org", ict, roles["staff"])
    fin = mk("g8f@x.org", finance, roles["staff"])
    client.force_login(officer)
    assert client.get(reverse("attendance:analytics")).status_code == 403
    client.force_login(director)
    r = client.get(reverse("attendance:analytics"))
    assert r.status_code == 200
    names = {p.user.pk for p in r.context["all_people"]}
    assert officer.pk in names and fin.pk not in names
    assert client.get(reverse("attendance:analytics_person", args=[fin.pk])).status_code == 403
    assert client.get(reverse("attendance:analytics_person", args=[officer.pk])).status_code == 200
    from django.contrib.auth.models import Permission
    es.user_permissions.add(Permission.objects.get(codename="view_all_attendance"))
    es = User.objects.get(pk=es.pk)
    client.force_login(es)
    r = client.get(reverse("attendance:analytics"))
    names = {p.user.pk for p in r.context["all_people"]}
    assert {officer.pk, fin.pk} <= names
    assert client.get(reverse("attendance:analytics_export")).status_code == 200


def test_g8_figures_match_records(ict, roles, hq):
    from attendance import analytics
    from attendance.models import AttendanceRecord

    a = mk("g8a@x.org", ict, roles["staff"])
    b = mk("g8b@x.org", ict, roles["staff"])
    month = [MONDAY + timedelta(days=i) for i in range(12) if (MONDAY + timedelta(days=i)).weekday() < 5]
    # a: early every day (07:30), one missing check-out; b: late twice, absent once, on leave once
    for i, d in enumerate(month):
        _present(a, d, 7, 30, hq, out=None if i == 0 else (16, 30))
    _present(b, month[0], 8, 0, hq, out=(16, 0))
    _present(b, month[1], 9, 10, hq, out=(17, 0))
    _present(b, month[2], 9, 40, hq, out=(17, 0))
    AttendanceRecord.objects.create(user=b, date=month[3], status="absent", source="auto")
    AttendanceRecord.objects.create(user=b, date=month[4], status="on_leave", source="leave")
    AttendanceRecord.objects.filter(user=a, date=month[0]).update(missing_checkout=True)
    data = analytics.compute([a, b], month[0], month[-1], today=month[-1] + timedelta(days=1))
    pa, pb = {p.user.pk: p for p in data["people"]}[a.pk], {p.user.pk: p for p in data["people"]}[b.pk]
    assert (pa.present, pa.late, pa.early, pa.missing_checkout, pa.counted) == (len(month), 0, len(month), 1,
                                                                                len(month))
    assert pa.avg_arrival_label == "07:30" and pa.punctuality_pct == 100 and pa.attendance_pct == 100
    assert (pb.present, pb.late, pb.absent, pb.on_leave, pb.counted) == (1, 2, 1, 1, 5)
    assert pb.punctuality_pct == 33 and pb.attendance_pct == 80
    assert pb.avg_arrival_label == "08:57"   # (8:00 + 9:10 + 9:40) / 3
    boards = analytics.leaderboards(data["people"])
    assert boards["earliest"][0].user == a and boards["late"][0].user == b and boards["absent"][0].user == b
    t = data["totals"]
    assert t["counted"] == len(month) + 5 and t["absent"] == 1 and t["late"] == 2
    assert sum(w["counted"] for w in data["weeks"]) == t["counted"]
