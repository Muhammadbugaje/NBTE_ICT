"""Fire-and-forget work that must never slow a page down (e-mail first of all).

``submit(fn, *args)`` runs ``fn`` on a small pool of background threads once
the surrounding database transaction has committed, so the user's action
finishes at full speed however slow (or broken) the mail provider is. A
failure is logged, never raised into the request.

When a real Celery worker is running, callers should keep using Celery; this
pool is for work that would otherwise run inline in the request (dev without
Redis, or code that never had a task). ``settings.EMAIL_DELIVERY_INLINE``
(on under pytest) runs the work immediately instead, so tests stay
deterministic.
"""
from __future__ import annotations

import atexit
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections, connection, transaction

logger = logging.getLogger("core.background")

_pool = None
_lock = threading.Lock()


def inline():
    return bool(getattr(settings, "EMAIL_DELIVERY_INLINE", False))


def _executor():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=int(getattr(settings, "BACKGROUND_WORKERS", 3)),
                                       thread_name_prefix="nbte-bg")
            atexit.register(_pool.shutdown, wait=False)
        return _pool


def _run(fn, args, kwargs, label):
    close_old_connections()
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.exception("Background job %s failed", label)
    finally:
        try:
            connection.close()  # this thread's own DB connection
        except Exception:
            pass


def submit(fn, *args, label="", **kwargs):
    """Run ``fn(*args, **kwargs)`` in the background after commit. Never raises."""
    label = label or getattr(fn, "__name__", "job")
    if inline():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("Background job %s failed (inline)", label)
        return

    def start():
        try:
            _executor().submit(_run, fn, args, kwargs, label)
        except Exception:  # pool shut down (server stopping): do it here rather than lose it
            _run(fn, args, kwargs, label)

    try:
        transaction.on_commit(start)
    except Exception:
        start()
