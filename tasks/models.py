"""Tasks app — hierarchical, rank-aware work assignment.

Task is management pushing work outward. Distinct from Tickets (reactive)
and Requests (authorization).

Structural concepts:
  - Container task  : has subtasks; state derives from children
  - Leaf task       : has assignees; per-person progress tracked
  - Reusable task   : template — cloned to spawn a fresh instance
  - Recurring task  : auto-spawned on schedule (daily/weekly/monthly)
  - Dependencies    : M2M gate on completion
  - Time entries    : per-user time logging, live timer
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.storage import restricted_storage


# Top-level so Django's migration serializer can reference it.
def task_attachment_upload(instance, filename):
    ext = os.path.splitext(filename or "")[1].lower()
    return f"tasks/{uuid.uuid4().hex}{ext}"


# ===========================================================================
# Category
# ===========================================================================
class TaskCategory(TimeStampedModel):
    """Admin-editable category.

    Controls who can be assigned, who can create, defaults for new tasks,
    and whether the pattern is reusable.
    """
    AUDITED = True

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    # Optional owning department (blank = cross-department).
    handling_department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="task_categories",
    )

    # --- Assignment policy ---
    allowed_role_codes = models.JSONField(
        default=list, blank=True,
        help_text="Role codes eligible for assignment. Empty list = any role.",
    )
    min_rank_to_assign = models.PositiveIntegerField(
        default=0,
        help_text="Minimum role rank to CREATE tasks in this category. 0 = any.",
    )

    # --- Defaults for new tasks ---
    default_priority = models.CharField(
        max_length=10,
        choices=[("urgent", "Urgent"), ("high", "High"),
                 ("normal", "Normal"), ("low", "Low")],
        default="normal",
    )
    default_due_days = models.PositiveIntegerField(default=7)
    requires_review = models.BooleanField(default=True)
    is_reusable = models.BooleanField(
        default=False,
        help_text="Flag as a reusable template.",
    )

    # --- Presentation ---
    icon = models.CharField(max_length=40, blank=True)
    colour = models.CharField(max_length=7, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Task category"
        verbose_name_plural = "Task categories"

    def __str__(self):
        return self.name


# ===========================================================================
# Task
# ===========================================================================
class Task(TimeStampedModel):
    """A unit of work — container, leaf, or template."""
    AUDITED = True

    # ---- States ----
    DRAFT = "draft"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    AWAITING_RESPONSE = "awaiting_response"
    RETURNED = "returned"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    STATE_CHOICES = [
        (DRAFT, "Draft"),
        (OPEN, "Open"),
        (IN_PROGRESS, "In progress"),
        (AWAITING_RESPONSE, "Awaiting response"),
        (RETURNED, "Returned"),
        (AWAITING_REVIEW, "Awaiting review"),
        (COMPLETED, "Completed"),
        (CANCELLED, "Cancelled"),
    ]

    OPEN_STATES = {OPEN, IN_PROGRESS, AWAITING_RESPONSE, RETURNED, AWAITING_REVIEW}
    ACTIVE_STATES = {OPEN, IN_PROGRESS, AWAITING_RESPONSE, AWAITING_REVIEW}
    CLOSED_STATES = {COMPLETED, CANCELLED}

    # ---- Priority ----
    URGENT, HIGH, NORMAL, LOW = "urgent", "high", "normal", "low"
    PRIORITY_CHOICES = [
        (URGENT, "Urgent"), (HIGH, "High"),
        (NORMAL, "Normal"), (LOW, "Low"),
    ]

    # ---- Recurrence ----
    RECURRENCE_NONE = "none"
    RECURRENCE_DAILY = "daily"
    RECURRENCE_WEEKLY = "weekly"
    RECURRENCE_MONTHLY = "monthly"
    RECURRENCE_CUSTOM = "custom"
    RECURRENCE_CHOICES = [
        (RECURRENCE_NONE, "One-off"),
        (RECURRENCE_DAILY, "Daily"),
        (RECURRENCE_WEEKLY, "Weekly"),
        (RECURRENCE_MONTHLY, "Monthly"),
        (RECURRENCE_CUSTOM, "Custom (see rule)"),
    ]

    # ---- Identity ----
    reference = models.CharField(max_length=20, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        TaskCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tasks",
    )

    # ---- Ownership ----
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="tasks_created",
    )
    owner_department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tasks_owned",
    )

    # ---- Hierarchy ----
    parent = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.CASCADE, related_name="subtasks",
    )

    # ---- Assignees (multi) ----
    assignees = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="TaskAssignee",
        through_fields=("task", "user"),
        related_name="tasks_assigned",
        blank=True,
    )

    # ---- Classification ----
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default=NORMAL,
    )
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default=DRAFT, db_index=True,
    )
    tags = models.JSONField(default=list, blank=True)

    # ---- Timing ----
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    estimated_hours = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
    )

    # ---- Review flow ----
    requires_review = models.BooleanField(default=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tasks_to_review",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="tasks_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    # ---- Reuse / recurrence ----
    cloned_from = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="clones",
    )
    is_recurring = models.BooleanField(default=False)
    recurrence_type = models.CharField(
        max_length=10, choices=RECURRENCE_CHOICES, default=RECURRENCE_NONE,
    )
    recurrence_rule = models.CharField(max_length=100, blank=True)
    recurrence_next_at = models.DateTimeField(null=True, blank=True)

    # ---- Bookkeeping ----
    last_reminder_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["created_by", "state"]),
            models.Index(fields=["owner_department", "state"]),
            models.Index(fields=["due_date"]),
            models.Index(fields=["parent"]),
        ]

    def __str__(self):
        return f"{self.reference} — {self.title}"

    # ---- Convenience ----
    @property
    def is_container(self):
        return self.subtasks.exists()

    @property
    def is_leaf(self):
        return not self.is_container

    @property
    def is_open(self):
        return self.state in self.OPEN_STATES

    @property
    def is_closed(self):
        return self.state in self.CLOSED_STATES

    @property
    def is_overdue(self):
        if not self.due_date or self.is_closed:
            return False
        return timezone.localdate() > self.due_date

    @property
    def progress_percent(self):
        if self.is_container:
            children = list(self.subtasks.all())
            if not children:
                return 0
            done = sum(1 for c in children if c.state == self.COMPLETED)
            return int(round(done * 100 / len(children)))
        rows = list(self.assignee_rows.all())
        if not rows:
            return 0
        done = sum(1 for r in rows if r.state == TaskAssignee.DONE)
        return int(round(done * 100 / len(rows)))

    @property
    def assignee_count(self):
        return self.assignee_rows.count()

    @property
    def completed_assignee_count(self):
        return self.assignee_rows.filter(state=TaskAssignee.DONE).count()

    @property
    def logged_hours(self):
        from django.db.models import Sum
        total = self.time_entries.aggregate(s=Sum("duration_seconds"))["s"] or 0
        return round(total / 3600.0, 2)


# ===========================================================================
# Per-assignee progress
# ===========================================================================
class TaskAssignee(TimeStampedModel):
    AUDITED = True

    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    BLOCKED = "blocked"
    DECLINED = "declined"

    STATE_CHOICES = [
        (ASSIGNED, "Assigned"),
        (ACCEPTED, "Accepted"),
        (IN_PROGRESS, "In progress"),
        (DONE, "Done"),
        (BLOCKED, "Blocked"),
        (DECLINED, "Declined"),
    ]

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="assignee_rows",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="task_assignments",
    )
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default=ASSIGNED)
    note = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    assigned_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("task", "user")
        ordering = ["assigned_at"]
        indexes = [models.Index(fields=["user", "state"])]

    def __str__(self):
        return f"{self.user} on {self.task.reference} ({self.state})"


# ===========================================================================
# Activity log
# ===========================================================================
class TaskActivity(TimeStampedModel):
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="activities",
    )
    kind = models.CharField(max_length=40)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    message = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]


# ===========================================================================
# Comments
# ===========================================================================
class TaskComment(TimeStampedModel):
    AUDITED = True

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    body = models.TextField()
    is_internal = models.BooleanField(default=False)

    # Pinning
    is_pinned = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(null=True, blank=True)
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["created_at"]


# ===========================================================================
# Attachments
# ===========================================================================
class TaskAttachment(TimeStampedModel):
    """Stored in restricted storage; served only via protected_download (H1)."""
    AUDITED = True
    PROTECTED_DOWNLOAD = True

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    file = models.FileField(upload_to=task_attachment_upload, storage=restricted_storage)
    original_name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        from core.uploads import enforce_allowed_extension
        enforce_allowed_extension(self)
        super().save(*args, **kwargs)

    def can_download(self, user):
        if not (user and user.is_authenticated):
            return False
        from .views import _visible_tasks_qs
        return _visible_tasks_qs(user).filter(pk=self.task_id).exists()

    def download_url(self):
        from django.urls import reverse
        return reverse("core:protected_download", args=[self._meta.label_lower, self.pk])


# ===========================================================================
# Time entries (live timer + manual)
# ===========================================================================
class TaskTimeEntry(TimeStampedModel):
    AUDITED = True

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="time_entries",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="task_time_entries",
    )
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["task", "user"]),
            models.Index(fields=["user", "-started_at"]),
        ]

    @property
    def is_running(self):
        return self.ended_at is None and self.duration_seconds == 0

    @property
    def hours(self):
        return round(self.duration_seconds / 3600.0, 2)


# ===========================================================================
# Dependencies
# ===========================================================================
class TaskDependency(TimeStampedModel):
    AUDITED = True

    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="dependency_links",
    )
    depends_on = models.ForeignKey(
        Task, on_delete=models.CASCADE, related_name="dependent_links",
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("task", "depends_on")

    def __str__(self):
        return f"{self.task.reference} → {self.depends_on.reference}"