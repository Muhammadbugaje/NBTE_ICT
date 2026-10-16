"""Regression tests for review finding M8 (audit log redaction)."""
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
from core.models import AuditLogEntry


def test_staff_personal_fields_redacted(ict):
    s = Staff.objects.create(employee_no="E8", first_name="A", last_name="B",
                             phone="0803-SECRET", emergency_contact_name="Kin SECRET")
    entry = AuditLogEntry.objects.filter(model_label="staff.Staff", object_pk=str(s.pk)).first()
    assert "SECRET" not in str(entry.changes)
    assert entry.changes["phone"]["new"] == "***"


def test_checkin_code_redacted(ict):
    from attendance.models import AttendanceLocation, LocationCheckInCode
    loc = AttendanceLocation.objects.create(name="HQ8", latitude=10.5, longitude=7.4)
    now = timezone.now()
    c = LocationCheckInCode.objects.create(location=loc, code="ZXCV98", window_start=now,
                                           window_end=now)
    entry = AuditLogEntry.objects.filter(model_label="attendance.LocationCheckInCode",
                                         object_pk=str(c.pk)).first()
    assert "ZXCV98" not in str(entry.changes)


def test_memo_text_redacted(ict, roles):
    from messaging import memos as ms
    from messaging.models import Memo
    sender = mk("s8@x.org", ict, roles["staff"])
    target = mk("t8@x.org", ict, roles["staff"])
    m = ms.create_memo(sender=sender, subject="Pay rise PRIVATE", body="Body PRIVATE",
                       visibility=Memo.SPECIFIC_USER, target_user=target)
    entries = AuditLogEntry.objects.filter(model_label="messaging.Memo", object_pk=str(m.pk))
    assert "PRIVATE" not in str([e.changes for e in entries])


def test_migration_redacts_old_rows():
    import importlib
    from django.apps import apps
    mod = importlib.import_module("core.migrations.0004_redact_sensitive_audit_values")
    AuditLogEntry.objects.create(action="create", model_label="staff.Staff", object_pk="999",
                                 changes={"phone": {"new": "0803-OLD"}, "first_name": {"new": "Ada"}})
    mod.redact(apps, None)
    row = AuditLogEntry.objects.get(model_label="staff.Staff", object_pk="999")
    assert row.changes["phone"]["new"] == "***" and row.changes["first_name"]["new"] == "Ada"
