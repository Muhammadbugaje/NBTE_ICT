"""config.get(): cached reads, invalidated on write (doc 31 §4, doc 33 caching)."""
from django.conf import settings
from django.core.cache import cache

from .models import GLOBAL_SCOPE

# Object sentinel, not a string - so no real config value can ever collide with
# it. (The previous "____config_missing____" string sentinel was theoretically
# collidable if a config value ever equaled it.)
_MISSING = object()


def _cache_key(key, scope):
    return f"config:{scope}:{key}"


def get(key, default=None, scope=GLOBAL_SCOPE):
    """Typed value for `key`. If `scope` isn't global and has no row, falls back
    to the global row, then `default`. Cached with a short TTL; writes invalidate."""
    scopes = [scope] if scope == GLOBAL_SCOPE else [scope, GLOBAL_SCOPE]
    for s in scopes:
        value = _lookup(key, s)
        if value is not _MISSING:
            return value
    return default


def _lookup(key, scope):
    ck = _cache_key(key, scope)
    cached = cache.get(ck, None)
    if cached is not None:
        # Cache stores (found: bool, value). Found=False means "no row exists".
        # This is how falsy values (False, 0, "", []) cache correctly.
        found, value = cached
        return value if found else _MISSING

    from .models import ConfigEntry

    entry = ConfigEntry.objects.filter(key=key, scope=scope).first()
    if entry is None:
        cache.set(ck, (False, None), settings.CONFIG_CACHE_TTL)
        return _MISSING
    value = entry.parsed()
    cache.set(ck, (True, value), settings.CONFIG_CACHE_TTL)
    return value


def invalidate(key, scope=GLOBAL_SCOPE):
    cache.delete(_cache_key(key, scope))