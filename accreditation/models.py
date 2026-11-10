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
from core.storage import restricted_storage
from qa_core.files import QAFileMixin, qa_upload_to
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
    # Phase 0 (Q1, Q2): programme selection submitted to NBTE for clearance.
    programme_round = models.PositiveSmallIntegerField(
        default=0, help_text="Programme clearance round (each programme submission starts one).")
    programmes_submitted_at = models.DateTimeField(null=True, blank=True)
    programmes_submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                                related_name="+")
    # v2.1 X14/X15 (F.15): the Chief Executive declares at "Declare and pay";
    # the application then submits itself once the fee is confirmed.
    declared_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    declared_at = models.DateTimeField(null=True, blank=True)
    auto_submitted = models.BooleanField(default=False)
    fee_obligation = models.ForeignKey("payments.PaymentObligation", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")

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
    # v2.1 X3: non-full viewers see only their own department's rows.
    DEPARTMENT_PATH = "department"
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
    # Phase 0 (O9): a returned document was re-uploaded while this clearance
    # was open, so it is back in the department's queue (no resubmission).
    resubmitted_at = models.DateTimeField(null=True, blank=True)
    resubmissions = models.PositiveSmallIntegerField(default=0)

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


class ClearanceDelegate(TimeStampedModel):
    """Phase 0 (O10, T-SEC-7): a named person the director of a department
    lets decide that department's onboarding clearances. Departmental
    clearance is otherwise reserved for the director of the clearance
    department, the Executive Secretary and superusers
    (``accreditation.clearance_access``)."""

    AUDITED = True

    department = models.ForeignKey("staff.Department", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="clearance_delegations")
    granted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    active = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Leave blank for no end date.")
    note = models.CharField(max_length=255, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["department", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["department", "user"], condition=models.Q(active=True),
                                    name="acc_one_active_clearance_delegate"),
        ]

    def __str__(self):
        return f"{self.user} for {self.department} ({'active' if self.active else 'revoked'})"

    @property
    def is_live(self):
        return self.active and (self.expires_at is None or self.expires_at > timezone.now())


class OnboardingProgrammeReview(InstitutionScopedModelMixin, TimeStampedModel):
    """Phase 0 (Q2): the programme-clearance decision on one programme of an
    onboarding application, per programme round. NTIRD (or the institution
    category's handling department) clears or returns each programme; when
    every programme is cleared the programme processing fee is issued."""

    AUDITED = True
    INSTITUTION_PATH = "application__institution"
    DEPARTMENT_PATH = "department"  # v2.1 X3
    PENDING, CLEARED, RETURNED, SUPERSEDED = "pending", "cleared", "returned", "superseded"
    STATUS_CHOICES = [(PENDING, "Waiting for clearance"), (CLEARED, "Cleared"), (RETURNED, "Returned"),
                      (SUPERSEDED, "Superseded")]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="programme_reviews")
    programme = models.ForeignKey("institutions.Programme", on_delete=models.CASCADE, related_name="+")
    department = models.ForeignKey("staff.Department", on_delete=models.PROTECT, related_name="+")
    round = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    remark = models.TextField(blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["application", "round", "programme__name"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["application", "programme", "round"],
                                    name="acc_programme_review_round_unique"),
        ]
        indexes = [models.Index(fields=["department", "status"], name="acc_progrev_dept_status")]

    def __str__(self):
        return f"{self.application.reference} · {self.programme_id} · round {self.round} · {self.status}"


