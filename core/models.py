from django.conf import settings
from django.db import models
from django.utils import timezone


# --------------------------------------------------------------------------
# Soft delete
# --------------------------------------------------------------------------
class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        return super().update(is_deleted=True, deleted_at=timezone.now())

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

    def __str__(self):
        return f"{self.name} <{self.email}> · {self.subject or '(no subject)'}"