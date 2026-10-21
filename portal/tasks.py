"""Portal Celery tasks."""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="portal.expire_announcements")
def expire_announcements():
    from .models import Announcement
    now = timezone.now()
    count = Announcement.objects.filter(
        archived_at__isnull=True,
        is_draft=False,
        expires_at__lte=now,
    ).update(archived_at=now, auto_archived=True)
    if count:
        logger.info("Auto-archived %d expired announcements.", count)
    return count


@shared_task(name="portal.publish_due_announcements")
def publish_due_announcements():
    from .models import Announcement
    from .services import expand_reminder_recipients  # reuse later
    from notifications.models import Notification
    from notifications.services import notify
    from django.db.models import Q

    now = timezone.now()
    due = Announcement.objects.filter(
        is_draft=False, archived_at__isnull=True,
        publish_at__lte=now, notified_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

    for ann in due:
        try:
            _fanout_announcement(ann)
            ann.notified_at = now
            ann.save(update_fields=["notified_at"])
        except Exception:
            logger.exception("Announcement fanout failed for %s", ann.pk)


def _fanout_announcement(ann):
    """Create a Notification for every in-scope user. Best-effort."""
    from django.contrib.auth import get_user_model
    from notifications.models import Notification
    from notifications.services import notify

    User = get_user_model()
    qs = User.objects.filter(is_active=True)

    if ann.visibility == "public":
        return  # public doesn't notify anyone; it's just visible
    if ann.visibility == "staff":
        pass
    elif ann.visibility == "department" and ann.department_id:
        qs = qs.filter(department_id=ann.department_id)
    elif ann.visibility == "role":
        qs = qs.filter(role_id__in=ann.roles.values_list("pk", flat=True))  # review M6
    elif ann.visibility == "individual":
        qs = qs.filter(pk__in=ann.recipients.values_list("pk", flat=True))

    for u in qs.distinct():
        try:
            notify(
                recipient=u,
                title=ann.title,
                body=ann.card_summary,
                url=f"/portal/announcements/{ann.pk}/",
                category=Notification.SYSTEM,
                level=Notification.INFO,
                actor=ann.author,
            )
        except Exception:
            pass


@shared_task(name="portal.fire_due_reminders")
def fire_due_reminders():
    """Fire reminders whose remind_at has arrived (runs every 60 seconds from
    Celery Beat). The logic is `portal.services.fire_due_reminders(now=None)`."""
    from .services import fire_due_reminders as _fire

    total = _fire()
    if total:
        logger.info("Fired %d reminder deliveries.", total)
    return total
    
    
@shared_task(name="portal.document_review_reminders")
def document_review_reminders():
    """Nudge document owners N days before review_date."""
    from datetime import timedelta
    from django.db.models import Q
    from .models import Document
    from notifications.models import Notification
    from notifications.services import notify

    today = timezone.localdate()
    horizon = today + timedelta(days=30)  # slight overshoot — per-doc days filtered below

    candidates = Document.objects.filter(
        review_date__isnull=False,
        status=Document.STATUS_PUBLISHED,
        deleted_at__isnull=True,
    ).exclude(review_reminder_sent_at__gte=today - timedelta(days=1))

    fired = 0
    for doc in candidates:
        trigger_date = doc.review_date - timedelta(days=doc.review_reminder_days or 14)
        if trigger_date > today:
            continue
        if doc.review_reminder_sent_at and doc.review_reminder_sent_at.date() >= trigger_date:
            continue
        try:
            notify(
                recipient=doc.owner,
                title=f"Review due: {doc.title}",
                body=f"Scheduled for review on {doc.review_date.strftime('%b %d, %Y')}.",
                url=reverse("portal:document_detail", kwargs={"pk": doc.pk}),
                category=Notification.SYSTEM,
                level=Notification.WARNING,
            )
            doc.review_reminder_sent_at = timezone.now()
            doc.save(update_fields=["review_reminder_sent_at"])
            fired += 1
        except Exception:
            logger.exception("Review reminder failed for doc %s", doc.pk)
    return fired    
    
    
    
    