class OnboardingSSQ(InstitutionScopedModelMixin, TimeStampedModel):
    """SOP-01 programme self-study, submitted before NBTE approves an institution."""

    AUDITED = True
    INSTITUTION_PATH = "application__institution"
    DRAFT, SUBMITTED = "draft", "submitted"
    STATUS_CHOICES = [(DRAFT, "Draft"), (SUBMITTED, "Submitted")]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="self_studies")
    programme = models.ForeignKey("institutions.Programme", on_delete=models.PROTECT, related_name="+")
    template = models.ForeignKey("SSQTemplate", on_delete=models.PROTECT, related_name="onboarding_ssqs")
    data = models.JSONField(default=dict, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    last_saved_at = models.DateTimeField(null=True, blank=True)
    last_saved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_data = models.JSONField(default=dict, blank=True)
    rendered_snapshot = models.JSONField(default=list, blank=True)
    evidence_snapshot = models.JSONField(default=list, blank=True)
    checksum = models.CharField(max_length=64, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["application", "programme__name"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["application", "programme"], name="onboarding_ssq_programme_unique"),
        ]

    def __str__(self):
        return f"Onboarding SSQ — {self.application.reference} · {self.programme}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            previous = type(self).unscoped.filter(pk=self.pk).values(
                "application_id", "programme_id", "template_id", "data", "meta", "status", "last_saved_at",
                "last_saved_by_id", "submitted_by_id", "submitted_at", "submitted_data", "rendered_snapshot",
                "evidence_snapshot", "checksum",
            ).first()
            if previous and previous["status"] == self.SUBMITTED:
                current = {
                    "application_id": self.application_id, "programme_id": self.programme_id,
                    "template_id": self.template_id, "data": self.data, "meta": self.meta, "status": self.status,
                    "last_saved_at": self.last_saved_at, "last_saved_by_id": self.last_saved_by_id,
                    "submitted_by_id": self.submitted_by_id, "submitted_at": self.submitted_at,
                    "submitted_data": self.submitted_data, "rendered_snapshot": self.rendered_snapshot,
                    "evidence_snapshot": self.evidence_snapshot, "checksum": self.checksum,
                }
                if current != previous:
                    raise PermissionError("A submitted onboarding self-study is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.SUBMITTED:
            raise PermissionError("A submitted onboarding self-study cannot be deleted.")
        super().delete(*args, **kwargs)

    def verify_submission(self):
        raw = json.dumps(
            {"data": self.submitted_data, "rendered": self.rendered_snapshot, "evidence": self.evidence_snapshot},
            sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")
        return bool(self.checksum and self.checksum == hashlib.sha256(raw).hexdigest())


class OnboardingSSQReview(TimeStampedModel):
    """Linear SOP-01 review of one submitted programme self-study
    (``accreditation.onboarding_linear``): routed to the handling department →
    the director assigns an officer → the officer assesses and sends it to the
    director → the director clears it (or returns it to the officer or to the
    institution). The self-study itself stays immutable; its review state lives
    here. ``onboarding_ssq.review_stage(ssq)`` reads it."""

    AUDITED = True
    AWAITING_ASSIGNMENT, UNDER_ASSESSMENT, AWAITING_DIRECTOR = (
        "awaiting_assignment", "under_assessment", "awaiting_director")
    CLEARED, RETURNED = "cleared", "returned_to_institution"
    STAGE_CHOICES = [
        (AWAITING_ASSIGNMENT, "Awaiting officer assignment"), (UNDER_ASSESSMENT, "Under assessment"),
        (AWAITING_DIRECTOR, "Awaiting the director"), (CLEARED, "Cleared"),
        (RETURNED, "Returned to the institution"),
    ]
    OPEN_STAGES = (AWAITING_ASSIGNMENT, UNDER_ASSESSMENT, AWAITING_DIRECTOR)
    RECOMMENDATION_CHOICES = [("clear", "Clear"), ("return", "Return to the institution")]

    ssq = models.OneToOneField(OnboardingSSQ, on_delete=models.CASCADE, related_name="linear_review")
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, default=AWAITING_ASSIGNMENT, db_index=True)
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="The handling department it was routed to.")
    director = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                 help_text="Who assigns and clears (the director; heads / the ES if vacant).")
    officer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)
    recommendation = models.CharField(max_length=10, choices=RECOMMENDATION_CHOICES, blank=True)
    officer_remark = models.TextField(blank=True)
    director_remark = models.TextField(blank=True)
    stage_since = models.DateTimeField(default=timezone.now)
    cleared_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cleared_at = models.DateTimeField(null=True, blank=True)
    round = models.PositiveSmallIntegerField(default=1)
    reminder = models.JSONField(default=dict, blank=True,
                                help_text="SLA reminders sent at the current stage: {stage, level, at}.")
    history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["ssq"]
        verbose_name = "onboarding self-study review"

    def __str__(self):
        return f"Review of {self.ssq} · {self.get_stage_display()}"


class OnboardingQAStart(TimeStampedModel):
    """The formal QA request NBTE started automatically when the institution
    was approved (``onboarding_linear.start_formal_qa``), for the programmes
    whose onboarding self-studies were cleared."""

    AUDITED = True

    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="qa_starts")
    qa_request = models.OneToOneField("QARequest", null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="onboarding_start")
    fee_waived = models.BooleanField(default=False)
    started_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    error = models.TextField(blank=True, help_text="Why the request could not be started (NBTE retries it).")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "onboarding QA start"

    def __str__(self):
        return f"{self.application.reference} → {getattr(self.qa_request, 'reference', '—')}"


# ===========================================================================
# Phase 3 — SOP-02 QA requests and the dynamic SSQ (Master Plan §5.4)
# ===========================================================================
slug_validator = RegexValidator(r"^[a-z][a-z0-9_]*$", "Use lower-case letters, digits and underscores.")


