"""Institutions (QA) — Master Plan §5.2, Phase 2.

The Phase 1 stub (`code`, `name`, `handling_department`, `status`) grows into
the full institution record by additive migrations only: every new column is
nullable or defaulted, so Phase 1 rows and code keep working.

Scoping (Master Plan §4): every model that belongs to an institution declares
``INSTITUTION_PATH`` and uses :class:`InstitutionScopedManager` as its
default manager, so ``Model.objects.for_user(user)`` is the only way to read
it from a view; system code uses ``Model.unscoped``.

Deliberate, documented differences from §5.2 (PHASE2_CHANGELOG.md):
* ``Institution.type`` is called ``institution_type`` (``type`` shadows a
  Python builtin and reads badly in queries).
* ``Institution`` is not a SoftDeleteModel: a TVET is never deleted, it is
  delisted (status ``delisted``), and every row is referenced by the
  append-only timeline.
* ``Programme.approval_type`` arrives with ``accreditation.ApprovalType`` in
  Phase 6 (decisions); Phase 2 programmes are all ``proposed``.
* ``ProgrammeLevel`` is a table (the plan: "the levels are an editable lookup").
* ``ImpersonationSession`` and ``InstitutionImportBatch`` are added: the first
  records every read-only portal view-as session (§6 safeguards), the second
  holds a validated spreadsheet while it waits for a second officer.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from qa_core.files import QAFileMixin
from qa_core.scoping import (
    InstitutionScopedManager,
    InstitutionScopedModelMixin,
    UnscopedInstitutionManager,
)

USER = settings.AUTH_USER_MODEL

# Q-02 default: 3–6 uppercase letters or digits. Codes are embedded in
# references, so once issued a code never changes (renames use
# "Update TVET Name").
institution_code_validator = RegexValidator(
    r"^[A-Z0-9]{3,6}$",
    "Use 3 to 6 uppercase letters or digits, for example KDP.",
)


class LookupBase(TimeStampedModel):
    """Admin-editable list with a stable code (Settings → QA → Lookups)."""

    AUDITED = True

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


# ===========================================================================
# Lookups
# ===========================================================================
class InstitutionOwnership(LookupBase):
    """Federal, State, Private. `is_public` drives the document rules."""

    is_public = models.BooleanField(default=True)

    class Meta(LookupBase.Meta):
        verbose_name = "ownership"


class InstitutionType(LookupBase):
    """Polytechnic, Monotechnic, College of Health …"""

    default_department = models.ForeignKey(
        "staff.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Department that handles approved institutions of this type when the category does not say.",
    )

    class Meta(LookupBase.Meta):
        verbose_name = "institution type"


class InstitutionCategory(LookupBase):
    """The "Update Category" target; `routing_department` is the
    category → department mapping (B2, R-10)."""

    institution_type = models.ForeignKey(InstitutionType, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="categories")
    routing_department = models.ForeignKey(
        "staff.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Department that handles approved institutions in this category (DPAP, DNHTP, VTSD …).",
    )

    class Meta(LookupBase.Meta):
        verbose_name = "institution category"
        verbose_name_plural = "institution categories"


class Discipline(LookupBase):
    class Meta(LookupBase.Meta):
        pass


class ProgrammeLevel(LookupBase):
    """ND, HND, NID, NVC, ADMIN (editable)."""

    class Meta(LookupBase.Meta):
        pass


class ProgrammeCategory(TimeStampedModel):
    AUDITED = True

    name = models.CharField(max_length=150, unique=True)
    discipline = models.ForeignKey(Discipline, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="programme_categories")
    students_per_stream = models.PositiveIntegerField(default=30, help_text="C1/R-06 (editable).")
    is_technology_based = models.BooleanField(default=False, help_text="Requirement 12 — internal only.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "programme categories"

    def __str__(self):
        return self.name


class DocumentKind(LookupBase):
    """CAC Certificate, Bank Guarantee, Enabling Law …"""

    description = models.TextField(blank=True)
    accepted_extensions = models.JSONField(default=list, blank=True,
                                           help_text='e.g. ["pdf", "jpg", "png"]')
    max_mb = models.PositiveIntegerField(default=10)

    class Meta(LookupBase.Meta):
        pass

    def extensions(self):
        exts = [str(e).lower().lstrip(".") for e in (self.accepted_extensions or []) if e]
        return exts or ["pdf"]


class DocumentRequirement(TimeStampedModel):
    """Requirement 1: which documents a process needs, by ownership and type.
    A blank ownership or type means "any"."""

    AUDITED = True
    PROCESS_REGISTRATION = "registration"
    PROCESS_OWNERSHIP_CHANGE = "ownership_change"

    ownership = models.ForeignKey(InstitutionOwnership, null=True, blank=True, on_delete=models.CASCADE,
                                  related_name="document_requirements")
    institution_type = models.ForeignKey(InstitutionType, null=True, blank=True, on_delete=models.CASCADE,
                                         related_name="document_requirements")
    process = models.CharField(max_length=60, default=PROCESS_REGISTRATION,
                               help_text="registration, ownership_change, qa_request:<type> …")
    document_kind = models.ForeignKey(DocumentKind, on_delete=models.PROTECT, related_name="requirements")
    is_mandatory = models.BooleanField(default=True)
    reviewing_department = models.ForeignKey("staff.Department", null=True, blank=True,
                                             on_delete=models.SET_NULL, related_name="+")
    refresh_after_months = models.PositiveIntegerField(
        null=True, blank=True, help_text="Ask again after this many months (blank = never ask twice).")
    notes = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["process", "document_kind__sort_order", "document_kind__name"]

    def __str__(self):
        scope = " · ".join(filter(None, [
            self.ownership.name if self.ownership_id else "Any ownership",
            self.institution_type.name if self.institution_type_id else "",
        ]))
        return f"{self.document_kind} ({scope}, {self.process})"


# ===========================================================================
# Institution
# ===========================================================================
class Institution(InstitutionScopedModelMixin, TimeStampedModel):
    """A TVET institution ("TVET" in the UI, matching the current portal)."""

    AUDITED = True
    # Scoping (Master Plan §4): this model IS the institution.
    INSTITUTION_PATH = ""
    # The institution-lifecycle flow mirrors its stage into `status`.
    FLOW_STATUS_FIELD = "status"

    PROPOSED, UNDER_REVIEW, APPROVED, SUSPENDED, DELISTED = (
        "proposed", "under_review", "approved", "suspended", "delisted",
    )
    STATUS_LABELS = {
        PROPOSED: "Proposed", UNDER_REVIEW: "Under review", APPROVED: "Approved",
        SUSPENDED: "Suspended", DELISTED: "Delisted",
    }
    ORIGIN_CHOICES = [("nbte", "Added by NBTE (Add TVET)"), ("self", "Self-registered on the portal"),
                      ("import", "Bulk import"), ("legacy", "Existing record")]

    code = models.CharField(
        max_length=6, unique=True, validators=[institution_code_validator],
        help_text="Short code used in references, e.g. KDP. Never changes once issued.",
    )
    name = models.CharField(max_length=255)
    former_names = models.JSONField(default=list, blank=True)
    ownership = models.ForeignKey(InstitutionOwnership, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="institutions")
    institution_type = models.ForeignKey(InstitutionType, null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="institutions", verbose_name="type")
    category = models.ForeignKey(InstitutionCategory, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="institutions")
    handling_department = models.ForeignKey(
        "staff.Department", on_delete=models.PROTECT, related_name="handled_institutions",
        help_text="DPAP, DNHTP, NTIRD or VTSD. Drives staff scoping.",
    )
    status = models.CharField(max_length=40, default=PROPOSED, db_index=True)
    state = models.ForeignKey("staff.StateOfOrigin", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    lga = models.CharField(max_length=100, blank=True)
    zone = models.CharField(max_length=20, blank=True, help_text="Derived from the state.")
    year_established = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1900), MaxValueValidator(2100)])
    cac_rc_number = models.CharField("CAC RC number", max_length=40, blank=True)
    website = models.URLField(blank=True)
    desk_officer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    portal_enabled = models.BooleanField(default=True)
    origin = models.CharField(max_length=10, choices=ORIGIN_CHOICES, default="legacy")
    approved_at = models.DateTimeField(null=True, blank=True)
    delisted_at = models.DateTimeField(null=True, blank=True)
    delist_reason = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["name"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name = "institution (TVET)"
        verbose_name_plural = "institutions (TVETs)"
        # Master Plan §12: every institutions.* codename. Two-step actions get
        # initiate_/confirm_ pairs; the handler registry maps them.
        permissions = [
            ("initiate_add_tvet", "QA: can start Add TVET"),
            ("confirm_add_tvet", "QA: can confirm Add TVET"),
            ("initiate_approve_institution", "QA: can start Approve Institution"),
            ("confirm_approve_institution", "QA: can confirm Approve Institution"),
            ("initiate_update_contact", "QA: can start Update Contact"),
            ("confirm_update_contact", "QA: can confirm Update Contact"),
            ("initiate_update_address", "QA: can start Update Address"),
            ("confirm_update_address", "QA: can confirm Update Address"),
            ("initiate_update_state_lga", "QA: can start Update State & LGA"),
            ("confirm_update_state_lga", "QA: can confirm Update State & LGA"),
            ("initiate_update_category", "QA: can start Update Category"),
            ("confirm_update_category", "QA: can confirm Update Category"),
            ("initiate_update_name", "QA: can start Update TVET Name"),
            ("confirm_update_name", "QA: can confirm Update TVET Name"),
            ("initiate_update_ownership", "QA: can start Update Ownership"),
            ("confirm_update_ownership", "QA: can confirm Update Ownership"),
            ("initiate_delist", "QA: can start Delist Institution"),
            ("confirm_delist", "QA: can confirm Delist Institution"),
            ("initiate_relist", "QA: can start Relist Institution"),
            ("confirm_relist", "QA: can confirm Relist Institution"),
            ("initiate_drop_programmes", "QA: can start Drop Programmes"),
            ("confirm_drop_programmes", "QA: can confirm Drop Programmes"),
            ("initiate_wipe_requests", "QA: can start Wipe Institution Requests"),
            ("confirm_wipe_requests", "QA: can confirm Wipe Institution Requests"),
            ("initiate_cancel_institution_request", "QA: can start Cancel Request"),
            ("confirm_cancel_institution_request", "QA: can confirm Cancel Request"),
            ("initiate_revert_qa_history", "QA: can start Revert QA History"),
            ("confirm_revert_qa_history", "QA: can confirm Revert QA History"),
            ("initiate_add_programme_to_qa", "QA: can start Add Programme to QA"),
            ("confirm_add_programme_to_qa", "QA: can confirm Add Programme to QA"),
            ("initiate_suspend_portal_access", "QA: can start Suspend/Restore Portal Access"),
            ("confirm_suspend_portal_access", "QA: can confirm Suspend/Restore Portal Access"),
            ("initiate_bulk_import", "QA: can start a bulk institution/programme import"),
            ("confirm_bulk_import", "QA: can confirm a bulk institution/programme import"),
            ("export_institutions", "QA: can export institutions and programmes"),
            ("send_login_credentials", "QA: can send portal login credentials"),
            ("impersonate_institution", "QA: can view the portal as an institution (read-only)"),
            ("manage_document_requirements", "QA: can edit document requirements"),
            ("manage_institution_lookups", "QA: can edit institution lookups"),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    # -- derived values ---------------------------------------------------
    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status.replace("_", " ").capitalize())

    @property
    def is_public(self):
        return bool(self.ownership_id and self.ownership.is_public)

    @property
    def ownership_code(self):
        return self.ownership.code if self.ownership_id else ""

    def derive_handling_department(self, *, status=None):
        """Master Plan §5.2: proposed → NTIRD; else category.routing_department
        (falling back to the type's default department)."""
        from staff.models import Department

        status = status or self.status
        if status in (self.PROPOSED, self.UNDER_REVIEW):
            ntird = Department.objects.filter(code="ntird").first()
            if ntird is not None:
                return ntird
        if self.category_id and self.category.routing_department_id:
            return self.category.routing_department
        if self.institution_type_id and self.institution_type.default_department_id:
            return self.institution_type.default_department
        return self.handling_department if self.handling_department_id else None

    def recompute_zone(self):
        self.zone = (self.state.zone if self.state_id else "") or ""
        return self.zone

    def contact(self, kind):
        return (InstitutionContact.unscoped.filter(institution=self, kind=kind, is_current=True)
                .order_by("-is_primary", "-id").first())

    def contacts_current(self):
        return InstitutionContact.unscoped.filter(institution=self, is_current=True).order_by("kind", "-is_primary")

    def address(self, kind=None):
        qs = InstitutionAddress.unscoped.filter(institution=self, is_current=True)
        if kind:
            qs = qs.filter(kind=kind)
        return qs.order_by("kind", "-id").first()

    def primary_user(self):
        link = (InstitutionUser.unscoped.filter(institution=self).select_related("user")
                .order_by("-is_primary_contact", "id").first())
        return link.user if link else None

    @property
    def state_name(self):
        return self.state.name if self.state_id else ""


class InstitutionContact(InstitutionScopedModelMixin, TimeStampedModel):
    """Edited through "Update Contact"; history is kept (old rows are closed)."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    KIND_CHOICES = [
        ("official_email", "Official e-mail"), ("phone", "Phone"),
        ("head_of_institution", "Head of institution"), ("registrar", "Registrar"),
        ("bursar", "Bursar"), ("librarian", "Librarian"),
    ]

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="contacts")
    kind = models.CharField(max_length=30, choices=KIND_CHOICES)
    value = models.CharField(max_length=255, help_text="E-mail address or phone number.")
    name = models.CharField(max_length=150, blank=True)
    is_primary = models.BooleanField(default=True)
    is_current = models.BooleanField(default=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["kind", "-is_primary", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.name or self.value}"


class InstitutionAddress(InstitutionScopedModelMixin, TimeStampedModel):
    """"Update Address" closes the old row and opens a new one."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    KIND_CHOICES = [("take_off_site", "Take-off site"), ("permanent_site", "Permanent site"), ("postal", "Postal")]

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="addresses")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="permanent_site")
    line1 = models.CharField("address", max_length=255)
    town = models.CharField(max_length=100, blank=True)
    lga = models.CharField(max_length=100, blank=True)
    state = models.ForeignKey("staff.StateOfOrigin", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+")
    gps = models.CharField("GPS", max_length=60, blank=True, help_text="Latitude, longitude (optional).")
    is_current = models.BooleanField(default=True, db_index=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["kind", "-started_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name_plural = "institution addresses"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.one_line}"

    @property
    def one_line(self):
        parts = [self.line1, self.town, self.lga, self.state.name if self.state_id else ""]
        return ", ".join(p for p in parts if p)


class InstitutionUser(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "institution"

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="memberships")
    user = models.OneToOneField(USER, on_delete=models.CASCADE, related_name="institution_membership")
    institution_role = models.ForeignKey("accounts.Role", null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="+")
    is_primary_contact = models.BooleanField(default=False)
    nominated_grade_level = models.PositiveSmallIntegerField(null=True, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    # v1.1 §9 (wired in Phase 5): the department recorded on an Institutional
    # Admin nomination, checked against qa.institution.admin_required_department.
    institution_department = models.CharField(max_length=120, blank=True)
    invited_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    credentials_last_sent_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-is_primary_contact", "user__first_name", "id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.user} @ {self.institution.code}"


# ===========================================================================
# Programmes and documents
# ===========================================================================
class Programme(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "institution"
    STATUS_CHOICES = [
        ("proposed", "Proposed"), ("resource_inspected", "Resource inspected"),
        ("approval_to_commence", "Approval to commence"), ("accredited", "Accredited"),
        ("interim", "Interim"), ("denied", "Denied"), ("expired", "Expired"), ("dropped", "Dropped"),
    ]

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="programmes")
    name = models.CharField(max_length=200)
    level = models.ForeignKey(ProgrammeLevel, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="programmes")
    category = models.ForeignKey(ProgrammeCategory, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="programmes")
    is_institutional_admin = models.BooleanField(default=False, help_text="Requirement 6.3.")
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="proposed", db_index=True)
    approved_streams = models.PositiveSmallIntegerField(default=0)
    requested_streams = models.PositiveSmallIntegerField(
        default=1, help_text="Streams asked for when the programme was proposed (SOP-01 programme fee).")
    approval_type = models.ForeignKey("accreditation.ApprovalType", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="programmes")
    commencement_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, help_text="Always derived (B18).")
    eligible_for_reassessment_at = models.DateField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    renewal_reminders_sent = models.JSONField(
        default=list, blank=True,
        help_text="Reminder thresholds already sent for the current expiry date, e.g. [\"2031-10-15:180\"].")
    source_application = models.ForeignKey("accreditation.Application", null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name="programmes")
    dropped_at = models.DateTimeField(null=True, blank=True)
    dropped_reason = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["name", "level__sort_order"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["institution", "name", "level"], name="inst_programme_unique"),
        ]

    def __str__(self):
        level = f"{self.level.code.upper()} " if self.level_id else ""
        return f"{level}{self.name}"


