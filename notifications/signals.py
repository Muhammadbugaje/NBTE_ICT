"""Wire notifications into events that happen elsewhere.

Kept here (in the notifications app) so the emitting app doesn't need to know
about the notifications API. Every hook is defensive: a failure to send a
notification must never break the action that triggered it.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification
from .services import notify

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Staff state change → notify the affected staff member
# ---------------------------------------------------------------------------
@receiver(post_save, sender="staff.StaffStatusHistory")
def staff_state_changed(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        staff = instance.staff
        if not staff.user_id:
            return
        new_state = instance.to_state.name if instance.to_state_id else "—"
        old_state = instance.from_state.name if instance.from_state_id else "—"
        notify(
            recipient=staff.user,
            title=f"Your state changed to {new_state}",
            body=f"Previously: {old_state}." + (f" Reason: {instance.reason}" if instance.reason else ""),
            url="",  # staff don't have a self-service page yet
            category=Notification.STAFF,
            level=Notification.INFO,
            actor=instance.changed_by,
            metadata={"staff_id": staff.pk, "from": old_state, "to": new_state},
        )
    except Exception:
        logger.exception("staff_state_changed notification failed")


# ---------------------------------------------------------------------------
# New message → notify every other participant
# ---------------------------------------------------------------------------
@receiver(post_save, sender="messaging.Message")
def message_posted(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.is_system:
        return
    try:
        thread = instance.thread
        sender_user = instance.sender
        recipients = [
            p.user for p in thread.participants.select_related("user")
            if p.user_id != (sender_user.pk if sender_user else None)
        ]
        if not recipients:
            return
        preview = (instance.body or "").strip().replace("\n", " ")
        if len(preview) > 90:
            preview = preview[:87] + "…"
        title = f"New message from {sender_user.get_full_name() or sender_user.email}" \
            if sender_user else "New message"
        notify_many(
            recipients,
            title=title,
            body=preview,
            url=f"/messages/{thread.pk}/",
            category=Notification.MESSAGE,
            level=Notification.INFO,
            actor=sender_user,
            metadata={"thread_id": thread.pk, "message_id": instance.pk},
        )
    except Exception:
        logger.exception("message_posted notification failed")


# Import `notify_many` here so it's available; the receiver above uses it.
from .services import notify_many  # noqa: E402