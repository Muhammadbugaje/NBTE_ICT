"""Tickets Celery tasks."""
import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)


@shared_task(name="tickets.tasks.sla_check")
def sla_check():
    """Periodic SLA sweep. Safe to run frequently."""
    return services.sla_check_task()