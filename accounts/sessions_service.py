"""Signed-in devices (Phase 9).

Plain functions so the website views and the future mobile-app API share one
implementation:

    record_login(request, user, kind="web") -> UserSession | None
    list_sessions(user, current_key=None) -> list[UserSession]
    revoke(user, session_id) -> bool
    revoke_all_except(user, current_key) -> int
    mark_logged_out(session_key) -> None
    touch(request) -> None          (called by LastSeenMiddleware)
    parse_device_label(user_agent) -> str

Mobile-app sessions (Phase 9 API, kind "app"): the credential is a rotating
JWT refresh token, so `session_key` is "app:<jti of the current refresh
token>" and revoking blacklists that token instead of deleting a Django
session:

    record_app_login(user, jti, *, device=None, door="internal", ip=None) -> UserSession
    rotate_app_session(user, old_jti, new_jti, session_id=None) -> UserSession | None
    set_push_token(row, push_token, device_id=None) -> UserSession

Revoking deletes the Django session from the configured session store (so the
browser is signed out on its next request) and stamps `revoked_at`.
"""
from __future__ import annotations

import re
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.utils import timezone

from .models import UserSession

# The middleware writes last_seen_at at most this often per session.
TOUCH_INTERVAL = timedelta(minutes=5)
SESSION_SEEN_KEY = "_p9_seen"


# ---------------------------------------------------------------------------
# User-agent -> "Chrome on Windows"
# ---------------------------------------------------------------------------
_BROWSERS = (
    (r"EdgA?/|Edg/|EdgiOS/", "Edge"),
    (r"OPR/|Opera", "Opera"),
    (r"SamsungBrowser/", "Samsung Internet"),
    (r"FxiOS/|Firefox/", "Firefox"),
    (r"CriOS/|Chrome/|Chromium/", "Chrome"),
    (r"Safari/", "Safari"),
)
_SYSTEMS = (
    (r"iPhone", "iPhone"),
    (r"iPad", "iPad"),
    (r"Android", "Android"),
    (r"CrOS", "ChromeOS"),
    (r"Windows", "Windows"),
    (r"Macintosh|Mac OS X", "macOS"),
    (r"Linux", "Linux"),
)


def parse_device_label(user_agent):
    """A short human label from a User-Agent string; never raises."""
    ua = user_agent or ""
    if not ua.strip():
        return "Unknown device"
    if "NBTE-App" in ua:  # the mobile app will identify itself
        app_os = next((name for pattern, name in _SYSTEMS if re.search(pattern, ua)), "phone")
        return f"NBTE app on {app_os}"
    browser = next((name for pattern, name in _BROWSERS if re.search(pattern, ua)), None)
    system = next((name for pattern, name in _SYSTEMS if re.search(pattern, ua)), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Unknown device"


def _client_ip(request):
    from core import ratelimit

    ip = ratelimit.client_ip(request)
    return ip if ip and ip != "unknown" else None


def _door(request):
    return "portal" if getattr(request, "is_qa_portal", False) else "internal"


def _store():
    return import_module(settings.SESSION_ENGINE).SessionStore


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def record_login(request, user, kind=UserSession.WEB):
    """Create (or refresh) the device row for the request's session."""
    session = getattr(request, "session", None)
    if session is None or user is None or not getattr(user, "pk", None):
        return None
    if not session.session_key:
        session.save()
    key = session.session_key
    if not key:
        return None
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:500]
    now = timezone.now()
    row, _ = UserSession.objects.update_or_create(
        session_key=key,
        defaults={
            "user": user, "last_seen_at": now, "ip": _client_ip(request), "user_agent": ua,
            "device_label": parse_device_label(ua)[:80], "kind": kind, "door": _door(request),
            "revoked_at": None,
        },
    )
    session[SESSION_SEEN_KEY] = now.timestamp()
    return row


def mark_logged_out(session_key):
    if session_key:
        UserSession.objects.filter(session_key=session_key, revoked_at__isnull=True).update(
            revoked_at=timezone.now())


def touch(request):
    """Update last_seen_at at most every TOUCH_INTERVAL (one session read per
    request; a DB write only when the stored timestamp is stale)."""
    user = getattr(request, "user", None)
    session = getattr(request, "session", None)
    if user is None or not user.is_authenticated or session is None or not session.session_key:
        return
    now = timezone.now()
    try:
        seen = float(session.get(SESSION_SEEN_KEY) or 0)
    except (TypeError, ValueError):
        seen = 0
    if now.timestamp() - seen < TOUCH_INTERVAL.total_seconds():
        return
    session[SESSION_SEEN_KEY] = now.timestamp()
    updated = UserSession.objects.filter(session_key=session.session_key, user=user,
                                         revoked_at__isnull=True).update(last_seen_at=now)
    if not updated and not UserSession.objects.filter(session_key=session.session_key).exists():
        # Signed in before Phase 9 shipped: start tracking this browser now.
        record_login(request, user)


APP_PREFIX = "app:"


def app_key(jti):
    return f"{APP_PREFIX}{jti}"


def _blacklist_jti(jti):
    """Blacklist the outstanding refresh token with this jti (app sessions)."""
    try:
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    except Exception:  # simplejwt blacklist not installed
        return
    for token in OutstandingToken.objects.filter(jti=jti):
        BlacklistedToken.objects.get_or_create(token=token)


