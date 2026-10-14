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
