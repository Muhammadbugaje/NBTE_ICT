"""What other apps call. Never sends in the request/response cycle (doc 31 §6)."""
import logging
import math
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

import config

from .models import EmailLog, EmailSendSlot
from .tasks import send_email_task

logger = logging.getLogger(__name__)


def _next_slot():
    """Reserve the next send time. Every email waits `email.initial_delay_seconds`
    and consecutive emails are at least `email.min_gap_seconds` apart, so bursts
    are trickled out instead of fired together (spam-filter friendly).
    Returns (planned_datetime, countdown_seconds)."""
    initial = float(config.get("email.initial_delay_seconds", 10))
    gap = float(config.get("email.min_gap_seconds", 5))
    with transaction.atomic():
        slot, _ = EmailSendSlot.objects.select_for_update().get_or_create(pk=1)
        now = timezone.now()
        start = max(now + timedelta(seconds=initial), slot.next_at or now)
        slot.next_at = start + timedelta(seconds=gap)
        slot.save(update_fields=["next_at"])
    # Round UP: truncating could shave up to a second off the gap between sends.
    return start, max(0, math.ceil((start - now).total_seconds()))


def queue_email(subject, recipients, template_name, context=None, category="general"):
    html = render_to_string(template_name, context or {})
    recipients = list(recipients)
    text = strip_tags(html)
    user = None
    if len(recipients) == 1:
        user = get_user_model().objects.filter(email__iexact=recipients[0]).first()
    planned, countdown = _next_slot()
    log = EmailLog.objects.create(
        subject=subject[:255], recipients=recipients, recipient_text=", ".join(recipients),
        category=category, user=user, scheduled_for=planned,
        body_html="" if category in EmailLog.sensitive_categories() else html,
    )
    logger.info(
        "Email queued log_id=%s category=%s recipient_count=%s scheduled_for=%s",
        log.pk, category, len(recipients), planned.isoformat(),
    )
    # Only enqueue once the surrounding transaction (if any) has committed.
    transaction.on_commit(lambda: send_email_task.apply_async(
        args=(log.pk, subject, recipients, html, text), countdown=countdown))
    return log