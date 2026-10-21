"""qa_core — the shared engines every QA app uses (Master Plan §1, §5.1).

Generic machinery only, keyed by ContentType. No business data (institutions,
payments, SSQs …) lives here.

    Flow engine          FlowDefinition, FlowStage, FlowTransition, FlowInstance, FlowEvent
    Two-step pattern     ActionPolicy, PendingAction
    References           ReferenceFormat, ReferenceCounter
    Task bridge          QATaskLink
    Institution record   TimelineEvent
    Holds                Suspension
    Notifications        NotificationTemplate, NotificationRule
    Seeding              DemoTag

Deliberate, documented additions to the §5.1 field lists (PHASE1_CHANGELOG.md):
* `FlowDefinition.subject_model` is the model *label* ("payments.PaymentObligation")
  rather than a ContentType FK, so a flow can be seeded before its subject
  model exists (the payment flow is seeded in Phase 1, its model in Phase 2).
* `FlowTransition.required_role_setting` names a setting whose value is the
  role code the actor must hold (`qa.payment.final_approver_role`). This is how
  the v1.1 rule "only the Director of Finance confirms" is data, not code.
* `FlowStage.allows_cancel` / `pauses_sla` (referenced by §5.4 and §11).
* `PendingAction.scope_department` — the department whose officers may see and
  confirm the item (§6: "in the same department unless confirm_any_department").
* `ReferenceFormat.sequence_scope` — whether `{SEQ}` counts globally, per
  institution or per department (§10.5 examples need all three).
* `created_by` / `updated_by` use SET_NULL rather than PROTECT: the audit log
  already keeps the actor's identity, and PROTECT would make demo/user
  removal impossible. Append-only records (`PendingAction`, `FlowEvent`) keep
  PROTECT on their actors as specified.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel

from .scoping import (
    InstitutionScopedManager,
    InstitutionScopedModelMixin,
    UnscopedInstitutionManager,
)

USER = settings.AUTH_USER_MODEL


class QABaseModel(TimeStampedModel):
    """TimeStampedModel + who created/last changed the row (Master Plan §2)."""

    AUDITED = True

    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        from core.audit import get_current_actor

        actor = get_current_actor()
        if actor is not None and getattr(actor, "pk", None):
            if self._state.adding and not self.created_by_id:
                self.created_by = actor
            self.updated_by = actor
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "updated_by" not in update_fields:
                kwargs["update_fields"] = list(update_fields) + ["updated_by"]
        super().save(*args, **kwargs)


class AppendOnlyMixin:
    """Rows that must never be edited once written (FlowEvent, TimelineEvent)."""

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise PermissionError(f"{self._meta.label} is append-only.")
        super().save(*args, **kwargs)


# ===========================================================================
# Flow engine (§5.1, §5.7)
# ===========================================================================
class FlowDefinition(QABaseModel):
    """A versioned state machine. Editing a published flow creates a new
    version; running instances stay pinned to the version they started on."""

    code = models.SlugField(max_length=80)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    subject_model = models.CharField(
        max_length=100,
        help_text="Model label of the subject, e.g. 'payments.PaymentObligation'.",
    )
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    class Meta:
        ordering = ["code", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["code", "version"], name="qa_flow_code_version_unique"),
        ]
        # Every qa_core permission codename (Master Plan §12, plus the two
        # v1.1 oversight permissions from §21). They live on this model only
        # because Django permissions must hang off a model.
        permissions = [
            ("view_all_institutions", "QA: can see every institution (all departments)"),
            ("manage_flows", "QA: can edit workflow drafts (flows, stages, transitions)"),
            ("publish_flow_version", "QA: can publish a new workflow version"),
            ("manage_reference_formats", "QA: can propose reference-format changes"),
            ("manage_notification_templates", "QA: can edit notification templates and rules"),
            ("edit_qa_settings", "QA: can edit QA settings (dual-control keys need a second officer)"),
            ("edit_qa_dual_control_settings", "QA: can confirm dual-control QA settings changes"),
            ("confirm_any_department", "QA: can confirm pending actions of any department"),
            ("reassign_qa_task", "QA: can reassign QA-linked tasks"),
            ("view_pending_actions", "QA: can see pending confirmations in scope"),
            ("place_suspension", "QA: can place a hold on a QA record"),
            ("lift_suspension", "QA: can lift a hold on a QA record"),
            ("view_timeline_internal", "QA: can see internal institution timeline events"),
            ("view_oversight_board", "QA: can see the ICT oversight board"),
            ("contact_on_oversight_board", "QA: can send reminders/escalations from the oversight board"),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    @property
    def is_published(self):
        return self.published_at is not None


class FlowStage(QABaseModel):
    START, ACTION, PARALLEL_GATE, JOIN, SYSTEM, END = (
        "start", "action", "parallel_gate", "join", "system", "end",
    )
    KIND_CHOICES = [
        (START, "Start"), (ACTION, "Action"), (PARALLEL_GATE, "Parallel gate"),
        (JOIN, "Join"), (SYSTEM, "System"), (END, "End"),
    ]
    ACTOR_KIND_CHOICES = [
        ("role_in_department", "Role in department"),
        ("owning_department_director", "Owning department Director"),
        ("owning_department_deputy", "Owning department Deputy Director"),
        ("division_director", "Division Director"),
        ("es", "Executive Secretary"),
        ("specific_user", "Specific user"),
        ("institution", "Institution"),
        ("resource_person", "Resource person"),
        ("system", "System"),
        ("finance_director", "Director of Finance"),
        ("audit_director", "Director of Audit"),
        ("ict_director", "Director of ICT"),
        ("cqac", "CQAC"),
        ("cqac_chair", "CQAC Chairman"),
    ]

    flow = models.ForeignKey(FlowDefinition, on_delete=models.CASCADE, related_name="stages")
    code = models.SlugField(max_length=80)
    label = models.CharField(max_length=150, help_text="Status name shown internally.")
    public_label = models.CharField(
        max_length=150, blank=True,
        help_text="Plain-language status shown to institutions (Requirement 18).",
    )
    sop_stage_no = models.PositiveSmallIntegerField(null=True, blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=ACTION)
    actor_kind = models.CharField(max_length=40, choices=ACTOR_KIND_CHOICES, default="system")
    actor_role = models.ForeignKey("accounts.Role", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    actor_scope = models.CharField(max_length=50, blank=True,
                                   help_text="'owning', 'institution_home', or a department code.")
    sla_hours = models.PositiveIntegerField(null=True, blank=True)
    sla_business_hours = models.BooleanField(default=False)
    creates_task = models.BooleanField(default=False)
    task_category = models.ForeignKey("tasks.TaskCategory", null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name="+")
    form_key = models.CharField(max_length=80, blank=True)
    is_terminal_success = models.BooleanField(default=False)
    is_terminal_failure = models.BooleanField(default=False)
    allows_cancel = models.BooleanField(default=False)
    pauses_sla = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["flow", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["flow", "code"], name="qa_stage_flow_code_unique"),
        ]

    def __str__(self):
        return f"{self.flow.code}:{self.code}"


class FlowTransition(QABaseModel):
    flow = models.ForeignKey(FlowDefinition, on_delete=models.CASCADE, related_name="transitions")
    from_stage = models.ForeignKey(FlowStage, on_delete=models.CASCADE, related_name="outgoing")
    to_stage = models.ForeignKey(FlowStage, on_delete=models.CASCADE, related_name="incoming")
    action_code = models.SlugField(max_length=60)
    label = models.CharField(max_length=150)
    requires_remark = models.BooleanField(default=False)
    required_permission = models.CharField(max_length=120, blank=True,
                                           help_text="Permission label, e.g. payments.final_confirm_payment.")
    required_role_setting = models.CharField(
        max_length=150, blank=True,
        help_text="Setting key whose value is the role code the actor must hold.",
    )
    condition = models.TextField(blank=True, help_text="Formula that must evaluate to true.")
    requires_confirmation = models.BooleanField(default=False,
                                                help_text="Route through PendingAction.")
    effects = models.JSONField(default=list, blank=True)
    notify_rule = models.ForeignKey("NotificationRule", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["flow", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["from_stage", "action_code"],
                                    name="qa_transition_from_action_unique"),
        ]

    def __str__(self):
        return f"{self.from_stage.code} --{self.action_code}--> {self.to_stage.code}"

    def clean(self):
        if self.from_stage_id and self.to_stage_id:
            if self.from_stage.flow_id != self.flow_id or self.to_stage.flow_id != self.flow_id:
                raise ValidationError("Both stages must belong to this flow.")


class FlowInstance(TimeStampedModel):
    AUDITED = True

    flow_version = models.ForeignKey(FlowDefinition, on_delete=models.PROTECT, related_name="instances")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    subject = GenericForeignKey("content_type", "object_id")
    current_stages = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=40, blank=True)
    paused = models.BooleanField(default=False)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.flow_version.code} #{self.pk} @ {','.join(self.current_stages) or '—'}"


class FlowEvent(AppendOnlyMixin, models.Model):
    """Append-only history of every move through a flow."""

    instance = models.ForeignKey(FlowInstance, on_delete=models.CASCADE, related_name="events")
    from_stage = models.CharField(max_length=80, blank=True)
    to_stage = models.CharField(max_length=80, blank=True)
    action_code = models.CharField(max_length=60)
    actor = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actor_role_label = models.CharField(max_length=100, blank=True)
    remark = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(default=timezone.now, db_index=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["at", "id"]

    def __str__(self):
        return f"{self.instance_id}: {self.from_stage} -> {self.to_stage} ({self.action_code})"


# ===========================================================================
# Two-step confirmation (§5.1, §7)
# ===========================================================================
class ActionPolicy(QABaseModel):
    """Data, not code: whether an action needs a second officer, who may
    initiate and confirm, the minimum confirmer rank, expiry, menu placement."""

    action_code = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    menu_group = models.CharField(max_length=60, blank=True)
    requires_second_officer = models.BooleanField(default=True)
    initiate_permission = models.CharField(max_length=120, blank=True)
    confirm_permission = models.CharField(max_length=120, blank=True)
    confirmer_min_rank = models.PositiveIntegerField(default=50)
    same_department_required = models.BooleanField(default=True)
    expires_after_hours = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Blank = qa.settings.pending_action_expiry_hours.",
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["menu_group", "label"]
        verbose_name_plural = "action policies"

    def __str__(self):
        return self.label


class PendingAction(TimeStampedModel):
    """One initiated change waiting for (or decided by) a second officer."""

    AUDITED = True

    PENDING, CONFIRMED, REJECTED, WITHDRAWN, EXPIRED, APPLIED, FAILED = (
        "pending", "confirmed", "rejected", "withdrawn", "expired", "applied", "failed",
    )
    STATUS_CHOICES = [
        (PENDING, "Pending"), (CONFIRMED, "Confirmed"), (REJECTED, "Rejected"),
        (WITHDRAWN, "Withdrawn"), (EXPIRED, "Expired"), (APPLIED, "Applied"),
        (FAILED, "Failed"),
    ]
    OPEN_STATUSES = (PENDING,)

    reference = models.CharField(max_length=60, unique=True)
    policy = models.ForeignKey(ActionPolicy, to_field="action_code", db_column="action_code",
                               on_delete=models.PROTECT, related_name="pending_actions")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    object_id = models.CharField(max_length=64, blank=True)
    target = GenericForeignKey("content_type", "object_id")
    target_label = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    target_version = models.CharField(max_length=64, blank=True,
                                      help_text="Target's updated_at at initiation (stale check).")
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    scope_department = models.ForeignKey("staff.Department", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="+")
    initiated_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    initiated_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remark = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    failure_detail = models.TextField(blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-initiated_at", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id", "status"])]

    def __str__(self):
        return f"{self.reference} · {self.policy_id} · {self.status}"

    @property
    def action_code(self):
        return self.policy_id

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def is_overdue(self):
        return self.is_open and self.expires_at is not None and self.expires_at <= timezone.now()


# ===========================================================================
# References (§5.1, §10.5)
# ===========================================================================
class ReferenceFormat(QABaseModel):
    NEVER, YEARLY = "never", "yearly"
    RESET_CHOICES = [(NEVER, "Never"), (YEARLY, "Yearly")]
    CHECK_NONE, CHECK_MOD11, CHECK_MOD97 = "none", "mod11", "mod97"
    CHECK_CHOICES = [(CHECK_NONE, "None"), (CHECK_MOD11, "Mod 11"), (CHECK_MOD97, "ISO 7064 mod 97-10")]
    SCOPE_GLOBAL, SCOPE_INSTITUTION, SCOPE_DEPARTMENT = "global", "institution", "department"
    SCOPE_CHOICES = [
        (SCOPE_GLOBAL, "One series for everything"),
        (SCOPE_INSTITUTION, "One series per institution"),
        (SCOPE_DEPARTMENT, "One series per department"),
    ]

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    pattern = models.CharField(max_length=120)
    reset_period = models.CharField(max_length=10, choices=RESET_CHOICES, default=YEARLY)
    check_digit = models.CharField(max_length=10, choices=CHECK_CHOICES, default=CHECK_NONE)
    allowed_chars = models.CharField(
        max_length=120, blank=True,
        help_text="Regex character class the rendered reference must match, e.g. A-Z0-9-.= (blank = any).",
    )
    sequence_scope = models.CharField(max_length=12, choices=SCOPE_CHOICES, default=SCOPE_GLOBAL)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code}: {self.pattern}"


class ReferenceCounter(models.Model):
    format = models.ForeignKey(ReferenceFormat, on_delete=models.CASCADE, related_name="counters")
    period_key = models.CharField(max_length=20, blank=True)
    scope_key = models.CharField(max_length=40, blank=True)
    last_value = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["format", "period_key", "scope_key"],
                                    name="qa_refcounter_unique"),
        ]

    def __str__(self):
        return f"{self.format.code}[{self.period_key}/{self.scope_key}] = {self.last_value}"


# ===========================================================================
# Task bridge (§5.1, §8) — model only in Phase 1; the bridge ships in Phase 4.
# ===========================================================================
class QATaskLink(TimeStampedModel):
    AUDITED = True
    KIND_CHOICES = [
        ("stage_action", "Stage action"), ("review", "Review"),
        ("assignment", "Assignment"), ("complaint_stage", "Complaint stage"),
    ]

    task = models.OneToOneField("tasks.Task", on_delete=models.CASCADE, related_name="qa_link")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.PositiveBigIntegerField()
    subject = GenericForeignKey("content_type", "object_id")
    flow_instance = models.ForeignKey(FlowInstance, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="task_links")
    stage_code = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="stage_action")
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=40, blank=True)
    # Phase 4: SLA reminders already sent (percent thresholds) and the breach notice.
    due_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.JSONField(default=list, blank=True)
    breach_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id", "closed_at"])]

    def __str__(self):
        return f"Task {self.task_id} ↔ {self.content_type.model}#{self.object_id} ({self.stage_code})"

    @property
    def is_open(self):
        return self.closed_at is None


class TaskLinkDriftReport(models.Model):
    """One run of the nightly task/QA reconciliation (§8 step 5), shown to
    the ICT monitor: what drifted and what the job repaired."""

    run_at = models.DateTimeField(default=timezone.now, db_index=True)
    checked = models.PositiveIntegerField(default=0)
    opened = models.PositiveIntegerField(default=0)
    closed = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=list, blank=True)
    dry_run = models.BooleanField(default=False)

    class Meta:
        ordering = ["-run_at"]

    def __str__(self):
        return f"Reconciliation {self.run_at:%Y-%m-%d %H:%M}: +{self.opened} / -{self.closed}"


# ===========================================================================
# Unified institution timeline (§5.1, D12, Requirement 18)
# ===========================================================================
class TimelineEvent(InstitutionScopedModelMixin, AppendOnlyMixin, models.Model):
    """Append-only record, written only by `qa_core.timeline.record()`."""

    INSTITUTION_PATH = "institution"
    PUBLIC, INTERNAL = "public", "internal"
    VISIBILITY_CHOICES = [(PUBLIC, "Public (institution can see)"), (INTERNAL, "Internal only")]

    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="timeline_events")
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    object_id = models.CharField(max_length=64, blank=True)
    subject = GenericForeignKey("content_type", "object_id")
    event_code = models.CharField(max_length=80, db_index=True)
    public_text = models.TextField(blank=True)
    internal_text = models.TextField(blank=True)
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default=INTERNAL)
    actor = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now, db_index=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.at:%Y-%m-%d} {self.event_code}"


# ===========================================================================
# Suspensions / holds (§5.1, Requirements 15–17; off by default)
# ===========================================================================
class Suspension(TimeStampedModel):
    AUDITED = True

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=64)
    target = GenericForeignKey("content_type", "object_id")
    reason = models.TextField()
    # The complaints app (Phase 7) links a hold to its complaint; until then
    # the complaint's reference is stored as text.
    linked_complaint_reference = models.CharField(max_length=60, blank=True)
    placed_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    placed_at = models.DateTimeField(default=timezone.now)
    lifted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    lifted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]

    @property
    def is_active(self):
        return self.lifted_at is None

    def __str__(self):
        return f"Hold on {self.content_type.model}#{self.object_id} ({'active' if self.is_active else 'lifted'})"


# ===========================================================================
# Notification templates and rules (§5.1)
# ===========================================================================
class NotificationTemplate(QABaseModel):
    IN_APP, EMAIL, SMS = "in_app", "email", "sms"
    CHANNEL_CHOICES = [(IN_APP, "In-app"), (EMAIL, "Email"), (SMS, "SMS")]

    code = models.SlugField(max_length=80)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default=IN_APP)
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(help_text="Django template syntax; rendered in a sandbox.")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code", "channel"]
        constraints = [
            models.UniqueConstraint(fields=["code", "channel"], name="qa_template_code_channel_unique"),
        ]

    def __str__(self):
        return f"{self.code} [{self.channel}]"


class NotificationRule(QABaseModel):
    RECIPIENT_CHOICES = [
        ("actor", "Stage actor"),
        ("institution_users", "Institution users"),
        ("initiator", "Initiator"),
        ("eligible_confirmers", "Eligible confirmers"),
        ("department_head", "Department head"),
        ("role", "Role"),
        ("escalation_target", "Escalation target"),
    ]

    event_code = models.CharField(max_length=80, db_index=True)
    flow = models.ForeignKey(FlowDefinition, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="notification_rules")
    stage = models.ForeignKey(FlowStage, null=True, blank=True, on_delete=models.CASCADE,
                              related_name="notification_rules")
    recipient_kind = models.CharField(max_length=30, choices=RECIPIENT_CHOICES)
    recipient_role = models.ForeignKey("accounts.Role", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")
    template = models.ForeignKey(NotificationTemplate, on_delete=models.PROTECT, related_name="rules")
    delay_minutes = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["event_code", "id"]

    def __str__(self):
        return f"{self.event_code} → {self.recipient_kind} ({self.template.code})"


# ===========================================================================
# Demo tagging (§15)
# ===========================================================================
class DemoTag(models.Model):
    """Rows created by `seed_startup --with-demo`. `--reset-demo` deletes
    exactly these rows (and nothing untagged)."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.CharField(max_length=64)
    target = GenericForeignKey("content_type", "object_id")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["content_type", "object_id"], name="qa_demotag_unique"),
        ]

    def __str__(self):
        return f"demo {self.content_type.app_label}.{self.content_type.model}#{self.object_id}"

    @classmethod
    def tag(cls, obj):
        ct = ContentType.objects.get_for_model(obj, for_concrete_model=True)
        cls.objects.get_or_create(content_type=ct, object_id=str(obj.pk))

    @classmethod
    def is_tagged(cls, obj):
        if obj is None or getattr(obj, "pk", None) is None:
            return False
        ct = ContentType.objects.get_for_model(obj, for_concrete_model=True)
        return cls.objects.filter(content_type=ct, object_id=str(obj.pk)).exists()


# ===========================================================================
# Console e-mail outbox (Phase 2: e-mails are logged, not sent)
# ===========================================================================
class OutboundEmail(models.Model):
    """Every QA e-mail while ``settings.QA_EMAIL_DELIVERY == "console"``.

    Phase 2 builds the mechanics; delivery is switched on in a later phase by
    setting ``QA_EMAIL_DELIVERY=queue``. Until then each message is written to
    the console log *and* kept here, so an administrator can read it (for
    example a set-password link) from *QA → E-mail outbox*."""

    subject = models.CharField(max_length=255)
    recipients = models.JSONField(default=list)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    category = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.subject}"
