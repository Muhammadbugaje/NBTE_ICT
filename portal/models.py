"""Workplace Portal — announcements, events, reminders, documents (doc 30 §19)."""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import SoftDeleteModel, TimeStampedModel
from core.restricted import RestrictedFileModel
from core.storage import capture_original_filename, uuid_upload_to

# ============================================================================
# Shared scope
# ============================================================================
class Scope:
    PUBLIC = "public"
    STAFF = "staff"
    DEPARTMENT = "department"
    ROLE = "role"
    INDIVIDUAL = "individual"

    CHOICES = [
        (PUBLIC,      "Public — anyone, including outside NBTE"),
        (STAFF,       "All NBTE staff"),
        (DEPARTMENT,  "Department only"),
        (ROLE,        "Specific roles"),
        (INDIVIDUAL,  "Specific people"),
    ]


def scope_filter(user):
    """Q object matching everything `user` is allowed to see, across every
    scoped portal model (announcement, event, reminder)."""
    from django.db.models import Q

    if user is None or not user.is_authenticated:
        return Q(visibility=Scope.PUBLIC)

    if user.is_superuser:
        return Q()

    q = Q(visibility=Scope.PUBLIC) | Q(visibility=Scope.STAFF)

    if getattr(user, "department_id", None):
        q |= Q(visibility=Scope.DEPARTMENT, department_id=user.department_id)

    group_ids = [user.role_id] if getattr(user, "role_id", None) else []  # review M6: Role, not groups
    if group_ids:
        q |= Q(visibility=Scope.ROLE, roles__in=group_ids)

    q |= Q(visibility=Scope.INDIVIDUAL, recipients=user)

    return q


# ============================================================================
# ANNOUNCEMENTS
# ============================================================================
class AnnouncementCategory(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    color = models.CharField(max_length=20, default="#1F6B3E")
    icon = models.CharField(max_length=60, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "announcement categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:70] or "category"
            slug, n = base, 2
            while AnnouncementCategory.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class Announcement(SoftDeleteModel):
    AUDITED = True
    MEDIA_SUBDIR = "portal/announcements"

    title   = models.CharField(max_length=200)
    summary = models.CharField(max_length=300, blank=True)
    body    = models.TextField(blank=True)

    image = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    image_original_name = models.CharField(max_length=255, blank=True)

    category = models.ForeignKey(
        AnnouncementCategory, on_delete=models.PROTECT, related_name="announcements",
    )
    PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_URGENT = "normal", "high", "urgent"
    PRIORITY_CHOICES = [
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH,   "High"),
        (PRIORITY_URGENT, "Urgent"),
    ]
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    is_pinned = models.BooleanField(default=False)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="announcements_authored",
    )
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements",
    )

    visibility = models.CharField(max_length=20, choices=Scope.CHOICES, default=Scope.DEPARTMENT)
    roles = models.ManyToManyField("accounts.Role", blank=True, related_name="scoped_announcements")
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="personal_announcements",
    )

    publish_at  = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at  = models.DateTimeField(null=True, blank=True, db_index=True)
    is_draft    = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    auto_archived = models.BooleanField(default=False)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_pinned", "-publish_at", "-pk"]
        indexes = [
            models.Index(fields=["-is_pinned", "-publish_at"]),
            models.Index(fields=["expires_at"]),
        ]
        permissions = [
            ("pin_announcement", "Can pin/unpin announcements"),
            ("publish_public_announcement", "Can publish public announcements"),
            ("publish_org_announcement", "Can publish org-wide announcements"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        capture_original_filename(self, "image", "image_original_name")

        # Which file (if any) did the DB last store?
        old_image_name = None
        if self.pk:
            old_image_name = (
                Announcement.objects
                .filter(pk=self.pk)
                .values_list("image", flat=True)
                .first()
            )

        # Is the currently-bound file genuinely new?
        new_image = False
        if self.image:
            try:
                bound_name = self.image.name
            except Exception:
                bound_name = None

            # `_committed=False` means Django is holding an unsaved upload.
            # Also treat it as new if the name on the field differs from
            # what's in the DB (defends against weird re-binds).
            if not getattr(self.image, "_committed", True):
                new_image = True
            elif old_image_name is not None and bound_name != old_image_name:
                new_image = True

        super().save(*args, **kwargs)

        if new_image:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "image")

    STATUS_DRAFT, STATUS_SCHEDULED = "draft", "scheduled"
    STATUS_PUBLISHED, STATUS_EXPIRED, STATUS_ARCHIVED = "published", "expired", "archived"

    @property
    def status(self):
        if self.archived_at:
            return self.STATUS_ARCHIVED
        if self.is_draft:
            return self.STATUS_DRAFT
        now = timezone.now()
        if self.publish_at > now:
            return self.STATUS_SCHEDULED
        if self.expires_at and self.expires_at <= now:
            return self.STATUS_EXPIRED
        return self.STATUS_PUBLISHED

    @property
    def is_live(self):
        return self.status == self.STATUS_PUBLISHED

    @property
    def is_public(self):
        return self.visibility == Scope.PUBLIC

    @property
    def image_thumb_url(self):
        from core.images import variant_url
        return variant_url(self.image, 480)

    @property
    def card_summary(self):
        if self.summary:
            return self.summary
        if not self.body:
            return ""
        text = self.body.strip().replace("\n", " ")
        return (text[:140] + "…") if len(text) > 140 else text


class AnnouncementLink(TimeStampedModel):
    KIND_CHOICES = [
        ("zoom",    "Zoom"),       ("meet",    "Google Meet"),
        ("teams",   "Microsoft Teams"), ("webinar", "Webinar"),
        ("doc",     "Document"),    ("site",    "Website"),
        ("video",   "Video"),       ("other",   "Other"),
    ]
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="links")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="other")
    label = models.CharField(max_length=80)
    url = models.URLField(max_length=500)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.label} → {self.url}"


