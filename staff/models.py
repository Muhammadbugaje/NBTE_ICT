"""Staff directory (doc 30 §5.2 / §19.6 / Appendix E)."""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core import audit
from core.models import SoftDeleteModel, TimeStampedModel
from core.restricted import RestrictedFileModel
from core.images import schedule_thumbnails, variant_url
from core.storage import capture_original_filename, uuid_upload_to


class Lookup(TimeStampedModel):
    """Admin-editable list (state list, staff category list, etc. - doc 30 §5.2).
    Dedicated tables now; can move into the config registry later if preferred."""

    AUDITED = True
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Department(Lookup):
    """An NBTE department (ICT, Planning, Finance, ...). The `code` is the stable
    handle used for department-scoped config (`department:<code>`) and logic -
    unlike `name`, it should never change once other things depend on it."""

    code = models.SlugField(
        max_length=50, unique=True, null=True, blank=True,
        help_text="Stable short code, e.g. 'ict'. Auto-filled from the name if left blank.",
    )

    def save(self, *args, **kwargs):
        if not self.code:
            base = slugify(self.name)[:45] or "dept"
            code, n = base, 2
            while Department.objects.exclude(pk=self.pk).filter(code=code).exists():
                code, n = f"{base}-{n}", n + 1
            self.code = code
        super().save(*args, **kwargs)

    @property
    def config_scope(self):
        """Scope string for per-department config overrides (see config.get_for_department)."""
        return f"department:{self.code}"


class Office(Lookup):
    """Technical Support / Dev Support / General Admin (doc 30 §6.3). Head is a
    Staff record so routing can resolve 'office head' without a second list."""
    head = models.ForeignKey(
        "Staff", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )


class StaffCategory(Lookup):
    class Meta(Lookup.Meta):
        verbose_name_plural = "staff categories"


class StaffState(Lookup):
    """Current-state list, e.g. Active, On Leave, Suspended (admin-editable).
    `counts_as_absent` feeds the absence indicator later (doc 30 §19.9)."""
    counts_as_absent = models.BooleanField(default=False)


