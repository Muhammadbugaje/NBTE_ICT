"""Access grants (v2.1 X5, spec_v21 G1.c).

``AccessGrant``  time-boxed read access to one record that follows from work
                 (a ticket assignee reads the linked asset while the ticket is
                 open). Created and revoked only through ``assignments.grants``;
                 every create / revoke writes ``core.audit.log``.
``AccessEvent``  append-only log of sensitive reads (downloads, previews,
                 full-journey views, exports, views made through a grant).

Phase P adds ``WorkAssignment`` and ``RotationDecision`` to this app; a
WorkAssignment becomes one more grant *source* (see docs/master_plan_v2_1/
notes/assignments_api.md).
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .locks import Lockable


class AccessGrantQuerySet(models.QuerySet):
    def live(self, user=None, now=None):
        """Grants in force now (not revoked, not expired)."""
        now = now or timezone.now()
        qs = self.filter(revoked_at__isnull=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        if user is not None:
            qs = qs.filter(grantee=user)
        return qs

    def for_object(self, obj):
        ct = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        return self.filter(object_ct=ct, object_id=str(obj.pk))

    def for_source(self, source):
        ct = ContentType.objects.get_for_model(source, for_concrete_model=False)
        return self.filter(source_ct=ct, source_id=str(source.pk))


class AccessGrant(models.Model):
    """Read access to one record for one person, for a reason, until a time
    or until the work that caused it closes."""

    SCOPE_SUMMARY = "summary"
    SCOPE_DOCUMENTS = "documents"
    SCOPE_SSQ = "ssq"
    SCOPE_PAYMENTS = "payments"
    SCOPE_FULL_JOURNEY = "full_journey"
    SCOPE_DEPARTMENT_PART = "department_part"
    SCOPE_ASSET = "asset"
    SCOPE_ACT_FOR = "act_for_institution"
    SCOPE_CHOICES = [
        (SCOPE_SUMMARY, "Summary (profile and contacts)"),
        (SCOPE_DOCUMENTS, "Documents"),
        (SCOPE_SSQ, "SSQs and evidence"),
        (SCOPE_PAYMENTS, "Payments"),
        (SCOPE_FULL_JOURNEY, "Full journey"),
        (SCOPE_DEPARTMENT_PART, "Department's part"),
        (SCOPE_ASSET, "Asset details and history"),
        (SCOPE_ACT_FOR, "Act for the institution"),
    ]

    R_COMPLETED = "work_completed"
    R_EXPIRED = "expired"
    R_MANUAL = "manual"
    R_REASSIGNED = "reassigned"
    REVOKE_REASONS = [
        (R_COMPLETED, "Work completed"),
        (R_EXPIRED, "Expired"),
        (R_MANUAL, "Revoked by hand"),
        (R_REASSIGNED, "Reassigned"),
    ]

    grantee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_grants")
    object_ct = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.CharField(max_length=64)
    obj = GenericForeignKey("object_ct", "object_id")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.CASCADE,
                                    related_name="+", db_index=True,
                                    help_text="Denormalised for the QA scoping join (Phase P).")
    scope = models.CharField(max_length=24, choices=SCOPE_CHOICES)
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="For scope department_part.")
    source_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source_id = models.CharField(max_length=64, blank=True)
    source = GenericForeignKey("source_ct", "source_id")
    reason = models.CharField(max_length=255)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="Empty = granted by the system.")
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    close_on_completion = models.BooleanField(default=True, help_text="Revoked when the source work closes.")
    revoked_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    revoke_reason = models.CharField(max_length=80, blank=True)
    long_reported_at = models.DateTimeField(
        null=True, blank=True, help_text="Last time the sweep reported this grant as long-running.")

    objects = AccessGrantQuerySet.as_manager()

    class Meta:
        ordering = ["-granted_at", "-pk"]
        indexes = [
            models.Index(fields=["grantee", "revoked_at", "expires_at"], name="asg_grant_live"),
            models.Index(fields=["object_ct", "object_id"], name="asg_grant_object"),
            models.Index(fields=["institution", "grantee"], name="asg_grant_inst"),
            models.Index(fields=["source_ct", "source_id"], name="asg_grant_source"),
        ]
        permissions = [
            ("view_grants_log", "Can view the access grants log"),
            ("revoke_access_grant", "Can revoke access grants by hand"),
        ]

    def __str__(self):
        return f"{self.get_scope_display()} on {self.object_ct.model} #{self.object_id} for user {self.grantee_id}"

    @property
    def is_live(self):
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > timezone.now())

    def get_revoke_reason_display(self):
        """Label of ``revoke_reason`` (the field has no ``choices``, so Django
        does not generate this; the notice and the grants log use it)."""
        return dict(self.REVOKE_REASONS).get(self.revoke_reason, self.revoke_reason)

    @property
    def status(self):
        if self.revoked_at is not None:
            return "expired" if self.revoke_reason == self.R_EXPIRED else "revoked"
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return "expired"
        return "live"


class AccessEvent(models.Model):
    """Append-only record of a sensitive read (X7/X8). Written for downloads of
    restricted files, full-journey views and reads made through a grant —
    not for every page view."""

    DOWNLOAD = "download"
    PREVIEW = "preview"
    VIEW_FULL_JOURNEY = "view_full_journey"
    EXPORT = "export"
    VIEW_VIA_GRANT = "view_via_grant"
    ACTION_CHOICES = [
        (DOWNLOAD, "Download"),
        (PREVIEW, "Preview"),
        (VIEW_FULL_JOURNEY, "Viewed full journey"),
        (EXPORT, "Export"),
        (VIEW_VIA_GRANT, "Viewed through a grant"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    object_ct = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=24, choices=ACTION_CHOICES)
    via_grant = models.ForeignKey(AccessGrant, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="events")
    at = models.DateTimeField(default=timezone.now, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-at", "-pk"]
        indexes = [models.Index(fields=["object_ct", "object_id", "at"], name="asg_event_object")]

    def __str__(self):
        return f"{self.action} {self.object_ct.model} #{self.object_id} by {self.user_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AccessEvent rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AccessEvent rows are append-only.")


# ---------------------------------------------------------------------------
# Phase P (v2.1 X1, spec_v21 G1.c): why a person works on an institution
# ---------------------------------------------------------------------------
class WorkAssignmentQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status__in=WorkAssignment.OPEN)

    def for_assignee(self, user):
        return self.filter(assignee=user)

    def for_subject(self, obj):
        ct = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        return self.filter(subject_ct=ct, subject_id=str(obj.pk))


class WorkAssignment(Lockable, models.Model):
    """The reason a person works on an institution (X1). Append-mostly:
    status changes are allowed (through ``assignments.work``), deletes are
    not. The QA scoping chokepoint reads open rows to decide what a staff
    member may see; ``assignments.grants`` gives the time-boxed grants that
    follow from accepted work (``qa.access.kind_scopes``)."""

    INSTITUTION_PATH = "institution"
    DEPARTMENT_PATH = "department"

    # Kinds (spec G1.c + task brief aliases)
    OVERSIGHT_ICT = "oversight_ict"
    PHYSICAL_VERIFICATION = "physical_verification"
    DOCUMENT_ANALYSIS = "document_analysis"
    SSQ_REVIEW = "ssq_review"
    VISIT_PANEL = "visit_panel"
    VISIT_LEAD = "visit_lead"
    COMPLAINT_TASK = "complaint_task"
    CLEARANCE = "clearance"
    PROGRAMME_CLEARANCE = "programme_clearance"
    CASE_DESK = "case_desk"
    CASE_SUPERVISION_AUDIT = "case_supervision_audit"
    CASE_SUPERVISION_FINANCE = "case_supervision_finance"
    PAYMENT_REVIEW = "payment_review"
    HONORARIA_SCHEDULE = "honoraria_schedule"
    DELEGATE_ACT_FOR = "delegate_act_for"
    KIND_CHOICES = [
        (OVERSIGHT_ICT, "ICT supervision (oversight)"),
        (PHYSICAL_VERIFICATION, "Physical verification"),
        (DOCUMENT_ANALYSIS, "Document analysis"),
        (SSQ_REVIEW, "SSQ review"),
        (VISIT_PANEL, "Visit panel member"),
        (VISIT_LEAD, "Visit team lead"),
        (COMPLAINT_TASK, "Complaint task"),
        (CLEARANCE, "Departmental clearance"),
        (PROGRAMME_CLEARANCE, "Programme clearance"),
        (CASE_DESK, "Case desk officer"),
        (CASE_SUPERVISION_AUDIT, "Case supervision (audit)"),
        (CASE_SUPERVISION_FINANCE, "Case supervision (finance)"),
        (PAYMENT_REVIEW, "Payment review"),
        (HONORARIA_SCHEDULE, "Honoraria schedule"),
        (DELEGATE_ACT_FOR, "Delegate acting for the institution"),
    ]
    KINDS = {code for code, _ in KIND_CHOICES}
    # The kind that opens an institution's whole journey to one person (X2).
    FULL_JOURNEY_KINDS = (OVERSIGHT_ICT,)

    ROLE_CHOICES = [("analyst", "Analyst"), ("reviewer", "Reviewer"), ("lead", "Lead"), ("member", "Member"),
                    ("supervisor", "Supervisor"), ("delegate", "Delegate"), ("decider", "Decider")]

    SUGGESTED, OFFERED, ACCEPTED, IN_PROGRESS = "suggested", "offered", "accepted", "in_progress"
    SUBMITTED, COMPLETED, RETURNED, DECLINED, CANCELLED = "submitted", "completed", "returned", "declined", "cancelled"
    STATUS_CHOICES = [
        (SUGGESTED, "Suggested"), (OFFERED, "Offered"), (ACCEPTED, "Accepted"), (IN_PROGRESS, "In progress"),
        (SUBMITTED, "Submitted"), (COMPLETED, "Completed"), (RETURNED, "Returned"), (DECLINED, "Declined"),
        (CANCELLED, "Cancelled"),
    ]
    OPEN = (SUGGESTED, OFFERED, ACCEPTED, IN_PROGRESS, SUBMITTED, RETURNED)
    # Open AND the assignee has taken it on: these rows open visibility.
    WORKING = (ACCEPTED, IN_PROGRESS, SUBMITTED, RETURNED)
    CLOSED = (COMPLETED, DECLINED, CANCELLED)

    CLOSED_REASONS = [("completed", "Completed"), ("reassigned", "Reassigned"), ("withdrawn", "Withdrawn"),
                      ("institution_closed", "Institution closed"), ("declined", "Declined")]

    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT, db_index=True,
                                    related_name="work_assignments")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    subject_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    subject_id = models.CharField(max_length=64, blank=True)
    subject = GenericForeignKey("subject_ct", "subject_id")
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="work_assignments",
                                   help_text="Department on whose behalf the work is done.")
    division = models.ForeignKey("org.Division", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="work_assignments")
    role_on_work = models.CharField(max_length=24, choices=ROLE_CHOICES, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=OFFERED, db_index=True)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", help_text="Empty = the system (rotation, back-fill).")
    assigned_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    closed_reason = models.CharField(max_length=40, choices=CLOSED_REASONS, blank=True)
    payable = models.BooleanField(default=False, help_text="Accrues an honorarium on completion (X32).")
    meta = models.JSONField(default=dict, blank=True)
    # X7: a completed step locks; reopening goes through assignments.locks.reopen.
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    objects = WorkAssignmentQuerySet.as_manager()

    class Meta:
        ordering = ["-assigned_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "subject_ct", "subject_id", "assignee"],
                condition=Q(status__in=("suggested", "offered", "accepted", "in_progress", "submitted", "returned")),
                name="one_open_assignment_per_person_subject"),
        ]
        indexes = [
            models.Index(fields=["assignee", "status"], name="asg_work_assignee"),
            models.Index(fields=["institution", "kind", "status"], name="asg_work_inst"),
            models.Index(fields=["department", "kind", "status"], name="asg_work_dept"),
            models.Index(fields=["subject_ct", "subject_id"], name="asg_work_subject"),
        ]
        permissions = [
            ("view_all_journeys", "QA: can see every institution's full journey"),
            ("view_department_qa", "QA: can see the department's part of institutions (delegated)"),
            ("assign_work", "QA: can assign work to staff"),
            ("reopen_completed_step", "QA: can reopen a completed (locked) step"),
            ("view_access_report", "QA: can see who can see an institution and why"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · inst {self.institution_id} · {self.assignee_id} · {self.status}"

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def is_working(self):
        return self.status in self.WORKING

    @property
    def subject_label(self):
        obj = self.subject
        if obj is None:
            return ""
        return getattr(obj, "reference", None) or str(obj)

    def delete(self, *args, **kwargs):
        raise ValueError("Work assignments are never deleted; cancel them instead.")


from .models_rotation import *  # noqa: E402,F401,F403  Phase P: rotation (X27-X29) + review chains (X30)
