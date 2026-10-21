from django.conf import settings
from django.db import models
from django.utils import timezone


# ============================================================================
# Email log (existing)
# ============================================================================
class EmailLog(models.Model):
    """One row per queued email: who it went to, when, via which provider, and
    whether it worked - including every failed attempt (`attempt_trail`).
    The body is stored EXCEPT for `sensitive_categories()`, whose bodies contain
    secrets and must not sit in the database."""

    QUEUED, SENT, RETRYING, FAILED = "queued", "sent", "retrying", "failed"
    STATUS_CHOICES = [
        (QUEUED, "Queued"), (SENT, "Sent"),
        (RETRYING, "Retrying"), (FAILED, "Failed"),
    ]

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    category = models.CharField(max_length=50, default="general", db_index=True)
    subject = models.CharField(max_length=255)
    recipients = models.JSONField(default=list)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    provider = models.CharField(max_length=20, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="email_logs")
    recipient_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    attempt_trail = models.JSONField(default=list, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.status}: {self.subject} -> {self.recipient_text[:60]}"

    DEFAULT_SENSITIVE_CATEGORIES = ("credentials", "reset")

    @classmethod
    def sensitive_categories(cls):
        import config
        return set(config.get("email.sensitive_categories", list(cls.DEFAULT_SENSITIVE_CATEGORIES)))


class EmailQuotaTracker(models.Model):
    """Emails sent per provider per (local) day - drives failover (NR-05)."""

    provider = models.CharField(max_length=20)
    date = models.DateField()
    sent_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("provider", "date")
        ordering = ["-date", "provider"]

    def __str__(self):
        return f"{self.provider} {self.date}: {self.sent_count}"


class EmailSendSlot(models.Model):
    """Single row (pk=1): the next moment an email may be sent."""

    next_at = models.DateTimeField(null=True, blank=True)


# ============================================================================
# In-app notifications (new)
# ============================================================================
class Notification(models.Model):
    """A single in-app notification for one user.

    Design:
    - Per-user rows (not broadcast). Read state is a simple bool.
    - `url` is a relative path (e.g. '/messages/12/') so we're not tied to
      a domain. The bell and list views both navigate to it on click.
    - `category` groups notifications (system, message, staff, ticket, …).
    - `level` drives icon + colour (info, success, warning, error).
    - `metadata` holds arbitrary context (IDs, counts) for future use.
    """

    SYSTEM, MESSAGE, STAFF, TICKET, ASSET, REQUEST, TASK, IMPORT = (
        "system", "message", "staff", "ticket", "asset", "request", "task", "import",
    )
    # QA (Master Plan §1, §8): choices-only addition; the column is unchanged.
    QA = "qa"
    CATEGORY_CHOICES = [
        (SYSTEM, "System"),
        (MESSAGE, "Message"),
        (STAFF, "Staff"),
        (TICKET, "Ticket"),
        (ASSET, "Asset"),
        (REQUEST, "Request"),
        (TASK, "Task"),
        (IMPORT, "Import"),
        (QA, "Quality assurance"),
    ]

    INFO, SUCCESS, WARNING, ERROR = "info", "success", "warning", "error"
    LEVEL_CHOICES = [
        (INFO, "Info"),
        (SUCCESS, "Success"),
        (WARNING, "Warning"),
        (ERROR, "Error"),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES,
                                default=SYSTEM, db_index=True)
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=INFO)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True,
                           help_text="Relative path to open when clicked (optional).")
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"],
                         name="notif_recipient_unread_idx"),
        ]
        verbose_name = "notification"
        verbose_name_plural = "notifications"

    def __str__(self):
        return f"[{self.category}] {self.title} → {self.recipient_id}"

    def mark_read(self, save=True):
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            if save:
                self.save(update_fields=["is_read", "read_at"])

    def mark_unread(self, save=True):
        if self.is_read:
            self.is_read = False
            self.read_at = None
            if save:
                self.save(update_fields=["is_read", "read_at"])