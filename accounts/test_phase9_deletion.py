"""Phase 9: account deletion requests (user starts it, an administrator handles it)."""
import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts import deletion
from accounts.models import AccountDeletionRequest, Passkey, UserSession
from notifications.models import Notification
from qa_core.hosts import portal_reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user(p9_make_user):
    return p9_make_user("root@nbte.test", superuser=True)


@pytest.fixture
def hr_user(p9_make_user):
    user = p9_make_user("hr@nbte.test")
    user.user_permissions.add(Permission.objects.get(content_type__app_label="accounts", codename="change_user"))
    return user


def test_request_from_profile_notifies_admins(staff_user, admin_user, hr_user, other_staff):
    c = Client()
    c.force_login(staff_user)
    r = c.post(reverse("accounts:deletion_request"), {"reason": "Leaving NBTE", "confirm": "yes"})
    assert r.status_code == 302
    req = AccountDeletionRequest.objects.get(user=staff_user)
    assert req.status == "pending" and req.reason == "Leaving NBTE"
    notified = set(Notification.objects.filter(metadata__deletion_request_id=req.pk)
                   .values_list("recipient__email", flat=True))
    assert notified == {"root@nbte.test", "hr@nbte.test"}
    note = Notification.objects.filter(recipient=admin_user).first()
    assert note.url == f"/admin/accounts/accountdeletionrequest/{req.pk}/change/"


def test_confirmation_is_required(staff_user):
    c = Client()
    c.force_login(staff_user)
    c.post(reverse("accounts:deletion_request"), {"reason": "x"})
    assert not AccountDeletionRequest.objects.exists()


def test_request_is_idempotent_and_withdrawable(staff_user):
    first = deletion.request_deletion(staff_user, "one")
    assert deletion.request_deletion(staff_user, "two").pk == first.pk
    c = Client()
    c.force_login(staff_user)
    c.post(reverse("accounts:deletion_withdraw"))
    first.refresh_from_db()
    assert first.status == "withdrawn"
    assert deletion.request_deletion(staff_user).pk != first.pk


def test_profile_shows_kept_and_removed(staff_user):
    c = Client()
    c.force_login(staff_user)
    html = c.get(reverse("accounts:profile")).content.decode()
    assert "Request account deletion" in html and "What NBTE must keep" in html and "Payment records" in html
    deletion.request_deletion(staff_user)
    html = c.get(reverse("accounts:profile")).content.decode()
    assert "Withdraw request" in html


def test_portal_user_can_request(institution_user, portal_host):
    c = Client(HTTP_HOST=portal_host)
    c.force_login(institution_user)
    assert "Request account deletion" in c.get("/account/").content.decode()
    r = c.post(portal_reverse("qa_portal:account_deletion_request"), {"confirm": "yes"})
    assert r.status_code == 302
    assert AccountDeletionRequest.objects.filter(user=institution_user, status="pending").exists()
    c.post(portal_reverse("qa_portal:account_deletion_withdraw"))
    assert AccountDeletionRequest.objects.get(user=institution_user).status == "withdrawn"


def test_approve_and_decline_notify_user(staff_user, admin_user):
    req = deletion.request_deletion(staff_user)
    assert deletion.approve(req, admin_user, "OK") is True
    assert deletion.approve(req, admin_user) is False  # not pending any more
    assert Notification.objects.filter(recipient=staff_user, title__icontains="approved").exists()
    other = AccountDeletionRequest.objects.create(user=staff_user)
    assert deletion.decline(other, admin_user, "Statutory hold") is True
    other.refresh_from_db()
    assert other.status == "declined" and other.handled_by == admin_user and other.note == "Statutory hold"


def test_complete_deactivates_and_blanks_personal_fields(staff_user, admin_user, settings, tmp_path):
    staff = staff_user.staff_profile
    staff.phone = "08012345678"
    staff.emergency_contact_name = "Next Of Kin"
    staff.emergency_contact_phone = "08099999999"
    staff.date_of_birth = "1990-01-01"
    staff.save()
    UserSession.objects.create(user=staff_user, session_key="k" * 32)
    Passkey.objects.create(user=staff_user, credential_id="abc", public_key="def")
    req = deletion.request_deletion(staff_user)
    assert deletion.complete(req, admin_user) is True
    staff_user.refresh_from_db()
    staff.refresh_from_db()
    req.refresh_from_db()
    assert staff_user.is_active is False and staff_user.email  # row and e-mail kept
    assert staff.phone == "" and staff.emergency_contact_name == "" and staff.emergency_contact_phone == ""
    assert str(staff.date_of_birth) == "1990-01-01"  # HR record kept
    assert not Passkey.objects.filter(user=staff_user).exists()
    assert UserSession.objects.get(user=staff_user).revoked_at is not None
    assert req.status == "completed" and "staff.phone" in req.note
    assert type(staff_user).objects.filter(pk=staff_user.pk).exists()


def test_admin_actions(staff_user, admin_user):
    req = deletion.request_deletion(staff_user)
    c = Client()
    c.force_login(admin_user)
    url = reverse("admin:accounts_accountdeletionrequest_changelist")
    assert c.get(url).status_code == 200
    assert c.get(url + "?status__exact=pending").status_code == 200
    c.post(url, {"action": "approve_requests", "_selected_action": [req.pk]})
    req.refresh_from_db()
    assert req.status == "approved"
    c.post(url, {"action": "complete_requests", "_selected_action": [req.pk]})
    req.refresh_from_db()
    staff_user.refresh_from_db()
    assert req.status == "completed" and req.handled_by == admin_user and staff_user.is_active is False
    assert c.get(reverse("admin:accounts_accountdeletionrequest_change", args=[req.pk])).status_code == 200


def test_photo_is_removed_on_completion(staff_user, admin_user):
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (10, 10), "red").save(buf, "PNG")
    staff = staff_user.staff_profile
    staff.photo = SimpleUploadedFile("me.png", buf.getvalue(), content_type="image/png")
    staff.save()
    assert staff.photo
    deletion.complete(deletion.request_deletion(staff_user), admin_user)
    staff.refresh_from_db()
    assert not staff.photo
