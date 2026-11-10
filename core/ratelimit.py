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
    """The client's address, from the ONE header the proxy in front of us sets.

    ``settings.CLIENT_IP_HEADER`` names that header's META key:

    * ``HTTP_X_REAL_IP`` (default) — the shipped nginx config sets it from the
      socket address, so the client cannot forge it.
    * ``HTTP_CF_CONNECTING_IP`` — Render (Cloudflare in front); chosen
      automatically in production when Render's ``RENDER`` variable is set.
    * ``""`` — no proxy: the socket address only.

    v2.2 (security): any other client-supplied header (X-Forwarded-For,
    X-Real-IP without nginx) is ignored, and a value that is not an IP
    address falls back to the socket address, so rate limits, the admin IP
    guard and the Remita webhook check cannot be dodged with a fake header.
    """
    import ipaddress

    from django.conf import settings

    header = getattr(settings, "CLIENT_IP_HEADER", "HTTP_X_REAL_IP")
    socket_ip = (request.META.get("REMOTE_ADDR") or "").strip()
    raw = (request.META.get(header) or "").strip() if header else ""
    raw = raw.split(",")[0].strip()
    for candidate in (raw, socket_ip):
        if not candidate:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return "unknown"


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
