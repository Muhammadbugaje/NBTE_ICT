"""Regression tests for review findings M13 (profile email), M14 (soft
delete) and M15 (email quota / secret redaction)."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import User
from staff.models import Department

pytestmark = pytest.mark.django_db


def mk(email, dept=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, **kw)
    u.must_change_password = False
    u.save()
    return u


# ----------------------------------------------------------------- M13
def test_profile_form_duplicate_email_is_form_error():
    from accounts.forms import ProfileForm
    mk("taken-m13@x.org")
    me = mk("me-m13@x.org")
    form = ProfileForm(me, data={"first_name": "A", "last_name": "B",
                                 "email": "TAKEN-m13@x.org",
                                 "contact_preference": "email"})
    assert not form.is_valid()
    assert "email" in form.errors


def test_profile_form_keeps_own_email():
    from accounts.forms import ProfileForm
    me = mk("own-m13@x.org")
    form = ProfileForm(me, data={"first_name": "A", "last_name": "B",
                                 "email": "own-m13@x.org", "contact_preference": "email"})
    form.is_valid()
    assert "email" not in form.errors


# ----------------------------------------------------------------- M14
def _assets(n):
    from assets.models import Asset, AssetCategory
    dept = Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]
    cat = AssetCategory.objects.create(name="Cat14", code="cat14")
    return [Asset.objects.create(asset_tag=f"ICT-M14-{i}", category=cat,
                                 owner_department=dept, state="in_service")
            for i in range(n)]


def test_queryset_soft_delete_is_audited():
    from assets.models import Asset
    from core.models import AuditLogEntry
    assets = _assets(3)
    before = AuditLogEntry.objects.filter(model_label="assets.Asset").count()
    count = Asset.objects.filter(pk__in=[a.pk for a in assets]).delete()
    assert count == 3
    assert Asset.all_objects.filter(pk__in=[a.pk for a in assets], is_deleted=True).count() == 3
    assert AuditLogEntry.objects.filter(model_label="assets.Asset").count() >= before + 3


def test_soft_delete_keeps_files_until_hard_delete(settings, tmp_path):
    from pathlib import Path
    from staff.models import Staff
    settings.MEDIA_ROOT = str(tmp_path)
    u = mk("photo-m14@x.org")
    staff = Staff.objects.get(user=u)
    staff.photo = SimpleUploadedFile("p.png", b"\x89PNG\r\n")
    staff.save()
    path = Path(staff.photo.path)
    assert path.exists()

    staff.delete()  # soft
    assert path.exists(), "soft delete must keep the file so restore works"

    Staff.all_objects.filter(pk=staff.pk).hard_delete()
    assert not path.exists()


# ----------------------------------------------------------------- M15
def test_brevo_smtp_and_api_share_one_quota(settings):
    from notifications import engine
    assert engine.quota_key("brevo_smtp") == engine.quota_key("brevo")
    assert engine._reserve("brevo_smtp", 1)
    assert engine.used_today("brevo") == 1
    assert engine.used_today("brevo_smtp") == 1


def test_brevo_quota_cannot_be_doubled(monkeypatch):
    from notifications import engine
    monkeypatch.setattr(engine, "daily_limit", lambda provider: 2)
    assert engine._reserve("brevo_smtp", 1)
    assert engine._reserve("brevo", 1)
    assert not engine._reserve("brevo_smtp", 1)
    assert not engine._reserve("brevo", 1)


def test_safe_error_message_redacts_brevo_smtp_key(settings):
    from notifications import engine
    providers = dict(settings.EMAIL_PROVIDERS)
    smtp = dict(providers.get("brevo_smtp", {}))
    smtp["OPTIONS"] = dict(smtp.get("OPTIONS", {}), password="xsmtpsib-SECRET-123")
    providers["brevo_smtp"] = smtp
    settings.EMAIL_PROVIDERS = providers
    msg = engine.safe_error_message(Exception("auth failed for xsmtpsib-SECRET-123"))
    assert "xsmtpsib-SECRET-123" not in msg


def test_settings_page_lists_brevo_quota_once(client):
    admin = mk("su-m15@x.org", is_superuser=True, is_staff=True)
    client.force_login(admin)
    resp = client.get(reverse("config:settings"))
    assert resp.status_code == 200
    providers = [q["provider"] for q in resp.context["quota"]]
    assert providers.count("brevo") == 1 and "brevo_smtp" not in providers
