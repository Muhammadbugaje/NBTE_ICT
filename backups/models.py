"""Backup and restore for the whole NBTE ICT System (Master Plan §22).

* ``BackupDestination`` — where copies go (a local folder, a mounted drive, or
  any S3-compatible bucket). Credential VALUES never live here: a destination
  only names the environment-variable prefix that holds them.
* ``BackupRun`` — one backup: what was captured (the manifest), how big it
  was, and how it ended.
* ``BackupCopy`` — one run written to one destination. Retention works on
  these rows, per destination.
* ``RestoreDrill`` — a test restore of a run into a scratch database.

These are system rows, not institution data: they are not institution-scoped.
"""
import os

from django.conf import settings
from django.db import models
from django.utils import timezone


class BackupDestination(models.Model):
    LOCAL, MOUNTED, S3 = "local", "mounted", "s3"
    KIND_CHOICES = [
        (LOCAL, "Folder on this server"),
        (MOUNTED, "USB or external drive / network mount"),
        (S3, "S3-compatible storage (AWS S3, Cloudflare R2, Supabase, Backblaze B2)"),
    ]

    name = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=LOCAL)
    config = models.JSONField(
        default=dict, blank=True,
        help_text="Per kind: local {path}; mounted {mount_path, subfolder}; "
                  "s3 {bucket, prefix, region, endpoint_url, addressing_style}. Never secrets.")
    credential_env_prefix = models.CharField(
        max_length=60, blank=True,
        help_text="Environment-variable prefix holding the credentials, e.g. BACKUP_S3_PRIMARY_. "
                  "The values stay in the server's .env, never in the database.")
    is_active = models.BooleanField(default=False)
    is_placeholder = models.BooleanField(default=False, editable=False)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_test_ok = models.BooleanField(null=True, blank=True)
    last_test_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self):
        return self.name

    # The env variables this destination reads (name, required, what it is).
    def env_spec(self):
        prefix = (self.credential_env_prefix or "").strip()
        if self.kind != self.S3 or not prefix:
            return []
        return [
            (f"{prefix}ACCESS_KEY_ID", True, "Access key ID"),
            (f"{prefix}SECRET_ACCESS_KEY", True, "Secret access key"),
            (f"{prefix}ENDPOINT_URL", False, "Endpoint URL (blank for AWS S3; overrides the saved endpoint)"),
            (f"{prefix}REGION", False, "Region (overrides the saved region)"),
        ]

    def env_status(self):
        """[{name, required, label, is_set}] — never the values."""
        return [{"name": n, "required": r, "label": label, "is_set": bool(os.environ.get(n, "").strip())}
                for n, r, label in self.env_spec()]

    @property
    def missing_env(self):
        return [row["name"] for row in self.env_status() if row["required"] and not row["is_set"]]

    @property
    def target_display(self):
        c = self.config or {}
        if self.kind == self.LOCAL:
            return c.get("path") or "No folder set"
        if self.kind == self.MOUNTED:
            base = c.get("mount_path") or "No drive chosen"
            sub = c.get("subfolder") or ""
            return f"{base.rstrip('/')}/{sub}" if sub else base
        if self.kind == self.S3:
            bucket = c.get("bucket") or "no bucket"
            prefix = (c.get("prefix") or "").strip("/")
            return f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
        return ""


class BackupRun(models.Model):
    SCHEDULE, MANUAL = "schedule", "manual"
    TRIGGER_CHOICES = [(SCHEDULE, "Scheduled"), (MANUAL, "Manual")]
    RUNNING, SUCCEEDED, PARTIAL, FAILED = "running", "succeeded", "partial", "failed"
    STATUS_CHOICES = [(RUNNING, "Running"), (SUCCEEDED, "Succeeded"), (PARTIAL, "Partial"), (FAILED, "Failed")]

    label = models.CharField(max_length=80, blank=True, db_index=True,
                             help_text="Folder name used at every destination.")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=10, choices=TRIGGER_CHOICES, default=MANUAL)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=RUNNING, db_index=True)
    size_bytes = models.BigIntegerField(default=0)
    manifest = models.JSONField(default=dict, blank=True)
    manifest_hash = models.CharField(max_length=64, blank=True)
    checksum = models.CharField(max_length=64, blank=True,
                                help_text="SHA-256 over the artefact hashes, in manifest order.")
    encrypted = models.BooleanField(default=False)
    progress = models.PositiveSmallIntegerField(default=0)
    progress_label = models.CharField(max_length=200, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        permissions = [
            ("view_backups", "Can view backups"),
            ("run_backup", "Can start a backup"),
            ("manage_destinations", "Can manage backup destinations and the schedule"),
            ("run_restore_drill", "Can run a restore drill"),
        ]

    def __str__(self):
        return self.label or f"Backup #{self.pk}"

    @property
    def is_running(self):
        return self.status == self.RUNNING

    @property
    def duration(self):
        if not self.finished_at:
            return None
        return self.finished_at - self.started_at

    @property
    def can_drill(self):
        return self.status in (self.SUCCEEDED, self.PARTIAL) and self.copies.filter(
            status=BackupCopy.OK, deleted_at__isnull=True).exists()


class BackupCopy(models.Model):
    OK, FAILED = "ok", "failed"
    STATUS_CHOICES = [(OK, "Written"), (FAILED, "Failed")]

    run = models.ForeignKey(BackupRun, on_delete=models.CASCADE, related_name="copies")
    destination = models.ForeignKey(BackupDestination, on_delete=models.PROTECT, related_name="copies")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    location = models.CharField(max_length=500, blank=True)
    error = models.TextField(blank=True)
    written_at = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True,
                                      help_text="When retention removed this copy from the destination.")

    class Meta:
        ordering = ["-written_at"]
        constraints = [models.UniqueConstraint(fields=["run", "destination"], name="backups_one_copy_per_dest")]

    def __str__(self):
        return f"{self.run} → {self.destination} ({self.status})"


class RestoreDrill(models.Model):
    RUNNING, PASSED, FAILED = "running", "passed", "failed"
    RESULT_CHOICES = [(RUNNING, "Running"), (PASSED, "Passed"), (FAILED, "Failed")]

    backup_run = models.ForeignKey(BackupRun, on_delete=models.CASCADE, related_name="drills")
    scratch_db = models.CharField(max_length=300, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    verified_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=10, choices=RESULT_CHOICES, default=RUNNING)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    automatic = models.BooleanField(default=False)
    report = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"Drill of {self.backup_run} ({self.result})"
