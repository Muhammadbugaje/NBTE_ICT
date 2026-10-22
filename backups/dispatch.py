"""Run long jobs off the request: a Celery worker when one is configured,
otherwise a background thread (development, where Celery runs eagerly)."""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections, connection

logger = logging.getLogger(__name__)


def _in_thread(func, *args):
    def target():
        close_old_connections()
        try:
            func(*args)
        except Exception:
            logger.exception("backups: background job failed")
        finally:
            connection.close()

    thread = threading.Thread(target=target, name=f"backups-{func.__name__}", daemon=True)
    thread.start()
    return thread


def background(task, *args):
    """`task` is a Celery task from backups.tasks."""
    if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        try:
            task.apply_async(args=args, retry=False)
            return "celery"
        except Exception:
            logger.warning("backups: Celery broker unavailable; running %s in a thread", task.name)
    _in_thread(task.run, *args)
    return "thread"
