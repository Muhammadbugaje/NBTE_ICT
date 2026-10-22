"""auth/ endpoints: token issue per door, refresh rotation, logout,
password change / forgot, must_change_password gating."""
import pytest
from django.test import override_settings

from .conftest import BASE, PASSWORD, bearer, err, login

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Token issue
# ---------------------------------------------------------------------------
def test_staff_token_on_staff_door(staff_api, staff_user):
    r = login(staff_api, staff_user.email, device={"platform": "android", "model": "Pixel 7",
                                                   "app_version": "1.0.0", "device_id": "abc", "push_token": None})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["door"] == "staff" and body["access"] and body["refresh"]
    assert body["user"]["email"] == staff_user.email and body["user"]["door"] == "staff"
    assert r["X-NBTE-Min-App-Version"] == "1.0.0"
    staff_user.refresh_from_db()
    assert staff_user.last_login is not None  # UPDATE_LAST_LOGIN


def test_token_carries_door_and_user_type_claims(staff_api, staff_user):
    from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

    body = login(staff_api, staff_user.email).json()
    assert AccessToken(body["access"])["door"] == "staff"
    assert RefreshToken(body["refresh"])["user_type"] == "staff"


def test_institution_token_on_portal_door(portal_api, institution_user):
    r = login(portal_api, institution_user.email)
    assert r.status_code == 200, r.content
    assert r.json()["door"] == "portal"
    assert r.json()["user"]["institution"]["code"] == "APX"


def test_resource_person_token_on_portal_door(portal_api, rp_user):
    r = login(portal_api, rp_user.email)
    assert r.status_code == 200, r.content
    assert r.json()["user"]["user_type"] == "resource_person"


def test_staff_on_portal_door_is_wrong_door(portal_api, staff_user):
    r = login(portal_api, staff_user.email)
    assert r.status_code == 403
    assert err(r)["code"] == "wrong_door" and err(r)["correct_door"] == "staff"


@pytest.mark.parametrize("who", ["institution_user", "rp_user"])
def test_portal_users_on_staff_door_are_wrong_door(staff_api, who, request):
    user = request.getfixturevalue(who)
    r = login(staff_api, user.email)
    assert r.status_code == 403
    assert err(r) == {"code": "wrong_door", "message": err(r)["message"], "correct_door": "portal"}


def test_wrong_door_needs_the_right_password(portal_api, staff_user):
    r = login(portal_api, staff_user.email, password="nope-nope-123")
    assert r.status_code == 401 and err(r)["code"] == "invalid_credentials"


def test_inactive_account(staff_api, staff_user):
    staff_user.is_active = False
    staff_user.save()
    r = login(staff_api, staff_user.email)
    assert r.status_code == 403 and err(r)["code"] == "account_inactive"
    # Without the right password nothing is revealed.
    r = login(staff_api, staff_user.email, password="wrong-password-1")
    assert r.status_code == 401 and err(r)["code"] == "invalid_credentials"


def test_wrong_password(staff_api, staff_user):
    r = login(staff_api, staff_user.email, password="definitely-wrong")
    assert r.status_code == 401
    assert err(r) == {"code": "invalid_credentials", "message": "Email or password is wrong."}
    assert r["WWW-Authenticate"].startswith("Bearer")


def test_unknown_email_is_invalid_credentials(staff_api, db):
    r = login(staff_api, "nobody@t.local")
    assert r.status_code == 401 and err(r)["code"] == "invalid_credentials"


def test_login_throttle_per_ip_and_email(staff_api, staff_user, make_user):
    for _ in range(10):
        assert login(staff_api, staff_user.email).status_code == 200
    r = login(staff_api, staff_user.email)
    assert r.status_code == 429
    assert err(r)["code"] == "throttled" and err(r)["retry_after"] > 0
    assert "Retry-After" in r
    other = make_user("other.staff@t.local")
    assert login(staff_api, other.email).status_code == 200  # another e-mail, own bucket


def test_failed_logins_share_the_website_lockout(staff_api, staff_user):
    for _ in range(5):
        assert login(staff_api, staff_user.email, password="wrong-pass-x").status_code == 401
    r = login(staff_api, staff_user.email)  # right password, but locked out
    assert r.status_code == 429 and err(r)["code"] == "throttled"


def test_institution_with_portal_switched_off(portal_api, institution_user, institution):
    institution.portal_enabled = False
    institution.save()
    r = login(portal_api, institution_user.email)
    assert r.status_code == 403 and err(r)["code"] == "account_inactive"


def test_device_object_is_validated(staff_api, staff_user):
    r = login(staff_api, staff_user.email, device={"platform": "nokia"})
    assert r.status_code == 400
    assert "device.platform" in err(r)["fields"]