def _staff_can_see(user, institution, obj=None, scope="ssq"):
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return False
    from assignments import access
    if access.assignment_mode():  # v2.1 X1 (P.3): need-to-know
        return access.can_see(user, obj if obj is not None else institution, scope)
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
    asks_streams = models.BooleanField(
        default=False, help_text="The institution says how many streams it wants for each programme "
                                 "(new programmes, increase in carrying capacity).")
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
    # v2.1 R.1 (X35): NBTE-only scoring data, never shown to institutions.
    weight = models.DecimalField(max_digits=6, decimal_places=2, default=1,
                                 help_text="Relative weight in the shortfall score (internal).")
    min_spec = models.TextField(blank=True, help_text="Minimum specification (e.g. 'Core i5, 8 GB RAM').")
    is_critical = models.BooleanField(default=False, help_text="Counts double in the shortfall score (internal).")
    programme_master = models.ForeignKey("institutions.ProgrammeMaster", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="catalogue_items")

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
    requested_streams = models.PositiveSmallIntegerField(
        default=1, help_text="Number of streams the institution asks for (fees and the capacity engine).")

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
    # v2.1 X3 (spec G1.c): the handling department until SSQ reviews carry their own.
    DEPARTMENT_PATH = "qa_request_programme__qa_request__institution__handling_department"
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


class BodyRequirementCheck(InstitutionScopedModelMixin, TimeStampedModel):
    """v2.1 R.6 (X37b): the reviewer's result for one professional-body
    requirement on one SSQ."""

    INSTITUTION_PATH = "ssq__qa_request_programme__qa_request__institution"

    RESULT_CHOICES = [("met", "Met"), ("partly", "Partly met"), ("not_met", "Not met"),
                      ("not_applicable", "Not applicable")]

    ssq = models.ForeignKey(SSQ, on_delete=models.CASCADE, related_name="requirement_checks")
    requirement = models.ForeignKey("institutions.BodyRequirement", on_delete=models.PROTECT,
                                    related_name="checks")
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default="not_applicable")
    remark = models.TextField(blank=True)
    checked_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    checked_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["requirement__sort_order", "requirement__code"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["ssq", "requirement"],
                                               name="body_requirement_check_unique")]

    def __str__(self):
        return f"{self.requirement} — {self.get_result_display()}"


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
    INSTITUTION_MEDIA_KIND = "ssq-evidence"
    STATUS_CHOICES = [("attached", "Attached"), ("accepted", "Accepted"), ("returned", "Returned"),
                      ("removed", "Removed")]

    file = models.FileField(upload_to=qa_upload_to, storage=restricted_storage, max_length=255, blank=True)
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
        if _staff_can_see(user, inst, self):
            return True
        if getattr(user, "user_type", "staff") != "staff":
            return False
        from inspection.services import can_review_cqac_evidence

        try:
            case = self.ssq.qa_request_programme.qa_request.case
        except QACase.DoesNotExist:
            return False
        return can_review_cqac_evidence(case, user)


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
    INSTITUTION_MEDIA_KIND = "queries"

    file = models.FileField(upload_to=qa_upload_to, storage=restricted_storage, max_length=255, blank=True)
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
        return _staff_can_see(user, inst, self)


