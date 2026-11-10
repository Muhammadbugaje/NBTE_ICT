"""Workflow engine — the data-driven approval chain that powers requests.

Model hierarchy:
    RequestCategory   — top-level grouping (ICT, Fleet, Facilities, HR…)
    ApprovalChain     — named, ordered set of steps
    ApprovalStep      — one step in a chain, resolved at runtime
    RequestType       — a kind of request; links category → handling dept → chain
    Request           — an instance of a RequestType, raised by a user
    RequestApproval   — one decision record per step
    RequestComment    — free-form comments on a request thread
    RequestAttachment — files on a request
    RequestActivity   — chronological timeline entry

Approver resolution is data-driven via `ApprovalStep.approver_kind`:
    - role_in_department      → any user with approver_role in scope
    - requester_head          → head of the requester's department
    - requester_deputy        → deputy director of the requester's department
    - requester_director      → director of the requester's department
    - receiving_director      → director of the handling department
    - specific_user           → a named user
    - es                      → Executive Secretary

Cross-department routing: if the request type is marked `is_cross_department`,
the chain first runs through the requester's own approval chain, then
transfers to the handling department's chain. The `receiving_department` field
is set at raise time and drives the "Incoming requests" queue for receiving
department heads.

Files: request record PDFs go to media/workflow/records/, request
attachments to media/workflow/attachments/.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import SoftDeleteModel, TimeStampedModel
from core.storage import restricted_storage, uuid_upload_to



def _approval_step_name(approval):
    """Return a display name for an approval's step.

    Synthetic steps (Finance / Audit / HR / ES / Records filing) are
    built at runtime and have no DB row, so `approval.step` is None for
    those. Fall back to the actor's role, then a generic label, so admin
    inlines and PDFs never crash on them.
    """
    if approval.step_id and approval.step is not None:
        return approval.step.name
    return approval.actor_role or "(synthetic step)"


# ===========================================================================
# Category
# ===========================================================================
class RequestCategory(TimeStampedModel):
    """Top-level grouping of request types. Purely organisational, but the
    UI groups request types by this so it's easy to scan."""

    AUDITED = True

    name = models.CharField(max_length=100, unique=True)
    code = models.SlugField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(
        max_length=50, blank=True,
        help_text="Optional icon hint (e.g. 'wrench', 'car'). Rendered by the UI.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "request categories"

    def __str__(self):
        return self.name


# ===========================================================================
# Chains
# ===========================================================================
class ApprovalChain(TimeStampedModel):
    """A named, ordered set of approval steps. Reusable across request types."""

    AUDITED = True

    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def step_count(self):
        return self.steps.count()


class ApprovalStep(models.Model):
    """One step in an `ApprovalChain`.

    `kind` determines what the step does:
      - approval     — a decision (approve / return / reject)
      - assignment   — the acting user assigns a person (staff) to do the work
      - fulfillment  — the assigned person marks the work done
      - closure      — the requester confirms receipt and closes the request

    `approver_kind` determines who acts at this step:
      - role_in_department     — resolved via `approver_role` + scope
      - requester_head         — head of the requester's department
      - requester_deputy       — deputy of the requester's department
      - requester_director     — director of the requester's department
      - receiving_director     — director of the handling department
      - specific_user          — a named user (`approver_user`)
      - es                     — Executive Secretary

    `scope` selects which department the role/user resolves against:
      - "requester"  — the requester's own department
      - "receiving"  — the handling department
      - a Department.code (specific department)
    """

    KIND_APPROVAL = "approval"
    KIND_ASSIGNMENT = "assignment"
    KIND_FULFILLMENT = "fulfillment"
    KIND_CLOSURE = "closure"
    KIND_CHOICES = [
        (KIND_APPROVAL, "Approval"),
        (KIND_ASSIGNMENT, "Assignment"),
        (KIND_FULFILLMENT, "Fulfillment"),
        (KIND_CLOSURE, "Closure"),
    ]

    APPROVER_ROLE = "role_in_department"
    APPROVER_REQUESTER_HEAD = "requester_head"
    APPROVER_REQUESTER_DEPUTY = "requester_deputy"
    APPROVER_REQUESTER_DIRECTOR = "requester_director"
    APPROVER_RECEIVING_DIRECTOR = "receiving_director"
    APPROVER_SPECIFIC = "specific_user"
    APPROVER_ES = "es"
    APPROVER_CHOICES = [
        (APPROVER_ROLE, "Anyone with a role in a department"),
        (APPROVER_REQUESTER_HEAD, "Requester's head"),
        (APPROVER_REQUESTER_DEPUTY, "Requester's deputy director"),
        (APPROVER_REQUESTER_DIRECTOR, "Requester's director"),
        (APPROVER_RECEIVING_DIRECTOR, "Receiving department's director"),
        (APPROVER_SPECIFIC, "A specific user"),
        (APPROVER_ES, "Executive Secretary"),
    ]

    AUDITED = True

    chain = models.ForeignKey(ApprovalChain, on_delete=models.CASCADE, related_name="steps")
    position = models.PositiveIntegerField(
        help_text="Order within the chain (1 = first).",
    )
    name = models.CharField(
        max_length=150,
        help_text="Human label shown on the request timeline (e.g. 'Head approval').",
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_APPROVAL)
    approver_kind = models.CharField(max_length=32, choices=APPROVER_CHOICES)
    approver_role = models.ForeignKey(
        "accounts.Role", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="Required when approver_kind = 'role_in_department'.",
    )
    approver_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="Required when approver_kind = 'specific_user'.",
    )
    scope = models.CharField(
        max_length=50, default="requester",
        help_text="'requester', 'receiving', or a specific Department.code.",
    )
    is_optional = models.BooleanField(
        default=False,
        help_text="If True, this step is skipped when no one fits the role.",
    )
    instructions = models.TextField(
        blank=True,
        help_text="Shown to the acting user as guidance.",
    )
    is_hierarchical = models.BooleanField(
        default=True,
        help_text=(
            "If True, this step is part of the organisational hierarchy and "
            "may be skipped when the resolved actor is the requester or is "
            "junior to the requester. Mandatory gates (Finance, Audit, HR, "
            "ES, Records filing) set this to False so they are never "
            "bypassed by rank."
        ),
    )    
    

    class Meta:
        ordering = ["chain", "position"]
        unique_together = ("chain", "position")
        verbose_name = "approval step"

    def __str__(self):
        return f"{self.chain.code}:{self.position} — {self.name}"