class InstitutionDocument(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """The permanent record of a statutory document (restricted storage)."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    MEDIA_SUBDIR = "qa/institution_documents"
    INSTITUTION_MEDIA_KIND = "documents"
    SUBMITTED, ACCEPTED, RETURNED, SUPERSEDED = "submitted", "accepted", "returned", "superseded"
    STATUS_CHOICES = [(SUBMITTED, "Submitted — awaiting review"), (ACCEPTED, "Accepted"),
                      (RETURNED, "Returned — upload a new version"), (SUPERSEDED, "Superseded")]

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="documents")
    kind = models.ForeignKey(DocumentKind, on_delete=models.PROTECT, related_name="documents")
    version = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="superseded_by")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=SUBMITTED, db_index=True)
    application = models.ForeignKey("accreditation.Application", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="documents")
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remark = models.TextField(blank=True)
    accepted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["kind__sort_order", "kind__name", "-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.kind} v{self.version} ({self.institution.code})"

    @property
    def is_live(self):
        return self.status in (self.SUBMITTED, self.ACCEPTED)

    def can_download(self, user):
        if user is None or not user.is_authenticated:
            return False
        if getattr(user, "user_type", "staff") == "institution":
            return user.institution_id == self.institution_id
        if getattr(user, "user_type", "staff") != "staff":
            return False
        if user.is_superuser or user.has_perm("qa_core.view_all_institutions") \
                or user.has_perm("accounts.access_all_departments"):
            return True
        if user.department_id and user.department_id == self.institution.handling_department_id:
            return True
        # Reviewing departments (Legal, Finance, Academic & Physical Planning)
        # may open the documents of applications in their clearance queue.
        if self.application_id and user.department_id:
            from accreditation.models import DepartmentalClearance

            return DepartmentalClearance.unscoped.filter(application_id=self.application_id,
                                                         department_id=user.department_id).exists()
        return False


# ===========================================================================
# Generic request container, zones
# ===========================================================================
class InstitutionRequestType(TimeStampedModel):
    AUDITED = True

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    flow = models.ForeignKey("qa_core.FlowDefinition", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    is_enabled = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class InstitutionRequest(InstitutionScopedModelMixin, TimeStampedModel):
    """The generic request container (confirmed decision 3)."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    OPEN_PRE_SUBMISSION = ("draft",)
    STATUS_CHOICES = [("draft", "Draft"), ("submitted", "Submitted"), ("in_progress", "In progress"),
                      ("closed", "Closed"), ("cancelled", "Cancelled")]

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="requests")
    request_type = models.ForeignKey(InstitutionRequestType, on_delete=models.PROTECT, related_name="requests")
    reference = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True)
    title = models.CharField(max_length=200, blank=True)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    object_id = models.CharField(max_length=64, blank=True)
    subject = GenericForeignKey("content_type", "object_id")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.request_type.name} {self.reference or '#' + str(self.pk)}"

    @property
    def is_pre_submission(self):
        return self.status in self.OPEN_PRE_SUBMISSION