def test_register_app_session_hook_is_called(staff_api, staff_user, monkeypatch):
    calls = []
    monkeypatch.setattr("api.views.auth.register_app_session", lambda *a, **kw: calls.append(a))
    login(staff_api, staff_user.email, device={"platform": "ios", "device_id": "x1"})
    user, device, jti = calls[0]
    assert user == staff_user and device["device_id"] == "x1" and jti


# ---------------------------------------------------------------------------
# Refresh / logout
# ---------------------------------------------------------------------------
def test_refresh_rotates_and_blacklists_old(staff_api, staff_user):
    from rest_framework_simplejwt.tokens import AccessToken

    first = login(staff_api, staff_user.email).json()
    r = staff_api.post(BASE + "auth/refresh/", {"refresh": first["refresh"]}, format="json")
    assert r.status_code == 200, r.content
    pair = r.json()
    assert pair["refresh"] != first["refresh"]
    assert AccessToken(pair["access"])["door"] == "staff"  # door claim kept
    again = staff_api.post(BASE + "auth/refresh/", {"refresh": first["refresh"]}, format="json")
    assert again.status_code == 401 and err(again)["code"] == "token_revoked"
    assert staff_api.post(BASE + "auth/refresh/", {"refresh": pair["refresh"]}, format="json").status_code == 200


def test_refresh_garbage_and_expired(staff_api, staff_user):
    from datetime import timedelta

    from api.tokens import refresh_for

    r = staff_api.post(BASE + "auth/refresh/", {"refresh": "not-a-token"}, format="json")
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"
    token = refresh_for(staff_user)
    token.set_exp(lifetime=-timedelta(seconds=5))
    r = staff_api.post(BASE + "auth/refresh/", {"refresh": str(token)}, format="json")
    assert r.status_code == 401 and err(r)["code"] == "token_expired"


