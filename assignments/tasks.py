"""Celery tasks of the grant engine."""
from celery import shared_task


@shared_task(name="assignments.tasks.sweep_grants")
def sweep_grants():
    """Hourly (nbte_ict/celery.py): extend, close, expire and report grants."""
    from .grants import sweep
    return sweep()


@shared_task(name="assignments.tasks.weekly_access_digest")
def weekly_access_digest():
    """Weekly (nbte_ict/celery.py): access digest to the Director of ICT (P.5)."""
    from .digest import send
    return send()
