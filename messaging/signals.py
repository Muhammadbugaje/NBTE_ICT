"""Signal handlers for the messaging app."""
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import (
    MemoAttachment,
    Message,
    MessageAttachment,
    ThreadParticipant,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Message)
def restore_hidden_thread_for_recipients(sender, instance, created, **kwargs):
    if not created:
        return
    ThreadParticipant.objects.filter(
        thread=instance.thread,
    ).exclude(user_id=instance.sender_id).update(is_deleted=False)


@receiver(post_delete, sender=MessageAttachment)
def delete_message_attachment_file(sender, instance, **kwargs):
    if not instance.file:
        return
    try:
        instance.file.storage.delete(instance.file.name)
    except Exception:
        logger.exception("Could not delete message attachment %s", instance.pk)


@receiver(post_delete, sender=MemoAttachment)
def delete_memo_attachment_file(sender, instance, **kwargs):
    if not instance.file:
        return
    try:
        instance.file.storage.delete(instance.file.name)
    except Exception:
        logger.exception("Could not delete memo attachment %s", instance.pk)


# ---------------------------------------------------------------------------
# Memo routing: when the people a step resolves to, or the route itself,
# change, re-check memos that are waiting with nobody able to act (a new
# director, a department head set, a step made optional or deleted ...).
# ---------------------------------------------------------------------------
def _memo_people_or_route_changed(sender, instance=None, raw=False, **kwargs):
    if raw:
        return
    from . import routing
    routing.schedule_reconcile()


def _connect_memo_reconcile():
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_delete, post_save

    from staff.models import Department, Office, Staff

    from .models import MemoRoute, MemoRouteRule, MemoRouteStep
    for model in (MemoRoute, MemoRouteStep, MemoRouteRule, get_user_model(), Department, Office, Staff):
        uid = f"memo-reconcile-{model._meta.label}"
        post_save.connect(_memo_people_or_route_changed, sender=model, dispatch_uid=uid + "-save", weak=False)
        post_delete.connect(_memo_people_or_route_changed, sender=model, dispatch_uid=uid + "-del", weak=False)


_connect_memo_reconcile()
