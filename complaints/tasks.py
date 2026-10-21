"""Celery task for the complaint SLA sweep (Master Plan §11:
``qa_core.complaints.sla_sweep`` every ``qa.complaints.sla_poll_minutes``).

Beat calls it every minute; the task itself honours the setting, so an admin
can change the interval without restarting Celery.
"""
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

LAST_RUN_KEY = "complaints:sla_sweep:last_run"


@shared_task(name="qa_core.complaints.sla_sweep")
def sla_sweep(force=False):
    from .services import _int, sla_sweep as run

    minutes = max(1, _int("qa.complaints.sla_poll_minutes", 5))
    now = timezone.now()
    last = cache.get(LAST_RUN_KEY)
    if not force and last is not None and (now - last).total_seconds() < minutes * 60 - 5:
        return {"skipped": True}
    cache.set(LAST_RUN_KEY, now, timeout=minutes * 120)
    return run(now)
