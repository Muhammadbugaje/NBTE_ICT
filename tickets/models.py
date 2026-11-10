"""Tickets & Helpdesk models.

A ticket is a *work item* — something is broken or needed, and one
technician fixes it. This is a fundamentally different flow from the
workflow app's `Request` model, which is an *authorization* flow
(approve/reject a formal ask).

Design notes
------------
- `TicketCategory` is admin-editable. Each category has a default
  handling department and can carry its own SLA windows. Blank SLA
  values fall back to the global defaults in the config registry.
- Effective SLA = category base window × priority multiplier.
  Multipliers live in `tickets.sla.multiplier.*` config keys.
- The state machine is enforced in `tickets/services.py` (Part 2), not
  here — models stay dumb so they can be inspected in admin safely.
"""
import os
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.storage import restricted_storage


# ---------------------------------------------------------------------------
# Upload path factory — always self-contained so it can't break on
# whatever `core.storage` happens to expose. Each call to
# uuid_upload_to("tickets") returns a `upload_to` callable for a
# FileField. On-disk filename is UUID4-based; the human-readable
# original is stored in `original_name` on the model.
# ---------------------------------------------------------------------------
def ticket_attachment_upload(instance, filename):
    """Upload path for ticket attachments. Top-level so Django's
    migration serializer can reference it."""
    ext = os.path.splitext(filename or "")[1].lower()
    return f"tickets/{uuid.uuid4().hex}{ext}"