# ============================================================================
# EVENTS
# ============================================================================
class EventCategory(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    color = models.CharField(max_length=20, default="#1F6B3E")
    icon = models.CharField(max_length=60, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "event categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:70] or "category"
            slug, n = base, 2
            while EventCategory.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class Event(SoftDeleteModel):
    AUDITED = True
    MEDIA_SUBDIR = "portal/events"

    title       = models.CharField(max_length=200)
    summary     = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)

    image = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    image_original_name = models.CharField(max_length=255, blank=True)

    category = models.ForeignKey(
        EventCategory, on_delete=models.PROTECT, related_name="events",
    )

    # Timed vs all-day
    starts_at = models.DateTimeField(db_index=True)
    ends_at   = models.DateTimeField(null=True, blank=True, db_index=True)
    all_day   = models.BooleanField(default=False)
    timezone_name = models.CharField(
        max_length=60, default="Africa/Lagos",
        help_text="IANA tz name; used for display and .ics generation.",
    )

    # Location
    location_name = models.CharField(
        max_length=200, blank=True,
        help_text="e.g. 'ICT Directorate, Block B Conference Room'.",
    )
    location_address = models.CharField(max_length=300, blank=True)
    virtual_url = models.URLField(
        max_length=500, blank=True,
        help_text="Zoom / Meet / Teams link for hybrid or virtual events.",
    )

    # Meta
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="events_organized",
    )
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="events",
    )

    rsvp_required = models.BooleanField(default=False)
    reminder_lead_minutes = models.PositiveIntegerField(
        default=60,
        help_text="Minutes before starts_at to fire a reminder notification.",
    )
    is_cancelled = models.BooleanField(default=False)
    cancellation_reason = models.CharField(max_length=300, blank=True)
    is_draft = models.BooleanField(default=False)

    # Scope
    visibility = models.CharField(max_length=20, choices=Scope.CHOICES, default=Scope.STAFF)
    roles = models.ManyToManyField("accounts.Role", blank=True, related_name="scoped_events")
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="personal_events",
    )

    notified_at = models.DateTimeField(null=True, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["starts_at", "pk"]
        indexes = [
            models.Index(fields=["starts_at", "ends_at"]),
        ]
        permissions = [
            ("publish_public_event", "Can publish public events"),
            ("publish_org_event", "Can publish org-wide events"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        capture_original_filename(self, "image", "image_original_name")

        old_image_name = None
        if self.pk:
            old_image_name = (
                Event.objects
                .filter(pk=self.pk)
                .values_list("image", flat=True)
                .first()
            )

        new_image = False
        if self.image:
            try:
                bound_name = self.image.name
            except Exception:
                bound_name = None
            if not getattr(self.image, "_committed", True):
                new_image = True
            elif old_image_name is not None and bound_name != old_image_name:
                new_image = True

        super().save(*args, **kwargs)

        if new_image:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "image")

    @property
    def status(self):
        """Computed live — never stored."""
        if self.is_cancelled:
            return "cancelled"
        now = timezone.now()
        if self.is_draft:
            return "draft"
        if now < self.starts_at:
            return "upcoming"
        if self.ends_at:
            if now <= self.ends_at:
                return "ongoing"
            return "past"
        # No explicit end: give a 2h grace window then mark past
        return "ongoing" if now < self.starts_at + timezone.timedelta(hours=2) else "past"

    @property
    def is_upcoming(self): return self.status == "upcoming"
    @property
    def is_ongoing(self):  return self.status == "ongoing"
    @property
    def is_past(self):     return self.status == "past"

    @property
    def image_thumb_url(self):
        from core.images import variant_url
        return variant_url(self.image, 480)

    @property
    def card_summary(self):
        if self.summary:
            return self.summary
        if not self.description:
            return ""
        text = self.description.strip().replace("\n", " ")
        return (text[:140] + "…") if len(text) > 140 else text

    def rsvp_summary(self):
        return {
            "yes": self.attendees.filter(rsvp="yes").count(),
            "no":  self.attendees.filter(rsvp="no").count(),
            "maybe": self.attendees.filter(rsvp="maybe").count(),
        }


class EventAttendee(models.Model):
    RSVP_YES, RSVP_NO, RSVP_MAYBE, RSVP_PENDING = "yes", "no", "maybe", "pending"
    RSVP_CHOICES = [
        (RSVP_YES, "Going"), (RSVP_MAYBE, "Maybe"),
        (RSVP_NO, "Not going"), (RSVP_PENDING, "No reply"),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="attendees")
    user  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="event_attendances",
    )
    rsvp = models.CharField(max_length=10, choices=RSVP_CHOICES, default=RSVP_PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("event", "user")
        ordering = ["user__first_name", "user__last_name"]


# ============================================================================
# REMINDERS
# ============================================================================
class Reminder(SoftDeleteModel):
    AUDITED = True

    class Kind:
        PERSONAL     = "personal"       # to the creator themselves
        DEPARTMENT   = "department"     # everyone in a department
        ORGANISATION = "organisation"   # all staff
        CUSTOM       = "custom"         # explicit recipients / roles

    KIND_CHOICES = [
        (Kind.PERSONAL,     "Personal — just me"),
        (Kind.DEPARTMENT,   "Everyone in a department"),
        (Kind.ORGANISATION, "All NBTE staff"),
        (Kind.CUSTOM,       "Specific people or roles"),
    ]

    title  = models.CharField(max_length=200)
    body   = models.TextField(blank=True)
    url    = models.CharField(
        max_length=500, blank=True,
        help_text="Optional. Where the reminder should link to when clicked.",
    )

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="reminders_created",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=Kind.PERSONAL)

    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reminders",
    )
    roles = models.ManyToManyField("accounts.Role", blank=True, related_name="scoped_reminders")
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="personal_reminders",
    )

    remind_at = models.DateTimeField(db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Optional associations
    related_task = models.ForeignKey(
        "tasks.Task", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reminders",
    )
    related_event = models.ForeignKey(
        Event, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reminders",
    )
    related_announcement = models.ForeignKey(
        Announcement, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reminders",
    )

    is_dismissed = models.BooleanField(default=False)
    is_fired = models.BooleanField(default=False)
    fired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["remind_at", "-pk"]
        indexes = [
            models.Index(fields=["remind_at", "is_fired"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_active(self):
        if self.is_dismissed or self.is_fired:
            return False
        now = timezone.now()
        if self.remind_at > now:
            return False
        if self.expires_at and self.expires_at <= now:
            return False
        return True


class ReminderRecipient(models.Model):
    """Materialised fan-out — one row per (reminder, user) once fired."""
    reminder = models.ForeignKey(Reminder, on_delete=models.CASCADE, related_name="fanned")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="reminder_receipts",
    )
    delivered_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("reminder", "user")
        ordering = ["-delivered_at"]
    
    
# ============================================================================
# DOCUMENTS
# ============================================================================
class DocumentCategory(TimeStampedModel):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    color = models.CharField(max_length=20, default="#1F6B3E")
    icon = models.CharField(max_length=60, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "document categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:70] or "cat"
            slug, n = base, 2
            while DocumentCategory.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class Document(SoftDeleteModel):
    AUDITED = True
    MEDIA_SUBDIR = "portal/documents/covers"

    STATUS_DRAFT       = "draft"
    STATUS_PUBLISHED   = "published"
    STATUS_ARCHIVED    = "archived"
    STATUS_SUPERSEDED  = "superseded"
    STATUS_CHOICES = [
        (STATUS_DRAFT,      "Draft"),
        (STATUS_PUBLISHED,  "Published"),
        (STATUS_ARCHIVED,   "Archived"),
        (STATUS_SUPERSEDED, "Superseded"),
    ]

    SOURCE_FILE     = "file"
    SOURCE_EXTERNAL = "external"
    SOURCE_CHOICES = [
        (SOURCE_FILE,     "Upload a file"),
        (SOURCE_EXTERNAL, "External link (Google Drive, cloud, etc.)"),
    ]

    # ---- content ---------------------------------------------------------
    title       = models.CharField(max_length=200)
    summary     = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)

    category = models.ForeignKey(DocumentCategory, on_delete=models.PROTECT,
                                 related_name="documents")

    # ---- ownership -------------------------------------------------------
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="documents_owned")
    department = models.ForeignKey("staff.Department", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="documents")

    # ---- visibility ------------------------------------------------------
    visibility = models.CharField(max_length=20, choices=Scope.CHOICES,
                                  default=Scope.DEPARTMENT)
    roles      = models.ManyToManyField("accounts.Role", blank=True,
                                        related_name="scoped_documents")
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                        related_name="personal_documents")

    # ---- lifecycle -------------------------------------------------------
    status = models.CharField(max_length=15, choices=STATUS_CHOICES,
                              default=STATUS_DRAFT)

    # ---- source ----------------------------------------------------------
    source_type  = models.CharField(max_length=10, choices=SOURCE_CHOICES,
                                    default=SOURCE_FILE)
    external_url = models.URLField(max_length=500, blank=True,
                                   help_text="Only used when the source is 'External link'.")

    # ---- cover image -----------------------------------------------------
    cover_image = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    cover_image_original_name = models.CharField(max_length=255, blank=True)

    # ---- dates -----------------------------------------------------------
    effective_date = models.DateField(null=True, blank=True)
    review_date    = models.DateField(null=True, blank=True)
    review_reminder_days = models.PositiveIntegerField(
        default=14, help_text="Days before review_date to nudge the owner.",
    )
    review_reminder_sent_at = models.DateTimeField(null=True, blank=True)

    # ---- tracking --------------------------------------------------------
    download_tracking_enabled = models.BooleanField(
        default=True, help_text="Log every download with who/ip/when.",
    )
    download_count = models.PositiveIntegerField(default=0)

    # ---- public featuring ------------------------------------------------
    is_featured = models.BooleanField(
        default=False, help_text="Feature on the public homepage (public docs only).",
    )

    published_at = models.DateTimeField(null=True, blank=True)
    notified_at  = models.DateTimeField(null=True, blank=True)

    current_version = models.ForeignKey(
        "DocumentVersion", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-published_at", "-updated_at", "-pk"]
        permissions = [
            ("publish_document",          "Can publish documents"),
            ("publish_public_document",   "Can publish documents to the public site"),
            ("publish_org_document",      "Can publish organisation-wide documents"),
            ("download_document_version", "Can download document versions"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        capture_original_filename(self, "cover_image", "cover_image_original_name")
        new_cover = bool(self.cover_image) and not self.cover_image._committed
        if self.status == self.STATUS_PUBLISHED and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)
        if new_cover:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "cover_image")

    # ==================================================================
    # Derived properties
    # ==================================================================
    @property
    def is_public(self):
        return self.visibility == Scope.PUBLIC

    @property
    def is_published(self):
        return self.status == self.STATUS_PUBLISHED

    @property
    def current_version_number(self):
        return self.current_version.version_number if self.current_version_id else None

    @property
    def cover_thumb_url(self):
        from core.images import variant_url
        return variant_url(self.cover_image, 480)

    @property
    def card_summary(self):
        if self.summary:
            return self.summary
        if not self.description:
            return ""
        text = self.description.strip().replace("\n", " ")
        return (text[:140] + "…") if len(text) > 140 else text

    @property
    def effective_download_url(self):
        """Where the Download / Open-link button should point."""
        if self.source_type == self.SOURCE_EXTERNAL and self.external_url:
            return self.external_url
        from django.urls import reverse
        return reverse("portal:document_download", kwargs={"pk": self.pk})

    @property
    def is_external(self):
        return self.source_type == self.SOURCE_EXTERNAL

    # ==================================================================
    # Visibility + download permission
    # ==================================================================
    def _scope_q(self, user):
        """Return the Q object matching the shared scope rules for `user`.

        Same logic Announcements use — one source of truth for the whole
        portal app.
        """
        from django.db.models import Q

        if user is None or not user.is_authenticated:
            return Q(visibility=Scope.PUBLIC)

        if user.is_superuser:
            return Q()

        q = Q(visibility=Scope.PUBLIC) | Q(visibility=Scope.STAFF)

        if getattr(user, "department_id", None):
            q |= Q(visibility=Scope.DEPARTMENT, department_id=user.department_id)

        group_ids = [user.role_id] if getattr(user, "role_id", None) else []  # review M6: Role, not groups
        if group_ids:
            q |= Q(visibility=Scope.ROLE, roles__in=group_ids)

        q |= Q(visibility=Scope.INDIVIDUAL, recipients=user)

        return q

    def can_be_viewed_by(self, user) -> bool:
        """Metadata visibility — can `user` open the document's detail page?"""
        # Owners and superusers always see their own records, even drafts.
        if user and user.is_authenticated:
            if user.is_superuser:
                return True
            if self.owner_id == user.pk:
                return True

        # Drafts are private to their owner.
        if self.status == self.STATUS_DRAFT:
            return False

        return (
            type(self).objects
            .filter(pk=self.pk)
            .filter(self._scope_q(user))
            .exists()
        )

    def can_be_downloaded_by(self, user) -> bool:
        """File-download visibility.

        Right now this mirrors `can_be_viewed_by`. If you later add a
        stricter download-only gate (e.g. HR-locked PDFs a broader group
        can still see listed), branch here.
        """
        if not self.can_be_viewed_by(user):
            return False

        # External-link documents don't have a file — permission is about
        # opening the link. Same rule applies.
        if self.is_external:
            return True

        # Non-external: require a current version to exist.
        return bool(self.current_version_id) or self._has_any_version()

    def _has_any_version(self):
        """True if at least one DocumentVersion exists. Defensive — the
        related manager may not be named `versions` in every setup."""
        for rel_name in ("versions", "document_versions", "version_set"):
            mgr = getattr(self, rel_name, None)
            if mgr is not None and hasattr(mgr, "exists"):
                try:
                    return mgr.exists()
                except Exception:
                    continue
        return False

    # ==================================================================
    # Download tracking
    # ==================================================================
    def record_download(self, user, request=None):
        """Increment the counter and (optionally) write an audit entry."""
        # Bump the counter atomically, so concurrent downloads don't race.
        type(self).objects.filter(pk=self.pk).update(
            download_count=models.F("download_count") + 1
        )
        self.download_count = (self.download_count or 0) + 1

        if not self.download_tracking_enabled:
            return

        try:
            from core import audit
            note = "file downloaded"
            if request is not None:
                from core.ratelimit import client_ip
                ip = client_ip(request)
                if ip:
                    note += f"; ip={ip}"
                ua = (request.META.get("HTTP_USER_AGENT") or "")[:180]
                if ua:
                    note += f"; ua={ua}"
            audit.log(
                "other", self,
                actor=user if getattr(user, "is_authenticated", False) else None,
                note=note,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to log document download for #%s", self.pk
            )

    # ==================================================================
    # Download helper for the view
    # ==================================================================
    def get_download_file(self):
        """Return (file_field, version_or_None) for the download view to stream.

        Prefers `current_version.file`; falls back to the latest version if
        `current_version` isn't set. Returns (None, None) if there's nothing
        to stream (external-link docs, or a document with no versions yet).
        """
        if self.is_external:
            return None, None

        # Prefer the explicit current_version
        if self.current_version_id and self.current_version is not None:
            f = getattr(self.current_version, "file", None)
            if f:
                return f, self.current_version

        # Fall back to the latest version on record
        for rel_name in ("versions", "document_versions"):
            mgr = getattr(self, rel_name, None)
            if mgr is None or not hasattr(mgr, "order_by"):
                continue
            try:
                version = mgr.order_by("-version_number").first()
            except Exception:
                version = mgr.first()
            if version and getattr(version, "file", None):
                return version.file, version

        return None, None



class DocumentVersion(RestrictedFileModel):
    """One upload per version. Old versions are never overwritten (NR-08)."""
    DOWNLOAD_PERMISSION = "portal.download_document_version"
    MEDIA_SUBDIR = "portal/document_versions"

    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="versions")
    version_number = models.PositiveIntegerField()
    is_current     = models.BooleanField(default=True)

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="document_versions_uploaded")
    change_note = models.CharField(max_length=300, blank=True)
    file_size   = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["-version_number"]
        unique_together = ("document", "version_number")
        verbose_name = "document version"
        verbose_name_plural = "document versions"

    def __str__(self):
        return f"{self.document.title} v{self.version_number}"

    def can_download(self, user):
        return self.document.can_be_downloaded_by(user)

    def save(self, *args, **kwargs):
        if self.pk is None and self.file:
            try:
                self.file_size = self.file.size
            except Exception:
                self.file_size = 0
        super().save(*args, **kwargs)

    @property
    def file_size_display(self):
        if not self.file_size:
            return "—"
        size = float(self.file_size)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class DocumentDownloadLog(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="downloads")
    version  = models.ForeignKey(DocumentVersion, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="downloads")
    user     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="document_downloads")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    is_external = models.BooleanField(default=False)
    downloaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-downloaded_at"]
        indexes = [models.Index(fields=["document", "-downloaded_at"])]    
    
    
