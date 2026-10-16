"""Small fixed-window rate limiter on Django's cache (review finding M18).

No new dependency: it uses whatever cache backend the settings configure
(LocMem in dev, Redis in production, so limits are shared by all workers
there). Each limit is a counter that expires `window` seconds after its
first hit.

Limits used by the app:

    login      5 failed sign-ins per email + IP per 15 minutes
    reset      3 password-reset emails per IP per hour (and per address)
    contact    5 public contact-form messages per IP per hour
"""
from django.core.cache import cache

LOGIN_FAILURES = (5, 15 * 60)
PASSWORD_RESETS = (3, 60 * 60)
CONTACT_MESSAGES = (5, 60 * 60)


def client_ip(request):
    """The client's address.

    Behind the shipped nginx config, `X-Real-IP` is set by nginx from the
    socket address and cannot be forged by the client (unlike the first
    entry of `X-Forwarded-For`). Without nginx, REMOTE_ADDR is used.
    """
    return (
        request.META.get("HTTP_X_REAL_IP")
        or request.META.get("REMOTE_ADDR")
        or "unknown"
    ).strip()


def _key(scope, ident):
    return f"ratelimit:{scope}:{ident}"


def count(scope, ident):
    try:
        return int(cache.get(_key(scope, ident)) or 0)
    except (TypeError, ValueError):
        return 0


def is_limited(scope, ident, limit):
    return count(scope, ident) >= limit


def hit(scope, ident, window):
    """Record one event; return the new count."""
    key = _key(scope, ident)
    cache.add(key, 0, timeout=window)
    try:
        return cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout=window)
        return 1


def reset(scope, ident):
    cache.delete(_key(scope, ident))
