"""App sessions (devices) for the mobile API.

Every sign-in from the app is an ``accounts.UserSession`` row of kind "app"
(``accounts.sessions_service.record_app_login``): its ``session_key`` is
``"app:<jti of the current refresh token>"`` and follows refresh-token
rotation, so GET me/devices/ lists app installs next to website browsers and
DELETE blacklists that install's refresh token (sessions_service.revoke).

The row id travels in the tokens as the ``sid`` claim (refresh and every
access token minted from it), so the server knows which device a request
comes from ("current" flag, push token, revoke-others) and can refuse an
access token whose device was signed out (``check_app_session``).
"""
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger("api")

SID_CLAIM = "sid"
TOUCH_INTERVAL = timedelta(minutes=5)


def _door_name(user):
    return "portal" if getattr(user, "user_type", "staff") != "staff" else "internal"


def register_app_session(user, device, refresh_jti, refresh=None, request=None):
    """Called after every successful auth/token/. ``device`` is the validated
    ``device`` object from the request (or None). When ``refresh`` (the
    RefreshToken) is given, the row id is written into it as ``sid``.
    Returns the UserSession row (None if it could not be stored)."""
    from accounts import sessions_service

    ip = None
    if request is not None:
        try:
            from core import ratelimit

            ip = ratelimit.client_ip(request)
            ip = ip if ip and ip != "unknown" else None
        except Exception:
            ip = None
    try:
        row = sessions_service.record_app_login(user, refresh_jti, device=dict(device or {}),
                                                door=_door_name(user), ip=ip)
    except Exception:
        logger.exception("could not record the app session")
        return None
    if refresh is not None:
        refresh[SID_CLAIM] = row.pk
    return row


def rotate(user, old_jti, new_jti, session_id=None):
    from accounts import sessions_service

    try:
        return sessions_service.rotate_app_session(user, old_jti, new_jti, session_id=session_id)
    except Exception:
        logger.exception("could not rotate the app session")
        return None


def logged_out(refresh_jti):
    from accounts import sessions_service

    sessions_service.mark_logged_out(sessions_service.app_key(refresh_jti))


def current_session(request):
    """The UserSession row of the request's access token (or None)."""
    from accounts.models import UserSession

    token = getattr(request, "auth", None)
    sid = token.get(SID_CLAIM) if token is not None and hasattr(token, "get") else None
    if not sid:
        return None
    return UserSession.objects.filter(pk=sid, user=request.user, kind=UserSession.APP,
                                      revoked_at__isnull=True).first()


def check_app_session(user, token):
    """True unless the token belongs to a device that was signed out. Also
    refreshes ``last_seen_at`` at most every TOUCH_INTERVAL."""
    from accounts.models import UserSession

    sid = token.get(SID_CLAIM)
    if not sid:
        return True  # tokens minted without a device row (tests, scripts)
    row = UserSession.objects.filter(pk=sid, user_id=user.pk).values("revoked_at", "last_seen_at").first()
    if row is None or row["revoked_at"] is not None:
        return False
    now = timezone.now()
    if row["last_seen_at"] is None or now - row["last_seen_at"] >= TOUCH_INTERVAL:
        UserSession.objects.filter(pk=sid).update(last_seen_at=now)
    return True
