"""Regression tests for review finding H4 (attendance integrity)."""
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department, Staff

pytestmark = pytest.mark.django_db


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
import io
from datetime import timedelta

from openpyxl import Workbook

from attendance.models import AttendanceRecord


def _past_weekday():
    d = timezone.localdate() - timedelta(days=7)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def test_staff_cannot_edit_own_record(client, ict, roles):
    u = mk("own4@x.org", ict, roles["staff"])
    rec = AttendanceRecord.objects.create(user=u, date=_past_weekday(), status="absent")
    client.force_login(u)
    assert client.post(reverse("attendance:manual_edit", args=[rec.pk]),
                       {"status": "present"}).status_code == 403
    rec.refresh_from_db()
    assert rec.status == "absent"


def test_director_cannot_edit_own_record(client, ict, roles):
    d = mk("dir4@x.org", ict, roles["director"])
    rec = AttendanceRecord.objects.create(user=d, date=_past_weekday(), status="absent")
    client.force_login(d)
    assert client.post(reverse("attendance:manual_edit", args=[rec.pk]),
                       {"status": "present"}).status_code == 403


def test_director_can_edit_department_member(client, ict, roles):
    d = mk("dir4b@x.org", ict, roles["director"])
    u = mk("mem4@x.org", ict, roles["staff"])
    rec = AttendanceRecord.objects.create(user=u, date=_past_weekday(), status="absent")
    client.force_login(d)
    client.post(reverse("attendance:manual_edit", args=[rec.pk]), {"status": "excused"})
    rec.refresh_from_db()
    assert rec.status == "excused"


def _matrix(emails, day):
    wb = Workbook()
    ws = wb.active
    ws.append(["Email", day.day])
    for e in emails:
        ws.append([e, "P"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_parse_matrix_rejects_other_department_rows(ict, roles):
    from attendance import services
    fin = Department.objects.create(name="Finance", code="finance")
    mine = mk("mine4@x.org", ict, roles["staff"])
    other = mk("other4@x.org", fin, roles["staff"])
    day = _past_weekday()
    parsed = services.parse_matrix(_matrix([mine.email, other.email], day),
                                   month=day.month, year=day.year, department=ict)
    by_email = {r.email: r for r in parsed.rows}
    assert not by_email[mine.email].errors
    assert by_email[other.email].errors
    result = services.apply_matrix(parsed, actor=None)
    assert AttendanceRecord.objects.filter(user=mine, date=day).exists()
    assert not AttendanceRecord.objects.filter(user=other, date=day).exists()
    assert result.skipped >= 1


def test_parse_matrix_without_department_is_unchanged(ict, roles):
    from attendance import services
    fin = Department.objects.create(name="Finance2", code="finance2")
    other = mk("other4b@x.org", fin, roles["staff"])
    day = _past_weekday()
    parsed = services.parse_matrix(_matrix([other.email], day), month=day.month, year=day.year)
    assert not parsed.rows[0].errors
