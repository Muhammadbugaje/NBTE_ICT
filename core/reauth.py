"""Recent authentication ("sudo mode", spec S-07, X23, X33).

Sensitive personal changes (signature, bank details; later: granting
permissions, approving large payments, revoking documents) need the person to
have typed their password within the last few minutes.

* ``is_recent(request)`` - True when the real, signed-in person re-entered
  their password within ``security.reauth_minutes`` (default 10).
* ``confirm(request, password)`` - checks the password of the signed-in
  person, marks the session as recently authenticated and returns True;
  rate-limited (5 failures per 15 minutes).
* ``require(request, password)`` - raises ``ReauthRequired`` unless the session
  is recent or the given password is correct (services and the API call this).

Never satisfied while a super admin is "acting as" someone: the password
belongs to the person, not to the impersonator.
"""
import time

from django.core.exceptions import PermissionDenied

from config import registry as config

from . import ratelimit

SESSION_KEY = "_reauth"
FAILURES = (5, 15 * 60)


class ReauthRequired(PermissionDenied):
    """The action needs the person's password (again)."""

    def __init__(self, message="Enter your password to confirm this change."):
        super().__init__(message)
        self.message = message


def window_seconds():
    try:
        minutes = int(config.get("security.reauth_minutes", 10))
    except (TypeError, ValueError):
        minutes = 10
    return max(1, minutes) * 60


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def is_recent(request):
    if request is None or _impersonating(request) or not request.user.is_authenticated:
        return False
    data = getattr(request, "session", {}).get(SESSION_KEY) or {}
    if data.get("uid") != request.user.pk:
        return False
    return (time.time() - float(data.get("at") or 0)) < window_seconds()


def mark(request):
    request.session[SESSION_KEY] = {"uid": request.user.pk, "at": time.time()}


def clear(request):
    request.session.pop(SESSION_KEY, None)


def _ident(request):
    return f"{request.user.pk}|{ratelimit.client_ip(request)}"


def confirm(request, password):
    """Check the signed-in person's password. True on success (and the session
    is marked); False on a wrong password. Raises ReauthRequired when blocked."""
    if _impersonating(request):
        raise ReauthRequired("This change can only be made by the account owner, not while acting as them.")
    if not request.user.is_authenticated:
        raise ReauthRequired("Sign in first.")
    limit, window = FAILURES
    if ratelimit.is_limited("reauth", _ident(request), limit):
        raise ReauthRequired("Too many wrong passwords. Wait 15 minutes and try again.")
    if password and request.user.check_password(password):
        ratelimit.reset("reauth", _ident(request))
        mark(request)
        return True
    ratelimit.hit("reauth", _ident(request), window)
    return False


def require(request, password=None):
    """Raise ReauthRequired unless recently authenticated or ``password`` is right."""
    if is_recent(request):
        return True
    if not password:
        raise ReauthRequired()
    if not confirm(request, password):
        raise ReauthRequired("That password is not right. Try again.")
    return True


def require_for_user(user, password):
    """API variant (no session): the password must be given and correct."""
    limit, window = FAILURES
    if ratelimit.is_limited("reauth-api", user.pk, limit):
        raise ReauthRequired("Too many wrong passwords. Wait 15 minutes and try again.")
    if not password or not user.check_password(password):
        if password:
            ratelimit.hit("reauth-api", user.pk, window)
        raise ReauthRequired("That password is not right." if password else "Enter your password to confirm.")
    ratelimit.reset("reauth-api", user.pk)
    return True