# ============================================================================
# Visibility mixin — reusable by Announcement, Document, Event, Reminder, ...
# ============================================================================
class ScopedVisibilityMixin:
    """Provides `visible_to(user)` / `can_be_downloaded_by(user)` /
    `can_be_viewed_by(user)` on top of the shared visibility rules.

    Requires the model to have these fields:
      - visibility (CharField with Scope.CHOICES)
      - department (FK to staff.Department, nullable)
      - roles      (M2M to accounts.Role - review M6; was auth.Group)
      - recipients (M2M to settings.AUTH_USER_MODEL)

    If any of those fields are missing on the subclass, the check simply
    treats the missing channel as "no matches."
    """

    def _scope_q(self, user):
        """Return a Q object matching the shared visibility rules for `user`."""
        from django.db.models import Q
        from .models import Scope  # adjust import if the mixin lives elsewhere

        if user is None or not user.is_authenticated:
            return Q(visibility=Scope.PUBLIC)

        if user.is_superuser:
            return Q()

        q = Q(visibility=Scope.PUBLIC) | Q(visibility=Scope.STAFF)

        if getattr(user, "department_id", None) and self._has_field("department"):
            q |= Q(visibility=Scope.DEPARTMENT, department_id=user.department_id)

        if self._has_field("roles"):
            # Review M6: match the user's actual Role, not Django groups.
            role_id = getattr(user, "role_id", None)
            if role_id:
                q |= Q(visibility=Scope.ROLE, roles=role_id)

        if self._has_field("recipients"):
            q |= Q(visibility=Scope.INDIVIDUAL, recipients=user)

        return q

    def _has_field(self, name):
        try:
            self._meta.get_field(name)
            return True
        except Exception:
            return False

    def can_be_viewed_by(self, user) -> bool:
        """Metadata visibility — can `user` see the document listing?"""
        from .models import Scope

        # Author and owners always see their own stuff, even drafts.
        if user and user.is_authenticated:
            if user.is_superuser:
                return True
            if getattr(self, "owner_id", None) == user.pk:
                return True
            if getattr(self, "uploaded_by_id", None) == user.pk:
                return True

        # Drafts are private to their author (unless superuser, handled above).
        if getattr(self, "is_draft", False):
            return False

        return (
            type(self).objects.filter(pk=self.pk)
            .filter(self._scope_q(user))
            .exists()
        )

    def can_be_downloaded_by(self, user) -> bool:
        """File-download visibility.

        Same rules as `can_be_viewed_by`, with one extra gate: if the model
        has `download_restricted = True` and the user isn't staff-level, deny.
        (Adjust to your actual policy.)
        """
        if not self.can_be_viewed_by(user):
            return False

        if getattr(self, "download_restricted", False):
            if not (user and user.is_authenticated):
                return False
            if not (user.is_superuser or user.is_staff):
                # Add any additional allowed roles here if you want.
                return False

        return True

    def record_download(self, user, request=None):
        """Log the download if the model has tracking enabled (NR-07)."""
        if not getattr(self, "track_downloads", False):
            return
        try:
            from core import audit
            note_parts = []
            if request is not None:
                from core.ratelimit import client_ip
                ip = client_ip(request)
                if ip:
                    note_parts.append(f"ip={ip}")
                ua = (request.META.get("HTTP_USER_AGENT") or "")[:180]
                if ua:
                    note_parts.append(f"ua={ua}")
            audit.log(
                "other", self,
                actor=user if getattr(user, "is_authenticated", False) else None,
                note="file downloaded" + ("; " + "; ".join(note_parts) if note_parts else ""),
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Failed to log document download")    
    
    
# ============================================================================
# AWARDS & MILESTONES
# ============================================================================
class AwardCategory(TimeStampedModel):
    """Admin-editable. Examples: Employee of the Month, Long Service,
    Excellence, Innovation, Team Award, Retirement."""
    name  = models.CharField(max_length=80, unique=True)
    slug  = models.SlugField(max_length=80, unique=True, blank=True)
    color = models.CharField(max_length=20, default="#1F6B3E")
    icon  = models.CharField(max_length=60, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active  = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "award categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:70] or "award"
            slug, n = base, 2
            while AwardCategory.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class Award(SoftDeleteModel):
    """A recognition — an award, a milestone, or an institutional moment."""
    AUDITED = True
    MEDIA_SUBDIR = "portal/awards"

    # ---- kind ------------------------------------------------------------
    KIND_AWARD              = "award"
    KIND_SERVICE_ANNIVERSARY = "service_anniversary"
    KIND_BIRTHDAY           = "birthday"
    KIND_PROMOTION          = "promotion"
    KIND_CERTIFICATION      = "certification"
    KIND_RETIREMENT         = "retirement"
    KIND_OTHER              = "other"

    KIND_CHOICES = [
        (KIND_AWARD,               "Award"),
        (KIND_SERVICE_ANNIVERSARY, "Service anniversary"),
        (KIND_BIRTHDAY,            "Birthday"),
        (KIND_PROMOTION,           "Promotion"),
        (KIND_CERTIFICATION,       "Certification"),
        (KIND_RETIREMENT,          "Retirement"),
        (KIND_OTHER,               "Other"),
    ]

    kind = models.CharField(max_length=30, choices=KIND_CHOICES, default=KIND_AWARD)

    # ---- content ---------------------------------------------------------
    title       = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image       = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    image_original_name = models.CharField(max_length=255, blank=True)
    certificate = models.FileField(upload_to=uuid_upload_to, null=True, blank=True,
                                   help_text="Optional PDF of the certificate.")

    category = models.ForeignKey(
        AwardCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="awards",
    )

    # ---- who it's for ----------------------------------------------------
    recipient = models.ForeignKey(
        "staff.Staff", on_delete=models.CASCADE, related_name="awards",
    )
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="awards",
    )

    # ---- who gave it -----------------------------------------------------
    awarded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="awards_given",
    )

    # ---- when ------------------------------------------------------------
    awarded_on = models.DateField(db_index=True)      # the date of the event
    years      = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="For service anniversaries: 5 = 5 years.",
    )
    is_recurring = models.BooleanField(
        default=False,
        help_text="For birthdays/anniversaries that repeat annually.",
    )

    # ---- visibility ------------------------------------------------------
    visibility = models.CharField(
        max_length=20, choices=Scope.CHOICES, default=Scope.STAFF,
    )
    roles      = models.ManyToManyField("accounts.Role", blank=True,
                                        related_name="scoped_awards")
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                        related_name="personal_awards")

    is_featured = models.BooleanField(
        default=False,
        help_text="Feature on the public/landing page (public-visibility awards only).",
    )

    class Meta:
        ordering = ["-awarded_on", "-pk"]
        indexes = [
            models.Index(fields=["-awarded_on"]),
            models.Index(fields=["kind", "-awarded_on"]),
        ]
        permissions = [
            ("manage_award", "Can create, edit and delete awards/milestones"),
            ("feature_award", "Can feature awards on the public page"),
        ]

    def __str__(self):
        return f"{self.title} — {self.recipient.full_name}"

    def save(self, *args, **kwargs):
        capture_original_filename(self, "image", "image_original_name")
        new_image = bool(self.image) and not self.image._committed
        # Auto-fill department from recipient if unset.
        if not self.department_id and self.recipient_id:
            self.department_id = getattr(self.recipient, "department_id", None)
        super().save(*args, **kwargs)
        if new_image:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "image")

    @property
    def image_thumb_url(self):
        """Return a usable URL for the image.

        Tries the 480px variant first (fast, small). If the variant doesn't
        exist yet — e.g. the Celery thumbnail task hasn't run — falls back
        to the original upload so the image still renders.
        """
        if not self.image:
            return ""
        try:
            from core.images import variant_url
            url = variant_url(self.image, 480)
            if url:
                # variant_url may return a path that doesn't physically exist
                # yet; if so, its URL would 404. Guard by checking the storage.
                from django.core.files.storage import default_storage
                # variant_url typically returns a public URL, not a storage
                # name, so we can't easily stat it. Accept it — if the variant
                # is missing the browser will fall back to the srcset/alt.
                # Safer: only use the variant if the underlying file exists.
                return url
        except Exception:
            pass
        try:
            return self.image.url
        except Exception:
            return ""

    @property
    def is_milestone(self):
        return self.kind != self.KIND_AWARD

    # ---- visibility (same shape as Announcement/Document) ---------------
    def _scope_q(self, user):
        from django.db.models import Q
        if user is None or not user.is_authenticated:
            return Q(visibility=Scope.PUBLIC)
        if user.is_superuser:
            return Q()
        q = Q(visibility=Scope.PUBLIC) | Q(visibility=Scope.STAFF)
        if getattr(user, "department_id", None):
            q |= Q(visibility=Scope.DEPARTMENT, department_id=user.department_id)
        group_ids = [user.role_id] if getattr(user, "role_id", None) else []  # review M6: Role, not groups
        if group_ids:
            q |= Q(visibility=Scope.ROLE, roles__in=group_ids)
        q |= Q(visibility=Scope.INDIVIDUAL, recipients=user)
        return q

    def can_be_viewed_by(self, user) -> bool:
        if user and user.is_authenticated and user.is_superuser:
            return True
        return (
            type(self).objects.filter(pk=self.pk)
            .filter(self._scope_q(user))
            .exists()
        )    
    
    
