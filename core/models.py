from django.conf import settings
from django.db import models
from django.utils import timezone


# --------------------------------------------------------------------------
# Soft delete
# --------------------------------------------------------------------------
class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        """Soft-delete every row.

        Review M14: this used to be a single UPDATE, which skipped the
        audit log (and the file signals). Each row now goes through
        `SoftDeleteModel.delete()`, so it is audited like a single delete.
        Returns the number of rows soft-deleted.
        """
        count = 0
        for obj in self.filter(is_deleted=False):
            obj.delete()
            count += 1
        return count

    def hard_delete(self):
        return super().delete()

    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AllObjectsManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    pass


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteModel(TimeStampedModel):
    AUDITED = True

    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True
        base_manager_name = "all_objects"

    def delete(self, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)


# --------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------
class AuditLogEntry(models.Model):
    """Append-only."""

    CREATE, UPDATE, DELETE, RESTORE, STATE_CHANGE, OTHER = (
        "create", "update", "delete", "restore", "state_change", "other",
    )
    ACTION_CHOICES = [
        (CREATE, "Created"),
        (UPDATE, "Updated"),
        (DELETE, "Deleted"),
        (RESTORE, "Restored"),
        (STATE_CHANGE, "State changed"),
        (OTHER, "Other"),
    ]

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    actor_repr = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    model_label = models.CharField(max_length=100, db_index=True)
    object_pk = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["model_label", "object_pk"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.model_label}#{self.object_pk}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("AuditLogEntry is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLogEntry is append-only.")


# --------------------------------------------------------------------------
# Contact messages (from the public landing page)
# --------------------------------------------------------------------------
class ContactMessage(TimeStampedModel):
    """A message submitted through the public landing page's contact form.

    Not part of internal messaging (which is for authenticated staff). This
    captures reach-outs from anyone — external, unauthenticated — and makes
    them visible to admins through the Django admin."""

    AUDITED = True

    name = models.CharField(max_length=150)
    email = models.EmailField()
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    handling_department = models.ForeignKey(
        "staff.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="public_contact_messages",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_contact_messages",
    )
    is_read = models.BooleanField(default=False, db_index=True)
    is_handled = models.BooleanField(default=False, db_index=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "contact message"
        verbose_name_plural = "contact messages"
        permissions = [
            (
                "can_handle_contact_messages",
                "Can view and handle public contact messages",
            ),
        ]

    def __str__(self):
        return f"{self.name} <{self.email}> · {self.subject or '(no subject)'}"


class ContactMessageActivity(TimeStampedModel):
    ACTION_RECEIVED = "received"
    ACTION_ASSIGNED = "assigned"
    ACTION_READ = "read"
    ACTION_HANDLED = "handled"
    ACTION_REOPENED = "reopened"
    ACTION_NOTE = "note"
    ACTION_CHOICES = [
        (ACTION_RECEIVED, "Received"),
        (ACTION_ASSIGNED, "Assignment changed"),
        (ACTION_READ, "Marked as read"),
        (ACTION_HANDLED, "Marked as handled"),
        (ACTION_REOPENED, "Reopened"),
        (ACTION_NOTE, "Internal note"),
    ]

    AUDITED = True

    contact_message = models.ForeignKey(
        ContactMessage,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES, db_index=True)
    details = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{self.get_action_display()} · {self.contact_message_id}"