class QACase(InstitutionScopedModelMixin, TimeStampedModel):
    """The SOP-03 internal case (stages 1–17, flow ``qa_case``). Phase 3
    creates it when every SSQ of a request is submitted; Phase 4 runs it.

    ``status`` mirrors the current stage of the flow. The people chosen on
    the way (desk officer, supervising officers) live here so the stage
    resolvers can route each task to the right person."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    DEPARTMENT_PATH = "owning_department"  # v2.1 X3
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
                    ("dta", "Duty tour allowance (per night)"), ("transport", "Transport"),
                    ("fuel", "Fuel"), ("airport_taxi", "Airport taxi"), ("other", "Other")]
    PER_DAY, PER_TRIP = "per_day", "per_trip"
    UNIT_CHOICES = [(PER_DAY, "Per day"), (PER_TRIP, "Per trip / flat")]

    code = models.SlugField(max_length=60)
    role = models.CharField(max_length=120, help_text="Who the rate is for, e.g. 'Team lead (Director)'.")
    allowance_type = models.CharField(max_length=20, choices=KIND_CHOICES, default="dta")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default=PER_DAY)
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", help_text="Blank = shared by every department.")
    # v2.1 X39: a state row overrides the shared row of the same kind for visits
    # to that state (nights, DSA, fuel, airport taxi); blank = every state.
    state = models.ForeignKey("staff.StateOfOrigin", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="+", help_text="Blank = every state.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        constraints = [models.UniqueConstraint(fields=["code", "department", "state"],
                                               name="rate_code_department_state_unique")]

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
    # v2.1 X39: what the visit actually cost, recorded after it is approved.
    actual_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                       help_text="Actual spend recorded by Finance after the visit.")
    actual_note = models.CharField(max_length=255, blank=True)
    actual_recorded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    actual_recorded_at = models.DateTimeField(null=True, blank=True)

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


class VisitExternalMember(InstitutionScopedModelMixin, TimeStampedModel):
    """An external participant on the visit team (SOP-03 stage 3), kept apart
    from the NBTE staff list (PersonnelEntry): a resource person from the
    inspection register whose discipline matches a case programme, or a
    professional-body representative for a joint-QA programme."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    RESOURCE_PERSON, PROFESSIONAL_BODY = "resource_person", "professional_body"
    KIND_CHOICES = [(RESOURCE_PERSON, "External resource person"), (PROFESSIONAL_BODY, "Professional body")]
    STATUS_CHOICES = PersonnelEntry.STATUS_CHOICES

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="external_members")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    resource_person = models.ForeignKey("inspection.ResourcePerson", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    discipline = models.ForeignKey("institutions.Discipline", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="The case discipline the resource person covers.")
    body = models.ForeignKey("institutions.ProfessionalBody", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                             help_text="The professional body's portal user (representative).")
    remark = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="proposed")
    added_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["case", "kind", "id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["case", "resource_person"], condition=models.Q(resource_person__isnull=False),
                                    name="visit_external_rp_unique"),
            models.UniqueConstraint(fields=["case", "user"], condition=models.Q(user__isnull=False),
                                    name="visit_external_user_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def name(self):
        if self.resource_person_id:
            return self.resource_person.full_name
        if self.user_id:
            return (self.user.get_full_name() or "").strip() or self.user.email
        return "External participant"

    @property
    def label(self):
        if self.kind == self.RESOURCE_PERSON:
            return "External resource person" + (f" · {self.discipline.name}" if self.discipline_id else "")
        return "Professional body" + (f" · {self.body.name}" if self.body_id else "")


class BodyVisitReport(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """v2.6: a professional body's own report of an accreditation visit. A body
    representative (assigned by the registrar) writes the findings; the body's
    administrator (the registrar) approves or returns it; the version sent on
    to NBTE lands on the case's Reports tab and feeds the approval decision —
    the body still uploads the official letter for each programme itself."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    MEDIA_SUBDIR = "qa/body-visit-reports"

    DRAFT, SUBMITTED, APPROVED, RETURNED, WITH_NBTE = "draft", "submitted", "approved", "returned", "with_nbte"
    STATUS_CHOICES = [(DRAFT, "Draft"), (SUBMITTED, "With the registrar"), (APPROVED, "Approved by the registrar"),
                      (RETURNED, "Returned for correction"), (WITH_NBTE, "Sent to NBTE")]
    SATISFACTORY, CONCERNS, UNSATISFACTORY = "satisfactory", "concerns", "unsatisfactory"
    RECOMMENDATION_CHOICES = [(SATISFACTORY, "Satisfactory"), (CONCERNS, "Satisfactory with concerns"),
                              (UNSATISFACTORY, "Not satisfactory")]

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="body_reports")
    body = models.ForeignKey("institutions.ProfessionalBody", on_delete=models.PROTECT, related_name="visit_reports")
    author = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="body_visit_reports",
                               help_text="The body representative who wrote the report.")
    member = models.ForeignKey(VisitExternalMember, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+", help_text="The visit assignment behind this report.")
    programme = models.ForeignKey("institutions.Programme", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+", help_text="The one programme the visit covered, if any.")
    title = models.CharField(max_length=200, default="Visit report")
    summary = models.CharField(max_length=200, blank=True, help_text="One line, shown on lists.")
    content = models.TextField(help_text="What the body's representative saw on the visit.")
    recommendation = models.CharField(max_length=16, choices=RECOMMENDATION_CHOICES, default=SATISFACTORY)
    remark = models.CharField(max_length=255, blank=True, help_text="Anything the representative adds.")
    # The registrar's review (approve / return works like the SSQs).
    review_remark = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT, db_index=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.body.acronym or self.body.name} report · {self.case.reference}"

    @property
    def institution(self):
        return self.case.institution

    def is_editable(self):
        return self.status in (self.DRAFT, self.RETURNED)

    def is_reviewable(self):
        return self.status == self.SUBMITTED

    def can_download(self, user):
        if user is None or not user.is_authenticated:
            return False
        kind = getattr(user, "user_type", "staff")
        if kind == "professional_body":
            return user.professional_body_id == self.body_id
        if kind == "institution":
            return user.institution_id == self.case.institution_id
        return user.is_superuser or any(user.has_perm(p) for p in
                                        ("institutions.manage_professional_bodies", "qa_core.view_all_institutions"))


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
    portal_notified_at = models.DateTimeField(null=True, blank=True)
    portal_recipient_count = models.PositiveIntegerField(default=0)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["case", "body"], name="pb_notice_once_per_case")]

    def __str__(self):
        return f"{self.case.reference} → {self.body}"


# ===========================================================================
# Phase 8 (Master Plan Phase 6) — decisions, letters and capacity assessment
# (Master Plan §5.4: ApprovalType, Decision, OutcomeLetter, CapacityAssessment)
# ===========================================================================
class ApprovalType(TimeStampedModel):
    """B18 / R-05: what an approval grants and for how long. The expiry date
    of a programme is always derived from ``duration_months`` — never typed.
    Edited in Settings → Approval types (``manage_approval_types``)."""

    AUDITED = True
    PROGRAMME_STATUS_CHOICES = [("accredited", "Accredited"), ("interim", "Interim"),
                                ("approval_to_commence", "Approval to commence")]

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    duration_months = models.PositiveSmallIntegerField(help_text="Expiry = commencement + this many months.")
    programme_status = models.CharField(
        max_length=30, choices=PROGRAMME_STATUS_CHOICES, default="accredited",
        help_text="The programme status an approval of this type gives (stage 17).")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        permissions = [("manage_approval_types", "QA: can edit the approval types (durations)")]

    def __str__(self):
        return self.name

    def expiry_for(self, commencement):
        return add_months(commencement, self.duration_months) if commencement else None


def add_months(day, months):
    """``day`` + ``months`` calendar months (the 31st becomes the month's last day)."""
    import calendar
    import datetime as _dt

    if day is None:
        return None
    month0 = day.month - 1 + int(months)
    year, month = day.year + month0 // 12, month0 % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return _dt.date(year, month, min(day.day, last))


class CQACVote(InstitutionScopedModelMixin, TimeStampedModel):
    """One CQAC member's vote on one programme at stage 14."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="cqac_votes")
    qa_request_programme = models.ForeignKey(QARequestProgramme, on_delete=models.PROTECT,
                                             related_name="cqac_votes")
    programme = models.ForeignKey("institutions.Programme", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="cqac_votes")
    outcome = models.CharField(max_length=10, choices=[
        ("approve", "Approve"), ("deny", "Deny"), ("return", "Return for more work"),
    ])
    approval_type = models.ForeignKey(ApprovalType, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+")
    approved_streams = models.PositiveSmallIntegerField(default=0)
    commencement_date = models.DateField(null=True, blank=True)
    weaknesses = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["case", "programme__name", "user_id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["case", "qa_request_programme", "user"],
                                    name="cqac_vote_once_per_member"),
        ]

    def __str__(self):
        return f"CQAC vote · {self.programme} · {self.user} · {self.get_outcome_display()}"


class Decision(InstitutionScopedModelMixin, TimeStampedModel):
    """One programme's decision at a decision stage of the SOP-03 case:
    14 CQAC recommendation, 15 the CQAC Chairman, 16 the ES (final).
    Stage 17 applies the ES decision to the programme record."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    CQAC, CHAIR, ES = "cqac_recommendation", "cqac_chair", "es_final"
    STAGE_CHOICES = [(CQAC, "CQAC recommendation"), (CHAIR, "CQAC Chairman"), (ES, "ES final approval")]
    FLOW_STAGE = {CQAC: "cqac_recommendation", CHAIR: "cqac_chair_approval", ES: "es_final_approval"}
    APPROVE, DENY, RETURN = "approve", "deny", "return"
    OUTCOME_CHOICES = [(APPROVE, "Approve"), (DENY, "Deny"), (RETURN, "Return for more work")]

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="decisions")
    qa_request_programme = models.ForeignKey(QARequestProgramme, on_delete=models.PROTECT, related_name="decisions")
    programme = models.ForeignKey("institutions.Programme", on_delete=models.PROTECT, related_name="decisions")
    stage = models.CharField(max_length=24, choices=STAGE_CHOICES, db_index=True)
    outcome = models.CharField(max_length=10, choices=OUTCOME_CHOICES)
    approval_type = models.ForeignKey(ApprovalType, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="decisions")
    approved_streams = models.PositiveSmallIntegerField(default=0)
    commencement_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, help_text="Derived: commencement + duration (B18).")
    weaknesses = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True)
    batch_key = models.CharField(max_length=40, blank=True, db_index=True,
                                 help_text="Set when the Chairman approved a batch (B23).")
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(default=timezone.now)
    applied_at = models.DateTimeField(null=True, blank=True, help_text="Stage 17 applied it to the programme.")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["case", "programme__name", "stage"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["case", "qa_request_programme", "stage"],
                                               name="decision_once_per_stage")]
        permissions = [
            ("cqac_review", "QA: can record the CQAC recommendation (stage 14)"),
            ("cqac_chair_decide", "QA: can record the CQAC Chairman's decision (stage 15)"),
            ("es_final_decide", "QA: can record the ES final decision (stage 16)"),
            ("batch_decide", "QA: can approve CQAC recommendations in a batch (B23)"),
        ]

    def __str__(self):
        return f"{self.get_stage_display()} · {self.programme} · {self.get_outcome_display()}"

    def save(self, *args, **kwargs):
        # B18: the expiry is always derived, never typed.
        if self.outcome == self.APPROVE and self.approval_type_id and self.commencement_date:
            self.expiry_date = self.approval_type.expiry_for(self.commencement_date)
        else:
            self.expiry_date = None
        super().save(*args, **kwargs)


