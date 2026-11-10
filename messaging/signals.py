"""Signal handlers for the messaging app."""
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import (
    MemoAttachment,
    Message,
    MessageAttachment,
    ThreadParticipant,
    VoiceNote,
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


@receiver(post_delete, sender=VoiceNote)
def delete_voice_note_file(sender, instance, **kwargs):  # Phase 9
    if not instance.file:
        return
    try:
        instance.file.storage.delete(instance.file.name)
    except Exception:
        logger.exception("Could not delete voice note %s", instance.pk)


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


# ---------------------------------------------------------------------------
# v2.1 Phase E (L9): replace the poll version tokens after every write that
# changes what a chat poll returns (messaging/live.py). Runs on commit.
# ---------------------------------------------------------------------------
def _connect_live_bumps():
    from . import live
    from .models import (
        ChatPollVote, MessageDeletion, MessageReaction, MessageStar, Thread, VoiceNotePlay,
    )

    def message_saved(sender, instance, raw=False, **kwargs):
        if not raw:
            live.bump_thread_and_members(instance.thread_id)

    def thread_saved(sender, instance, raw=False, **kwargs):
        if not raw:
            live.bump_thread_and_members(instance.pk)

    def participant_saved(sender, instance, created=False, raw=False, **kwargs):
        if raw:
            return
        if created:
            live.bump_thread_and_members(instance.thread_id)
        else:
            live.bump_inbox(instance.user_id)

    def participant_deleted(sender, instance, **kwargs):
        live.bump_thread(instance.thread_id,
                         inbox_user_ids=live.participant_user_ids(instance.thread_id) + [instance.user_id])

    def by_message(sender, instance, raw=False, **kwargs):
        if raw:
            return
        try:
            if getattr(instance, "poll_id", None):
                thread_id = instance.poll.message.thread_id
            elif getattr(instance, "voice_note_id", None):
                thread_id = instance.voice_note.message.thread_id
            else:
                thread_id = instance.message.thread_id
        except Exception:  # parent already gone (cascade delete)
            return
        live.bump_thread(thread_id)

    def hidden_for_me(sender, instance, raw=False, **kwargs):
        if not raw:
            live.bump_thread(instance.message.thread_id, inbox_user_ids=[instance.user_id])

    post_save.connect(message_saved, sender=Message, dispatch_uid="live-message", weak=False)
    post_save.connect(thread_saved, sender=Thread, dispatch_uid="live-thread", weak=False)
    post_save.connect(participant_saved, sender=ThreadParticipant, dispatch_uid="live-part-save", weak=False)
    post_delete.connect(participant_deleted, sender=ThreadParticipant, dispatch_uid="live-part-del", weak=False)
    for model in (MessageReaction, ChatPollVote, MessageStar, VoiceNotePlay):
        uid = f"live-{model._meta.model_name}"
        post_save.connect(by_message, sender=model, dispatch_uid=uid + "-save", weak=False)
        post_delete.connect(by_message, sender=model, dispatch_uid=uid + "-del", weak=False)
    post_save.connect(hidden_for_me, sender=MessageDeletion, dispatch_uid="live-hidden", weak=False)

    from notifications.models import Notification

    def notification_saved(sender, instance, raw=False, **kwargs):
        if not raw:
            live.forget_notification_unread(instance.recipient_id)

    post_save.connect(notification_saved, sender=Notification, dispatch_uid="live-notif-save", weak=False)
    post_delete.connect(notification_saved, sender=Notification, dispatch_uid="live-notif-del", weak=False)


_connect_live_bumps()
