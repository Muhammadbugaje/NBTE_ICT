"""Session ages and the optional e-mail sign-in code (v2.1 S-24, S-26).

Session ages (ConfigEntry, minutes; read through config.get, cached):

* ``security.session.staff_idle_minutes``      480  (8 h without a request)
* ``security.session.staff_absolute_minutes``  720  (12 h after sign-in)
* ``security.session.portal_idle_minutes``     120  (institution portal, 2 h)
* ``security.session.portal_absolute_minutes`` 720
* portal sessions also end when the browser closes.

When a limit passes, the person is signed out and sent to the sign-in page
(HTMX requests get an ``HX-Redirect``). The API (JWT) is not affected.

E-mail sign-in code (S-26): ``security.2fa.email_code_enabled`` (default
OFF). No OTP/TOTP library is installed, so the second factor is a 6-digit code
e-mailed after a password sign-in, for the people `core.twofactor.required_for`
selects. Passkey sign-ins count as two factors and skip it.
"""
import time

from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from config import registry as config

AUTH_AT = "_auth_at"
SEEN_AT = "_seen_at"
TOUCH_EVERY = 60  # seconds: do not write the session on every request

# Heartbeat endpoints the bell/poll JavaScript hits every few seconds. They
# must never WRITE the session: each write is a database save, and on SQLite a
# session save that lands while a stage action (or its inline e-mail task) is
# writing gets "database is locked" - which then blew up the poll as a
# SessionInterrupted 400. Silent polls still keep working while the session is
# open (they are checked against the ages below, read-only), but they never
# extend it, so a person who stops clicking still gets signed out on time.
QUIET_PREFIXES = ("/notifications/bell/", "/notifications/stream/", "/health")


def _minutes(key, default):
    try:
        return max(0, int(config.get(key, default)))
    except (TypeError, ValueError):
        return default


def limits(portal):
    if portal:
        return (_minutes("security.session.portal_idle_minutes", 120) * 60,
                _minutes("security.session.portal_absolute_minutes", 720) * 60)
    return (_minutes("security.session.staff_idle_minutes", 480) * 60,
            _minutes("security.session.staff_absolute_minutes", 720) * 60)


def _is_portal(request):
    try:
        from qa_core.hosts import is_portal_request

        return is_portal_request(request)
    except Exception:
        return False


def _login_url(request, portal):
    if portal:
        return "/login/"
    try:
        return reverse("accounts:login")
    except Exception:
        return "/accounts/login/"


def _expired_response(request, portal):
    url = _login_url(request, portal) + "?expired=1"
    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


def mark_login(request):
    now = time.time()
    request.session[AUTH_AT] = now
    request.session[SEEN_AT] = now


class SessionPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (user is None or not user.is_authenticated or request.path.startswith("/api/")
                or not hasattr(request, "session")):
            return self.get_response(request)
        portal = _is_portal(request)
        session = request.session
        now = time.time()
        first, last = session.get(AUTH_AT), session.get(SEEN_AT)
        quiet = request.path.startswith(QUIET_PREFIXES)
        if first is None or last is None:
            if not quiet:
                mark_login(request)
        else:
            idle, absolute = limits(portal)
            if (idle and now - float(last) > idle) or (absolute and now - float(first) > absolute):
                logout(request)
                return _expired_response(request, portal)
            if not quiet and now - float(last) > TOUCH_EVERY:
                session[SEEN_AT] = now
        if portal and not quiet and not session.get_expire_at_browser_close():
            session.set_expiry(0)
        from . import twofactor

        blocked = twofactor.gate(request)
        if blocked is not None:
            return blocked
        return self.get_response(request)
