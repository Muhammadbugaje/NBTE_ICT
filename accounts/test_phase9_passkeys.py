"""Phase 9: passkeys (WebAuthn). A real ceremony needs an authenticator, so the
finish endpoints are tested with webauthn.verify_* monkeypatched."""
import json
from types import SimpleNamespace

import pytest
import webauthn
from django.test import Client
from django.urls import reverse
from webauthn.helpers import bytes_to_base64url

from accounts import passkeys
from accounts.models import Passkey
from qa_core.hosts import portal_reverse

pytestmark = pytest.mark.django_db


def _post_json(client, url, body=None):
    return client.post(url, data=json.dumps(body or {}), content_type="application/json")


def _fake_registration(monkeypatch, cred_id=b"credential-one"):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(credential_id=cred_id, credential_public_key=b"public-key-bytes", sign_count=3)

    monkeypatch.setattr(webauthn, "verify_registration_response", fake)
    return calls


def _fake_authentication(monkeypatch, new_count=7, fail=False):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        if fail:
            raise ValueError("bad signature")
        return SimpleNamespace(credential_id=b"x", new_sign_count=new_count, user_verified=True)

    monkeypatch.setattr(webauthn, "verify_authentication_response", fake)
    return calls


def _passkey(user, cred=b"credential-one", count=3, name="Laptop"):
    return Passkey.objects.create(user=user, credential_id=bytes_to_base64url(cred),
                                  public_key=bytes_to_base64url(b"public-key-bytes"), sign_count=count, name=name)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def test_register_begin_requires_login():
    r = _post_json(Client(), reverse("accounts:passkey_register_begin"))
    assert r.status_code == 401 and r.json()["ok"] is False


def test_register_begin_options_shape_and_challenge(staff_user):
    _passkey(staff_user, b"existing")
    c = Client()
    c.force_login(staff_user)
    r = _post_json(c, reverse("accounts:passkey_register_begin"))
    assert r.status_code == 200
    data = r.json()
    assert data["rp"] == {"id": "testserver", "name": "NBTE"}
    assert data["user"]["name"] == staff_user.email and data["user"]["id"]
    assert data["authenticatorSelection"]["residentKey"] == "required"
    assert [c_["id"] for c_ in data["excludeCredentials"]] == [bytes_to_base64url(b"existing")]
    assert c.session[passkeys.REG_CHALLENGE_KEY] == data["challenge"]


def test_register_begin_get_not_allowed(staff_user):
    c = Client()
    c.force_login(staff_user)
    assert c.get(reverse("accounts:passkey_register_begin")).status_code == 405


