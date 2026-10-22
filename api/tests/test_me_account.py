"""me/preferences/, me/devices/, me/push-token/, me/deletion-request/, me/staff-id/."""
import pytest

from .conftest import BASE, PASSWORD, bearer, err, login

pytestmark = pytest.mark.django_db

DEVICE = {"platform": "android", "model": "Pixel 7", "os_version": "15", "app_version": "1.0.0",
          "device_id": "install-1", "push_token": None}


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def test_preferences_staff_get_and_patch(staff_api, staff_user):
    bearer(staff_api, staff_user)
    r = staff_api.get(BASE + "me/preferences/")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["tasks_tickets"] is True and body["approvals_memos"] is True
    assert body["quiet_from"] == "22:00" and body["quiet_to"] == "06:30"
    assert [k["key"] for k in body["kinds"]][:3] == ["chats", "tasks_tickets", "approvals_memos"]

    r = staff_api.patch(BASE + "me/preferences/", {"chats": False, "quiet_hours_enabled": True,
                                                   "quiet_from": "21:15", "show_previews": "never"}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["chats"] is False and body["quiet_from"] == "21:15" and body["show_previews"] == "never"
    assert body["tasks_tickets"] is True  # untouched (PATCH semantics)
    from notifications.preferences import get_preferences

    pref = get_preferences(staff_user)
    assert pref.pk and pref.chats is False and pref.quiet_hours_enabled is True


def test_preferences_validation(staff_api, staff_user):
    bearer(staff_api, staff_user)
    r = staff_api.patch(BASE + "me/preferences/", {"show_previews": "sometimes"}, format="json")
    assert r.status_code == 400 and "show_previews" in err(r)["fields"]
    r = staff_api.patch(BASE + "me/preferences/", {"timezone": "Mars/Base"}, format="json")
    assert r.status_code == 400 and "timezone" in err(r)["fields"]
    r = staff_api.patch(BASE + "me/preferences/", {}, format="json")
    assert r.status_code == 400


def test_preferences_portal_only_portal_kinds(portal_api, institution_user):
    bearer(portal_api, institution_user)
    body = portal_api.get(BASE + "me/preferences/").json()
    assert "tasks_tickets" not in body and "approvals_memos" not in body
    assert "payments" in body and "qa_cases" in body
    r = portal_api.patch(BASE + "me/preferences/", {"tasks_tickets": False}, format="json")
    assert r.status_code == 400 and "tasks_tickets" in err(r)["fields"]
    r = portal_api.patch(BASE + "me/preferences/", {"payments": False}, format="json")
    assert r.status_code == 200 and r.json()["payments"] is False
    from notifications.preferences import get_preferences

    assert get_preferences(institution_user).tasks_tickets is True  # never switched off from the portal


def test_preferences_auth_rules(staff_api, portal_api, staff_user, make_user):
    assert staff_api.get(BASE + "me/preferences/").status_code == 401
    r = bearer(portal_api, staff_user).get(BASE + "me/preferences/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"
    temp = make_user("temp@t.local", must_change=True)
    r = bearer(staff_api, temp).get(BASE + "me/preferences/")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
def _sign_in(client, user, device=DEVICE):
    r = login(client, user.email, device=device)
    assert r.status_code == 200, r.content
    return r.json()


def test_sign_in_creates_app_device_and_rotation_keeps_it(staff_api, staff_user):
    from accounts.models import UserSession

    tokens = _sign_in(staff_api, staff_user)
    rows = UserSession.objects.filter(user=staff_user, kind="app")
    assert rows.count() == 1
    row = rows.get()
    assert row.device_label == "NBTE app on Pixel 7" and row.platform == "android" and row.device_id == "install-1"
    old_key = row.session_key
    assert old_key.startswith("app:")

    r = staff_api.post(BASE + "auth/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert r.status_code == 200
    row.refresh_from_db()
    assert row.session_key != old_key and UserSession.objects.filter(user=staff_user, kind="app").count() == 1

    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
    devices = staff_api.get(BASE + "me/devices/").json()
    assert len(devices) == 1 and devices[0]["current"] is True and devices[0]["kind"] == "app"

    # Signing in again from the same install reuses the row.
    _sign_in(staff_api, staff_user)
    assert UserSession.objects.filter(user=staff_user, kind="app").count() == 1


def test_devices_list_includes_web_and_revoke(staff_api, staff_user, client):
    from accounts.models import UserSession

    assert client.login(username=staff_user.email, password=PASSWORD)
    # a website session row as the sign-in signal would create
    from django.contrib.sessions.models import Session

    web_key = Session.objects.order_by("-expire_date").first().session_key
    web, _ = UserSession.objects.update_or_create(session_key=web_key, defaults={
        "user": staff_user, "device_label": "Chrome on Windows"})
    phone = _sign_in(staff_api, staff_user)
    other = _sign_in(staff_api, staff_user, {**DEVICE, "device_id": "install-2", "model": "iPhone 15",
                                              "platform": "ios"})
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {phone['access']}")
    rows = staff_api.get(BASE + "me/devices/").json()
    kinds = sorted(r["kind"] for r in rows)
    assert kinds == ["app", "app", "web"]
    assert [r["current"] for r in rows].count(True) == 1 and rows[0]["current"] is True

    other_row = UserSession.objects.get(user=staff_user, device_id="install-2")
    r = staff_api.delete(BASE + f"me/devices/{other_row.pk}/")
    assert r.status_code == 204
    # the revoked install's refresh token is blacklisted, and its access token refused
    r = staff_api.post(BASE + "auth/refresh/", {"refresh": other["refresh"]}, format="json")
    assert r.status_code == 401 and err(r)["code"] == "token_revoked"
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {other['access']}")
    r = staff_api.get(BASE + "me/")
    assert r.status_code == 401 and err(r)["code"] == "token_revoked"

    # web session: DELETE removes the Django session
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {phone['access']}")
    assert staff_api.delete(BASE + f"me/devices/{web.pk}/").status_code == 204
    assert not Session.objects.filter(session_key=web_key).exists()
    assert staff_api.delete(BASE + f"me/devices/{web.pk}/").status_code == 404


def test_devices_revoke_others_keeps_current(staff_api, staff_user):
    from accounts.models import UserSession

    phone = _sign_in(staff_api, staff_user)
    tablet = _sign_in(staff_api, staff_user, {**DEVICE, "device_id": "install-3"})
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {phone['access']}")
    assert staff_api.post(BASE + "me/devices/revoke-others/").status_code == 204
    active = UserSession.objects.filter(user=staff_user, revoked_at__isnull=True)
    assert list(active.values_list("device_id", flat=True)) == ["install-1"]
    r = staff_api.post(BASE + "auth/refresh/", {"refresh": tablet["refresh"]}, format="json")
    assert r.status_code == 401
    assert staff_api.get(BASE + "me/").status_code == 200


def test_device_of_another_user_is_404(staff_api, staff_user, make_user):
    from accounts.models import UserSession

    other = make_user("other@t.local")
    row = UserSession.objects.create(user=other, session_key="x" * 32, device_label="Firefox")
    r = bearer(staff_api, staff_user).delete(BASE + f"me/devices/{row.pk}/")
    assert r.status_code == 404 and err(r)["code"] == "not_found"
    row.refresh_from_db()
    assert row.revoked_at is None


def test_logout_marks_device_revoked(staff_api, staff_user):
    from accounts.models import UserSession

    tokens = _sign_in(staff_api, staff_user)
    assert staff_api.post(BASE + "auth/logout/", {"refresh": tokens["refresh"]}, format="json").status_code == 204
    assert UserSession.objects.get(user=staff_user, kind="app").revoked_at is not None


def test_push_token(staff_api, staff_user):
    from accounts.models import UserSession

    tokens = _sign_in(staff_api, staff_user)
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    r = staff_api.post(BASE + "me/push-token/", {"push_token": "fcm-abc", "device_id": "install-1"}, format="json")
    assert r.status_code == 204
    assert UserSession.objects.get(user=staff_user, kind="app").push_token == "fcm-abc"
    r = staff_api.post(BASE + "me/push-token/", {"device_id": "x"}, format="json")
    assert r.status_code == 400 and "push_token" in err(r)["fields"]
    # a token without a device row and an unknown device id
    r = bearer(staff_api, staff_user).post(BASE + "me/push-token/", {"push_token": "t", "device_id": "nope"},
                                           format="json")
    assert r.status_code == 400 and "device_id" in err(r)["fields"]


def test_push_token_portal_door(portal_api, institution_user):
    from accounts.models import UserSession

    tokens = _sign_in(portal_api, institution_user)
    portal_api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert portal_api.post(BASE + "me/push-token/", {"push_token": "apns-1"}, format="json").status_code == 204
    row = UserSession.objects.get(user=institution_user, kind="app")
    assert row.push_token == "apns-1" and row.door == "portal"
    assert portal_api.get(BASE + "me/devices/").json()[0]["current"] is True


# ---------------------------------------------------------------------------
# Deletion request
# ---------------------------------------------------------------------------
def test_deletion_request_flow(staff_api, staff_user):
    bearer(staff_api, staff_user)
    body = staff_api.get(BASE + "me/deletion-request/").json()
    assert body["request"] is None and body["open"] is False and body["kept_items"]
    r = staff_api.post(BASE + "me/deletion-request/", {"reason": "Leaving NBTE"}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["status"] == "pending" and r.json()["reason"] == "Leaving NBTE"
    again = staff_api.post(BASE + "me/deletion-request/", {}, format="json")
    assert again.status_code == 200 and again.json()["id"] == r.json()["id"]
    assert staff_api.get(BASE + "me/deletion-request/").json()["open"] is True
    assert staff_api.delete(BASE + "me/deletion-request/").status_code == 204
    body = staff_api.get(BASE + "me/deletion-request/").json()
    assert body["request"]["status"] == "withdrawn" and body["open"] is False
    assert staff_api.delete(BASE + "me/deletion-request/").status_code == 404


def test_deletion_request_portal_and_validation(portal_api, rp_user):
    bearer(portal_api, rp_user)
    r = portal_api.post(BASE + "me/deletion-request/", {"reason": "x" * 2001}, format="json")
    assert r.status_code == 400 and "reason" in err(r)["fields"]
    assert portal_api.post(BASE + "me/deletion-request/", {"reason": "bye"}, format="json").status_code == 201


# ---------------------------------------------------------------------------
# Staff ID
# ---------------------------------------------------------------------------
def test_staff_id(staff_api, staff_user):
    from accounts.staff_id import verify_staff_token

    r = bearer(staff_api, staff_user).get(BASE + "me/staff-id/")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["verify_url"].startswith("http://localhost/verify/staff/")
    assert body["verify_url"].endswith(f"/{body['token']}/")
    assert body["qr_svg"].startswith("<svg") and body["expires_in"] == 60
    assert body["card"]["full_name"] == "Fatima Test"
    assert verify_staff_token(body["token"]).is_valid
    assert r["Cache-Control"] == "no-store"


def test_staff_id_internal_host_setting(staff_api, staff_user, settings):
    settings.INTERNAL_HOST = "staff.nbte.example"
    body = bearer(staff_api, staff_user).get(BASE + "me/staff-id/").json()
    assert body["verify_url"].startswith("http://staff.nbte.example/verify/staff/")


def test_staff_id_not_on_portal(portal_api, institution_user, staff_api):
    r = bearer(portal_api, institution_user).get(BASE + "me/staff-id/")
    assert r.status_code == 404
    assert staff_api.get(BASE + "me/staff-id/").status_code == 401