# ===========================================================================
# Ticket category
# ===========================================================================
class TicketCategory(TimeStampedModel):
    """Admin-editable category for tickets.

    Examples: Hardware, Software, Network, Access, Facilities, Other.
    Each category has a default handling department (who gets the ticket)
    and optional SLA windows (which fall back to global defaults).
    """
    AUDITED = True

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    # Which department owns tickets in this category. Required.
    handling_department = models.ForeignKey(
        "staff.Department",
        on_delete=models.PROTECT,
        related_name="handled_ticket_categories",
    )

    # Optional per-category SLA overrides (seconds). Null = use global config.
    response_seconds = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Base response window in seconds. Blank = global default.",
    )
    resolution_seconds = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Base resolution window in seconds. Blank = global default.",
    )

    # Priority floor — a ticket in this category can't be set below this.
    minimum_priority = models.CharField(
        max_length=10,
        choices=[
            ("urgent", "Urgent"),
            ("high", "High"),
            ("normal", "Normal"),
            ("low", "Low"),
        ],
        default="low",
    )

    # Optional auto-assignment — every ticket in this category goes to
    # this user on creation. Leave blank to require manual assignment.
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # --- Resolution workflow toggles ---
    requires_director_signoff = models.BooleanField(
        default=False,
        help_text=(
            "If True, the assignee's resolution must be signed off by "
            "the handling department's director before the raiser is "
            "notified. If False, the assignee's resolution goes "
            "straight to the raiser."
        ),
    )
    allow_raiser_reassign = models.BooleanField(
        default=True,
        help_text=(
            "If True, the raiser can reassign the ticket after an "
            "initial assignee has been picked. If False, only "
            "department heads can reassign."
        ),
    )

    # v2.1 Phase C (I2): tickets of this category go to a division of the
    # handling department; the division head accepts before members see it.
    handling_division = models.ForeignKey(
        "org.Division", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="handled_ticket_categories",
        help_text="Optional: route new tickets of this category to this division (head accepts).",
    )

    # Optional hero icon name (used by the templates).
    icon = models.CharField(
        max_length=40, blank=True,
        help_text="Optional icon key for the ticket picker.",
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Ticket category"
        verbose_name_plural = "Ticket categories"

    def __str__(self):
        return self.name

    def effective_response_seconds(self):
        return self.response_seconds or _cfg_int(
            "tickets.sla.default_response_seconds", 3600)

    def effective_resolution_seconds(self):
        return self.resolution_seconds or _cfg_int(
            "tickets.sla.default_resolution_seconds", 86400)


# ===========================================================================
# Ticket
# ===========================================================================
class Ticket(TimeStampedModel):
    """A helpdesk ticket.

    State machine (enforced in `tickets.services`):

        NEW ──► ASSIGNED ──► IN_PROGRESS ──► RESOLVED ──► CLOSED
                 │              │                
                 │              ├──► AWAITING_RESPONSE
                 │              │
                 │              └──► ESCALATED (SLA breach)
                 │
                 └──► CANCELLED

        RESOLVED ──► REOPENED ──► IN_PROGRESS ...
        CLOSED   ──► REOPENED
    """
    AUDITED = True

    # ---------------- State ----------------
    NEW = "new"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    AWAITING_RESPONSE = "awaiting_response"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"
    CANCELLED = "cancelled"

    AWAITING_DIRECTOR = "awaiting_director"
    AWAITING_FUNDS = "awaiting_funds"   # v2.1 I8: a funds request is with Finance / ES

    STATE_CHOICES = [
        (NEW, "New"),
        (ASSIGNED, "Assigned"),
        (IN_PROGRESS, "In progress"),
        (AWAITING_RESPONSE, "Awaiting response"),
        (AWAITING_DIRECTOR, "Awaiting director sign-off"),
        (AWAITING_FUNDS, "Awaiting funds approval"),
        (ESCALATED, "Escalated"),
        (RESOLVED, "Resolved"),
        (CLOSED, "Closed"),
        (REOPENED, "Reopened"),
        (CANCELLED, "Cancelled"),
    ]

    OPEN_STATES = {
        NEW, ASSIGNED, IN_PROGRESS, AWAITING_RESPONSE,
        AWAITING_DIRECTOR, AWAITING_FUNDS, ESCALATED, REOPENED,
    }
    CLOSED_STATES = {RESOLVED, CLOSED, CANCELLED}

    # ---------------- Priority ----------------
    URGENT = "urgent"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    PRIORITY_CHOICES = [
        (URGENT, "Urgent"),
        (HIGH, "High"),
        (NORMAL, "Normal"),
        (LOW, "Low"),
    ]

    # ---------------- Identity ----------------
    reference = models.CharField(
        max_length=20, unique=True, db_index=True,
        help_text="Allocated at creation: TKT-YYYY-NNNNN.",
    )

    # ---------------- Ownership ----------------
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tickets_raised",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_assigned",
    )
    handling_department = models.ForeignKey(
        "staff.Department",
        on_delete=models.PROTECT,
        related_name="tickets_handled",
        help_text="Set from the category on creation.",
    )

    # ---------------- Classification ----------------
    category = models.ForeignKey(
        TicketCategory,
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    priority = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default=NORMAL,
    )

    # ---------------- Content ----------------
    subject = models.CharField(max_length=255)
    body = models.TextField(help_text="What's wrong / what's needed.")
    current_location = models.CharField(
        max_length=200, blank=True,
        help_text="Where the affected item or work is.",
    )

    # ---------------- State ----------------
    state = models.CharField(
        max_length=20, choices=STATE_CHOICES, default=NEW, db_index=True,
    )
    resolution_notes = models.TextField(
        blank=True,
        help_text="What was done. Required when resolving.",
    )

    # ---------------- Optional asset link ----------------
    asset = models.ForeignKey(
        "assets.Asset",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets",
        help_text="The device this ticket concerns, if any.",
    )

    # ---------------- SLA tracking ----------------
    response_due_at = models.DateTimeField(null=True, blank=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    sla_response_breached = models.BooleanField(default=False)
    sla_resolution_breached = models.BooleanField(default=False)

    # ---------------- Escalation ----------------
    escalated_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.PositiveSmallIntegerField(
        default=0,
        help_text="0=not escalated, 1=supervisor, 2=deputy, 3=director.",
    )

    # ---------------- Reopen handling ----------------
    reopen_count = models.PositiveSmallIntegerField(default=0)
    last_reopened_at = models.DateTimeField(null=True, blank=True)

    # ---------------- Satisfaction (optional) ----------------
    satisfaction_rating = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="1–5 stars. Collected when the raiser closes the ticket.",
    )
    satisfaction_comment = models.TextField(blank=True)

    # --- Collection (filled in at resolve time) ---
    collection_location = models.CharField(
        max_length=255, blank=True,
        help_text="Where the raiser picks up the fixed item.",
    )
    collection_timeframe = models.CharField(
        max_length=255, blank=True,
        help_text="When the raiser can collect it (free text or date range).",
    )

    # --- Director sign-off ---
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_resolved",
        help_text="Assignee who marked the work done.",
    )
    director_signoff_required = models.BooleanField(
        default=False,
        help_text="Snapshot from category at resolve time.",
    )
    director_signed_off_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets_signed_off",
    )
    director_signed_off_at = models.DateTimeField(null=True, blank=True)
    director_signoff_notes = models.TextField(blank=True)

    # --- Email bookkeeping ---
    resolution_email_sent_at = models.DateTimeField(null=True, blank=True)

    # --- v2.1 Phase C (I2, I3): division routing --------------------------
    DIV_PENDING = "pending"
    DIV_ACCEPTED = "accepted"
    DIVISION_STATE_CHOICES = [("", "Not routed"), (DIV_PENDING, "Awaiting the head"),
                              (DIV_ACCEPTED, "Accepted by the division")]
    division = models.ForeignKey(
        "org.Division", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="tickets", help_text="Division handling the ticket (routed by the director).",
    )
    division_state = models.CharField(max_length=12, choices=DIVISION_STATE_CHOICES, blank=True, default="",
                                      db_index=True)
    division_routed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    division_routed_at = models.DateTimeField(null=True, blank=True)
    division_accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    division_accepted_at = models.DateTimeField(null=True, blank=True)

    # --- v2.1 Phase C (I8-I10): ticket <-> workflow request ----------------
    FUNDS_REQUESTED = "requested"
    FUNDS_APPROVED = "approved"
    FUNDS_REJECTED = "rejected"
    FUNDS_STATE_CHOICES = [("", "None"), (FUNDS_REQUESTED, "Requested"), (FUNDS_APPROVED, "Approved"),
                           (FUNDS_REJECTED, "Rejected")]
    funds_request = models.ForeignKey(
        "workflow.Request", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="funded_tickets", help_text="The latest funds request raised from this ticket.",
    )
    funds_state = models.CharField(max_length=12, choices=FUNDS_STATE_CHOICES, blank=True, default="")
    funds_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                       help_text="Amount asked for (approved amount once approved).")
    funds_decision_note = models.TextField(blank=True)
    source_request = models.ForeignKey(
        "workflow.Request", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="converted_tickets", help_text="The request this ticket was converted from (I9).",
    )

    # --- v2.1 Phase C (I4): rating reminders --------------------------------
    rated_at = models.DateTimeField(null=True, blank=True)
    rating_reminders_sent = models.PositiveSmallIntegerField(default=0)
    last_rating_reminder_at = models.DateTimeField(null=True, blank=True)


    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["assigned_to", "state"]),
            models.Index(fields=["handling_department", "state"]),
            models.Index(fields=["raised_by", "-created_at"]),
            models.Index(fields=["division", "division_state"]),
        ]

    def __str__(self):
        return f"{self.reference} — {self.subject}"

    # ---------------- Convenience properties ----------------
    @property
    def is_open(self):
        return self.state in self.OPEN_STATES

    @property
    def is_closed(self):
        return self.state in self.CLOSED_STATES

    @property
    def needs_rating(self):
        """Resolved or closed by the system, and not rated yet (I4)."""
        return self.state in (self.RESOLVED, self.CLOSED) and not self.satisfaction_rating

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("tickets:detail", args=[self.pk])

    @property
    def has_first_response(self):
        return self.first_response_at is not None

    @property
    def sla_response_overdue(self):
        """True if response SLA has passed and no first response yet."""
        if self.first_response_at or not self.response_due_at:
            return False
        return timezone.now() > self.response_due_at

    @property
    def sla_resolution_overdue(self):
        """True if resolution SLA has passed and ticket is still open."""
        if self.is_closed or not self.resolution_due_at:
            return False
        return timezone.now() > self.resolution_due_at

    @property
    def age_seconds(self):
        return int((timezone.now() - self.created_at).total_seconds())

    @property
    def time_to_response_seconds(self):
        if not self.first_response_at:
            return None
        return int((self.first_response_at - self.created_at).total_seconds())

    @property
    def time_to_resolution_seconds(self):
        if not self.resolved_at:
            return None
        return int((self.resolved_at - self.created_at).total_seconds())


