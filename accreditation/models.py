"""Accreditation (QA) — Phase 2: SOP-01 onboarding only (Master Plan §5.4).

* ``ApplicationCycle`` — optional window for new applications.
* ``Application`` — a proposed institution's onboarding application. Its
  ``status`` mirrors the seeded ``onboarding`` flow (§5.7), driven by the
  Phase 1 flow engine — the state machine is data, not code.
* ``DepartmentalClearance`` — one row per parallel branch (Legal, Finance,
  Academic Planning, Physical Planning) per review round.

Phase 3 adds SOP-02 and the dynamic SSQ (§5.4):

* ``QAType`` — the seven QA types (editable list); each points at its flow,
  fee schedule and current published ``SSQTemplate``.
* ``QARequest`` / ``QARequestProgramme`` — an approved institution's request
  for QA on some of its programmes (flow ``qa_request``; cancel, drop and
  replace only in the four R-25 stages).
* ``SSQTemplate`` — a versioned JSON schema (sections → fields) built in
  Settings → SSQ templates (draft, then publish).
* ``SSQ`` — one per request programme (flow ``ssq``); returned SSQs are
  superseded by a new version, never overwritten (Requirement 16).
* ``Submission`` — the immutable snapshot taken at each submission.
* ``Evidence`` — files attached to SSQ fields, uploaded or re-used from the
  accepted permanent record.
* ``Query`` / ``Response`` — NBTE's questions and the institution's replies.
* ``CatalogueItem`` — the curriculum catalogue (institutions enter quantities
  only, R-22).
* ``QACase`` — created (empty) when every SSQ of a request is submitted; the
  SOP-03 stages arrive in Phase 4.
"""
from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.storage import restricted_storage, uuid_upload_to
from qa_core.files import QAFileMixin
from qa_core.scoping import (
    InstitutionScopedManager,
    InstitutionScopedModelMixin,
    UnscopedInstitutionManager,
)

USER = settings.AUTH_USER_MODEL


class ApplicationCycle(TimeStampedModel):
    """Optionally limits when new applications are accepted. With no cycle
    configured, applications are accepted at any time."""

    AUDITED = True

    name = models.CharField(max_length=120)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    is_open = models.BooleanField(default=True)
    applies_to_onboarding = models.BooleanField(default=True)
    qa_types = models.ManyToManyField("QAType", blank=True, related_name="cycles",
                                      help_text="QA types this window limits (Phase 3). Leave empty for SOP-01 "
                                                "only.")

    class Meta:
        ordering = ["-opens_at", "-id"]

    def __str__(self):
        return self.name

    def accepting(self, now=None):
        now = now or timezone.now()
        if not self.is_open:
            return False
        if self.opens_at and now < self.opens_at:
            return False
        if self.closes_at and now > self.closes_at:
            return False
        return True