# ===========================================================================
# RequestType
# ===========================================================================
class RequestType(TimeStampedModel):
    """A kind of request. Determines routing and the approval chain.

    Examples:
      - "Broken computer" (ICT, cross-department)
      - "Vehicle trip" (Fleet, cross-department)
      - "Leave application" (HR, internal chain)
      - "Office repair" (Facilities, cross-department)
    """

    AUDITED = True

    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=80, unique=True)
    category = models.ForeignKey(
        RequestCategory, on_delete=models.PROTECT, related_name="request_types",
    )
    handling_department = models.ForeignKey(
        "staff.Department", on_delete=models.PROTECT,
        related_name="handled_request_types",
        help_text="Which department receives and acts on requests of this type.",
    )
    description = models.TextField(blank=True)

    # Approval chain used for the request
    approval_chain = models.ForeignKey(
        ApprovalChain, on_delete=models.PROTECT, related_name="request_types",
    )

    # For cross-department requests, the chain that runs in the REQUESTER's
    # own department BEFORE the request is handed off to the receiving
    # department.
    requester_chain = models.ForeignKey(
        ApprovalChain, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
        help_text="Requester-side chain that runs first for cross-department requests.",
    )

    form_schema = models.JSONField(
        default=dict, blank=True,
        help_text=(
            'Optional per-request-type form fields. Format: '
            '{"fields": [{"key": "asset", "label": "Which device?", '
            '"type": "asset_picker", "required": true, "category_codes": ["computer","laptop"]}]}'
        ),
    )

    # Extra sign-off requirements (each adds one step to the runtime chain)
    requires_es_approval = models.BooleanField(
        default=False, help_text="Add an Executive Secretary approval before fulfilment.",
    )
    requires_finance_approval = models.BooleanField(
        default=False, help_text="Add a Finance Director approval.",
    )
    requires_audit_approval = models.BooleanField(
        default=False, help_text="Add an Audit Director approval.",
    )
    requires_hr_approval = models.BooleanField(
        default=False, help_text="Add an HR Director approval.",
    )
    requires_records_filing = models.BooleanField(
        default=False,
        help_text="Add a Records step at the end to file the completed request.",
    )

    # Routing behaviour
    is_cross_department = models.BooleanField(
        default=False,
        help_text=(
            "When True, the request first runs through the requester's own "
            "approval chain, then transfers to the handling department."
        ),
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    icon = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["category__sort_order", "sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.category.code})"


# ===========================================================================
# Request
# ===========================================================================
class Request(SoftDeleteModel):
    """An instance of a `RequestType`, raised by a user.

    Status lifecycle:

        Draft → Awaiting approval → (through chain steps)
              → Awaiting receiving approval (for cross-department)
              → In progress (assigned)
              → Awaiting fulfilment confirmation
              → Ready for handover
              → Completed
        Or, at any approval step: Rejected / Returned (back to previous)
        Or, by the requester: Cancelled
    """

    AUDITED = True
    MEDIA_SUBDIR = "workflow/records"

    DRAFT = "draft"
    AWAITING = "awaiting_approval"
    AWAITING_RECEIVING = "awaiting_receiving"
    RETURNED = "returned"
    IN_PROGRESS = "in_progress"
    AWAITING_FULFILLMENT = "awaiting_fulfillment"
    READY = "ready_for_handover"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (AWAITING, "Awaiting approval"),
        (AWAITING_RECEIVING, "Awaiting receiving department"),
        (IN_PROGRESS, "In progress"),
        (AWAITING_FULFILLMENT, "Awaiting fulfilment confirmation"),
        (RETURNED, "Returned for revision"),
        (READY, "Ready for handover"),
        (COMPLETED, "Completed"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]

    CLOSED_STATUSES = {COMPLETED, REJECTED, CANCELLED}

    # --- identity ---
    reference = models.CharField(max_length=30, unique=True, db_index=True)
    request_type = models.ForeignKey(
        RequestType, on_delete=models.PROTECT, related_name="requests",
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="raised_requests",
    )

    # --- content ---
    subject = models.CharField(max_length=255)
    body = models.TextField()

    # --- routing ---
    receiving_department = models.ForeignKey(
        "staff.Department", on_delete=models.PROTECT,
        related_name="received_requests",
        help_text="Snapshot of the request type's handling department at raise time.",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_requests",
        help_text="The staff member responsible for fulfilment.",
    )

    # --- workflow state ---
    status = models.CharField(max_length=32, choices=STATUS_CHOICES,
                              default=AWAITING, db_index=True)
    current_step = models.ForeignKey(
        ApprovalStep, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    current_step_started_at = models.DateTimeField(null=True, blank=True)

    # --- return-to-requester state (revision cycle) ---
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="The approver who last returned this request to the requester.",
    )
    returned_from_step = models.ForeignKey(
        ApprovalStep, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="The step the request was at when it was returned.",
    )
    returned_at = models.DateTimeField(null=True, blank=True)
    revision_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="How many times the requester has revised and resubmitted.",
    )

    # --- completion ---
    completed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    # --- final record ---
    # Phase 0 (U3): restricted storage - served only by workflow:record_pdf
    # after the request's view check (move old files: move_record_pdfs).
    record_pdf = models.FileField(
        upload_to=uuid_upload_to, storage=restricted_storage, null=True, blank=True,
        help_text="Watermarked PDF generated when the request is completed.",
    )
    record_original_name = models.CharField(max_length=255, blank=True)
    record_generated_at = models.DateTimeField(null=True, blank=True)

    # --- misc ---
    metadata = models.JSONField(default=dict, blank=True)
    form_data = models.JSONField(
        default=dict, blank=True,
        help_text="Values submitted against the request type's form_schema.",
    )

    priority = models.CharField(
        max_length=10,
        choices=[("low", "Low"), ("normal", "Normal"),
                 ("high", "High"), ("urgent", "Urgent")],
        default="normal",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["requester", "-created_at"]),
            models.Index(fields=["assigned_to", "status"]),
            models.Index(fields=["receiving_department", "status"]),
        ]

    def __str__(self):
        return f"{self.reference} — {self.subject}"

    # ---- helpers ------------------------------------------------------
    @property
    def is_closed(self):
        return self.status in self.CLOSED_STATUSES

    @property
    def is_cross_department(self):
        return self.request_type.is_cross_department

    def timeline(self):
        """Chronological activity feed for the request detail page."""
        return self.activity.order_by("created_at", "id")