# ===========================================================================
# Comment
# ===========================================================================
class TicketComment(TimeStampedModel):
    """A message on the ticket. Public unless `is_internal`."""
    AUDITED = True
    # Review M8: comment text (including internal notes) stays out of the audit log.
    AUDIT_EXCLUDE_FIELDS = ("body",)

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="comments",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT, related_name="+",
    )
    body = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        help_text="Internal notes are hidden from the ticket raiser.",
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        who = self.author.get_full_name() or self.author.email
        return f"Comment by {who} on {self.ticket.reference}"


# ===========================================================================
# Attachment
# ===========================================================================
class TicketAttachment(TimeStampedModel):
    """File attached to a ticket (screenshot, photo of broken device, log).
    Stored in restricted storage; served only via protected_download (H1)."""
    AUDITED = True
    PROTECTED_DOWNLOAD = True

    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT, related_name="+",
    )
    file = models.FileField(upload_to=ticket_attachment_upload, storage=restricted_storage)
    original_name = models.CharField(max_length=255, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_name or os.path.basename(self.file.name or "")

    def save(self, *args, **kwargs):
        from core.uploads import enforce_allowed_extension
        enforce_allowed_extension(self)
        super().save(*args, **kwargs)

    def can_download(self, user):
        if not (user and user.is_authenticated):
            return False
        from .views import _visible_tickets_qs
        return _visible_tickets_qs(user).filter(pk=self.ticket_id).exists()

    def download_url(self):
        from django.urls import reverse
        return reverse("core:protected_download", args=[self._meta.label_lower, self.pk])


# ===========================================================================
# Activity log — same shape as workflow.RequestActivity
# ===========================================================================
class TicketActivity(TimeStampedModel):
    """Append-only timeline entry — every state change, comment, assignment."""
    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name="activities",
    )
    kind = models.CharField(max_length=40)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    message = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.kind} on {self.ticket.reference}"


# ===========================================================================
# Config helper
# ===========================================================================
def _cfg_int(key, default):
    """Read an int from the config registry, silently falling back."""
    try:
        import config as _cfg
        value = _cfg.get(key, default)
        return int(value)
    except Exception:
        return default