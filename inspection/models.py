"""Inspection (QA) — Phase 5 (Master Plan §5.5, §9).

* ``ResourcePerson`` / ``ResourcePersonDiscipline`` / ``ResourcePersonFile`` —
  the external (and NBTE) assessors: nominated → vetted → active; CV and
  promotion proof are restricted files.
* ``QAPersonnel`` — NBTE staff registered for QA duty (*Edit QA Personnel*).
* ``QAMapping`` — who covers which programme on which case, for which duty
  (*Edit QA Mapping*); exactly one of QA personnel / resource person.
* ``SSQReviewAssignment`` — an officer's SSQ review (R-01 counts the open ones).
* ``Inspection`` / ``InspectionTeamMember`` — the visit and its planned team
  (flow ``inspection``).
* ``PostVisitPersonnel`` — who actually went (*Override Post-Visit
  Personnel*); the ground truth for the service history.
* ``PostVisitReport`` / ``ReportEndorsement`` — SOP-03 stage 12: one report
  per programme, Division Head → Deputy Director → Director (flow
  ``post_visit_report``; B14, B17).
* ``ServiceRecord`` — the derived, append-only service history; corrections
  are compensating rows, never edits.

Eligibility and availability are rules with configurable thresholds
(``inspection.eligibility`` / ``inspection.availability``); there is no
override path.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimeStampedModel
from qa_core.files import QAFileMixin
from qa_core.models import AppendOnlyMixin
from qa_core.scoping import InstitutionScopedManager, InstitutionScopedModelMixin, UnscopedInstitutionManager

USER = settings.AUTH_USER_MODEL

DUTY_CHOICES = [
    ("ssq_review", "SSQ review"),
    ("physical_verification", "Physical verification"),
    ("resource_inspection", "Resource inspection"),
    ("tracking", "Tracking"),
]
VISIT_DUTIES = ("physical_verification", "resource_inspection")


# ===========================================================================
# Resource persons
# ===========================================================================
class ResourcePerson(TimeStampedModel):
    """An assessor. ``home_institution`` is an attribute used for conflict of
    interest, not ownership: the directory is NBTE-wide, so it is not
    institution-scoped (``INSTITUTION_FK_ATTRIBUTES``)."""

    AUDITED = True
    INSTITUTION_FK_ATTRIBUTES = ("home_institution",)

    SOURCE_CHOICES = [("academia", "Academia"), ("professional_body", "Professional body"),
                      ("nbte_retired", "NBTE (retired)"), ("industry", "Industry"), ("nbte_staff", "NBTE staff")]
    NOMINATED, VETTED, ACTIVE, SUSPENDED, RETIRED, REJECTED = (
        "nominated", "vetted", "active", "suspended", "retired", "rejected")
    STATUS_CHOICES = [(NOMINATED, "Nominated"), (VETTED, "Vetted"), (ACTIVE, "Active"),
                      (SUSPENDED, "Suspended"), (RETIRED, "Retired"), (REJECTED, "Not approved")]

    user = models.OneToOneField(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="resource_person")
    full_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="academia")
    staff = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                              help_text="For NBTE staff serving as resource persons (their role and designation "
                                        "decide eligibility).")
    home_institution = models.ForeignKey("institutions.Institution", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="+",
                                         help_text="Conflict of interest: never sent to their own institution.")
    professional_body = models.CharField(max_length=150, blank=True)
    rank = models.CharField(max_length=60, blank=True,
                            help_text="Controlled code from qa.resource_person.required_designations "
                                      "(e.g. chief-lecturer).")
    qualifications = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=NOMINATED, db_index=True)
    nominated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    nominated_at = models.DateTimeField(default=timezone.now)
    vetted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    vetted_at = models.DateTimeField(null=True, blank=True)
    vetting_remark = models.TextField(blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    # Bumped by every booking; a booking whose version moved under it is refused
    # (the double-booking guard that also works on SQLite, §9).
    booking_version = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["full_name"]
        permissions = [
            ("view_resource_person", "QA: can see the resource-person directory"),
            ("manage_resource_person", "QA: can nominate and edit resource persons"),
            ("vet_resource_person", "QA: can vet (approve / reject) resource persons"),
            ("initiate_edit_qa_personnel", "QA: can start Edit QA Personnel"),
            ("confirm_edit_qa_personnel", "QA: can confirm Edit QA Personnel"),
            ("initiate_edit_qa_mapping", "QA: can start Edit QA Mapping"),
            ("confirm_edit_qa_mapping", "QA: can confirm Edit QA Mapping"),
            ("initiate_override_post_visit_personnel", "QA: can start Override Post-Visit Personnel"),
            ("confirm_override_post_visit_personnel", "QA: can confirm Override Post-Visit Personnel"),
            ("submit_post_visit_report", "QA: can submit a post-visit report (attendees only, B17)"),
            ("endorse_post_visit_report", "QA: can endorse a post-visit report (B14)"),
            ("raise_falsification", "QA: can raise a falsification finding (Phase 6)"),
            ("view_service_history", "QA: can see the service history"),
            ("export_service_history", "QA: can export the service history"),
        ]

    def __str__(self):
        return self.full_name

    @property
    def is_staff_person(self):
        return self.source == "nbte_staff" and self.staff_id is not None


class ResourcePersonDiscipline(models.Model):
    resource_person = models.ForeignKey(ResourcePerson, on_delete=models.CASCADE, related_name="disciplines")
    discipline = models.ForeignKey("institutions.Discipline", on_delete=models.PROTECT, related_name="+")
    is_primary = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["resource_person", "discipline"],
                                               name="rp_discipline_unique")]

    def __str__(self):
        return f"{self.resource_person} · {self.discipline}{' (primary)' if self.is_primary else ''}"


class ResourcePersonFile(QAFileMixin, TimeStampedModel):
    """CV and promotion proof (restricted)."""

    AUDITED = True
    MEDIA_SUBDIR = "qa/resource_persons"

    def media_owner_folder(self):
        """CVs and promotion proofs live under ``resource-persons/<name>/``."""
        from qa_core.files import RESOURCE_PERSONS_ROOT, safe_folder_name

        person = self.resource_person if self.resource_person_id else None
        if person is None:
            return None
        return f"{RESOURCE_PERSONS_ROOT}/{safe_folder_name(person.full_name or f'person {person.pk}')}"
    KIND_CHOICES = [("cv", "CV"), ("promotion_proof", "Promotion proof"), ("other", "Other")]

    resource_person = models.ForeignKey(ResourcePerson, on_delete=models.CASCADE, related_name="files")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="cv")
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def can_download(self, user):
        if user is None or not user.is_authenticated:
            return False
        if self.resource_person.user_id and self.resource_person.user_id == user.pk:
            return True
        return getattr(user, "user_type", "") == "staff" and (
            user.is_superuser or user.has_perm("inspection.view_resource_person"))


class QAPersonnel(TimeStampedModel):
    """NBTE staff registered for QA duty (*Edit QA Personnel*, name kept)."""

    AUDITED = True

    staff = models.OneToOneField("staff.Staff", on_delete=models.PROTECT, related_name="qa_personnel")
    disciplines = models.ManyToManyField("institutions.Discipline", blank=True, related_name="+")
    is_verification_officer = models.BooleanField(default=True)
    is_team_lead_eligible = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    booking_version = models.PositiveIntegerField(default=0)
    registered_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["staff__first_name", "staff__last_name"]
        verbose_name = "QA personnel"
        verbose_name_plural = "QA personnel"

    def __str__(self):
        return str(self.staff)

    @property
    def user(self):
        return self.staff.user


# ===========================================================================
# Mapping (who covers which programme)
# ===========================================================================
class QAMapping(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    LIVE = ("proposed", "active")
    STATUS_CHOICES = [("proposed", "Proposed"), ("active", "Active"), ("released", "Released"),
                      ("overridden", "Overridden")]

    case = models.ForeignKey("accreditation.QACase", on_delete=models.CASCADE, related_name="qa_mappings")
    qa_request_programme = models.ForeignKey("accreditation.QARequestProgramme", on_delete=models.CASCADE,
                                             related_name="qa_mappings")
    duty = models.CharField(max_length=24, choices=DUTY_CHOICES)
    qa_personnel = models.ForeignKey(QAPersonnel, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="mappings")
    resource_person = models.ForeignKey(ResourcePerson, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="mappings")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="active", db_index=True)
    from_date = models.DateField()
    to_date = models.DateField()
    findings = models.TextField(blank=True, help_text="A resource person's notes from the portal.")
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_via = models.ForeignKey("qa_core.PendingAction", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-from_date", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(qa_personnel__isnull=False, resource_person__isnull=True)
                           | Q(qa_personnel__isnull=True, resource_person__isnull=False)),
                name="qamapping_exactly_one_person"),
            models.CheckConstraint(condition=Q(to_date__gte=models.F("from_date")), name="qamapping_dates_ordered"),
        ]
        indexes = [models.Index(fields=["resource_person", "status"]),
                   models.Index(fields=["qa_personnel", "status"])]

    def __str__(self):
        return f"{self.person} → {self.qa_request_programme.programme} ({self.get_duty_display()})"

    @property
    def person(self):
        return self.resource_person or self.qa_personnel

    @property
    def person_kind(self):
        return "resource_person" if self.resource_person_id else "qa_personnel"


class SSQReviewAssignment(InstitutionScopedModelMixin, TimeStampedModel):
    """An officer reviewing an SSQ. Open rows count against R-01; completing
    one writes a ServiceRecord (duty ``ssq_review``)."""

    AUDITED = True
    INSTITUTION_PATH = "ssq__qa_request_programme__qa_request__institution"

    ssq = models.ForeignKey("accreditation.SSQ", on_delete=models.CASCADE, related_name="review_assignments")
    reviewer = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    assigned_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=20, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-assigned_at"]

    def __str__(self):
        return f"{self.reviewer} · SSQ {self.ssq_id}"


# ===========================================================================
# Inspections
# ===========================================================================
class Inspection(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "case__institution"
    FLOW_CODE = "inspection"
    FLOW_STATUS_FIELD = "status"
    KIND_CHOICES = [("resource_inspection", "Resource inspection"), ("accreditation_visit", "Accreditation visit"),
                    ("verification", "Verification"), ("monitoring", "Monitoring")]

    reference = models.CharField(max_length=60, unique=True)
    case = models.ForeignKey("accreditation.QACase", on_delete=models.CASCADE, related_name="inspections")
    kind = models.CharField(max_length=24, choices=KIND_CHOICES, default="accreditation_visit")
    team_lead = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    scheduled_start = models.DateField()
    scheduled_end = models.DateField()
    status = models.CharField(max_length=40, blank=True, db_index=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)
    completed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-scheduled_start"]

    def __str__(self):
        return self.reference

    @property
    def institution(self):
        return self.case.institution


class InspectionTeamMember(InstitutionScopedModelMixin, TimeStampedModel):
    """The planned team: from Edit QA Mapping (assessors) and from the case's
    personnel list (NBTE staff on the visit)."""

    AUDITED = True
    INSTITUTION_PATH = "inspection__case__institution"

    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="team")
    qa_mapping = models.ForeignKey(QAMapping, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    staff = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    role_on_team = models.CharField(max_length=60, blank=True)
    confirmed_by_division_director = models.BooleanField(default=False)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        who = self.qa_mapping.person if self.qa_mapping_id else self.staff
        return f"{who} ({self.role_on_team})"


class PostVisitPersonnel(InstitutionScopedModelMixin, TimeStampedModel):
    """Who actually went (*Override Post-Visit Personnel*, name kept). Exactly
    one of staff / QA personnel / resource person."""

    AUDITED = True
    INSTITUTION_PATH = "inspection__case__institution"

    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="attendance")
    staff = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    qa_personnel = models.ForeignKey(QAPersonnel, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    resource_person = models.ForeignKey(ResourcePerson, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    role_on_team = models.CharField(max_length=60, blank=True)
    duty = models.CharField(max_length=24, choices=DUTY_CHOICES, default="physical_verification")
    planned = models.BooleanField(default=True)
    attended = models.BooleanField(default=True)
    override_reason = models.TextField(blank=True)
    overridden_via = models.ForeignKey("qa_core.PendingAction", null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["pk"]
        constraints = [models.CheckConstraint(
            condition=(Q(staff__isnull=False, qa_personnel__isnull=True, resource_person__isnull=True)
                       | Q(staff__isnull=True, qa_personnel__isnull=False, resource_person__isnull=True)
                       | Q(staff__isnull=True, qa_personnel__isnull=True, resource_person__isnull=False)),
            name="pvp_exactly_one_person")]

    def __str__(self):
        return f"{self.person} — {'attended' if self.attended else 'did not attend'}"

    @property
    def person(self):
        return self.resource_person or self.qa_personnel or self.staff

    @property
    def person_type(self):
        if self.resource_person_id:
            return "resource_person"
        if self.qa_personnel_id:
            return "qa_personnel"
        return "staff"

    @property
    def person_id(self):
        return self.resource_person_id or self.qa_personnel_id or self.staff_id

    @property
    def user(self):
        if self.resource_person_id:
            return self.resource_person.user
        if self.qa_personnel_id:
            return self.qa_personnel.staff.user
        return self.staff.user if self.staff_id else None


# ===========================================================================
# Post-visit reports (SOP-03 stage 12)
# ===========================================================================
class PostVisitReport(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "inspection__case__institution"
    FLOW_CODE = "post_visit_report"
    FLOW_STATUS_FIELD = "status"
    RECOMMENDATION_CHOICES = [("accredit", "Accredit"), ("interim", "Interim accreditation"),
                              ("deny", "Deny"), ("revisit", "Revisit needed")]

    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="reports")
    qa_request_programme = models.ForeignKey("accreditation.QARequestProgramme", on_delete=models.PROTECT,
                                             related_name="post_visit_reports")
    findings = models.JSONField(default=dict, blank=True, help_text="Findings by SSQ section.")
    summary = models.TextField(blank=True)
    recommendation = models.CharField(max_length=12, choices=RECOMMENDATION_CHOICES, blank=True)
    recommendation_note = models.TextField(blank=True, help_text="The Director's recommendation (stage 13).")
    recommended_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=40, blank=True, db_index=True)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["inspection", "qa_request_programme"],
                                               name="one_report_per_programme")]

    def __str__(self):
        return f"Report · {self.qa_request_programme.programme}"

    @property
    def case(self):
        return self.inspection.case

    @property
    def institution(self):
        return self.inspection.case.institution


class ReportEndorsement(AppendOnlyMixin, InstitutionScopedModelMixin, models.Model):
    """B14: one row per endorsement action (append-only)."""

    INSTITUTION_PATH = "report__inspection__case__institution"
    LAYER_CHOICES = [("team", "Team"), ("division_head", "Division Head"), ("deputy_director", "Deputy Director"),
                     ("director", "Director")]
    ACTION_CHOICES = [("submit", "Submitted"), ("endorse", "Endorsed"), ("add_content", "Added content"),
                      ("return", "Returned")]

    report = models.ForeignKey(PostVisitReport, on_delete=models.CASCADE, related_name="endorsements")
    layer = models.CharField(max_length=20, choices=LAYER_CHOICES)
    action = models.CharField(max_length=12, choices=ACTION_CHOICES)
    content = models.TextField(blank=True)
    actor = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["at", "pk"]

    def __str__(self):
        return f"{self.get_layer_display()} {self.get_action_display().lower()}"


# ===========================================================================
# Service history (§9) — derived, append-only
# ===========================================================================
class ServiceRecord(AppendOnlyMixin, InstitutionScopedModelMixin, models.Model):
    AUDITED = True
    INSTITUTION_PATH = "institution"
    PERSON_TYPES = [("resource_person", "Resource person"), ("qa_personnel", "QA personnel"),
                    ("staff", "NBTE staff")]

    person_type = models.CharField(max_length=20, choices=PERSON_TYPES)
    person_id = models.PositiveBigIntegerField()
    person_name = models.CharField(max_length=150)
    resource_person = models.ForeignKey(ResourcePerson, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="service_records")
    qa_personnel = models.ForeignKey(QAPersonnel, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="service_records")
    staff = models.ForeignKey("staff.Staff", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    case = models.ForeignKey("accreditation.QACase", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+")
    inspection = models.ForeignKey(Inspection, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="service_records")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    programme = models.ForeignKey("institutions.Programme", null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="+")
    discipline = models.ForeignKey("institutions.Discipline", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    duty = models.CharField(max_length=24, choices=DUTY_CHOICES)
    role_on_team = models.CharField(max_length=60, blank=True)
    served_from = models.DateField()
    served_to = models.DateField()
    visit_count = models.IntegerField(default=0, help_text="+1 per visit served; −1 on a compensating row.")
    ssq_count = models.IntegerField(default=0, help_text="+1 per SSQ reviewed; −1 on a compensating row.")
    rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    allowance_paid = models.BooleanField(default=False)
    voids = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="voided_by",
                                 help_text="The row this compensating row cancels.")
    reason = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=30, blank=True, help_text="inspection_closed / attendance_override / "
                                                                  "ssq_review / backfill")
    created_at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-served_to", "-pk"]
        indexes = [models.Index(fields=["person_type", "person_id"]), models.Index(fields=["served_to"])]

    def __str__(self):
        sign = "−" if (self.visit_count < 0 or self.ssq_count < 0) else ""
        return f"{sign}{self.person_name} · {self.get_duty_display()} · {self.served_from}"

    def delete(self, *args, **kwargs):
        raise PermissionError("ServiceRecord is append-only; write a compensating row instead.")
