"""Backward-compatible entry point (same name/signature as NAMETS'
send_templated_email). Now queues through the notifications app: the request
returns immediately and Celery delivers, with Brevo->Gmail failover."""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def send_templated_email(subject, recipients, template_name, context=None, category="general"):
    from notifications.api import queue_email

    ctx = {"site_name": settings.SITE_NAME, "site_url": getattr(settings, "SITE_URL", "")}
    ctx.update(context or {})
    try:
        return queue_email(subject, recipients, template_name, ctx, category=category)
    except Exception:  # a mail problem must never break the admin action
        logger.exception("Could not queue email: %s", subject)
        return None