class Application(InstitutionScopedModelMixin, TimeStampedModel):
    """SOP-01: a proposed institution's onboarding application."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    FLOW_CODE = "onboarding"
    FLOW_STATUS_FIELD = "status"

    reference = models.CharField(max_length=60, unique=True)
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="applications")
    cycle = models.ForeignKey(ApplicationCycle, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="applications")
    status = models.CharField(max_length=60, default="draft", db_index=True)
    round = models.PositiveSmallIntegerField(default=0, help_text="Clearance round (each submission starts one).")
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    routed_department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+")
    returned_at = models.DateTimeField(null=True, blank=True)
    return_remark = models.TextField(blank=True)
    returned_by_department = models.ForeignKey("staff.Department", null=True, blank=True,
                                               on_delete=models.SET_NULL, related_name="+")
    declaration_accepted = models.BooleanField(default=False)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        permissions = [
            ("view_onboarding_application", "QA: can see onboarding applications in scope"),
            ("decide_departmental_clearance", "QA: can clear or return an application for their department"),
        ]

    def __str__(self):
        return self.reference

    @property
    def is_draft(self):
        return self.status == "draft"


class DepartmentalClearance(InstitutionScopedModelMixin, TimeStampedModel):
    """One parallel branch of the onboarding flow, per review round."""

    AUDITED = True
    INSTITUTION_PATH = "application__institution"
    PENDING, CLEARED, RETURNED, CLOSED = "pending", "cleared", "returned", "closed"
    STATUS_CHOICES = [(PENDING, "Pending"), (CLEARED, "Cleared"), (RETURNED, "Returned"),
                      (CLOSED, "Closed (another department returned it)")]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="clearances")
    department = models.ForeignKey("staff.Department", on_delete=models.PROTECT, related_name="+")
    branch_code = models.CharField(max_length=80, help_text="Onboarding flow stage of this branch.")
    round = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    remark = models.TextField(blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["application", "round", "branch_code"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["application", "branch_code", "round"],
                                    name="acc_clearance_branch_round_unique"),
        ]

    def __str__(self):
        return f"{self.application.reference} · {self.department} · round {self.round} · {self.status}"


# ===========================================================================
# Phase 3 — SOP-02 QA requests and the dynamic SSQ (Master Plan §5.4)
# ===========================================================================
slug_validator = RegexValidator(r"^[a-z][a-z0-9_]*$", "Use lower-case letters, digits and underscores.")


def _staff_can_see(user, institution):
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return False
    if user.is_superuser or user.has_perm("qa_core.view_all_institutions") \
            or user.has_perm("accounts.access_all_departments"):
        return True
    return bool(user.department_id and user.department_id == institution.handling_department_id)


class QAType(TimeStampedModel):
    """The QA types (D1): Resource Inspection, Accreditation, Reaccreditation,
    Validation, Verification, Increase in Carrying Capacity, Institutional
    Administration. Editable data; who may request what is configured here."""

    AUDITED = True

    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    flow_code = models.SlugField(max_length=80, default="qa_request",
                                 help_text="Workflow (Settings → Workflows) a request of this type follows.")
    fee_schedule = models.ForeignKey("payments.FeeSchedule", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="qa_types")
    ssq_template = models.ForeignKey("SSQTemplate", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", help_text="The current published SSQ template.")
    eligible_programme_statuses = models.JSONField(
        default=list, blank=True,
        help_text="Programme statuses that may be included, e.g. [\"accredited\", \"interim\"].")
    uses_reassessment_window = models.BooleanField(
        default=False, help_text="Only programmes inside qa.programme.reassessment_window_months of expiry "
                                 "(or already expired) are eligible (Requirement 4).")
    includes_new_programmes = models.BooleanField(
        default=False, help_text="Proposed programmes may be included; they go through programme clearance.")
    admin_programme_only = models.BooleanField(default=False,
                                               help_text="Only the Institutional Administration programme.")
    max_programmes = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "QA type"

    def __str__(self):
        return self.name

    @property
    def is_available(self):
        return self.is_active and self.ssq_template_id is not None


class SSQTemplate(TimeStampedModel):
    """A versioned SSQ schema. Drafts are edited in Settings → SSQ templates;
    publishing makes a version current for its QA type. A published version
    is never edited: changes start a new draft."""

    AUDITED = True
    DRAFT, PUBLISHED, RETIRED = "draft", "published", "retired"
    STATUS_CHOICES = [(DRAFT, "Draft"), (PUBLISHED, "Published"), (RETIRED, "Retired")]

    qa_type = models.ForeignKey(QAType, on_delete=models.PROTECT, related_name="templates")
    version = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=150)
    schema = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    is_current = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["qa_type__sort_order", "-version"]
        constraints = [models.UniqueConstraint(fields=["qa_type", "version"], name="ssq_template_version_unique")]
        permissions = [
            ("manage_ssq_templates", "QA: can edit SSQ template drafts"),
            ("publish_ssq_template", "QA: can publish an SSQ template version"),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def sections(self):
        return list((self.schema or {}).get("sections") or [])

    def field_map(self):
        return {f["key"]: f for sec in self.sections() for f in sec.get("fields", [])}


class CatalogueItem(TimeStampedModel):
    """The curriculum catalogue (B3/D3): what a programme must have. Items are
    versioned; institutions enter quantities only (R-22)."""

    AUDITED = True
    FACILITY, EQUIPMENT = "facility", "equipment"
    KIND_CHOICES = [(FACILITY, "Facility"), (EQUIPMENT, "Equipment")]

    category = models.ForeignKey("institutions.ProgrammeCategory", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="catalogue_items")
    programme_name = models.CharField(max_length=200, blank=True,
                                      help_text="Programme this item belongs to; blank = every programme in the "
                                                "category.")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=EQUIPMENT)
    name = models.CharField(max_length=200)
    required_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    unit = models.CharField(max_length=40, blank=True, default="no.")
    version = models.PositiveIntegerField(default=1)
    effective_from = models.DateField(default=timezone.localdate)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["programme_name", "kind", "sort_order", "name"]
        permissions = [("manage_catalogue", "QA: can edit the curriculum catalogue")]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display().lower()}, {self.programme_name or self.category})"


class QARequest(InstitutionScopedModelMixin, TimeStampedModel):
    """SOP-02: an approved institution asks for QA on some programmes."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    FLOW_STATUS_FIELD = "status"
    CANCELLABLE_STAGES = ("draft", "submitted", "fee_due", "payment_pending_confirmation")  # R-25 (default)

    reference = models.CharField(max_length=60, unique=True)
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="qa_requests")
    qa_type = models.ForeignKey(QAType, on_delete=models.PROTECT, related_name="requests")
    cycle = models.ForeignKey(ApplicationCycle, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="qa_requests")
    status = models.CharField(max_length=60, default="draft", db_index=True)
    institution_request = models.OneToOneField("institutions.InstitutionRequest", null=True, blank=True,
                                               on_delete=models.SET_NULL, related_name="qa_request")
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    declaration_accepted = models.BooleanField(default=False)
    fee_note = models.CharField(max_length=255, blank=True,
                                help_text="Why the fee could not be issued yet (shown to Finance).")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # Phase 6: a programme mapped to a professional body makes this a joint
    # QA exercise. Kept in step with the active programmes by
    # institutions.professional_bodies.refresh_joint_qa().
    is_joint_qa = models.BooleanField(default=False, db_index=True)
    joint_bodies = models.ManyToManyField("institutions.ProfessionalBody", blank=True, related_name="qa_requests")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name = "QA request"
        permissions = [
            ("view_qa_case", "QA: can see QA requests, SSQs and cases in scope"),
            ("raise_query", "QA: can raise a query to an institution"),
            ("review_ssq", "QA: can accept or return a submitted SSQ"),
            ("clear_qa_programmes", "QA: can clear or return new programmes on a QA request"),
        ]

    def __str__(self):
        return self.reference

    @property
    def flow_code(self):
        return self.qa_type.flow_code if self.qa_type_id else "qa_request"

    def active_programmes(self):
        return [rp for rp in QARequestProgramme.unscoped.filter(qa_request=self, status="active")
                .select_related("programme", "programme__level")]