class OutcomeLetter(InstitutionScopedModelMixin, TimeStampedModel):
    """B24: the letter that tells the institution the final decision on a
    programme. An original and a duplicate (with a visible watermark) are
    generated together as two restricted files (``OutcomeLetterCopy``)."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"

    case = models.ForeignKey(QACase, on_delete=models.PROTECT, related_name="letters")
    decision = models.ForeignKey(Decision, on_delete=models.PROTECT, related_name="letters")
    programme = models.ForeignKey("institutions.Programme", on_delete=models.PROTECT, related_name="letters")
    reference = models.CharField(max_length=60, unique=True)
    version = models.PositiveSmallIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)
    template_version = models.CharField(max_length=16, blank=True,
                                        help_text="Short hash of the qa.letters.* settings used.")
    template_snapshot = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)
    generated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    regenerated_reason = models.CharField(max_length=255, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-generated_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        permissions = [("regenerate_letter", "QA: can regenerate an outcome letter")]

    def __str__(self):
        return f"{self.reference} — {self.programme}"

    @property
    def institution(self):
        return self.case.institution

    def copy(self, kind):
        return OutcomeLetterCopy.unscoped.filter(letter=self, kind=kind).first()

    @property
    def all_copies(self):
        """The letter's copies. The letter itself was loaded through a scoped
        query, so its own copies are read in system scope (templates must not
        use ``letter.copies.all``, which the scoping guard refuses)."""
        return list(OutcomeLetterCopy.unscoped.filter(letter=self).order_by("pk"))

    @property
    def pdf_original(self):
        return self.copy(OutcomeLetterCopy.ORIGINAL)

    @property
    def pdf_duplicate(self):
        return self.copy(OutcomeLetterCopy.DUPLICATE)


class OutcomeLetterCopy(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """One PDF of an outcome letter: the original or the watermarked duplicate."""

    AUDITED = True
    INSTITUTION_PATH = "letter__case__institution"
    MEDIA_SUBDIR = "qa/letters"
    INSTITUTION_MEDIA_KIND = "letters"  # institutions/<CODE - Name>/letters/
    ORIGINAL, DUPLICATE = "original", "duplicate"
    KIND_CHOICES = [(ORIGINAL, "Original"), (DUPLICATE, "Duplicate (watermarked)")]

    letter = models.ForeignKey(OutcomeLetter, on_delete=models.CASCADE, related_name="copies")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["letter", "kind"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["letter", "kind"], name="letter_copy_kind_unique")]

    def __str__(self):
        return f"{self.letter.reference} ({self.get_kind_display()})"

    def can_download(self, user):
        inst = self.letter.case.institution
        if user is None or not user.is_authenticated:
            return False
        if getattr(user, "user_type", "staff") == "institution":
            return user.institution_id == inst.pk
        from assignments import access
        if access.assignment_mode():
            return _staff_can_see(user, inst, self, "case")
        return _staff_can_see(user, inst) or user.has_perm("accreditation.view_qa_case")


class CapacityAssessment(InstitutionScopedModelMixin, TimeStampedModel):
    """Requirements 5–10 / Spec §7: one run of the capacity engine for a
    request programme and a resource kind. ``parameters_snapshot`` keeps every
    constant and formula in effect at the time, so a later settings change
    never alters a stored assessment. Re-running adds a new version."""

    AUDITED = True
    INSTITUTION_PATH = "qa_request_programme__qa_request__institution"
    KIND_CHOICES = [("classroom", "Classrooms"), ("laboratory", "Laboratories and workshops"),
                    ("library", "Library"), ("teaching_staff", "Teaching staff"),
                    ("support_staff", "Support staff")]

    qa_request_programme = models.ForeignKey(QARequestProgramme, on_delete=models.CASCADE,
                                             related_name="capacity_assessments")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    version = models.PositiveSmallIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)
    inputs = models.JSONField(default=dict)
    parameters_snapshot = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    computed_at = models.DateTimeField(default=timezone.now)
    computed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["qa_request_programme", "kind", "-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["qa_request_programme", "kind", "version"],
                                               name="capacity_version_unique")]
        permissions = [("view_capacity_assessment", "QA: can see capacity assessments"),
                       ("run_capacity_assessment", "QA: can run or re-run a capacity assessment")]

    def __str__(self):
        return f"{self.get_kind_display()} v{self.version} · {self.qa_request_programme}"


class CapacityReview(InstitutionScopedModelMixin, TimeStampedModel):
    """Director's sign-off of the exact current capacity assessments."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"

    case = models.ForeignKey(QACase, on_delete=models.CASCADE, related_name="capacity_reviews")
    qa_request_programme = models.ForeignKey(QARequestProgramme, on_delete=models.CASCADE,
                                             related_name="capacity_reviews")
    version = models.PositiveSmallIntegerField(default=1)
    is_current = models.BooleanField(default=True, db_index=True)
    assessment_ids = models.JSONField(default=dict)
    reviewed_at = models.DateTimeField(default=timezone.now)
    reviewed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["qa_request_programme", "-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["qa_request_programme", "version"],
                                    name="capacity_review_version_unique"),
            models.UniqueConstraint(fields=["case", "qa_request_programme"], condition=models.Q(is_current=True),
                                    name="capacity_review_current_unique"),
        ]

    def __str__(self):
        return f"Capacity review v{self.version} · {self.qa_request_programme}"



