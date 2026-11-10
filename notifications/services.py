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
        notification = Notification.objects.create(
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
    _push([notification])
    return notification


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
        created = Notification.objects.bulk_create(rows)
    except Exception:
        logger.exception("Failed to bulk-create %d notifications", len(rows))
        return []
    _forget_badges(seen)
    _push(created)
    return created


def _forget_badges(user_ids):
    """v2.1 L9: drop the 5-second unread-count cache of these people."""
    try:
        from messaging.live import forget_notification_unread

        forget_notification_unread(*user_ids)
    except Exception:  # pragma: no cover
        pass


def _push(notifications):
    """Phase 9: push to the recipients' app devices (notifications.push; a
    no-op unless FCM is configured). Never raises into the caller."""
    try:
        from . import push

        push.queue(notifications)
    except Exception:
        logger.exception("Failed to queue push notifications")

# ---------------------------------------------------------------------------
# Bulk actions (Phase 8; shared by the website's bulk views and the Phase 9
# mobile API)
# ---------------------------------------------------------------------------
BULK_ACTIONS = ("delete", "mark_read", "mark_unread")
NOT_YOURS = "Some items are not yours and were left alone."


def bulk_apply(user, action, ids, actor=None):
    """Apply ``action`` (delete | mark_read | mark_unread) to the ids that are
    ``user``'s OWN notifications; others' ids are skipped.

    Returns {"done": [ids], "skipped": [{"id", "reason"}], "verb": "deleted"}.
    Raises ValueError for an unknown action. Writes the audit entry.
    """
    from django.utils import timezone

    from core import audit

    if action not in BULK_ACTIONS:
        raise ValueError(f"Unknown action {action!r}")
    mine = Notification.objects.filter(recipient=user)
    own = mine.filter(pk__in=ids)
    own_ids = set(own.values_list("pk", flat=True))
    skipped = [{"id": i, "reason": NOT_YOURS} for i in ids if i not in own_ids]
    done = [i for i in ids if i in own_ids]
    if action == "delete":
        own.delete()
        verb = "deleted"
    elif action == "mark_read":
        own.filter(is_read=False).update(is_read=True, read_at=timezone.now())
        verb = "marked as read"
    else:
        own.filter(is_read=True).update(is_read=False, read_at=None)
        verb = "marked as unread"
    _forget_badges([user.pk])
    if done:
        audit.log("delete" if action == "delete" else "update", user, actor=actor or user,
                  note=f"notifications {verb}: {len(done)}", changes={"ids": done[:100]})
    return {"done": done, "skipped": skipped, "verb": verb}


def mark_all_read(user):
    """Mark every unread notification of ``user`` read; returns how many."""
    from django.utils import timezone

    count = Notification.objects.filter(recipient=user, is_read=False).update(is_read=True, read_at=timezone.now())
    _forget_badges([user.pk])
    return count
