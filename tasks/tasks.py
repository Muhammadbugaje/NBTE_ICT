"""Celery task wrappers for the tasks app."""
import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)


@shared_task(name="tasks.tasks.reminder_sweep")
def reminder_sweep():
    return services.reminder_sweep()


@shared_task(name="tasks.tasks.spawn_recurring")
def spawn_recurring():
    return services.spawn_recurring()


@shared_task(name="tasks.tasks.escalate_overdue")
def escalate_overdue():
    return services.escalate_overdue_priorities()