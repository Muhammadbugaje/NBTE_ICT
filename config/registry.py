"""config.get(): cached reads, invalidated on write (doc 31 §4, doc 33 caching).

v2.3 Phase 1: one query per scope instead of one query per key.
A page reads dozens of settings; on a cold cache every key used to cost its own
query (61 on the QA home page). Now the first miss loads every row of that
scope in one query. Each scope has a small version token in the shared cache
(``config:ver:<scope>``); every process keeps its own parsed copy of the scope
and reuses it while the token is unchanged, so a read costs one tiny cache GET
(as before) instead of a query. Any save or delete of a ConfigEntry, or a
cache.clear(), changes the token, and every process reloads on its next read.

Raw writes that skip signals (bulk_create, queryset.update(), a Data Hub or
restore job) are picked up within LOCAL_MAX_AGE_SECONDS. A key that is not in
the scope is looked up on its own once and the answer cached per key, exactly
as before, so a row created behind our back is still found.
"""
import threading
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from .models import GLOBAL_SCOPE

# Object sentinel, not a string - so no real config value can ever collide with it.
_MISSING = object()

# Process-local copy of each scope: {scope: (token, loaded_at, {key: ConfigEntry})}.
LOCAL_MAX_AGE_SECONDS = 60
_LOCAL = {}
_LOCK = threading.Lock()


def _cache_key(key, scope):
    """Kept for compatibility (the old per-key entries are still deleted on write)."""
    return f"config:{scope}:{key}"


def _ver_key(scope):
    return f"config:ver:{scope}"


def get(key, default=None, scope=GLOBAL_SCOPE):
    """Typed value for `key`. If `scope` isn't global and has no row, falls back
    to the global row, then `default`. Cached; writes invalidate."""
    scopes = [scope] if scope == GLOBAL_SCOPE else [scope, GLOBAL_SCOPE]
    for s in scopes:
        value = _lookup(key, s)
        if value is not _MISSING:
            return value
    return default


def _token(scope):
    try:
        token = cache.get(_ver_key(scope))
        if token is None:
            token = uuid.uuid4().hex
            # add(): if another process set one first, use theirs.
            cache.add(_ver_key(scope), token, settings.CONFIG_CACHE_TTL * 12)
            token = cache.get(_ver_key(scope)) or token
        return token
    except Exception:  # noqa: BLE001 - a cache outage must not break settings
        return None


def _scope_map(scope):
    token = _token(scope)
    now = time.monotonic()
    hit = _LOCAL.get(scope)
    if token is not None and hit is not None and hit[0] == token and now - hit[1] < LOCAL_MAX_AGE_SECONDS:
        return hit[2]
    from .models import ConfigEntry

    entries = {entry.key: entry for entry in ConfigEntry.objects.filter(scope=scope)}
    if token is not None:
        with _LOCK:
            _LOCAL[scope] = (token, now, entries)
    return entries


def _lookup(key, scope):
    entry = _scope_map(scope).get(key)
    if entry is not None:
        # parsed() builds a fresh value each time, so a caller that mutates a
        # list/dict setting never changes the cached copy.
        return entry.parsed()
    return _lookup_single(key, scope)


def _lookup_single(key, scope):
    """A key the scope map does not hold: one query, cached per key (the pre-v2.3 path)."""
    ck = _cache_key(key, scope)
    try:
        cached = cache.get(ck, None)
    except Exception:  # noqa: BLE001
        cached = None
    if cached is not None:
        found, value = cached
        return value if found else _MISSING
    from .models import ConfigEntry

    row = ConfigEntry.objects.filter(key=key, scope=scope).first()
    result = (False, None) if row is None else (True, row.parsed())
    try:
        cache.set(ck, result, settings.CONFIG_CACHE_TTL)
    except Exception:  # noqa: BLE001
        pass
    return result[1] if result[0] else _MISSING


def _drop(scope):
    with _LOCK:
        _LOCAL.pop(scope, None)
    try:
        cache.delete(_ver_key(scope))
    except Exception:  # noqa: BLE001
        pass


def invalidate(key, scope=GLOBAL_SCOPE):
    try:
        cache.delete(_cache_key(key, scope))
    except Exception:  # noqa: BLE001
        pass
    _drop(scope)
    # A request reading between this save and the commit would load the old
    # row again; change the token once more when the transaction commits.
    transaction.on_commit(lambda: _drop(scope))


def clear_local():
    """Forget the process-local copies (tests, and after a restore)."""
    with _LOCK:
        _LOCAL.clear()