class ShortfallAnalysis(InstitutionScopedModelMixin, TimeStampedModel):
    """v2.1 R.2 (X36): resource shortfall of one SSQ submission, computed when
    the institution submits. Internal to NBTE (never on the portal).

    score = 100 x sum(w x min(declared / required, 1)) / sum(w), where w is the
    item weight, doubled for critical items. Bands: >= 80 good, >= 60 fair,
    else major."""

    INSTITUTION_PATH = "ssq__qa_request_programme__qa_request__institution"
    GOOD, FAIR, MAJOR = "good", "fair", "major"
    BAND_CHOICES = [(GOOD, "Good"), (FAIR, "Fair"), (MAJOR, "Major shortfall")]

    submission = models.OneToOneField(Submission, on_delete=models.CASCADE, related_name="shortfall")
    ssq = models.ForeignKey(SSQ, on_delete=models.CASCADE, related_name="shortfalls")
    score = models.DecimalField(max_digits=5, decimal_places=1)
    band = models.CharField(max_length=8, choices=BAND_CHOICES)
    items_scored = models.PositiveIntegerField(default=0)
    items = models.JSONField(default=list, blank=True, help_text="Missing and short items, worst first.")
    suggested = models.CharField(max_length=20, blank=True, help_text="Suggested recommendation.")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Shortfall {self.score}% ({self.band}) · {self.ssq}"


