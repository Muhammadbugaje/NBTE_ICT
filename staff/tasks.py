"""Celery tasks for staff (B.9 / D10)."""
from celery import shared_task


@shared_task(name="staff.tasks.promotion_overdue_notice")
def promotion_overdue_notice():
    """Weekly: notify HR and the ES of staff past their time in grade."""
    from .promotions import send_overdue_notice
    return send_overdue_notice()