def test_register_finish_creates_passkey(staff_user, monkeypatch):
    calls = _fake_registration(monkeypatch)
    c = Client(HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/126.0 Safari/537.36")
    c.force_login(staff_user)
    challenge = _post_json(c, reverse("accounts:passkey_register_begin")).json()["challenge"]
    cred = {"id": "abc", "rawId": "abc", "type": "public-key",
            "response": {"clientDataJSON": "x", "attestationObject": "y", "transports": ["internal", "hybrid"]}}
    r = _post_json(c, reverse("accounts:passkey_register_finish"), {"credential": cred, "name": ""})
    assert r.status_code == 200 and r.json()["ok"] is True
    pk = Passkey.objects.get(user=staff_user)
    assert pk.credential_id == bytes_to_base64url(b"credential-one")
    assert pk.sign_count == 3 and pk.transports == ["internal", "hybrid"] and pk.name == "Chrome on Windows"
    kw = calls[0]
    assert kw["expected_rp_id"] == "testserver" and kw["expected_origin"] == "http://testserver"
    assert bytes_to_base64url(kw["expected_challenge"]) == challenge
    assert passkeys.REG_CHALLENGE_KEY not in c.session  # single use


def test_register_finish_without_challenge_fails(staff_user, monkeypatch):
    _fake_registration(monkeypatch)
    c = Client()
    c.force_login(staff_user)
    r = _post_json(c, reverse("accounts:passkey_register_finish"), {"credential": {"id": "a"}})
    assert r.status_code == 400 and "expired" in r.json()["error"]
    assert not Passkey.objects.exists()


def test_register_finish_rejects_verification_failure(staff_user, monkeypatch):
    def boom(**kwargs):
        raise ValueError("nope")

    monkeypatch.setattr(webauthn, "verify_registration_response", boom)
    c = Client()
    c.force_login(staff_user)
    _post_json(c, reverse("accounts:passkey_register_begin"))
    r = _post_json(c, reverse("accounts:passkey_register_finish"), {"credential": {"id": "a"}})
    assert r.status_code == 400 and not Passkey.objects.exists()


def test_portal_registration_uses_portal_rp(institution_user, portal_host, monkeypatch):
    _fake_registration(monkeypatch, b"inst-cred")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(institution_user)
    data = _post_json(c, portal_reverse("qa_portal:account_passkey_register_begin")).json()
    assert data["rp"]["id"] == portal_host
    r = _post_json(c, portal_reverse("qa_portal:account_passkey_register_finish"),
                   {"credential": {"id": "a"}, "name": "My phone"})
    assert r.status_code == 200
    assert Passkey.objects.get(user=institution_user).name == "My phone"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def test_auth_begin_is_usernameless(db):
    c = Client()
    r = _post_json(c, reverse("accounts:passkey_auth_begin"))
    data = r.json()
    assert r.status_code == 200 and data["rpId"] == "testserver"
    assert data.get("allowCredentials", []) == []
    assert c.session[passkeys.AUTH_CHALLENGE_KEY] == data["challenge"]


def _auth(client, url_begin, url_finish, cred_id=b"credential-one", next_url=""):
    _post_json(client, url_begin)
    return _post_json(client, url_finish, {"credential": {"id": bytes_to_base64url(cred_id), "type": "public-key"},
                                           "next": next_url})


def test_auth_finish_signs_in_staff_and_updates_count(staff_user, monkeypatch):
    calls = _fake_authentication(monkeypatch, new_count=9)
    pk = _passkey(staff_user)
    c = Client()
    r = _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"))
    assert r.status_code == 200, r.content
    assert r.json() == {"ok": True, "redirect": reverse("core:home")}
    assert int(c.session["_auth_user_id"]) == staff_user.pk
    pk.refresh_from_db()
    assert pk.sign_count == 9 and pk.last_used_at is not None
    assert calls[0]["credential_current_sign_count"] == 3
    assert calls[0]["credential_public_key"] == b"public-key-bytes"


def test_auth_finish_honours_safe_next_only(staff_user, monkeypatch):
    _fake_authentication(monkeypatch)
    _passkey(staff_user)
    r = _auth(Client(), reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"),
              next_url="/tasks/")
    assert r.json()["redirect"] == "/tasks/"
    r = _auth(Client(), reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"),
              next_url="https://evil.example/")
    assert r.json()["redirect"] == reverse("core:home")


def test_auth_unknown_credential(db, monkeypatch):
    _fake_authentication(monkeypatch)
    c = Client()
    r = _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"), b"nope")
    assert r.status_code == 400 and "not registered" in r.json()["error"]
    assert "_auth_user_id" not in c.session


def test_auth_verification_failure(staff_user, monkeypatch):
    _fake_authentication(monkeypatch, fail=True)
    _passkey(staff_user)
    c = Client()
    r = _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"))
    assert r.status_code == 400 and "_auth_user_id" not in c.session


def test_auth_challenge_is_single_use(staff_user, monkeypatch):
    _fake_authentication(monkeypatch)
    _passkey(staff_user)
    c = Client()
    _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"))
    c.logout()
    r = _post_json(c, reverse("accounts:passkey_auth_finish"),
                   {"credential": {"id": bytes_to_base64url(b"credential-one")}})
    assert r.status_code == 400


def test_institution_passkey_refused_on_internal(institution_user, monkeypatch):
    _fake_authentication(monkeypatch)
    _passkey(institution_user)
    c = Client()
    r = _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"))
    assert r.status_code == 400 and "QA portal" in r.json()["error"]
    assert "_auth_user_id" not in c.session


def test_institution_passkey_signs_in_on_portal(institution_user, portal_host, monkeypatch):
    _fake_authentication(monkeypatch)
    _passkey(institution_user)
    c = Client(HTTP_HOST=portal_host)
    r = _auth(c, portal_reverse("qa_portal:passkey_sign_in_begin"), portal_reverse("qa_portal:passkey_sign_in_finish"))
    assert r.status_code == 200 and r.json()["redirect"] == "/"
    assert int(c.session["_auth_user_id"]) == institution_user.pk


def test_staff_passkey_refused_on_portal(staff_user, portal_host, monkeypatch):
    _fake_authentication(monkeypatch)
    _passkey(staff_user)
    c = Client(HTTP_HOST=portal_host)
    r = _auth(c, portal_reverse("qa_portal:passkey_sign_in_begin"), portal_reverse("qa_portal:passkey_sign_in_finish"))
    assert r.status_code == 400 and "internal" in r.json()["error"]


def test_inactive_user_refused(staff_user, monkeypatch):
    _fake_authentication(monkeypatch)
    _passkey(staff_user)
    staff_user.is_active = False
    staff_user.save()
    r = _auth(Client(), reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"))
    assert r.status_code == 400


def test_must_change_password_user_is_signed_in_then_redirected(p9_make_user, monkeypatch):
    _fake_authentication(monkeypatch)
    user = p9_make_user("newbie@nbte.test")
    user.must_change_password = True
    user.save()
    _passkey(user)
    c = Client()
    assert _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish")).status_code == 200
    r = c.get(reverse("core:home"))
    assert r.status_code == 302 and r["Location"] == reverse("accounts:force_password_change")


def test_failed_attempts_are_rate_limited(db, monkeypatch):
    _fake_authentication(monkeypatch)
    c = Client()
    for _ in range(10):
        _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"), b"nope")
    r = _auth(c, reverse("accounts:passkey_auth_begin"), reverse("accounts:passkey_auth_finish"), b"nope")
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# Management: rename / remove (own passkeys only)
# ---------------------------------------------------------------------------
def test_rename_and_remove_own(staff_user):
    pk = _passkey(staff_user)
    c = Client()
    c.force_login(staff_user)
    c.post(reverse("accounts:passkey_rename", args=[pk.pk]), {"name": "Office PC"})
    pk.refresh_from_db()
    assert pk.name == "Office PC"
    r = c.post(reverse("accounts:passkey_remove", args=[pk.pk]))
    assert r.status_code == 302 and not Passkey.objects.filter(pk=pk.pk).exists()


def test_cannot_touch_someone_elses_passkey(staff_user, other_staff):
    theirs = _passkey(other_staff)
    c = Client()
    c.force_login(staff_user)
    c.post(reverse("accounts:passkey_rename", args=[theirs.pk]), {"name": "Mine now"})
    assert c.post(reverse("accounts:passkey_remove", args=[theirs.pk])).status_code == 404
    theirs.refresh_from_db()
    assert theirs.name == "Laptop"


def test_profile_and_login_pages_show_passkey_ui(staff_user, institution_user, portal_host):
    _passkey(staff_user, name="Work laptop")
    c = Client()
    c.force_login(staff_user)
    html = c.get(reverse("accounts:profile")).content.decode()
    assert "Work laptop" in html and "data-passkey-add" in html and "passkeys.js" in html
    login_html = Client().get(reverse("accounts:login")).content.decode()
    assert "data-passkey-signin" in login_html and reverse("accounts:passkey_auth_finish") in login_html
    portal_login = Client(HTTP_HOST=portal_host).get("/login/").content.decode()
    assert "data-passkey-signin" in portal_login and "/passkeys/sign-in/finish/" in portal_login