# ===========================================================================
# v2.1 X40 (Phase H): structured visit report NBTE/QA/ICT/VVT/001
# ===========================================================================
class VisitConflictDeclaration(InstitutionScopedModelMixin, TimeStampedModel):
    """One panel member's conflict-of-interest declaration before the visit
    starts (X40). A visit cannot be reported on until every panel member has
    declared."""

    AUDITED = True
    INSTITUTION_PATH = "inspection__case__institution"

    inspection = models.ForeignKey("inspection.Inspection", on_delete=models.CASCADE,
                                   related_name="declarations")
    staff = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    resource_person = models.ForeignKey("inspection.ResourcePerson", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="+")
    has_conflict = models.BooleanField(default=False)
    details = models.TextField(blank=True)
    declared_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    declared_at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["pk"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(staff__isnull=False, resource_person__isnull=True)
                           | models.Q(staff__isnull=True, resource_person__isnull=False)),
                name="visit_declaration_exactly_one_person"),
            models.UniqueConstraint(fields=["inspection", "staff"], condition=models.Q(staff__isnull=False),
                                    name="visit_declaration_staff_unique"),
            models.UniqueConstraint(fields=["inspection", "resource_person"],
                                    condition=models.Q(resource_person__isnull=False),
                                    name="visit_declaration_rp_unique"),
        ]

    def __str__(self):
        return f"Declaration · {self.person} · {'conflict' if self.has_conflict else 'clear'}"

    @property
    def person(self):
        return self.resource_person or self.staff


