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