"""v2.1 Phase B core models (imported at the end of core/models.py).

* ImportRun / ImportRow - the import diff engine (X45, core/importdiff.py).
* SeedRecord - provenance of rows created by `seed_nbte` (X51, core/seeding.py).
"""
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models

from .storage import restricted_storage, uuid_upload_to


class ImportRun(models.Model):
    PARSED, PREVIEWED, APPLYING, APPLIED, PARTIAL, DISCARDED, EXPIRED, PENDING_CONFIRMATION = (
        "parsed", "previewed", "applying", "applied", "partially_applied", "discarded", "expired",
        "pending_confirmation")
    STATUS_CHOICES = [(PARSED, "Read"), (PREVIEWED, "Ready to check"), (APPLYING, "Applying"),
                      (APPLIED, "Applied"), (PARTIAL, "Partly applied"), (DISCARDED, "Discarded"),
                      (EXPIRED, "Expired"), (PENDING_CONFIRMATION, "Waiting for a second officer")]
    MEDIA_SUBDIR = "imports/runs"

    spec = models.CharField(max_length=60, db_index=True)
    spec_version = models.PositiveIntegerField(default=1)
    file = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                    related_name="+")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=PARSED, db_index=True)
    header_signature = models.CharField(max_length=64, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    pending_action = models.ForeignKey("qa_core.PendingAction", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Import #{self.pk} ({self.spec}, {self.status})"


class ImportRow(models.Model):
    NEW, CHANGED, UNCHANGED, MISSING, ERROR, CONFLICT = "new", "changed", "unchanged", "missing", "error", "conflict"
    ACTION_CHOICES = [(NEW, "New"), (CHANGED, "Changed"), (UNCHANGED, "Unchanged"), (MISSING, "Missing from file"),
                      (ERROR, "Error"), (CONFLICT, "Conflict")]

    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField(default=0)
    key = models.CharField(max_length=200, blank=True)
    label = models.CharField(max_length=200, blank=True)
    sublabel = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=12, choices=ACTION_CHOICES, db_index=True)
    target_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    target_id = models.CharField(max_length=64, blank=True)
    target_version = models.CharField(max_length=64, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    clean = models.JSONField(default=dict, blank=True)
    diff = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=dict, blank=True)
    accepted = models.BooleanField(default=False)
    accepted_fields = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    applied = models.BooleanField(default=False)
    apply_error = models.TextField(blank=True)

    class Meta:
        ordering = ["row_number", "id"]
        indexes = [models.Index(fields=["run", "action"])]

    def __str__(self):
        return f"Row {self.row_number} {self.action}"

    @property
    def target(self):
        if not self.target_ct_id or not self.target_id:
            return None
        model = self.target_ct.model_class()
        return model._base_manager.filter(pk=self.target_id).first() if model else None


class SeedRecord(models.Model):
    """Which seed set created a row, with a hash of each seeded field value so
    `seed_nbte --update-unedited` can tell untouched rows from edited ones."""

    set_name = models.CharField(max_length=80, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.CharField(max_length=64)
    natural_key = models.JSONField(default=dict, blank=True)
    field_hashes = models.JSONField(default=dict, blank=True)
    seed_version = models.PositiveIntegerField(default=1)
    seeded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["content_type", "object_id"],
                                               name="core_seedrecord_object_uniq")]

    def __str__(self):
        return f"{self.set_name}: {self.content_type_id}#{self.object_id}"