class Staff(SoftDeleteModel):
    AUDIT_IGNORE_FIELDS = ("photo_original_name",)
    MEDIA_SUBDIR = "staff/photos"

    CONTACT_CHOICES = [
        ("email", "Email"),
        ("phone", "Phone"),
        ("inapp", "In-app message"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="staff_profile",
    )
    employee_no = models.CharField(max_length=30, unique=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)

    photo = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    photo_original_name = models.CharField(max_length=255, blank=True)

    department = models.ForeignKey(
        Department, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="staff",
    )
    office = models.ForeignKey(
        Office, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="staff",
    )
    subsection = models.CharField(max_length=100, blank=True, help_text="Subsection / unit")
    designation = models.CharField(max_length=150, blank=True)
    grade_level = models.CharField(max_length=20, blank=True)
    office_location = models.CharField(max_length=150, blank=True)
    category = models.ForeignKey(
        StaffCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="staff",
    )
    reports_to = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="direct_reports",
    )
    duties = models.TextField(blank=True)

    state = models.ForeignKey(
        StaffState, null=True, blank=True,
        on_delete=models.PROTECT, related_name="staff",
    )
    state_start = models.DateField(null=True, blank=True)
    state_end = models.DateField(null=True, blank=True)

    contact_preference = models.CharField(
        max_length=10, choices=CONTACT_CHOICES, default="email",
    )
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=30, blank=True)

    # Doc 30 Q1 recommendation: month/day only (no birth year), pending Director decision.
    birth_month = models.PositiveSmallIntegerField(null=True, blank=True)
    birth_day = models.PositiveSmallIntegerField(null=True, blank=True)
    date_joined_service = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "staff"
        permissions = [
            ("change_staff_state", "Can change a staff member's state"),
            ("view_own_department_staff", "Can view staff in own department"),
            ("manage_own_department_staff", "Can manage staff in own department"),
        ]
        indexes = [models.Index(fields=["last_name", "first_name"])]

    def __str__(self):
        return f"{self.full_name} ({self.employee_no})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @classmethod
    def from_db(cls, db, field_names, values):
        inst = super().from_db(db, field_names, values)
        loaded = set(field_names)
        if {"state_id", "state_start", "state_end"} <= loaded:
            inst._orig_state = inst._state_snapshot()
        else:  # deferred state fields: can't diff, so don't guess
            inst._orig_state = None
        return inst

    def _state_snapshot(self):
        return (self.state_id, self.state_start, self.state_end)

    def save(self, *args, **kwargs):
        capture_original_filename(self, "photo", "photo_original_name")
        new_photo = bool(self.photo) and not self.photo._committed
        creating = self._state.adding
        orig = getattr(self, "_orig_state", None)
        user = self.user if self.user_id else None
        if user is not None and not self.department_id and user.department_id:
            self.department_id = user.department_id  # blank never overrides; fill from the account
        super().save(*args, **kwargs)
        if user is not None and self.department_id and user.department_id != self.department_id \
                and not getattr(user, "_dept_syncing", False):
            self._dept_syncing = True  # push the directory record's department to the account
            user.department_id = self.department_id
            user.save()
            self._dept_syncing = False
        snap = self._state_snapshot()
        if (creating and self.state_id) or (not creating and orig is not None and orig != snap):
            self._write_state_history(previous_state_id=None if creating else orig[0])
        self._orig_state = snap
        if new_photo:
            schedule_thumbnails(self, "photo")

    def photo_thumb_url(self, size=300):
        return variant_url(self.photo, size)

    def _write_state_history(self, previous_state_id, reason=""):
        actor = getattr(self, "_state_changed_by", None) or audit.get_current_actor()
        StaffStatusHistory.objects.create(
            staff=self,
            from_state_id=previous_state_id,
            to_state=self.state,
            start_date=self.state_start,
            end_date=self.state_end,
            changed_by=actor if getattr(actor, "pk", None) else None,
            reason=reason or getattr(self, "_state_change_reason", ""),
        )

    def change_state(self, new_state, by, start=None, end=None, reason=""):
        """Permission-checked state change (NR-09). Writes history (via save)
        and a STATE_CHANGE audit entry."""
        if not by.has_perm("staff.change_staff_state"):
            raise PermissionError("change_staff_state permission required.")
        old = self.state
        self.state = new_state
        self.state_start = start or timezone.localdate()
        self.state_end = end
        self._state_changed_by = by
        self._state_change_reason = reason
        self.save()
        audit.log(
            "state_change", self, actor=by,
            changes={"state": {
                "old": old.name if old else None,
                "new": new_state.name if new_state else None,
            }},
            note=reason,
        )


