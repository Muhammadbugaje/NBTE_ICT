"""Staff directory (doc 30 §5.2 / §19.6 / Appendix E)."""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core import audit
from core.models import SoftDeleteModel, TimeStampedModel
from core.restricted import RestrictedFileModel
from core.images import schedule_thumbnails, variant_url
from core.storage import capture_original_filename, restricted_storage, uuid_upload_to


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
    # Review M12: who receives the weekly attendance summary. If empty, the
    # department's active Director is used.
    head = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="headed_departments",
        help_text="Receives the weekly attendance summary. Defaults to the department's Director.",
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


class StateOfOrigin(Lookup):
    """Nigerian state of origin (36 states + FCT). Seeded by `seed_nbte`;
    the nominal-roll importer also creates unknown spellings it can't map."""

    ZONE_CHOICES = [
        ("NC", "North Central"), ("NE", "North East"), ("NW", "North West"),
        ("SE", "South East"), ("SS", "South South"), ("SW", "South West"),
    ]
    code = models.SlugField(max_length=8, unique=True, blank=True, null=True)
    zone = models.CharField(max_length=20, blank=True, choices=ZONE_CHOICES)

    class Meta(Lookup.Meta):
        verbose_name = "state of origin"
        verbose_name_plural = "states of origin"


class Designation(TimeStampedModel):
    """A post title from the nominal roll, e.g. "SNR. PROG. OFFR.".

    The promotion backbone: `rank` orders designations by seniority and
    `next_designation` is the normal promotion step. `aliases` holds the other
    spellings found in imported files (one per line) so the importer maps
    "SNR. PROG. OFFR", "SNR.  PROG. OFFR." etc. to the same record.
    """

    AUDITED = True

    name = models.CharField(max_length=150, unique=True)
    code = models.SlugField(max_length=80, unique=True, blank=True)
    rank = models.PositiveIntegerField(
        default=0, help_text="Higher = more senior. Used for promotion and routing.")
    contediss_min = models.CharField(
        max_length=20, blank=True,
        help_text="Lowest CONTEDISS/CONHESS level for this designation, e.g. '7'.")
    contediss_max = models.CharField(max_length=20, blank=True, help_text="Highest level, e.g. '9'.")
    next_designation = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="predecessors",
        help_text="The designation a holder of this one is normally promoted into.",
    )
    aliases = models.TextField(
        blank=True,
        help_text="Other spellings of this designation found in imported files, one per line.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-rank", "sort_order", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            base = slugify(self.name)[:72] or "designation"
            code, n = base, 2
            while Designation.objects.exclude(pk=self.pk).filter(code=code).exists():
                code, n = f"{base}-{n}", n + 1
            self.code = code
        super().save(*args, **kwargs)

    @property
    def alias_list(self):
        return [a.strip() for a in (self.aliases or "").splitlines() if a.strip()]


class Staff(SoftDeleteModel):
    AUDIT_IGNORE_FIELDS = ("photo_original_name",)
    # Review M8: personal details are recorded as changed ("***"), never stored.
    AUDIT_EXCLUDE_FIELDS = (
        "phone", "emergency_contact_name", "emergency_contact_phone",
        "birth_month", "birth_day", "date_of_birth",
    )
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
    designation = models.ForeignKey(
        Designation, null=True, blank=True, on_delete=models.PROTECT, related_name="staff",
    )
    grade_level = models.CharField(max_length=20, blank=True,
                                   help_text="CONTEDISS / CONHESS level, e.g. '8'.")
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
    birth_month = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Deprecated — use date_of_birth.")
    birth_day = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Deprecated — use date_of_birth.")
    date_joined_service = models.DateField(null=True, blank=True)

    # ---- Nominal roll fields (NBTE nominal roll columns) -------------------
    GENDER_CHOICES = [("M", "Male"), ("F", "Female")]
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True)
    state_of_origin = models.ForeignKey(
        StateOfOrigin, null=True, blank=True, on_delete=models.SET_NULL, related_name="staff",
    )
    lga = models.CharField("LGA", max_length=100, blank=True, help_text="Local government area")
    date_of_birth = models.DateField(null=True, blank=True)
    date_first_appointment = models.DateField(
        "date of first appointment", null=True, blank=True, help_text="D_FAPPMT")
    date_joined_board = models.DateField(
        "date joined the Board", null=True, blank=True, help_text="D_JBOARD")
    date_present_appointment = models.DateField(
        "date of present appointment", null=True, blank=True, help_text="D_PRAPPMT")
    date_confirmation = models.DateField(
        "date of confirmation", null=True, blank=True, help_text="D_COF")
    qualifications = models.TextField(blank=True, help_text="QUALIFICATION(S) — free text")

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "staff"
        permissions = [
            ("change_staff_state", "Can change a staff member's state"),
            ("view_own_department_staff", "Can view staff in own department"),
            ("manage_own_department_staff", "Can manage staff in own department"),
            # Nominal roll & staff lifecycle
            ("import_nominal_roll", "Can import the NBTE nominal roll"),
            ("export_nominal_roll", "Can export staff records as a nominal roll"),
            ("promote_staff", "Can promote staff to a new designation"),
            ("view_own_office_staff", "Can view staff in own office"),
            ("manage_shared_queries", "Can save staff queries shared with everyone"),
            ("transfer_staff", "Can transfer staff to another department or office"),
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
        inst._orig_place = ((inst.department_id, inst.office_id)
                            if {"department_id", "office_id"} <= loaded else None)
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
        self._record_transfer(creating)
        self._sync_placeholder_login(user)
        if new_photo:
            schedule_thumbnails(self, "photo")

    def _sync_placeholder_login(self, user):
        """An account made from the nominal roll for someone with no email
        signs in with a placeholder address. As soon as the record gets a real
        email (edit form, admin or a later import), the account uses it — so
        the person can sign in with it and reset a forgotten password."""
        if user is None or not self.email:
            return
        from .account_provisioning import is_placeholder_email
        if not is_placeholder_email(user.email) or self.email.lower() == user.email:
            return
        from django.contrib.auth import get_user_model
        if get_user_model().objects.filter(email__iexact=self.email).exclude(pk=user.pk).exists():
            return
        user.email = self.email.lower()
        user.save(update_fields=["email"])

    def _record_transfer(self, creating):
        """Every move between departments / offices lands in StaffTransfer,
        whether it came from the Transfer action, the edit form, the admin or
        an import. A first posting (nothing -> something) is not a transfer."""
        orig = getattr(self, "_orig_place", None)
        place = (self.department_id, self.office_id)
        self._orig_place = place
        if creating or orig is None or orig == place:
            return
        dept_moved = orig[0] is not None and orig[0] != place[0]
        office_moved = orig[1] is not None and orig[1] != place[1]
        if not (dept_moved or office_moved or getattr(self, "_transfer_reason", "")):
            return
        actor = getattr(self, "_transfer_by", None) or audit.get_current_actor()
        StaffTransfer.objects.create(
            staff=self,
            from_department_id=orig[0], to_department_id=place[0],
            from_office_id=orig[1], to_office_id=place[1],
            effective_date=getattr(self, "_transfer_date", None) or timezone.localdate(),
            reason=getattr(self, "_transfer_reason", ""),
            source=getattr(self, "_transfer_source", StaffTransfer.SOURCE_EDIT),
            recorded_by=actor if getattr(actor, "pk", None) else None,
        )

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

    def can_download(self, user):
        """Review M7: the permission alone no longer covers every department."""
        if not super().can_download(user):
            return False
        if user.is_superuser or user.has_perm("accounts.access_all_departments"):
            return True
        return bool(user.department_id) and self.staff.department_id == user.department_id

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


# ============================================================================
# STAFF LIFECYCLE: promotions, nominal-roll imports, saved queries
# ============================================================================
class Promotion(TimeStampedModel):
    """One promotion (or a compensating reversal) of one staff member.
    Written only through staff.promotions — never edited afterwards."""

    AUDITED = True

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="promotions")
    from_designation = models.ForeignKey(
        Designation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    to_designation = models.ForeignKey(Designation, on_delete=models.PROTECT, related_name="+")
    from_grade_level = models.CharField(max_length=20, blank=True)
    to_grade_level = models.CharField(max_length=20, blank=True)
    from_present_appointment = models.DateField(
        null=True, blank=True,
        help_text="The staff member's date of present appointment before this promotion "
                  "(restored on rollback).")
    effective_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="promotions_approved")
    cascade_source = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="cascaded_promotions",
        help_text="If this promotion was part of a cascade, the root promotion.")
    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="reversed_by",
        help_text="Set on a compensating promotion: the promotion it rolls back.")

    class Meta:
        ordering = ["-effective_date", "-created_at"]
        indexes = [models.Index(fields=["staff", "-effective_date"])]

    def __str__(self):
        return f"{self.staff.full_name}: {self.from_designation or '—'} → {self.to_designation}"

    @property
    def is_reversal(self):
        return self.reverses_id is not None