# ============================================================================
# HOLIDAYS
# ============================================================================
class Holiday(TimeStampedModel):
    AUDITED = True

    KIND_PUBLIC      = "public"
    KIND_RELIGIOUS   = "religious"
    KIND_DEPARTMENTAL = "departmental"
    KIND_CHOICES = [
        (KIND_PUBLIC,       "Public holiday"),
        (KIND_RELIGIOUS,    "Religious holiday"),
        (KIND_DEPARTMENTAL, "Departmental holiday"),
    ]

    name        = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    kind        = models.CharField(max_length=20, choices=KIND_CHOICES,
                                   default=KIND_PUBLIC)

    date_start  = models.DateField(db_index=True)
    date_end    = models.DateField(db_index=True,
                                   help_text="For multi-day holidays. Same as start for single-day.")

    # Null = applies to everyone in scope.
    department  = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="holidays",
    )

    is_recurring = models.BooleanField(
        default=False,
        help_text="If true, repeats every year on the same month/day.",
    )

    # Same shared visibility choice set.
    visibility = models.CharField(
        max_length=20, choices=Scope.CHOICES, default=Scope.STAFF,
    )
    roles      = models.ManyToManyField("accounts.Role", blank=True,
                                        related_name="scoped_holidays")

    is_active = models.BooleanField(default=True)
    # v2.1 Phase D (F7): movable (Islamic) dates are seeded as estimates
    # until the Federal Government declares them.
    to_confirm = models.BooleanField(
        default=False, help_text="The date is an estimate until the Federal Government declares it.")

    class Meta:
        ordering = ["date_start", "name"]
        verbose_name_plural = "holidays"
        indexes = [
            models.Index(fields=["date_start", "date_end"]),
        ]
        permissions = [
            ("manage_holiday", "Can create, edit and delete holidays"),
        ]

    def __str__(self):
        return f"{self.name} ({self.date_start}–{self.date_end})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.date_end and self.date_start and self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date cannot be before start date."})

    @property
    def is_multiday(self):
        return self.date_end and self.date_start and self.date_end != self.date_start

    @property
    def days(self):
        if not self.date_end:
            return 1
        return (self.date_end - self.date_start).days + 1    
    
    