class QARequestProgramme(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "qa_request__institution"
    ACTIVE, DROPPED, REPLACED = "active", "dropped", "replaced"
    STATUS_CHOICES = [(ACTIVE, "Active"), (DROPPED, "Dropped"), (REPLACED, "Replaced")]
    CLEARANCE_CHOICES = [("", "Not needed"), ("pending", "Pending"), ("cleared", "Cleared"),
                         ("returned", "Returned")]

    qa_request = models.ForeignKey(QARequest, on_delete=models.CASCADE, related_name="request_programmes")
    programme = models.ForeignKey("institutions.Programme", on_delete=models.PROTECT,
                                  related_name="qa_request_rows")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ACTIVE, db_index=True)
    replaced_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dropped_reason = models.TextField(blank=True)
    dropped_at = models.DateTimeField(null=True, blank=True)
    is_new_programme = models.BooleanField(default=False)
    clearance_status = models.CharField(max_length=10, choices=CLEARANCE_CHOICES, blank=True, default="")
    clearance_remark = models.TextField(blank=True)
    cleared_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cleared_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["qa_request", "programme__name"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.qa_request.reference} · {self.programme}"


class SSQ(InstitutionScopedModelMixin, TimeStampedModel):
    """One self-study questionnaire per request programme. Each version is a
    row: a returned SSQ is superseded by a new version (Requirement 16)."""

    AUDITED = True
    INSTITUTION_PATH = "qa_request_programme__qa_request__institution"
    FLOW_CODE = "ssq"
    FLOW_STATUS_FIELD = "status"
    EDITABLE_STATUSES = ("draft", "returned_to_department", "submitted_by_department")

    qa_request_programme = models.ForeignKey(QARequestProgramme, on_delete=models.CASCADE, related_name="ssqs")
    template = models.ForeignKey(SSQTemplate, on_delete=models.PROTECT, related_name="ssqs")
    data = models.JSONField(default=dict, blank=True)
    meta = models.JSONField(default=dict, blank=True,
                            help_text="prefilled keys, per-section save times …")
    status = models.CharField(max_length=40, default="draft", db_index=True)
    version = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="superseded_by")
    is_current = models.BooleanField(default=True, db_index=True)
    assigned_department_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                                 related_name="+")
    assigned_sections = models.JSONField(default=list, blank=True)
    department_remark = models.TextField(blank=True)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remark = models.TextField(blank=True)
    last_saved_at = models.DateTimeField(null=True, blank=True)
    last_saved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["qa_request_programme", "-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name = "SSQ"
        constraints = [models.UniqueConstraint(fields=["qa_request_programme", "version"],
                                               name="ssq_version_unique")]

    def __str__(self):
        return f"SSQ {self.qa_request_programme.programme} v{self.version}"

    @property
    def institution(self):
        return self.qa_request_programme.qa_request.institution

    @property
    def qa_request(self):
        return self.qa_request_programme.qa_request

    @property
    def is_editable(self):
        return self.is_current and self.status in self.EDITABLE_STATUSES


class Submission(InstitutionScopedModelMixin, models.Model):
    """The immutable snapshot taken each time an SSQ is submitted to NBTE."""

    INSTITUTION_PATH = "ssq__qa_request_programme__qa_request__institution"

    ssq = models.ForeignKey(SSQ, on_delete=models.PROTECT, related_name="submissions")
    version = models.PositiveIntegerField()
    template_version = models.PositiveIntegerField()
    data_snapshot = models.JSONField(default=dict)
    rendered_snapshot = models.JSONField(default=list, help_text="Sections with labels and display values.")
    evidence_snapshot = models.JSONField(default=list)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    submitted_at = models.DateTimeField(default=timezone.now)
    checksum = models.CharField(max_length=64)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["ssq", "-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["ssq", "version"], name="submission_version_unique")]

    def __str__(self):
        return f"Submission {self.ssq_id} v{self.version}"

    @staticmethod
    def compute_checksum(data, rendered, evidence):
        raw = json.dumps({"data": data, "rendered": rendered, "evidence": evidence}, sort_keys=True,
                         separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise PermissionError("A submission snapshot is immutable.")
        if not self.checksum:
            self.checksum = self.compute_checksum(self.data_snapshot, self.rendered_snapshot,
                                                  self.evidence_snapshot)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("A submission snapshot is immutable.")

    def verify(self):
        return self.checksum == self.compute_checksum(self.data_snapshot, self.rendered_snapshot,
                                                      self.evidence_snapshot)


class Evidence(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """A file behind an SSQ field: uploaded now, or re-used from the accepted
    permanent record (``reused_from``; the retention principle)."""

    AUDITED = True
    INSTITUTION_PATH = "ssq__qa_request_programme__qa_request__institution"
    MEDIA_SUBDIR = "qa/evidence"
    STATUS_CHOICES = [("attached", "Attached"), ("accepted", "Accepted"), ("returned", "Returned"),
                      ("removed", "Removed")]

    file = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage, max_length=255, blank=True)
    ssq = models.ForeignKey(SSQ, on_delete=models.CASCADE, related_name="evidence")
    field_key = models.CharField(max_length=80)
    document_kind = models.ForeignKey("institutions.DocumentKind", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="+")
    reused_from = models.ForeignKey("institutions.InstitutionDocument", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="reused_as_evidence")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="attached", db_index=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["ssq", "field_key", "-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name_plural = "evidence"

    def __str__(self):
        return f"{self.field_key}: {self.label}"

    @property
    def label(self):
        if self.reused_from_id:
            doc = self.reused_from
            return f"{doc.kind.name} (v{doc.version}, from your record)"
        return self.display_name

    @property
    def institution(self):
        return self.ssq.institution

    def can_download(self, user):
        inst = self.institution
        if user is None or not user.is_authenticated:
            return False
        if getattr(user, "user_type", "staff") == "institution":
            return user.institution_id == inst.pk
        return _staff_can_see(user, inst)


class Query(InstitutionScopedModelMixin, TimeStampedModel):
    """NBTE asks the institution for more information (an SSQ return with
    structure). The subject is generic: an SSQ, a piece of evidence or a QA
    request."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    OPEN, ANSWERED, CLOSED = "open", "answered", "closed"
    STATUS_CHOICES = [(OPEN, "Open"), (ANSWERED, "Answered"), (CLOSED, "Closed")]

    reference = models.CharField(max_length=60, unique=True)
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT, related_name="queries")
    qa_request = models.ForeignKey(QARequest, null=True, blank=True, on_delete=models.CASCADE,
                                   related_name="queries")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    object_id = models.CharField(max_length=64, blank=True)
    subject = GenericForeignKey("content_type", "object_id")
    subject_label = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    raised_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    due_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OPEN, db_index=True)
    closed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    close_remark = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name_plural = "queries"

    def __str__(self):
        return f"{self.reference}: {self.title}"

    @property
    def is_overdue(self):
        return self.status == self.OPEN and self.due_at is not None and self.due_at < timezone.now()


class Response(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """The institution's reply to a query (optionally with one attachment)."""

    AUDITED = True
    INSTITUTION_PATH = "query__institution"
    MEDIA_SUBDIR = "qa/query_responses"

    file = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage, max_length=255, blank=True)
    query = models.ForeignKey(Query, on_delete=models.CASCADE, related_name="responses")
    body = models.TextField()
    responded_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["query", "at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Response to {self.query.reference}"

    def can_download(self, user):
        inst = self.query.institution
        if user is None or not user.is_authenticated:
            return False
        if getattr(user, "user_type", "staff") == "institution":
            return user.institution_id == inst.pk
        return _staff_can_see(user, inst)


class QACase(InstitutionScopedModelMixin, TimeStampedModel):
    """The SOP-03 internal case (stages 1–17, flow ``qa_case``). Phase 3
    creates it when every SSQ of a request is submitted; Phase 4 runs it.

    ``status`` mirrors the current stage of the flow. The people chosen on
    the way (desk officer, supervising officers) live here so the stage
    resolvers can route each task to the right person."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    FLOW_CODE = "qa_case"
    FLOW_STATUS_FIELD = "status"

    reference = models.CharField(max_length=60, unique=True)
    qa_request = models.OneToOneField(QARequest, on_delete=models.PROTECT, related_name="case")
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT, related_name="qa_cases")
    owning_department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+")
    status = models.CharField(max_length=40, default="awaiting_sop03", db_index=True)
    programme_count = models.PositiveSmallIntegerField(default=0)
    # Phase 4
    requires_second_ict_officer = models.BooleanField(
        default=False, help_text="programme_count ≥ qa.assignment.second_ict_officer_threshold (R-04).")
    desk_officer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                     help_text="The owning department's officer who builds the cost sheet, "
                                               "personnel list and visit details.")
    supervising_audit_officer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                                  related_name="+")
    supervising_finance_officer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                                    related_name="+")
    tracking_ict_officers = models.ManyToManyField(USER, blank=True, related_name="+")
    needs_reassignment = models.BooleanField(
        default=False, help_text="An assignee returned a stage task (\"I can't do this\"); the department head "
                                 "must reassign it. The case does not move.")
    stage_actors = models.JSONField(
        default=dict, blank=True,
        help_text="Who acts for a role on this case once chosen (e.g. {\"es\": 12}): the director who opened "
                  "the case, a reassigned holder… Later stages for that role go to the same person.")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name = "QA case"
        permissions = [
            ("act_on_qa_stage", "QA: can act on SOP-03 case stages"),
            ("edit_cost_sheet", "QA: can build a case cost sheet"),
            ("audit_cost_sheet", "QA: can audit (review and edit) a case cost sheet"),
            ("approve_fund_release", "QA: can approve a case fund release"),
            ("release_funds", "QA: can release case funds (Pay Officer)"),
            ("manage_rate_table", "QA: can edit the allowance rate table"),
        ]

    def __str__(self):
        return self.reference



# ===========================================================================
# Phase 4 — SOP-03 stage data (Master Plan §5.4, B11–B16)
# ===========================================================================
class RateTable(TimeStampedModel):
    """Allowance rates the cost sheet is built from (B12). Shared rates have
    no department; a department row overrides the shared one for the same
    ``code``. Edited in Settings → Rate table (``manage_rate_table``)."""

    AUDITED = True
    KIND_CHOICES = [("fee", "Fee / honorarium"), ("academic_allowance", "Academic allowance"),
                    ("dta", "Duty tour allowance (per night)"), ("transport", "Transport"), ("other", "Other")]
    PER_DAY, PER_TRIP = "per_day", "per_trip"
    UNIT_CHOICES = [(PER_DAY, "Per day"), (PER_TRIP, "Per trip / flat")]

    code = models.SlugField(max_length=60)
    role = models.CharField(max_length=120, help_text="Who the rate is for, e.g. 'Team lead (Director)'.")
    allowance_type = models.CharField(max_length=20, choices=KIND_CHOICES, default="dta")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default=PER_DAY)
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", help_text="Blank = shared by every department.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        constraints = [models.UniqueConstraint(fields=["code", "department"], name="rate_code_department_unique")]

    def __str__(self):
        return f"{self.role} — {self.get_allowance_type_display()}"


class CostSheet(InstitutionScopedModelMixin, TimeStampedModel):
    """The visit cost sheet (B12): built by the department (stage 2), audited
    (stages 5–6), approved by the ES (stage 7)."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    STATUS_CHOICES = [("draft", "Being prepared"), ("submitted", "Submitted"), ("audited", "Audited"),
                      ("es_approved", "Approved by the ES"), ("returned", "Returned")]

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="cost_sheets")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft")
    is_current = models.BooleanField(default=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    prepared_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    audited_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["case", "-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["case", "version"], name="costsheet_case_version_unique")]

    def __str__(self):
        return f"Cost sheet v{self.version} — {self.case}"


class CostLine(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "sheet__case__institution"
    KIND_CHOICES = RateTable.KIND_CHOICES

    sheet = models.ForeignKey(CostSheet, on_delete=models.CASCADE, related_name="lines")
    staff = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    role_on_exercise = models.CharField(max_length=120)
    rate = models.ForeignKey(RateTable, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="dta")
    days = models.DecimalField(max_digits=6, decimal_places=1, default=1)
    unit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0,
                                      help_text="The rate's amount when the line was written (frozen).")
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    edited_by_audit = models.BooleanField(default=False)
    audit_note = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["sheet", "sort_order", "id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.role_on_exercise}: {self.amount}"


class PersonnelList(InstitutionScopedModelMixin, TimeStampedModel):
    """Who the department proposes for the exercise (B11, stages 3–4)."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    STATUS_CHOICES = [("draft", "Being prepared"), ("submitted", "Submitted"), ("approved", "Approved"),
                      ("returned", "Returned")]

    case = models.OneToOneField(QACase, on_delete=models.CASCADE, related_name="personnel_list")
    division = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="draft")
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Personnel — {self.case}"


class PersonnelEntry(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "personnel_list__case__institution"
    STATUS_CHOICES = [("proposed", "Proposed"), ("approved", "Approved"), ("returned", "Returned")]
    DUTY_CHOICES = [("team_lead", "Team lead"), ("member", "Team member"), ("finance", "Finance officer"),
                    ("audit", "Audit officer"), ("ict", "ICT officer"), ("support", "Support")]

    personnel_list = models.ForeignKey(PersonnelList, on_delete=models.CASCADE, related_name="entries")
    staff = models.ForeignKey("staff.Staff", on_delete=models.PROTECT, related_name="+")
    duty = models.CharField(max_length=12, choices=DUTY_CHOICES, default="member")
    remark = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="proposed")
    added_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["personnel_list", "duty", "id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["personnel_list", "staff"], name="personnel_staff_unique")]

    def __str__(self):
        return f"{self.staff} ({self.get_duty_display()})"


class VisitDetail(InstitutionScopedModelMixin, TimeStampedModel):
    """Dates, team lead and logistics of the visit (stages 8–10)."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    STATUS_CHOICES = [("draft", "Being prepared"), ("submitted", "Submitted"), ("confirmed", "Confirmed"),
                      ("es_approved", "Approved by the ES"), ("returned", "Returned")]

    case = models.OneToOneField(QACase, on_delete=models.CASCADE, related_name="visit")
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    team_lead = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    logistics_notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft")
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Visit — {self.case}"


class FundRelease(InstitutionScopedModelMixin, TimeStampedModel):
    """Money for the visit (B16): requested when the ES has approved the cost
    and the visit; the Pay Officer releases it (stage 11)."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    STATUS_CHOICES = [("requested", "Requested"), ("es_approved", "Approved by the ES"),
                      ("released", "Released"), ("returned", "Returned")]

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="fund_releases")
    cost_sheet = models.ForeignKey(CostSheet, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="requested")
    pay_officer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    released_reference = models.CharField(max_length=80, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    remark = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["case", "-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Fund release {self.amount} — {self.case}"


# ===========================================================================
# Phase 6 — joint QA notices to professional bodies
# ===========================================================================
class ProfessionalBodyNotice(InstitutionScopedModelMixin, TimeStampedModel):
    """One notice per (case, body): the professional body is told that NBTE
    has opened a joint QA case on a programme it regulates."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    SENT, NO_EMAIL, FAILED = "sent", "no_email", "failed"
    STATUS_CHOICES = [(SENT, "Sent"), (NO_EMAIL, "No e-mail on file"), (FAILED, "Failed")]

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="body_notices")
    body = models.ForeignKey("institutions.ProfessionalBody", on_delete=models.PROTECT, related_name="notices")
    email = models.EmailField(blank=True)
    programmes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=SENT)
    sent_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["case", "body"], name="pb_notice_once_per_case")]

    def __str__(self):
        return f"{self.case.reference} → {self.body}"
