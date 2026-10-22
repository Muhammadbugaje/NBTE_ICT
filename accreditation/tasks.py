"""Celery tasks for accreditation (Phase 8)."""
from celery import shared_task


@shared_task(name="accreditation.programme_expiry")
def programme_expiry():
    """Daily: expire programmes past their expiry date and send renewal reminders."""
    from . import expiry

    result = expiry.run()
    return {"checked": result["checked"], "expired": len(result["expired"]), "reminded": len(result["reminded"])}