class StaffStatusHistory(models.Model):
    """staff_status_history (NR-09). Append-only record of every state change."""

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="status_history")
    from_state = models.ForeignKey(
        StaffState, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    to_state = models.ForeignKey(
        StaffState, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    reason = models.CharField(max_length=255, blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-changed_at", "-id"]
        verbose_name_plural = "staff status history"

    def __str__(self):
        return f"{self.staff_id}: {self.from_state} -> {self.to_state}"


class StaffAttachment(RestrictedFileModel):
    """Private HR document on a staff record (contract, certificate scan...).
    Served only through the permission-checked download view."""

    DOWNLOAD_PERMISSION = "staff.view_staffattachment"
    MEDIA_SUBDIR = "staff/private_documents"

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="attachments")
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or f"attachment {self.pk}"


# ============================================================================
# EXECUTIVE / MANAGEMENT TIMELINE
# ============================================================================
class ExecutivePosition(TimeStampedModel):
    """A leadership position: Executive Secretary, Director of ICT,
    Chairman Governing Council, Department Head, etc.

    A *position* outlives any person. `ExecutiveTenure` records who held it
    and when."""
    SCOPE_FEDERAL      = "federal"
    SCOPE_DEPARTMENTAL = "departmental"
    SCOPE_BOARD        = "board"
    SCOPE_CHOICES = [
        (SCOPE_FEDERAL,      "Federal / Board level"),
        (SCOPE_BOARD,        "Governing Council"),
        (SCOPE_DEPARTMENTAL, "Departmental"),
    ]

    name        = models.CharField(max_length=120, unique=True)
    code        = models.SlugField(max_length=80, unique=True, blank=True)
    scope       = models.CharField(max_length=20, choices=SCOPE_CHOICES,
                                   default=SCOPE_DEPARTMENTAL)
    description = models.TextField(blank=True,
                                   help_text="What this role does.")
    department  = models.ForeignKey(
        Department, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="executive_positions",
        help_text="Only used for departmental positions.",
    )
    # Higher = higher rank on the public leadership grid.
    order       = models.PositiveIntegerField(default=100)
    is_active   = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "executive positions"
        permissions = [
            ("manage_executive_position",
             "Can create, edit and delete executive positions"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            base = slugify(self.name)[:70] or "position"
            code, n = base, 2
            while ExecutivePosition.objects.exclude(pk=self.pk).filter(code=code).exists():
                code, n = f"{base}-{n}", n + 1
            self.code = code
        super().save(*args, **kwargs)

    # ---- Current holder --------------------------------------------------
    @property
    def current_tenure(self):
        return (
            self.tenures
            .filter(ended_on__isnull=True)
            .select_related("staff")
            .order_by("-started_on")
            .first()
        )

    @property
    def current_holder(self):
        t = self.current_tenure
        return t.staff if t else None


class ExecutiveTenure(TimeStampedModel):
    """A person's service in an executive position, over a date range.

    `ended_on is null` = current holder."""
    AUDITED = True
    MEDIA_SUBDIR = "staff/executive"

    position = models.ForeignKey(
        ExecutivePosition, on_delete=models.CASCADE, related_name="tenures",
    )
    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="executive_tenures",
    )

    started_on = models.DateField(db_index=True)
    ended_on   = models.DateField(null=True, blank=True, db_index=True)
    is_acting  = models.BooleanField(
        default=False,
        help_text="Acting appointment — not confirmed in substantive capacity.",
    )
    appointment_reference = models.CharField(
        max_length=120, blank=True,
        help_text="Board resolution / memo number.",
    )
    qualifications = models.TextField(
        blank=True,
        help_text="Degrees, certifications — shown on the public page.",
    )
    bio = models.TextField(
        blank=True,
        help_text="Short biography for the public leadership grid.",
    )
    photo = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    photo_original_name = models.CharField(max_length=255, blank=True)

    # ---- public page ------------------------------------------------------
    is_public      = models.BooleanField(default=True)
    is_featured    = models.BooleanField(
        default=False,
        help_text="Feature this tenure on the public homepage leadership strip.",
    )
    display_order  = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["display_order", "-started_on"]
        indexes = [
            models.Index(fields=["position", "-started_on"]),
            models.Index(fields=["ended_on"]),
        ]
        verbose_name = "executive tenure"
        verbose_name_plural = "executive tenures"
        permissions = [
            ("manage_executive_tenure",
             "Can record / edit executive tenure history"),
        ]

    def __str__(self):
        state = "current" if self.is_current else f"ended {self.ended_on}"
        return f"{self.staff.full_name} — {self.position.name} ({state})"

    def save(self, *args, **kwargs):
        capture_original_filename(self, "photo", "photo_original_name")
        new_photo = bool(self.photo) and not self.photo._committed
        super().save(*args, **kwargs)
        if new_photo:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "photo")

    # ---- Derived ----------------------------------------------------------
    @property
    def is_current(self):
        return self.ended_on is None

    @property
    def duration_years(self):
        from datetime import date as _date
        end = self.ended_on or _date.today()
        return round((end - self.started_on).days / 365.25, 1)

    @property
    def photo_thumb_url(self):
        from core.images import variant_url
        return variant_url(self.photo, 400) or (self.photo.url if self.photo else "")

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.ended_on and self.started_on and self.ended_on < self.started_on:
            raise ValidationError({"ended_on":
                "End date cannot be before start date."})






