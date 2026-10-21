"""Celery tasks for QA payments (§10.6): slip expiry, the Finance
confirmation window, SLA reminders and provider status polls. The sweep never
confirms a payment — it only reports, reminds and flags."""
from celery import shared_task


@shared_task(name="payments.sweep")
def payments_sweep():
    from . import services

    return services.sweep()