class VisitFinding(InstitutionScopedModelMixin, TimeStampedModel):
    """A catalogue item checked on the visit: what the institution declared on
    its SSQ vs what the panel found. A falsified item carries the fine (X40)."""

    AUDITED = True
    INSTITUTION_PATH = "report__inspection__case__institution"

    report = models.ForeignKey("inspection.PostVisitReport", on_delete=models.CASCADE,
                                  related_name="visit_findings")
    item = models.ForeignKey(CatalogueItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=CatalogueItem.KIND_CHOICES, default=CatalogueItem.EQUIPMENT)
    declared_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    found_quantity = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_falsified = models.BooleanField(default=False, help_text="Declared but not found: a fine applies.")
    remark = models.CharField(max_length=255, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["report", "kind", "name"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.name}: {self.declared_quantity} declared, {self.found_quantity} found"


class VisitReportForm(InstitutionScopedModelMixin, TimeStampedModel):
    """v2.4: a template-driven visit report filled by the panel at SOP-03 stage
    12, on the same engine as the SSQ (``accreditation/report_forms.py``).

    * ``programme_evaluation`` — NBTE/P/4A, one per post-visit report
      (programme): ratings per criterion, the computed recommendation;
    * ``ict_verification`` — NBTE/QA/ICT/VVT/001, one per visit: scored ICT
      components, mandatory items, capacity arithmetic, evidence register.

    ``score`` holds the last computed result; ``rendered_snapshot`` freezes the
    form (labels and values) when it is submitted."""

    AUDITED = True
    INSTITUTION_PATH = "inspection__case__institution"
    EVALUATION, ICT = "programme_evaluation", "ict_verification"
    KIND_CHOICES = [(EVALUATION, "Programme evaluation (NBTE/P/4A)"),
                    (ICT, "ICT verification (NBTE/QA/ICT/VVT/001)")]
    STATUS_CHOICES = [("draft", "Being written"), ("submitted", "Submitted")]

    inspection = models.ForeignKey("inspection.Inspection", on_delete=models.CASCADE, related_name="report_forms")
    report = models.ForeignKey("inspection.PostVisitReport", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="forms")
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    template = models.ForeignKey(SSQTemplate, on_delete=models.PROTECT, related_name="report_forms")
    data = models.JSONField(default=dict, blank=True)
    meta = models.JSONField(default=dict, blank=True, help_text="prefilled keys, suggestions …")
    score = models.JSONField(default=dict, blank=True, help_text="The last computed score and recommendation.")
    computed_outcome = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft", db_index=True)
    rendered_snapshot = models.JSONField(default=list, blank=True)
    last_saved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_saved_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["inspection", "kind", "pk"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [
            models.UniqueConstraint(fields=["report", "kind"], condition=models.Q(report__isnull=False),
                                    name="visit_report_form_per_report"),
            models.UniqueConstraint(fields=["inspection", "kind"], condition=models.Q(report__isnull=True),
                                    name="visit_report_form_per_visit"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.report or self.inspection}"

    @property
    def institution(self):
        return self.inspection.case.institution


class VisitReportAttachment(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """Restricted evidence files attached to a visit-report template field."""

    AUDITED = True
    INSTITUTION_PATH = "form__inspection__case__institution"
    MEDIA_SUBDIR = "qa/visit-report-evidence"
    INSTITUTION_MEDIA_KIND = "qa/visit-report-evidence"

    form = models.ForeignKey(VisitReportForm, on_delete=models.CASCADE, related_name="attachments")
    field_key = models.CharField(max_length=80)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["form", "field_key", "created_at", "pk"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.field_key}: {self.display_name}"

    @property
    def institution(self):
        return self.form.institution

    def can_download(self, user):
        if user is None or not user.is_authenticated:
            return False
        from inspection import services as insp

        if self.form.kind == VisitReportForm.ICT:
            return (insp.can_edit_ict(self.form.inspection, user)[0]
                    or insp.can_review_cqac_evidence(self.form.inspection.case, user))
        return bool(self.form.report_id and insp.visible_reports(user).filter(pk=self.form.report_id).exists())


class CaseChangeRequest(InstitutionScopedModelMixin, TimeStampedModel):
    """v2.7: a controlled change to a live SOP-03 case.

    Two kinds, one approval chain (handling-department Director → ES):

    * ``visit_date`` — the visit dates are fixed once the ES approves them.
      The desk officer (or the Director) asks; the Director approves, then the
      ES; only then may the desk officer apply the new dates.
    * ``rollback`` — move the case back to an earlier stage. Everything after
      that stage has to be done again (their tasks are closed and recreated
      as the case moves forward).

    Nothing is applied until the ES approves; the request stays as the audit
    record (who asked, who agreed, and why)."""

    AUDITED = True
    INSTITUTION_PATH = "case__institution"

    VISIT_DATE, ROLLBACK = "visit_date", "rollback"
    KIND_CHOICES = [(VISIT_DATE, "Change the visit dates"), (ROLLBACK, "Move the case back a stage")]
    STATUS_CHOICES = [("pending_director", "Waiting for the Director"), ("pending_es", "Waiting for the ES"),
                      ("approved", "Approved — ready to apply"), ("applied", "Applied"),
                      ("returned", "Returned for revision"), ("rejected", "Rejected"),
                      ("cancelled", "Cancelled")]
    OPEN = ("pending_director", "pending_es", "approved")

    case = models.ForeignKey("accreditation.QACase", on_delete=models.CASCADE, related_name="change_requests")
    kind = models.CharField(max_length=16, choices=KIND_CHOICES, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending_director", db_index=True)
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    requested_at = models.DateTimeField(default=timezone.now)
    reason = models.TextField(help_text="Why the change is needed — the approvers see this.")
    proposed_start = models.DateField(null=True, blank=True)
    proposed_end = models.DateField(null=True, blank=True)
    target_stage = models.CharField(max_length=40, blank=True, help_text="Stage code to move back to.")
    director_approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    director_approved_at = models.DateTimeField(null=True, blank=True)
    director_remark = models.TextField(blank=True)
    es_approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    es_approved_at = models.DateTimeField(null=True, blank=True)
    es_remark = models.TextField(blank=True)
    applied_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    applied_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-requested_at", "-pk"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        indexes = [models.Index(fields=["case", "status"])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.case.reference} · {self.get_status_display()}"

    @property
    def is_open(self):
        return self.status in self.OPEN


# Visit invitations to professional bodies and resource persons (kept in their own module).
from .visit_invitation_models import VisitInvitation, VisitInvitationContact  # noqa: E402,F401
