"""Celery tasks for accreditation (Phase 8)."""
from celery import shared_task


@shared_task(name="accreditation.programme_expiry")
def programme_expiry():
    """Daily: expire programmes past their expiry date and send renewal reminders."""
    from . import expiry

    result = expiry.run()
    return {"checked": result["checked"], "expired": len(result["expired"]), "reminded": len(result["reminded"])}


@shared_task(name="accreditation.ssq_assignment_reminders")
def ssq_assignment_reminders():
    """v2.1 Part 2.2: remind officers of SSQ reviews not accepted, and of overdue reviews."""
    from .ssq_assign import send_reminders

    return send_reminders()


@shared_task(name="accreditation.onboarding_stage_reminders")
def onboarding_stage_reminders():
    """Linear onboarding: remind the holder of an overdue self-study; escalate to the director, then the ES."""
    from .onboarding_linear import send_stage_reminders

    return send_stage_reminders()
