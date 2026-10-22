"""Phase 9: signed-in devices and "sign out everywhere"."""
import time

import pytest
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from accounts import sessions_service
from accounts.conftest import P9_PASSWORD
from accounts.models import UserSession
from qa_core.hosts import portal_reverse

pytestmark = pytest.mark.django_db

CHROME_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


@pytest.mark.parametrize("ua, label", [
    (CHROME_WIN, "Chrome on Windows"),
    (SAFARI_IPHONE, "Safari on iPhone"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0", "Firefox on Linux"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/126.0 Safari/537.36 Edg/126.0", "Edge on macOS"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/126.0 Mobile Safari/537.36", "Chrome on Android"),
    ("", "Unknown device"),
    ("NBTE-App/1.0 (iPhone; iOS 17)", "NBTE app on iPhone"),
])
def test_parse_device_label(ua, label):
    assert sessions_service.parse_device_label(ua) == label


def _login(email, host="localhost", ua=CHROME_WIN):
    c = Client(HTTP_HOST=host, HTTP_USER_AGENT=ua)
    url = "/accounts/login/" if host == "localhost" else "/login/"
    r = c.post(url, {"username": email, "password": P9_PASSWORD})
    assert r.status_code == 302, r.content[:400]
    return c


def test_internal_login_records_device(staff_user):
    c = _login(staff_user.email)
    row = UserSession.objects.get(user=staff_user)
    assert row.session_key == c.session.session_key
    assert row.device_label == "Chrome on Windows" and row.door == "internal" and row.kind == "web"
    assert row.ip == "127.0.0.1" and row.revoked_at is None and row.last_seen_at


def test_portal_login_records_device(institution_user, portal_host):
    c = _login(institution_user.email, host=portal_host, ua=SAFARI_IPHONE)
    row = UserSession.objects.get(user=institution_user)
    assert row.door == "portal" and row.device_label == "Safari on iPhone"
    assert row.session_key == c.session.session_key


def test_logout_marks_revoked(staff_user):
    c = _login(staff_user.email)
    c.post(reverse("accounts:logout"))
    assert UserSession.objects.get(user=staff_user).revoked_at is not None


def test_last_seen_touched_at_most_every_five_minutes(staff_user):
    c = _login(staff_user.email)
    row = UserSession.objects.get(user=staff_user)
    first = row.last_seen_at
    c.get(reverse("accounts:profile"))
    row.refresh_from_db()
    assert row.last_seen_at == first  # fresh: no write
    session = c.session
    session[sessions_service.SESSION_SEEN_KEY] = time.time() - 6 * 60
    session.save()
    c.get(reverse("accounts:profile"))
    row.refresh_from_db()
    assert row.last_seen_at > first


def test_pre_existing_session_is_picked_up(staff_user):
    c = Client()
    c.force_login(staff_user)
    UserSession.objects.all().delete()
    session = c.session
    session.pop(sessions_service.SESSION_SEEN_KEY, None)
    session.save()
    c.get(reverse("accounts:profile"))
    assert UserSession.objects.filter(user=staff_user, session_key=c.session.session_key).exists()


def test_list_marks_current_and_drops_dead_sessions(staff_user):
    a = _login(staff_user.email)
    _login(staff_user.email, ua=SAFARI_IPHONE)
    gone = UserSession.objects.create(user=staff_user, session_key="x" * 32, device_label="Old")
    rows = sessions_service.list_sessions(staff_user, a.session.session_key)
    assert [r.is_current for r in rows] == [True, False]
    gone.refresh_from_db()
    assert gone.revoked_at is not None


def test_revoke_signs_other_device_out(staff_user):
    a = _login(staff_user.email)
    b = _login(staff_user.email, ua=SAFARI_IPHONE)
    other = UserSession.objects.get(session_key=b.session.session_key)
    r = a.post(reverse("accounts:device_revoke", args=[other.pk]))
    assert r.status_code == 302
    other.refresh_from_db()
    assert other.revoked_at is not None
    assert not Session.objects.filter(session_key=other.session_key).exists()
    assert b.get(reverse("accounts:profile")).status_code == 302  # back to the login page
    assert a.get(reverse("accounts:profile")).status_code == 200


def test_cannot_revoke_someone_elses_device(staff_user, other_staff):
    a = _login(staff_user.email)
    b = _login(other_staff.email)
    theirs = UserSession.objects.get(session_key=b.session.session_key)
    a.post(reverse("accounts:device_revoke", args=[theirs.pk]))
    theirs.refresh_from_db()
    assert theirs.revoked_at is None
    assert sessions_service.revoke(staff_user, theirs.pk) is False


def test_current_device_is_not_revoked_by_its_row(staff_user):
    a = _login(staff_user.email)
    mine = UserSession.objects.get(session_key=a.session.session_key)
    a.post(reverse("accounts:device_revoke", args=[mine.pk]))
    mine.refresh_from_db()
    assert mine.revoked_at is None


def test_sign_out_all_other_devices(staff_user):
    a = _login(staff_user.email)
    b = _login(staff_user.email, ua=SAFARI_IPHONE)
    c = _login(staff_user.email)
    a.post(reverse("accounts:devices_revoke_others"))
    assert b.get(reverse("accounts:profile")).status_code == 302
    assert c.get(reverse("accounts:profile")).status_code == 302
    assert a.get(reverse("accounts:profile")).status_code == 200
    assert UserSession.objects.filter(user=staff_user, revoked_at__isnull=True).count() == 1
    assert sessions_service.revoke_all_except(staff_user, a.session.session_key) == 0


def test_get_not_allowed_for_revoke(staff_user):
    a = _login(staff_user.email)
    assert a.get(reverse("accounts:devices_revoke_others")).status_code == 405


def test_profile_lists_devices(staff_user):
    a = _login(staff_user.email)
    _login(staff_user.email, ua=SAFARI_IPHONE)
    html = a.get(reverse("accounts:profile")).content.decode()
    assert "Signed-in devices" in html and "This device" in html and "Safari on iPhone" in html
    assert "Sign out of all other devices" in html


def test_portal_account_lists_devices_and_revokes(institution_user, portal_host):
    a = _login(institution_user.email, host=portal_host)
    b = _login(institution_user.email, host=portal_host, ua=SAFARI_IPHONE)
    html = a.get("/account/").content.decode()
    assert "Signed-in devices" in html and "Safari on iPhone" in html and "Notifications" in html
    other = UserSession.objects.get(session_key=b.session.session_key)
    r = a.post(portal_reverse("qa_portal:account_device_revoke", other.pk))
    assert r.status_code == 302 and r["Location"].startswith("/account/")
    assert b.get("/account/").status_code == 302
    a.post(portal_reverse("qa_portal:account_devices_revoke_others"))


def test_impersonation_cannot_revoke(staff_user, p9_make_user):
    from core.impersonation import IMPERSONATE_SESSION_KEY

    boss = p9_make_user("root@nbte.test", superuser=True)
    victim_browser = _login(staff_user.email)
    row = UserSession.objects.get(session_key=victim_browser.session.session_key)
    c = Client()
    c.force_login(boss)
    s = c.session
    s[IMPERSONATE_SESSION_KEY] = staff_user.pk
    s.save()
    c.post(reverse("accounts:device_revoke", args=[row.pk]))
    row.refresh_from_db()
    assert row.revoked_at is None
