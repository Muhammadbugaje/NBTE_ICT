"""v2.2: a Redis cache that degrades instead of taking the site down.

Django's RedisCache raises on every call when Redis is unreachable or full,
which turns a cache outage into a full site outage (every page reads the
cache). ``ResilientRedisCache`` keeps the stock backend but:

* fails fast: short connect / read timeouts (OPTIONS in settings.prod);
* on a connection or server error, reads behave as a miss, writes are
  skipped and ``add`` returns False (so a lock is NOT taken — callers that
  use ``cache.add`` as a lock stay safe), and the error is logged once per
  minute instead of on every call;
* ``incr`` (rate-limit counters) re-raises ValueError as Django expects for a
  missing key, and otherwise returns 1 so a limit check keeps working on the
  one request.
"""
import logging
import time

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger("core.cache")

_last_warning = [0.0]


def _redis_errors():
    try:
        import redis

        return (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError,
                redis.exceptions.ResponseError, redis.exceptions.BusyLoadingError, OSError)
    except ImportError:  # pragma: no cover
        return (OSError,)


ERRORS = _redis_errors()


def _warn(op, exc):
    now = time.monotonic()
    if now - _last_warning[0] > 60:
        _last_warning[0] = now
        logger.error("Redis cache unavailable during %s (%s); serving without cache.", op, exc.__class__.__name__)


class ResilientRedisCache(RedisCache):
    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except ERRORS as exc:
            _warn("get", exc)
            return default

    def get_many(self, keys, version=None):
        try:
            return super().get_many(keys, version)
        except ERRORS as exc:
            _warn("get_many", exc)
            return {}

    def has_key(self, key, version=None):
        try:
            return super().has_key(key, version)
        except ERRORS as exc:
            _warn("has_key", exc)
            return False

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None, **kwargs):
        try:
            return super().set(key, value, timeout, version)
        except ERRORS as exc:
            _warn("set", exc)
            return None

    def set_many(self, data, timeout=DEFAULT_TIMEOUT, version=None, **kwargs):
        try:
            return super().set_many(data, timeout, version)
        except ERRORS as exc:
            _warn("set_many", exc)
            return list(data)

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None, **kwargs):
        try:
            return super().add(key, value, timeout, version)
        except ERRORS as exc:
            _warn("add", exc)
            return False

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().touch(key, timeout, version)
        except ERRORS as exc:
            _warn("touch", exc)
            return False

    def delete(self, key, version=None):
        try:
            return super().delete(key, version)
        except ERRORS as exc:
            _warn("delete", exc)
            return False

    def delete_many(self, keys, version=None):
        try:
            return super().delete_many(keys, version)
        except ERRORS as exc:
            _warn("delete_many", exc)
            return None

    def incr(self, key, delta=1, version=None):
        try:
            return super().incr(key, delta, version)
        except ERRORS as exc:
            _warn("incr", exc)
            return delta

    def get_or_set(self, key, default, timeout=DEFAULT_TIMEOUT, version=None):
        value = self.get(key, None, version)
        if value is None:
            value = default() if callable(default) else default
            if value is not None:
                self.set(key, value, timeout, version)
        return value