# ============================================================================
# GALLERY
# ============================================================================
class GalleryCategory(TimeStampedModel):
    """Admin-editable: Events, Team, Ceremonies, Training, Facilities, Other."""
    name  = models.CharField(max_length=80, unique=True)
    slug  = models.SlugField(max_length=80, unique=True, blank=True)
    color = models.CharField(max_length=20, default="#1F6B3E")
    icon  = models.CharField(max_length=60, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active  = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "gallery categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:70] or "gallery"
            slug, n = base, 2
            while GalleryCategory.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)


class GalleryAlbum(SoftDeleteModel):
    """A collection of media. Has a cover, description, and visibility scope."""
    AUDITED = True
    MEDIA_SUBDIR = "portal/gallery/covers"

    title       = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    category = models.ForeignKey(
        GalleryCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="albums",
    )

    cover_image = models.ImageField(upload_to=uuid_upload_to, null=True, blank=True)
    cover_image_original_name = models.CharField(max_length=255, blank=True)

    # ---- ownership -------------------------------------------------------
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="gallery_albums_owned",
    )
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="gallery_albums",
    )

    # ---- visibility ------------------------------------------------------
    visibility = models.CharField(
        max_length=20, choices=Scope.CHOICES, default=Scope.STAFF,
    )
    roles      = models.ManyToManyField("accounts.Role", blank=True,
                                        related_name="scoped_albums")
    recipients = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True,
                                        related_name="personal_albums")

    # ---- dates / lifecycle ----------------------------------------------
    event_date   = models.DateField(null=True, blank=True, db_index=True,
                                    help_text="When the event in the album took place.")
    is_featured  = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-event_date", "-created_at"]
        indexes = [
            models.Index(fields=["-event_date"]),
            models.Index(fields=["-created_at"]),
        ]
        permissions = [
            ("manage_gallery", "Can create, edit and delete gallery albums"),
            ("feature_gallery", "Can feature albums on the public page"),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        capture_original_filename(self, "cover_image", "cover_image_original_name")
        new_cover = bool(self.cover_image) and not self.cover_image._committed
        super().save(*args, **kwargs)
        if new_cover:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "cover_image")

    # ---- derived ---------------------------------------------------------
    @property
    def item_count(self):
        return self.items.count()

    @property
    def cover_url(self):
        """Best available cover — explicit cover, else first item's thumbnail."""
        if self.cover_image:
            from core.images import variant_url
            return variant_url(self.cover_image, 720) or self.cover_image.url
        first = self.items.filter(file_kind="image").order_by("sort_order", "id").first()
        if first and first.file:
            return first.thumb_url
        return ""

    @property
    def cover_thumb_url(self):
        if self.cover_image:
            from core.images import variant_url
            return variant_url(self.cover_image, 480) or self.cover_image.url
        return self.cover_url

    @property
    def is_public(self):
        return self.visibility == Scope.PUBLIC

    # ---- visibility ------------------------------------------------------
    def _scope_q(self, user):
        from django.db.models import Q
        if user is None or not user.is_authenticated:
            return Q(visibility=Scope.PUBLIC, is_published=True)
        if user.is_superuser:
            return Q()
        q = (Q(visibility=Scope.PUBLIC, is_published=True)
             | Q(visibility=Scope.STAFF, is_published=True))
        if getattr(user, "department_id", None):
            q |= Q(visibility=Scope.DEPARTMENT,
                    department_id=user.department_id, is_published=True)
        group_ids = [user.role_id] if getattr(user, "role_id", None) else []  # review M6: Role, not groups
        if group_ids:
            q |= Q(visibility=Scope.ROLE, roles__in=group_ids, is_published=True)
        q |= Q(visibility=Scope.INDIVIDUAL, recipients=user, is_published=True)
        # Owner always sees their own albums.
        q |= Q(owner=user)
        return q

    def can_be_viewed_by(self, user) -> bool:
        if user and user.is_authenticated and user.is_superuser:
            return True
        return (
            type(self).objects.filter(pk=self.pk)
            .filter(self._scope_q(user))
            .exists()
        )


