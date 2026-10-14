"""Notification service — the one function other apps call.

    from notifications.services import notify, notify_many

    notify(user, title="Ticket assigned to you",
           body="Ticket #42 — Printer not working",
           url="/tickets/42/",
           category=Notification.TICKET,
           level=Notification.INFO,
           actor=request.user)

Never raises into the caller: a failed notification should never break the
action that triggered it, so every public function swallows exceptions and
logs them.
"""
import logging

from .models import Notification

logger = logging.getLogger(__name__)


def notify(recipient, *, title, body="", url="", level=Notification.INFO,
           category=Notification.SYSTEM, actor=None, metadata=None):
    """Create one notification. Returns the Notification, or None on failure."""
    if recipient is None or not getattr(recipient, "pk", None):
        return None
    if actor is not None and not getattr(actor, "pk", None):
        actor = None
    try:
        return Notification.objects.create(
            recipient=recipient,
            actor=actor,
            title=str(title)[:255],
            body=str(body or ""),
            url=str(url or "")[:500],
            level=level,
            category=category,
            metadata=metadata or {},
        )
    except Exception:
        logger.exception("Failed to create notification for user %s", getattr(recipient, "pk", None))
        return None


def notify_many(recipients, **kwargs):
    """Bulk-create the same notification for many users.

    Recipients is any iterable of User objects. Skips duplicates by pk and
    anyone without a pk (unsaved). Returns the list of created Notifications.
    """
    seen, rows = set(), []
    for user in recipients or []:
        if user is None or not getattr(user, "pk", None):
            continue
        if user.pk in seen:
            continue
        seen.add(user.pk)
        rows.append(Notification(
            recipient=user,
            actor=kwargs.get("actor") or None,
            title=str(kwargs.get("title", ""))[:255],
            body=str(kwargs.get("body", "") or ""),
            url=str(kwargs.get("url", "") or "")[:500],
            level=kwargs.get("level", Notification.INFO),
            category=kwargs.get("category", Notification.SYSTEM),
            metadata=kwargs.get("metadata") or {},
        ))
    if not rows:
        return []
    try:
        return Notification.objects.bulk_create(rows)
    except Exception:
        logger.exception("Failed to bulk-create %d notifications", len(rows))
        return []