def _delete_session(session_key):
    if session_key and session_key.startswith(APP_PREFIX):
        _blacklist_jti(session_key[len(APP_PREFIX):])
        return
    try:
        _store()(session_key=session_key).delete()
    except Exception:  # an already-expired session is fine
        pass


def _app_label(device):
    model = (device.get("model") or "").strip()
    platform = {"android": "Android", "ios": "iPhone"}.get(device.get("platform") or "", "phone")
    return (f"NBTE app on {model}" if model else f"NBTE app on {platform}")[:80]


def record_app_login(user, jti, *, device=None, door="internal", ip=None):
    """One row per app install: signing in again from the same `device_id`
    reuses the row (its previous refresh token is blacklisted)."""
    device = device or {}
    now = timezone.now()
    device_id = (device.get("device_id") or "").strip()[:100]
    row = None
    if device_id:
        row = (UserSession.objects.filter(user=user, kind=UserSession.APP, device_id=device_id)
               .order_by("-created_at").first())
    if row is not None and row.revoked_at is None:
        _delete_session(row.session_key)
    if row is None:
        row = UserSession(user=user, kind=UserSession.APP, device_id=device_id)
    row.session_key = app_key(jti)
    row.last_seen_at = now
    row.revoked_at = None
    row.door = door
    row.ip = ip
    row.platform = (device.get("platform") or "")[:20]
    row.app_version = (device.get("app_version") or "")[:40]
    row.user_agent = " ".join(p for p in ("NBTE-App", device.get("platform") or "", device.get("os_version") or "",
                                          device.get("model") or "") if p)[:500]
    row.device_label = _app_label(device)
    if device.get("push_token"):
        row.push_token = device["push_token"]
    row.save()
    return row


def rotate_app_session(user, old_jti, new_jti, session_id=None):
    """Keep the same device row when the app rotates its refresh token."""
    qs = UserSession.objects.filter(user=user, kind=UserSession.APP, revoked_at__isnull=True)
    row = qs.filter(pk=session_id).first() if session_id else None
    if row is None:
        row = qs.filter(session_key=app_key(old_jti)).first()
    if row is None:
        return None
    row.session_key = app_key(new_jti)
    row.last_seen_at = timezone.now()
    row.save(update_fields=["session_key", "last_seen_at"])
    return row


def set_push_token(row, push_token, device_id=None):
    row.push_token = push_token or ""
    fields = ["push_token"]
    if device_id:
        row.device_id = device_id[:100]
        fields.append("device_id")
    row.save(update_fields=fields)
    return row


def revoke(user, session_id):
    """Sign one of the user's devices out. False when it is not theirs."""
    row = UserSession.objects.filter(pk=session_id, user=user, revoked_at__isnull=True).first()
    if row is None:
        return False
    _delete_session(row.session_key)
    row.revoked_at = timezone.now()
    row.save(update_fields=["revoked_at"])
    return True


def revoke_all_except(user, current_key):
    """Sign out every other device; returns how many were signed out."""
    rows = list(UserSession.objects.filter(user=user, revoked_at__isnull=True).exclude(session_key=current_key or ""))
    for row in rows:
        _delete_session(row.session_key)
    if rows:
        UserSession.objects.filter(pk__in=[r.pk for r in rows]).update(revoked_at=timezone.now())
    return len(rows)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def list_sessions(user, current_key=None):
    """Active devices, most recent first, with `is_current` set. Rows whose
    Django session has expired or vanished are closed off on the way."""
    rows = list(UserSession.objects.filter(user=user, revoked_at__isnull=True))
    if not rows:
        return []
    alive = _alive_keys([r.session_key for r in rows if not r.session_key.startswith(APP_PREFIX)])
    app_alive = _alive_app_keys([r.session_key for r in rows if r.session_key.startswith(APP_PREFIX)])
    out, dead = [], []
    for row in rows:
        is_app = row.session_key.startswith(APP_PREFIX)
        known = app_alive if is_app else alive
        if known is not None and row.session_key not in known and row.session_key != current_key:
            dead.append(row.pk)
            continue
        row.is_current = bool(current_key) and row.session_key == current_key
        row.seen_recently = bool(row.last_seen_at) and (timezone.now() - row.last_seen_at) < timedelta(minutes=2)
        out.append(row)
    if dead:
        UserSession.objects.filter(pk__in=dead).update(revoked_at=timezone.now())
    out.sort(key=lambda r: (not r.is_current, -(r.last_seen_at or r.created_at).timestamp()))
    return out


def _alive_app_keys(keys):
    """App keys whose refresh token is outstanding, unexpired and not
    blacklisted (None when the blacklist app is missing)."""
    if not keys:
        return set()
    try:
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
    except Exception:
        return None
    jtis = [k[len(APP_PREFIX):] for k in keys]
    live = (OutstandingToken.objects.filter(jti__in=jtis, expires_at__gt=timezone.now(),
                                            blacklistedtoken__isnull=True)
            .values_list("jti", flat=True))
    return {app_key(j) for j in live}


def _alive_keys(keys):
    """Keys still present and unexpired in the DB session store, or None when
    another engine is configured (then every row is trusted)."""
    if settings.SESSION_ENGINE not in ("django.contrib.sessions.backends.db",
                                       "django.contrib.sessions.backends.cached_db"):
        return None
    from django.contrib.sessions.models import Session

    return set(Session.objects.filter(session_key__in=keys, expire_date__gt=timezone.now())
               .values_list("session_key", flat=True))
