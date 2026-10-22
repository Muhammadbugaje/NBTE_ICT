from celery import shared_task
from django.utils import timezone

from .models import MessageAttachment


@shared_task(name="messaging.delete_expired_attachments")
def delete_expired_attachments():
    expired = MessageAttachment.objects.filter(expires_at__lte=timezone.now())
    count = 0
    for attachment in expired.iterator():
        attachment.hard_delete()
        count += 1
    return count


# Phase 9: voice-note transcription (queued with transaction.on_commit after
# upload; runs inline when CELERY_TASK_ALWAYS_EAGER is on, e.g. in dev).
@shared_task(name="messaging.transcribe_voice_note")
def transcribe_voice_note(voice_note_id):
    from . import transcription
    from .models import VoiceNote

    voice = VoiceNote.objects.select_related("message__sender").filter(pk=voice_note_id).first()
    if voice is None:
        return None
    transcription.transcribe(voice)
    return voice.transcript_status
