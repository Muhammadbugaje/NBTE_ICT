"""Shared caching helpers.

Design:
  - Loose  — every cached value has a TTL. If a write is missed, it heals.
  - Epoch  — a monotonically increasing integer per namespace. Keys embed
             the epoch, so bumping it silently orphans the old keys. No
             `delete_pattern` needed, so it works on LocMem *and* Redis.

Both backends produce identical observable behaviour. Nothing here reads
Redis-specific APIs.
"""
from __future__ import annotations

from functools import wraps

from django.core.cache import cache
from django.utils.encoding import force_str

# ---------------------------------------------------------------------------
# Default TTLs (seconds)
# ---------------------------------------------------------------------------
TTL_SHORT = 30          # unread counts, sidebar badges
TTL_MEDIUM = 120        # home KPI tiles, module cards
TTL_LONG = 300          # dashboard contexts
TTL_VERY_LONG = 900     # config registry reads, public content (site info)

# Sentinel so we don't confuse a real `None` with a cache miss.
_MISS = object()


# ---------------------------------------------------------------------------
# Epoch machinery
# ---------------------------------------------------------------------------
def _epoch_key(namespace: str) -> str:
    return f"epoch:{namespace}"


def epoch(namespace: str) -> int:
    """Current epoch for `namespace`. Creates one lazily on first use."""
    key = _epoch_key(namespace)
    value = cache.get(key)
    if value is None:
        # add() is atomic — if two workers race, only one wins.
        cache.add(key, 1, timeout=None)
        value = cache.get(key) or 1
    try:
        return int(value)
    except (TypeError, ValueError):
        cache.set(key, 1, timeout=None)
        return 1


def bump_epoch(namespace: str) -> int:
    """Increment the epoch — every old key becomes instantly unreachable."""
    key = _epoch_key(namespace)
    try:
        return cache.incr(key)
    except ValueError:
        # Key doesn't exist yet (or was evicted) — start at 2 so any old
        # cached value with epoch=1 is already orphaned.
        cache.set(key, 2, timeout=None)
        return 2


# ---------------------------------------------------------------------------
# Key building
# ---------------------------------------------------------------------------
def make_key(namespace: str, *parts, user=None) -> str:
    """Build a versioned cache key.

    Every part is force_str()'d and joined with `:` so the key is stable
    and never depends on Python object identity.
    """
    bits = [namespace, f"v{epoch(namespace)}"]
    if user is not None:
        uid = getattr(user, "pk", user)
        bits.append(f"u{uid}")
    for p in parts:
        if p is None:
            continue
        bits.append(force_str(p))
    return ":".join(bits)


# ---------------------------------------------------------------------------
# get_or_set with sentinel + epoch
# ---------------------------------------------------------------------------
def get_or_set_context(namespace: str, key_parts, builder, *, ttl, user=None):
    """Return cached value if present; otherwise run `builder()` and cache it.

    `builder` is only called on a miss. Exceptions inside `builder` are
    NOT swallowed here — the caller decides whether to catch them.
    """
    key = make_key(namespace, *key_parts, user=user)
    value = cache.get(key, _MISS)
    if value is not _MISS:
        return value
    value = builder()
    try:
        cache.set(key, value, timeout=ttl)
    except Exception:
        # Caching is best-effort — a serialization failure must never
        # break the request.
        pass
    return value


def invalidate(namespace: str) -> int:
    """Public alias for `bump_epoch`. Use this from signals."""
    return bump_epoch(namespace)


# ---------------------------------------------------------------------------
# Decorator — for whole-view caching, optional
# ---------------------------------------------------------------------------
def cached_context(namespace: str, *, ttl, key="default",
                   key_from=None, user_arg="user"):
    """Decorator for functions returning a serialisable dict.

    Example:
        @cached_context("dash", ttl=300, key_from=lambda user: [user.role.code])
        def build_dashboard_context(user, **kw): ...
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = kwargs.get(user_arg) or (args[0] if args else None)
            parts = []
            if key_from is not None:
                try:
                    parts = list(key_from(user, *args, **kwargs) or [])
                except Exception:
                    parts = []
            else:
                parts = [key]
            return get_or_set_context(
                namespace, parts,
                lambda: fn(*args, **kwargs),
                ttl=ttl, user=user,
            )
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Convenience: safe get-or-set with a fallback on builder failure
# ---------------------------------------------------------------------------
def safe_context(namespace, key_parts, builder, *, ttl, user=None, default=None):
    """Like get_or_set_context but swallows builder exceptions and returns
    `default`. Used by views that must never 500 on a cache-adjacent path."""
    try:
        return get_or_set_context(namespace, key_parts, builder, ttl=ttl, user=user)
    except Exception:
        return default if default is not None else {}