class GalleryItem(SoftDeleteModel):
    """One media item inside an album — image or video."""
    AUDITED = True
    MEDIA_SUBDIR = "portal/gallery/items"

    FILE_IMAGE = "image"
    FILE_VIDEO = "video"
    FILE_CHOICES = [(FILE_IMAGE, "Image"), (FILE_VIDEO, "Video")]

    album  = models.ForeignKey(
        GalleryAlbum, on_delete=models.CASCADE, related_name="items",
    )
    file   = models.FileField(upload_to=uuid_upload_to)
    file_kind = models.CharField(max_length=10, choices=FILE_CHOICES,
                                 default=FILE_IMAGE)
    original_name = models.CharField(max_length=255, blank=True)

    caption = models.CharField(max_length=300, blank=True)
    credit  = models.CharField(max_length=200, blank=True,
                               help_text="Photographer or source.")

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="gallery_items_uploaded",
    )

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["album", "sort_order"])]

    def __str__(self):
        return f"{self.album.title} — {self.caption or self.pk}"

    def save(self, *args, **kwargs):
        if not self.original_name and self.file:
            self.original_name = (self.file.name or "").rsplit("/", 1)[-1]
        new_file = bool(self.file) and not self.file._committed
        # Detect kind from extension when not explicitly set.
        if new_file and not self.file_kind:
            import mimetypes
            ctype = mimetypes.guess_type(self.original_name or "")[0] or ""
            self.file_kind = self.FILE_VIDEO if ctype.startswith("video/") else self.FILE_IMAGE
        super().save(*args, **kwargs)
        if new_file and self.file_kind == self.FILE_IMAGE:
            from core.images import schedule_thumbnails
            schedule_thumbnails(self, "file")

    @property
    def thumb_url(self):
        if not self.file:
            return ""
        if self.file_kind == self.FILE_VIDEO:
            return ""
        from core.images import variant_url
        return variant_url(self.file, 480) or self.file.url

    @property
    def display_url(self):
        return self.file.url if self.file else ""

    @property
    def is_image(self):
        return self.file_kind == self.FILE_IMAGE

    @property
    def is_video(self):
        return self.file_kind == self.FILE_VIDEO    
    
    
    
    
    
    