def test_refresh_on_other_door_and_inactive(staff_api, portal_api, staff_user):
    body = login(staff_api, staff_user.email).json()
    r = portal_api.post(BASE + "auth/refresh/", {"refresh": body["refresh"]}, format="json")
    assert r.status_code == 403 and err(r)["correct_door"] == "staff"
    staff_user.is_active = False
    staff_user.save()
    r = staff_api.post(BASE + "auth/refresh/", {"refresh": body["refresh"]}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "account_inactive"


def test_logout_revokes_the_refresh_token(staff_api, staff_user):
    body = login(staff_api, staff_user.email).json()
    r = staff_api.post(BASE + "auth/logout/", {"refresh": body["refresh"]}, format="json")
    assert r.status_code == 204
    r = staff_api.post(BASE + "auth/refresh/", {"refresh": body["refresh"]}, format="json")
    assert r.status_code == 401 and err(r)["code"] == "token_revoked"
    # Logging out twice is harmless.
    assert staff_api.post(BASE + "auth/logout/", {"refresh": body["refresh"]}, format="json").status_code == 204


# ---------------------------------------------------------------------------
# Access tokens across doors / inactive / expired
# ---------------------------------------------------------------------------
def test_staff_token_rejected_on_portal_door(portal_api, staff_api, staff_user):
    access = login(staff_api, staff_user.email).json()["access"]
    portal_api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    r = portal_api.get(BASE + "me/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door" and err(r)["correct_door"] == "staff"


def test_portal_token_rejected_on_staff_door(portal_api, staff_api, institution_user):
    access = login(portal_api, institution_user.email).json()["access"]
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    r = staff_api.get(BASE + "me/")
    assert r.status_code == 403 and err(r)["correct_door"] == "portal"


def test_user_deactivated_after_issue(staff_api, staff_user):
    bearer(staff_api, staff_user)
    staff_user.is_active = False
    staff_user.save()
    r = staff_api.get(BASE + "me/")
    assert r.status_code == 403 and err(r)["code"] == "account_inactive"


def test_expired_access_token(staff_api, staff_user):
    from datetime import timedelta

    from api.tokens import refresh_for

    access = refresh_for(staff_user).access_token
    access.set_exp(lifetime=-timedelta(seconds=5))
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    r = staff_api.get(BASE + "me/")
    assert r.status_code == 401 and err(r)["code"] == "token_expired"


# ---------------------------------------------------------------------------
# must_change_password
# ---------------------------------------------------------------------------
def test_must_change_password_gates_everything_but_me_change_logout(staff_api, make_user):
    user = make_user("temp.pw@t.local", must_change=True)
    body = login(staff_api, user.email).json()
    assert body["user"]["must_change_password"] is True
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
    assert staff_api.get(BASE + "me/").status_code == 200
    r = staff_api.get(BASE + "home/")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"
    assert err(staff_api.get(BASE + "me/preferences/"))["code"] == "password_change_required"

    # A temporary password needs no old_password; clears the flag.
    r = staff_api.post(BASE + "auth/password/change/", {"new_password": "Brand-new-pass-77"}, format="json")
    assert r.status_code == 204, r.content
    user.refresh_from_db()
    assert user.must_change_password is False and user.check_password("Brand-new-pass-77")
    assert staff_api.get(BASE + "home/").status_code == 200
    assert staff_api.post(BASE + "auth/logout/", {"refresh": body["refresh"]}, format="json").status_code == 204


def test_portal_must_change_password_then_change(portal_api, make_user, institution):
    user = make_user("temp.inst@t.local", user_type="institution", institution=institution, must_change=True)
    bearer(portal_api, user)
    assert err(portal_api.get(BASE + "home/"))["code"] == "password_change_required"
    r = portal_api.post(BASE + "auth/password/change/", {"new_password": "Portal-pass-2026x"}, format="json")
    assert r.status_code == 204, r.content
    user.refresh_from_db()
    assert not user.must_change_password
    assert portal_api.get(BASE + "home/").status_code == 200


def test_voluntary_change_needs_old_password(staff_api, staff_user):
    bearer(staff_api, staff_user)
    r = staff_api.post(BASE + "auth/password/change/", {"new_password": "Another-pass-2026"}, format="json")
    assert r.status_code == 400 and "old_password" in err(r)["fields"]
    r = staff_api.post(BASE + "auth/password/change/",
                       {"old_password": "wrong", "new_password": "Another-pass-2026"}, format="json")
    assert r.status_code == 400 and "old_password" in err(r)["fields"]
    r = staff_api.post(BASE + "auth/password/change/",
                       {"old_password": PASSWORD, "new_password": "Another-pass-2026"}, format="json")
    assert r.status_code == 204


@pytest.mark.parametrize("weak", ["short1", "12345678901", "password123"])
def test_password_validators_enforced(staff_api, staff_user, weak):
    bearer(staff_api, staff_user)
    r = staff_api.post(BASE + "auth/password/change/", {"old_password": PASSWORD, "new_password": weak},
                       format="json")
    assert r.status_code == 400
    assert err(r)["code"] == "validation_error" and err(r)["fields"]["new_password"]
    staff_user.refresh_from_db()
    assert staff_user.check_password(PASSWORD)


# ---------------------------------------------------------------------------
# Forgot password
# ---------------------------------------------------------------------------
def test_forgot_is_always_204_staff_door(staff_api, staff_user, monkeypatch):
    sent = []
    monkeypatch.setattr("accounts.forms.PortalPasswordResetForm.send_mail",
                        lambda self, *a, **k: sent.append(a[4]))
    assert staff_api.post(BASE + "auth/password/forgot/", {"email": staff_user.email},
                          format="json").status_code == 204
    assert staff_api.post(BASE + "auth/password/forgot/", {"email": "ghost@t.local"},
                          format="json").status_code == 204
    assert sent == [staff_user.email]


def test_forgot_is_always_204_portal_door(portal_api, institution_user, monkeypatch):
    sent = []
    monkeypatch.setattr("qa_core.portal_views.send_set_password_link", lambda req, user: sent.append(user.email))
    assert portal_api.post(BASE + "auth/password/forgot/", {"email": institution_user.email},
                           format="json").status_code == 204
    assert portal_api.post(BASE + "auth/password/forgot/", {"email": "ghost@t.local"},
                           format="json").status_code == 204
    assert sent == [institution_user.email]


# ---------------------------------------------------------------------------
# Development door header
# ---------------------------------------------------------------------------
@override_settings(DEBUG=True, API_DEV_DOOR_HEADER=True)
def test_dev_header_selects_portal_door(staff_api, institution_user):
    r = staff_api.post(BASE + "auth/token/", {"email": institution_user.email, "password": PASSWORD},
                       format="json", HTTP_X_NBTE_DOOR="portal")
    assert r.status_code == 200 and r.json()["door"] == "portal"
    staff_api.credentials(HTTP_AUTHORIZATION=f"Bearer {r.json()['access']}")
    assert staff_api.get(BASE + "me/", HTTP_X_NBTE_DOOR="portal").status_code == 200
    assert err(staff_api.get(BASE + "me/"))["code"] == "wrong_door"  # no header: staff door


@pytest.mark.parametrize("debug,flag", [(False, True), (True, False)])
def test_dev_header_ignored_unless_debug_and_flag(staff_api, institution_user, debug, flag):
    with override_settings(DEBUG=debug, API_DEV_DOOR_HEADER=flag):
        r = staff_api.post(BASE + "auth/token/", {"email": institution_user.email, "password": PASSWORD},
                           format="json", HTTP_X_NBTE_DOOR="portal")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


@override_settings(DEBUG=True, API_DEV_DOOR_HEADER=True)
def test_dev_header_never_affects_website_pages(rf):
    from qa_core import hosts

    assert hosts.is_portal_request(rf.get("/api/v1/me/", HTTP_HOST="localhost", HTTP_X_NBTE_DOOR="portal"))
    assert not hosts.is_portal_request(rf.get("/login/", HTTP_HOST="localhost", HTTP_X_NBTE_DOOR="portal"))
