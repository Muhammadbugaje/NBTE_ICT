"""Celery Beat tasks for attendance."""
import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)


@shared_task(name="attendance.tasks.auto_mark_absent")
def auto_mark_absent():
    """Fires once a day after the workday ends; marks everyone with no
    AttendanceRecord as absent. Idempotent — safe to re-run."""
    try:
        count = services.auto_mark_absent()
        logger.info("auto_mark_absent: marked %s record(s)", count)
        return count
    except Exception:
        logger.exception("auto_mark_absent failed")
        return 0


@shared_task(name="attendance.tasks.auto_mark_absent_backfill")
def auto_mark_absent_backfill(days_back=3):
    """Backfill absent records for the last N days. Use this manually if
    Beat missed a run — e.g. if the worker was down over a weekend."""
    try:
        count = services.auto_mark_absent(days_back=days_back)
        logger.info("auto_mark_absent_backfill: marked %s record(s)", count)
        return count
    except Exception:
        logger.exception("auto_mark_absent_backfill failed")
        return 0


@shared_task(name="attendance.tasks.weekly_summary")
def weekly_summary():
    """Fires Monday morning; emails every department head last week's stats."""
    try:
        count = services.send_weekly_summary()
        logger.info("weekly_summary: emailed %s head(s)", count)
        return count
    except Exception:
        logger.exception("weekly_summary failed")
        return 0