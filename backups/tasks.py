"""Celery tasks for backups (registered in nbte_ict/celery.py's beat schedule)."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="backups.scheduled_check")
def scheduled_check():
    """Daily: take a backup when qa.backup.interval_days have passed since the
    last successful one (default 30 days, i.e. monthly)."""
    from . import engine
    from .models import BackupRun

    due, reason = engine.schedule_due()
    if not due:
        logger.info("backups: not due (%s)", reason)
        return reason
    try:
        run = engine.run_backup(trigger=BackupRun.SCHEDULE)
    except engine.AlreadyRunning as exc:
        return str(exc)
    return f"{run.label}: {run.status}"


@shared_task(name="backups.perform_run")
def perform_run(run_id):
    from . import engine

    return engine.perform_run(run_id).status


@shared_task(name="backups.perform_drill")
def perform_drill(drill_id):
    from . import engine

    return engine.perform_drill(drill_id).result
