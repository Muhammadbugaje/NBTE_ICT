"""Internal messaging (doc 30 §19.8). Non-real-time inbox — direct and group
threads. Every routed action elsewhere in the app can drop a message here in
addition to firing email, so outcomes stay visible in-system.

Design notes:
- `ThreadParticipant.last_read_at` drives unread counts per user, per thread.
- `ThreadParticipant.is_archived` archives per-participant (my archive ≠ yours).
- `Message.is_system` marks auto-generated notices (e.g. "Asset assigned").
- No hard deletes; threads and messages are preserved for audit.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.restricted import RestrictedFileModel


class Thread(TimeStampedModel):
    AUDITED = True

    DIRECT, GROUP = "direct", "group"
    TYPE_CHOICES = [(DIRECT, "Direct"), (GROUP, "Group")]

    subject = models.CharField(max_length=255, blank=True)
    thread_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=GROUP)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="threads_created",
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-last_message_at", "-updated_at"]

    def __str__(self):
        if self.subject:
            return self.subject
        participants = ", ".join(p.user.get_full_name() or p.user.email for p in self.participants.all()[:3])
        return participants or f"Thread #{self.pk}"

    def display_title_for(self, user):
        """Human-friendly title: subject if set, else the OTHER participant's name."""
        if self.subject:
            return self.subject
        others = [p.user for p in self.participants.select_related("user") if p.user_id != user.pk]
        if not others:
            return "Self thread"
        names = ", ".join((u.get_full_name() or u.email) for u in others[:3])
        if len(others) > 3:
            names += f" +{len(others) - 3}"
        return names

    def display_title(self):
        """Template-safe title fallback.

        Views that know the request user should shadow this per-instance:
            t.display_title = t.display_title_for(request.user)

        If they don't, this fallback still produces something usable —
        the subject if set, else a slice of participant names.
        """
        if self.subject:
            return self.subject
        names = []
        for p in self.participants.all()[:3]:
            try:
                names.append(p.user.get_full_name() or p.user.email)
            except Exception:
                continue
        if not names:
            return f"Thread #{self.pk}"
        more = self.participants.count() - len(names)
        return ", ".join(names) + (f" +{more}" if more > 0 else "")

    def unread_count_for(self, user):
        participant = self.participants.filter(user=user).first()
        if not participant:
            return 0
        qs = self.messages.exclude(sender=user).filter(is_deleted_for_everyone=False)
        qs = qs.exclude(hidden_for__user=user).distinct()
        if participant.last_read_at:
            qs = qs.filter(sent_at__gt=participant.last_read_at)
        return qs.count()

    def last_message(self, user=None):
        qs = self.messages.filter(is_deleted_for_everyone=False)
        if user is not None:
            qs = qs.exclude(hidden_for__user=user).distinct()
        return qs.order_by("-sent_at", "-id").first()


class ThreadParticipant(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="thread_participations")
    joined_at = models.DateTimeField(default=timezone.now)
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = ("thread", "user")
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.user} in {self.thread_id}"


class Message(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="messages_sent",
    )
    body = models.TextField()
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_system = models.BooleanField(default=False, help_text="Auto-generated (not typed by a human).")
    reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="replies",
    )
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted_for_everyone = models.BooleanField(default=False)

    class Meta:
        ordering = ["sent_at", "id"]

    def __str__(self):
        return f"[{self.thread_id}] {self.sender}: {self.body[:40]}"


class MessageDeletion(models.Model):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="hidden_for",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="hidden_messages",
    )
    deleted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="messaging_unique_message_hidden_per_user",
            ),
        ]


class MessageRead(models.Model):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="read_receipts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="message_read_receipts",
    )
    read_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="messaging_unique_message_read_per_user",
            ),
        ]


def attachment_expiry():
    from datetime import timedelta

    return timezone.now() + timedelta(days=30)


class MessageAttachment(RestrictedFileModel):
    MEDIA_SUBDIR = "messaging/attachments"

    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, related_name="attachments",
    )
    expires_at = models.DateTimeField(default=attachment_expiry, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def can_download(self, user):
        if (
            not user.is_authenticated
            or self.is_deleted
            or self.expires_at <= timezone.now()
            or self.message.is_deleted_for_everyone
            or self.message.hidden_for.filter(user=user).exists()
        ):
            return False
        return self.message.thread.participants.filter(
            user=user, is_deleted=False,
        ).exists()


def start_thread(*, creator, participants, subject="", initial_body="", thread_type=None):
    """Convenience used by other modules (or a view) to open a thread and post
    the first message atomically. Returns the Thread."""
    from django.db import transaction

    users = list({creator, *participants})
    if thread_type is None:
        thread_type = Thread.DIRECT if len(users) == 2 else Thread.GROUP

    with transaction.atomic():
        thread = Thread.objects.create(subject=subject[:255], thread_type=thread_type, created_by=creator)
        for user in users:
            ThreadParticipant.objects.create(
                thread=thread, user=user,
                last_read_at=timezone.now() if user == creator else None,
            )
        Message.objects.create(thread=thread, sender=creator, body=initial_body)
        thread.last_message_at = timezone.now()
        thread.save(update_fields=["last_message_at"])
    return thread