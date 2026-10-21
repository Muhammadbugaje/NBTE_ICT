import re
from io import BytesIO

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from PIL import Image

from accounts.models import Role, User
from core.images import variant_name
from core.models import AuditLogEntry
from staff.models import Staff, StaffAttachment

pytestmark = pytest.mark.django_db


def _png(w=800, h=400, mode="RGB", color="red"):
    buf = BytesIO()
    Image.new(mode, (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _user(email, perm=None):
    role = None
    if perm:
        role = Role.objects.create(code=email, name=email)
        role.permissions.add(Permission.objects.get(codename=perm))
    return User.objects.create_user(email, "Pass-word-123", role=role, must_change_password=False)


@pytest.fixture
def attachment(settings):
    s = Staff.objects.create(employee_no="A1", first_name="A", last_name="B")
    return StaffAttachment.objects.create(
        staff=s, description="contract",
        file=SimpleUploadedFile("Contract (final).pdf", b"%PDF-secret-bytes", content_type="application/pdf"))


def test_restricted_file_lives_outside_media_root_with_uuid_name(attachment, settings):
    # Review L9: private documents now live under MEDIA_SUBDIR "staff/private_documents".
    assert re.fullmatch(r"staff/private_documents/[0-9a-f]{32}\.pdf", attachment.file.name)
    assert attachment.original_name == "Contract (final).pdf"
    on_disk = settings.PROTECTED_MEDIA_ROOT / attachment.file.name
    assert on_disk.read_bytes() == b"%PDF-secret-bytes"
    assert not (settings.MEDIA_ROOT / attachment.file.name).exists()  # no /media/ path reaches it


def test_restricted_file_has_no_public_url(attachment):
    with pytest.raises(ValueError):
        attachment.file.url


def test_anonymous_is_sent_to_login(attachment):
    r = Client().get(attachment.download_url())
    assert r.status_code == 302 and "/accounts/login/" in r["Location"]


def test_user_without_permission_gets_403_even_with_exact_url(attachment):
    c = Client()
    c.force_login(_user("nope@x.org"))
    r = c.get(attachment.download_url())
    assert r.status_code == 403
    assert b"secret" not in r.content


def _same_department_user(email, attachment):
    # Review M7: the permission alone no longer opens every department's
    # documents; the downloader must be in the record's department.
    from staff.models import Department
    dept = Department.objects.create(name="HR docs", code="hr-docs")
    Staff.objects.filter(pk=attachment.staff_id).update(department=dept)
    u = _user(email, "view_staffattachment")
    u.department = dept
    u.save()
    return u


def test_permitted_user_downloads_in_dev_mode(attachment):
    c = Client()
    c.force_login(_same_department_user("ok@x.org", attachment))
    r = c.get(attachment.download_url())
    assert r.status_code == 200
    assert b"".join(r.streaming_content) == b"%PDF-secret-bytes"
    assert r["Content-Disposition"] == "attachment; filename*=UTF-8''Contract%20%28final%29.pdf"
    assert r["Content-Type"] == "application/pdf" and "no-store" in r["Cache-Control"]
    assert AuditLogEntry.objects.filter(action="other", note="file downloaded", object_pk=attachment.pk).exists()


def test_permitted_user_gets_x_accel_redirect_with_empty_body(attachment, settings):
    settings.USE_X_ACCEL_REDIRECT = True
    c = Client()
    c.force_login(_same_department_user("ok@x.org", attachment))
    r = c.get(attachment.download_url())
    assert r.status_code == 200 and r.content == b""
    assert r["X-Accel-Redirect"] == "/protected/" + attachment.file.name
    assert "Contract" in r["Content-Disposition"]


def test_denied_user_never_gets_x_accel_header(attachment, settings):
    settings.USE_X_ACCEL_REDIRECT = True
    c = Client()
    c.force_login(_user("nope@x.org"))
    r = c.get(attachment.download_url())
    assert r.status_code == 403 and "X-Accel-Redirect" not in r


def test_download_view_refuses_non_restricted_models_and_missing_rows(attachment):
    c = Client()
    c.force_login(_user("ok@x.org", "view_staffattachment"))
    assert c.get(f"/files/staff.staff/{attachment.staff_id}/").status_code == 404     # not a restricted model
    assert c.get("/files/accounts.user/1/").status_code == 404
    assert c.get("/files/nope.nothing/1/").status_code == 404
    assert c.get("/files/staff.staffattachment/99999/").status_code == 404


def test_soft_deleted_attachment_is_not_downloadable(attachment):
    c = Client()
    c.force_login(_user("ok@x.org", "view_staffattachment"))
    attachment.delete()
    assert c.get(attachment.download_url()).status_code == 404


# ---------------- thumbnails ----------------
def test_upload_fires_thumbnail_task_and_writes_variants(settings, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        s = Staff.objects.create(
            employee_no="T1", first_name="P", last_name="H",
            photo=SimpleUploadedFile("Holiday.png", _png(800, 400), content_type="image/png"))
    assert re.fullmatch(r"staff/photos/[0-9a-f]{32}\.png", s.photo.name)  # review L9: MEDIA_SUBDIR
    big, small = (settings.MEDIA_ROOT / variant_name(s.photo.name, n) for n in (300, 64))
    assert big.exists() and small.exists()
    assert Image.open(big).size == (300, 150)      # longest edge 300, aspect kept
    assert Image.open(small).size == (64, 32)
    assert s.photo_thumb_url(300) == "/media/" + variant_name(s.photo.name, 300)
    assert s.photo_thumb_url(999) is None           # never generated


def test_no_thumbnails_when_unrelated_field_saved(settings, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        s = Staff.objects.create(employee_no="T2", first_name="P", last_name="H",
                                 photo=SimpleUploadedFile("a.png", _png(), content_type="image/png"))
    big = settings.MEDIA_ROOT / variant_name(s.photo.name, 300)
    big.unlink()
    with django_capture_on_commit_callbacks(execute=True) as cbs:
        s = Staff.objects.get(pk=s.pk)
        from staff.models import Designation
        s.designation = Designation.objects.create(name="Officer")  # FK since the nominal-roll release
        s.save()
    assert cbs == [] and not big.exists()           # no regeneration without a new upload


def test_thumbnail_sizes_come_from_config(settings, django_capture_on_commit_callbacks):
    from config.models import ConfigEntry
    ConfigEntry.objects.create(key="media.thumbnail_sizes", value="[120]", value_type="json")
    with django_capture_on_commit_callbacks(execute=True):
        s = Staff.objects.create(employee_no="T3", first_name="P", last_name="H",
                                 photo=SimpleUploadedFile("a.png", _png(), content_type="image/png"))
    assert (settings.MEDIA_ROOT / variant_name(s.photo.name, 120)).exists()
    assert not (settings.MEDIA_ROOT / variant_name(s.photo.name, 300)).exists()


def test_transparent_png_is_flattened_not_crashing(settings, django_capture_on_commit_callbacks):
    png = _png(50, 50, "RGBA", (255, 0, 0, 0))
    with django_capture_on_commit_callbacks(execute=True):
        s = Staff.objects.create(employee_no="T4", first_name="P", last_name="H",
                                 photo=SimpleUploadedFile("t.png", png, content_type="image/png"))
    img = Image.open(settings.MEDIA_ROOT / variant_name(s.photo.name, 64))
    assert img.mode == "RGB" and img.getpixel((1, 1)) == (255, 255, 255)


def test_undecodable_image_does_not_raise(settings, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        s = Staff.objects.create(employee_no="T5", first_name="P", last_name="H",
                                 photo=SimpleUploadedFile("bad.png", b"not an image", content_type="image/png"))
    assert s.pk and not (settings.MEDIA_ROOT / variant_name(s.photo.name, 300)).exists()