class ZonalAssignment(TimeStampedModel):
    """Gives Zonal Directors their scope (B9). Zones are StateOfOrigin.zone."""

    AUDITED = True

    zone_code = models.CharField(max_length=4)
    staff = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="zonal_assignments")
    from_date = models.DateField(default=datetime.date.today)
    to_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["zone_code", "-from_date"]

    def __str__(self):
        return f"{self.zone_code}: {self.staff}"


# ===========================================================================
# Added: impersonation sessions and import batches
# ===========================================================================
class ImpersonationSession(InstitutionScopedModelMixin, TimeStampedModel):
    """One read-only, time-boxed portal view-as session (§6 safeguards)."""

    AUDITED = True
    INSTITUTION_PATH = "institution"

    institution = models.ForeignKey(Institution, on_delete=models.CASCADE, related_name="impersonations")
    staff = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    view_as = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    reason = models.TextField()
    write_mode = models.BooleanField(default=False)
    token = models.CharField(max_length=64, unique=True)
    started_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    token_used_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=40, blank=True)
    pages_viewed = models.PositiveIntegerField(default=0)
    page_log = models.JSONField(default=list, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-started_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.staff} as {self.institution.code} @ {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def is_live(self):
        return self.ended_at is None and self.expires_at > timezone.now()


class InstitutionImportBatch(QAFileMixin, TimeStampedModel):
    """A validated spreadsheet waiting for (or applied by) a second officer."""

    AUDITED = True
    MEDIA_SUBDIR = "qa/imports"
    KIND_CHOICES = [("institutions", "Institutions"), ("programmes", "Programmes"),
                    ("legacy", "Migration from the previous system")]
    STATUS_CHOICES = [("validated", "Validated"), ("invalid", "Has errors"),
                      ("pending", "Waiting for a second officer"), ("applied", "Applied"),
                      ("failed", "Failed"), ("rejected", "Rejected"), ("withdrawn", "Withdrawn")]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="validated")
    rows = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "institution import batches"

    def __str__(self):
        return f"{self.get_kind_display()} import #{self.pk} ({self.get_status_display()})"

    def can_download(self, user):
        return bool(user and user.is_authenticated and getattr(user, "user_type", "staff") == "staff"
                    and (user.pk == self.created_by_id or user.has_perm("institutions.confirm_bulk_import")))


