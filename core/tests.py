import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from accounts.models import User
from core.models import AuditLogEntry
from core.storage import uuid_upload_to
from staff.models import Staff, StaffState

pytestmark = pytest.mark.django_db


def test_audit_create_update_and_redaction():
    u = User.objects.create_user("a@x.org", "pw-12345-x", first_name="A", last_name="B")
    entry = AuditLogEntry.objects.get(model_label="accounts.User", object_pk=u.pk, action="create")
    assert entry.changes["password"]["new"] == "***"
    u.first_name = "Z"
    u.save()
    upd = AuditLogEntry.objects.filter(object_pk=u.pk, action="update").first()
    assert upd.changes["first_name"] == {"old": "A", "new": "Z"}
    assert "password" not in upd.changes  # unchanged


def test_last_login_not_audited():
    u = User.objects.create_user("b@x.org", "pw-12345-x")
    n = AuditLogEntry.objects.count()
    from django.utils import timezone
    u.last_login = timezone.now()
    u.save(update_fields=["last_login"])
    assert AuditLogEntry.objects.count() == n


def test_audit_append_only():
    e = AuditLogEntry.objects.create(action="other", model_label="x.Y", object_pk="1")
    with pytest.raises(PermissionError):
        e.save()
    with pytest.raises(PermissionError):
        e.delete()


def test_soft_delete_hides_and_restores_and_audits():
    s = Staff.objects.create(employee_no="E1", first_name="Sa", last_name="Li")
    s.delete()
    assert not Staff.objects.filter(pk=s.pk).exists()
    assert Staff.all_objects.filter(pk=s.pk).exists()
    assert AuditLogEntry.objects.filter(object_pk=s.pk, model_label="staff.Staff", action="delete").exists()
    Staff.all_objects.get(pk=s.pk).restore()
    assert Staff.objects.filter(pk=s.pk).exists()
    assert AuditLogEntry.objects.filter(object_pk=s.pk, action="restore").exists()


def test_queryset_delete_is_soft():
    Staff.objects.create(employee_no="E2", first_name="A", last_name="B")
    Staff.objects.all().delete()
    assert Staff.all_objects.filter(employee_no="E2", is_deleted=True).exists()


def test_uuid_upload_to_ignores_original_name():
    s = Staff(employee_no="E3")
    path = uuid_upload_to(s, "../../Evil Name.PNG")
    assert re.fullmatch(r"staff/[0-9a-f]{32}\.png", path)
    assert "Evil" not in path
    assert re.fullmatch(r"staff/[0-9a-f]{32}", uuid_upload_to(s, "x.<script>"))  # unsafe ext dropped


def test_staff_photo_stored_with_uuid_and_original_name_kept(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.STORAGES = dict(settings.STORAGES)  # reassigning fires setting_changed -> storages rebuilt
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (4, 4)).save(buf, "PNG")
    s = Staff(employee_no="E4", first_name="P", last_name="H")
    s.photo = SimpleUploadedFile("My Holiday Photo.png", buf.getvalue(), content_type="image/png")
    s.save()
    assert re.fullmatch(r"staff/[0-9a-f]{32}\.png", s.photo.name)
    assert s.photo_original_name == "My Holiday Photo.png"
    assert (tmp_path / s.photo.name).exists()