# ===========================================================================
# Approval record
# ===========================================================================
class RequestApproval(models.Model):
    """One decision at one step. Append-only.

    Multiple rows per `Request` — one per step per pass (a step that gets
    returned and re-approved produces two rows).
    """

    APPROVE = "approve"
    RETURN = "return"
    REJECT = "reject"
    DECISION_CHOICES = [
        (APPROVE, "Approved"),
        (RETURN, "Returned"),
        (REJECT, "Rejected"),
    ]

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="approvals")
    step = models.ForeignKey(ApprovalStep, null=True, blank=True, on_delete=models.PROTECT, related_name="approvals")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    actor_role = models.CharField(max_length=150, blank=True)
    decision = models.CharField(max_length=16, choices=DECISION_CHOICES)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now, db_index=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["decided_at", "id"]

    def __str__(self):
        actor = (
            (self.actor.get_full_name() or self.actor.email)
            if self.actor_id else "—"
        )
        return f"{self.get_decision_display()} · {_approval_step_name(self)} · {actor}"


# ===========================================================================
# Comments
# ===========================================================================
class RequestComment(models.Model):
    """Free-form comment on the request thread. Order preserved."""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    body = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        help_text="When True, only approvers/staff see this comment — not the requester.",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Comment on {self.request.reference} by {self.author_id}"