# ===========================================================================
# Phase 6 — professional bodies and joint QA (Master Plan §6 extension)
# ===========================================================================
def programme_key(name):
    """Normalised programme name used to match the curriculum mapping to an
    institution's programme ("Pharmacy Technician" == "pharmacy  technician")."""
    import re

    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


class ProfessionalBody(TimeStampedModel):
    """A regulatory council that co-assesses some programmes with NBTE
    (e.g. the Pharmacy Council of Nigeria). NBTE-wide, not institution-scoped."""

    AUDITED = True

    name = models.CharField(max_length=200, unique=True)
    acronym = models.CharField(max_length=20, blank=True, help_text="e.g. PCN.")
    address = models.TextField(blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True, help_text="Joint QA notices are e-mailed here.")
    phone = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "professional bodies"
        permissions = [("manage_professional_bodies",
                        "QA: can edit professional bodies and their programme mappings")]

    def __str__(self):
        return f"{self.name} ({self.acronym})" if self.acronym else self.name


class ProgrammeBodyMapping(TimeStampedModel):
    """Links a curriculum programme (name + level, as in the catalogue) to the
    professional body that regulates it. Matching is by programme name, so one
    row covers that programme at every institution. A blank level covers every
    level (ND and HND)."""

    AUDITED = True

    body = models.ForeignKey(ProfessionalBody, on_delete=models.CASCADE, related_name="mappings")
    programme_name = models.CharField(max_length=200)
    programme_key = models.CharField(max_length=200, db_index=True, editable=False)
    level = models.ForeignKey(ProgrammeLevel, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="body_mappings", help_text="Blank = every level.")
    note = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["programme_name", "level__sort_order", "body__name"]
        constraints = [
            models.UniqueConstraint(fields=["body", "programme_key", "level"], name="pb_mapping_unique"),
            models.UniqueConstraint(fields=["body", "programme_key"], condition=models.Q(level__isnull=True),
                                    name="pb_mapping_unique_all_levels"),
        ]

    def __str__(self):
        level = f"{self.level.code.upper()} " if self.level_id else ""
        return f"{level}{self.programme_name} → {self.body}"

    def save(self, *args, **kwargs):
        self.programme_name = " ".join(str(self.programme_name or "").split())
        self.programme_key = programme_key(self.programme_name)
        super().save(*args, **kwargs)

    def matches(self, programme):
        return (self.programme_key == programme_key(programme.name)
                and (self.level_id is None or self.level_id == programme.level_id))