class ImportSession(TimeStampedModel):
    """One run of the nominal-roll importer (web or command line)."""

    AUDITED = True
    MEDIA_SUBDIR = "staff/imports/reports"

    STATUS_CHOICES = [("ok", "OK"), ("partial", "Partial"), ("failed", "Failed")]

    source_filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    sheets_processed = models.JSONField(default=list, blank=True)
    new_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)  # [{row, sheet, message}]
    report_file = models.FileField(
        upload_to=uuid_upload_to, null=True, blank=True, storage=restricted_storage)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="ok")
    mode = models.CharField(max_length=10, blank=True, help_text="update / skip / fail")
    accounts_created = models.PositiveIntegerField(default=0)
    credentials_file = models.FileField(
        upload_to=uuid_upload_to, null=True, blank=True, storage=restricted_storage,
        help_text="One-time download of the random passwords given to new accounts; "
                  "deleted after it is downloaded.")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.source_filename} ({self.get_status_display()})"


class SavedQuery(TimeStampedModel):
    """A named staff query from the Advanced query builder (staff.query)."""

    AUDITED = True
    LOGIC_CHOICES = [("AND", "All conditions (AND)"), ("OR", "Any condition (OR)")]

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_queries")
    is_shared = models.BooleanField(default=False)
    conditions = models.JSONField(default=list, blank=True)  # [{"field", "op", "value"}]
    logic = models.CharField(max_length=3, choices=LOGIC_CHOICES, default="AND")

    class Meta:
        unique_together = ("owner", "name")
        ordering = ["name"]
        verbose_name_plural = "saved queries"

    def __str__(self):
        return self.name


# Optional LGA lookup model. The project keeps Staff.lga as free text with
# autocomplete from staff/data/lgas.json (see `seed_nbte --with-lgas`).
# Uncomment and add a migration if a real FK is wanted later.
#
# class LGA(Lookup):
#     state = models.ForeignKey(StateOfOrigin, on_delete=models.CASCADE, related_name="lgas")



class StaffTransfer(TimeStampedModel):
    """A move to another department and/or office (e.g. a zonal office).
    Written by Staff.save() whenever the department or office changes."""

    AUDITED = True
    SOURCE_TRANSFER, SOURCE_EDIT, SOURCE_IMPORT = "transfer", "edit", "import"
    SOURCE_CHOICES = [(SOURCE_TRANSFER, "Transfer"), (SOURCE_EDIT, "Record edited"),
                      (SOURCE_IMPORT, "Nominal roll import")]

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="transfers")
    from_department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    to_department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    from_office = models.ForeignKey(Office, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    to_office = models.ForeignKey(Office, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    effective_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_EDIT)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-effective_date", "-created_at"]

    def __str__(self):
        return f"{self.staff.full_name}: {self.from_department or '—'} → {self.to_department or '—'}"