# ===========================================================================
# Attachments
# ===========================================================================
class RequestAttachment(TimeStampedModel):
    """File attached to a request. Stored in restricted storage and served
    only through core.views.protected_download (review finding H1)."""

    AUDITED = True
    MEDIA_SUBDIR = "workflow/attachments"
    PROTECTED_DOWNLOAD = True

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
    )
    file = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage)
    original_name = models.CharField(max_length=255)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or f"attachment {self.pk}"

    def save(self, *args, **kwargs):
        from core.uploads import enforce_allowed_extension
        enforce_allowed_extension(self)
        super().save(*args, **kwargs)

    def can_download(self, user):
        from .views import can_view_request
        return bool(user and user.is_authenticated) and can_view_request(user, self.request)

    def download_url(self):
        from django.urls import reverse
        return reverse("core:protected_download", args=[self._meta.label_lower, self.pk])


# ===========================================================================
# Activity (timeline)
# ===========================================================================
class RequestActivity(models.Model):
    """Append-only timeline of everything that ever happened on a request.

    The detail page renders `request.timeline()` verbatim. Adding a new kind
    of event only requires calling `services.log_activity()` with a new
    `kind` value — no schema change.
    """

    CREATED = "created"
    APPROVED = "approved"
    RETURNED = "returned"
    REJECTED = "rejected"
    COMMENTED = "commented"
    ATTACHED = "attached"
    ASSIGNED = "assigned"
    STATUS_CHANGED = "status_changed"
    FULFILLMENT_MARKED = "fulfillment_marked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PDF_GENERATED = "pdf_generated"
    NOTIFIED = "notified"
    DELETED = "deleted"

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="activity")
    kind = models.CharField(max_length=32, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "request activity"

    def __str__(self):
        return f"{self.kind} on {self.request.reference}"