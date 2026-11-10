"""S-24 session ages, S-07 recent authentication, S-26 optional e-mail code."""
import time

import pytest
from django.test import Client

from core import reauth, session_policy, twofactor

pytestmark = pytest.mark.django_db
PW = "Sess-test-2026!"


def _user(email, role=None):
    from accounts.models import Role, User

    u = User(email=email, first_name="Demo", last_name="User", user_type="staff",
             role=Role.objects.filter(code=role).first() if role else None, must_change_password=False)
    u.set_password(PW)
    u.save()
    return u



def test_session_ages():
    u = _user("ages@demo.nbte.local")
    c = Client()
    c.force_login(u)
    assert c.get("/home/").status_code == 200
    s = c.session
    s[session_policy.SEEN_AT] = time.time() - 9 * 3600           # idle past 8 h
    s.save()
    r = c.get("/home/")
    assert r.status_code == 302 and "expired=1" in r["Location"]
    c.force_login(u)
    c.get("/home/")
    s = c.session
    s[session_policy.AUTH_AT] = time.time() - 13 * 3600          # absolute past 12 h
    s.save()
    r = c.get("/home/", HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and "expired=1" in r["HX-Redirect"]
    assert session_policy.limits(portal=True) == (120 * 60, 720 * 60)
    assert session_policy.limits(portal=False) == (480 * 60, 720 * 60)


def test_reauth_required_and_window(rf):
    u = _user("reauth@demo.nbte.local")
    c = Client()
    c.force_login(u)
    r = c.post("/accounts/profile/signature/", {"section": "reauth", "password": "wrong"})
    assert r.status_code == 302
    assert reauth.SESSION_KEY not in c.session
    c.post("/accounts/profile/signature/", {"section": "reauth", "password": PW})
    data = c.session[reauth.SESSION_KEY]
    assert data["uid"] == u.pk and time.time() - data["at"] < 5


def test_2fa_required_roles(monkeypatch):
    from config.models import ConfigEntry

    from accounts.models import Role

    Role.objects.get_or_create(code="director", defaults={"name": "Director", "rank": 100, "requires_2fa": True})
    director = _user("twofa.dir@demo.nbte.local", role="director")
    officer = _user("twofa.off@demo.nbte.local")
    assert twofactor.required_for(director) and not twofactor.required_for(officer)
    # off by default: password sign-in goes straight in
    c = Client()
    assert c.post("/accounts/login/", {"username": director.email, "password": PW}).status_code == 302
    assert c.get("/home/").status_code == 200
    ConfigEntry.objects.create(key="security.2fa.email_code_enabled", value="true", value_type="bool")
    codes = []
    real_start = twofactor.start
    monkeypatch.setattr(twofactor, "start", lambda req, user: codes.append(real_start(req, user)) or codes[-1])
    c = Client()
    c.post("/accounts/login/", {"username": director.email, "password": PW})
    r = c.get("/home/")
    assert r.status_code == 302 and r["Location"].endswith("/security/verify-code/")
    assert c.post("/security/verify-code/", {"code": "000000" if codes[0] != "000000" else "111111"}).status_code == 200
    r = c.post("/security/verify-code/", {"code": codes[0]})
    assert r.status_code == 302 and c.get("/home/").status_code == 200
    # officers are not asked
    c = Client()
    c.post("/accounts/login/", {"username": officer.email, "password": PW})
    assert c.get("/home/").status_code == 200


def test_heartbeat_polls_never_write_the_session():
    """v2.4 speed fix: /notifications/stream|bell (and /health) are polled
    every few seconds while a case stage action (or its inline e-mail task)
    may be writing. Their poll must not save the session - it used to write
    `_seen_at`, and on SQLite that save could hit `database is locked`, which
    surfaced as a SessionInterrupted 400 on the poll. Silent polls are still
    age-checked read-only, and a real page click still refreshes the age."""
    u = _user("poll@demo.nbte.local")
    c = Client()
    c.force_login(u)
    assert c.get("/home/").status_code == 200          # stamps _auth_at/_seen_at
    s = c.session
    seen = time.time() - session_policy.TOUCH_EVERY - 5  # older than the touch window
    s[session_policy.SEEN_AT] = seen
    s.save()

    assert c.get("/notifications/stream/").status_code == 200
    reloaded = {session_policy.SEEN_AT: c.session.get(session_policy.SEEN_AT)}
    assert reloaded[session_policy.SEEN_AT] == seen     # untouched by the poll

    assert c.get("/home/").status_code == 200
    assert c.session.get(session_policy.SEEN_AT) > seen  # a real page refreshes it
