"""Complaints (QA) — Phase 7 (Master Plan §5.6, §11; PHASES.md Phase 7).

Layers pick a flow of stages (``qa_core.FlowDefinition``); routing rules
pick who handles each stage; every stage gets a business-hour SLA, and
breaches escalate through configured levels. Nothing about the flow is in
code: an admin can add a layer with its own flow from Settings.

The internal ICT ``tickets`` app is a different thing and is untouched.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from qa_core.scoping import InstitutionScopedManager, InstitutionScopedModelMixin, UnscopedInstitutionManager
from core.models import TimeStampedModel
from qa_core.files import QAFileMixin

USER = settings.AUTH_USER_MODEL

PRIORITY_CHOICES = [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("critical", "Critical")]
RAISER_TYPES = [("institution", "Institution"), ("resource_person", "Resource person"), ("staff", "NBTE staff")]


class ComplaintLayer(TimeStampedModel):
    """A category of complaint (Master Plan §8.5 "layers"), nestable two deep."""

    AUDITED = True

    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True,
                                   help_text="Shown to the person raising the complaint.")
    applies_to_user_types = models.JSONField(default=list, blank=True,
                                             help_text='Who may raise it: ["institution", "resource_person", '
                                                       '"staff"]. Empty = everyone.')
    default_priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    flow = models.ForeignKey("qa_core.FlowDefinition", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="+", help_text="Blank = the default complaint flow.")
    custom_fields = models.JSONField(default=list, blank=True,
                                     help_text='Extra questions: [{"key", "label", "type": "text|textarea|'
                                               'choice|date", "required", "options"}].')
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        permissions = [
            # Master Plan §12, complaints row.
            ("view_complaint_queue", "Complaints: can see the department complaint queue"),
            ("handle_complaint", "Complaints: can act on a complaint stage"),
            ("assign_complaint", "Complaints: can assign or refer a complaint to someone else"),
            ("escalate_complaint", "Complaints: can escalate a complaint manually"),
            ("view_internal_messages", "Complaints: can read internal complaint notes"),
            ("view_complaint_reports", "Complaints: can see the SLA dashboard and reports"),
            ("manage_complaint_layers", "Complaints: can edit layers"),
            ("manage_routing_rules", "Complaints: can edit routing rules"),
            ("manage_escalation_rules", "Complaints: can edit escalation rules"),
            ("manage_knowledge_base", "Complaints: can edit the knowledge base"),
        ]

    def __str__(self):
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name

    def allows(self, user_type):
        return not self.applies_to_user_types or user_type in self.applies_to_user_types

    @property
    def flow_code(self):
        if self.flow_id:
            return self.flow.code
        parent = self.parent
        if parent is not None and parent.flow_id:
            return parent.flow.code
        return "complaint"


class RoutingRule(TimeStampedModel):
    """Who handles a stage. Evaluated in ``order`` on entry to each stage;
    the first rule whose ``condition`` is true wins (blank = always)."""

    AUDITED = True
    TARGET_CHOICES = [
        ("department", "A department queue"),
        ("role", "A role (in a department, or the institution's handling department)"),
        ("user", "A named person"),
        ("institution_handling_department", "The institution's handling department"),
        ("desk_officer", "The institution's desk officer (latest QA case)"),
    ]

    layer = models.ForeignKey(ComplaintLayer, null=True, blank=True, on_delete=models.CASCADE,
                              related_name="routing_rules", help_text="Blank = every layer.")
    stage_code = models.CharField(max_length=80, blank=True, help_text="Blank = every stage that has a task.")
    condition = models.CharField(max_length=500, blank=True,
                                 help_text='A formula, e.g. priority == "high". Variables: priority, layer, '
                                           'raiser_type, institution_state, institution_code, '
                                           'handling_department.')
    target_kind = models.CharField(max_length=40, choices=TARGET_CHOICES, default="department")
    target_department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="+")
    target_role = models.ForeignKey("accounts.Role", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    target_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"#{self.order} {self.layer or 'Every layer'} → {self.get_target_kind_display()}"


class Complaint(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "institution"
    FLOW_STATUS_FIELD = "status"
    OPEN_EXCLUDED = ("closed", "withdrawn")

    reference = models.CharField(max_length=40, unique=True)
    layer = models.ForeignKey(ComplaintLayer, on_delete=models.PROTECT, related_name="complaints")
    raised_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="complaints_raised")
    raiser_type = models.CharField(max_length=20, choices=RAISER_TYPES, default="institution")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="complaints")
    subject = models.CharField(max_length=200)
    body = models.TextField()
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="normal")
    answers = models.JSONField(default=dict, blank=True, help_text="Answers to the layer's custom fields.")
    related_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    related_id = models.CharField(max_length=64, blank=True)
    related_record = GenericForeignKey("related_type", "related_id")
    related_label = models.CharField(max_length=200, blank=True)
    flow_code = models.CharField(max_length=80, default="complaint")
    status = models.CharField(max_length=80, blank=True, db_index=True)
    current_department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                           related_name="+")
    current_assignee = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    referred_department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                            related_name="+", help_text="Set by 'Refer internally'.")
    referred_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    watchers = models.ManyToManyField(USER, blank=True, related_name="complaints_watching")
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    reopened_count = models.PositiveSmallIntegerField(default=0)
    satisfaction = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1–5 from the raiser.")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.reference} · {self.subject}"

    @property
    def is_open(self):
        """Open until its flow reaches an end stage (closed, withdrawn, or a
        layer's own end stage); ``closed_at`` is set then."""
        return self.closed_at is None

    def open_stage(self):
        return self.stages.filter(left_at__isnull=True).order_by("-entered_at", "-id").first()


class ComplaintStage(models.Model):
    """One row per stage entered (its SLA clock, assignment and escalation)."""

    AUDITED = True

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="stages")
    stage_code = models.CharField(max_length=80)
    stage_label = models.CharField(max_length=150, blank=True)
    assigned_department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.PROTECT,
                                            related_name="+")
    assigned_to = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    routed_by = models.ForeignKey(RoutingRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    entered_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    sla_seconds = models.PositiveIntegerField(null=True, blank=True)
    business_hours = models.BooleanField(default=False)
    pauses_sla = models.BooleanField(default=False)
    remaining_seconds = models.PositiveIntegerField(null=True, blank=True,
                                                    help_text="SLA left when the stage was left for a pause.")
    reminded_at = models.DateTimeField(null=True, blank=True)
    breached_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)
    left_at = models.DateTimeField(null=True, blank=True)
    task = models.ForeignKey("tasks.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["entered_at", "id"]
        indexes = [models.Index(fields=["left_at", "due_at"])]

    def __str__(self):
        return f"{self.complaint.reference} · {self.stage_code}"


class ComplaintMessage(models.Model):
    """A message on a complaint. ``internal`` messages never reach the portal (TR-12)."""

    AUDITED = True
    PUBLIC, INTERNAL = "public", "internal"
    VISIBILITY_CHOICES = [(PUBLIC, "Visible to the raiser"), (INTERNAL, "Internal note (NBTE only)")]

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default=PUBLIC)
    mentions = models.ManyToManyField(USER, blank=True, related_name="+")
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]


class ComplaintAttachment(QAFileMixin, TimeStampedModel):
    AUDITED = True
    MEDIA_SUBDIR = "qa/complaints"
    INSTITUTION_MEDIA_KIND = "complaints"

    def media_institution(self):
        """Files land under the complained-about institution's folder."""
        return self.complaint.institution if self.complaint_id else None

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(ComplaintMessage, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="attachments")
    uploaded_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    scan_status = models.CharField(max_length=12, default="not_scanned")

    def can_download(self, user):
        from . import services

        if self.message_id and self.message.visibility == ComplaintMessage.INTERNAL:
            return services.can_handle_view(user, self.complaint)
        return services.can_view(user, self.complaint)


class EscalationRule(TimeStampedModel):
    """After a breach: who is told, at which level and how long after."""

    AUDITED = True
    TARGET_CHOICES = [
        ("department_director", "The Director of the handling department"),
        ("deputy_director", "The Deputy Director of the handling department"),
        ("division_head", "The Division Head of the handling department"),
        ("es", "The Executive Secretary"),
        ("role", "Anyone holding a role"),
        ("user", "A named person"),
    ]
    ONLY_IF_CHOICES = [("", "Always"), ("critical_or_repeat", "Only for Critical priority or a repeated breach")]

    flow = models.ForeignKey("qa_core.FlowDefinition", null=True, blank=True, on_delete=models.CASCADE,
                             related_name="+", help_text="Blank = every complaint flow.")
    stage_code = models.CharField(max_length=80, blank=True, help_text="Blank = every stage.")
    level = models.PositiveSmallIntegerField()
    after_breach_minutes = models.PositiveIntegerField(default=0)
    target_kind = models.CharField(max_length=30, choices=TARGET_CHOICES, default="department_director")
    target_role = models.ForeignKey("accounts.Role", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    target_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    only_if = models.CharField(max_length=30, choices=ONLY_IF_CHOICES, blank=True)
    notify_template = models.CharField(max_length=80, default="complaint.escalated")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["level", "after_breach_minutes", "id"]

    def __str__(self):
        return f"Level {self.level} after {self.after_breach_minutes} min → {self.get_target_kind_display()}"


class Escalation(models.Model):
    AUDITED = True

    complaint_stage = models.ForeignKey(ComplaintStage, on_delete=models.CASCADE, related_name="escalations")
    rule = models.ForeignKey(EscalationRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    level = models.PositiveSmallIntegerField()
    escalated_to = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name="+")
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                           help_text="Blank = the SLA sweep; a person for a manual escalation.")
    reason = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    task = models.ForeignKey("tasks.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["at", "id"]


class KnowledgeArticle(TimeStampedModel):
    """§8.10 / TR-17: help shown while the complaint subject is typed."""

    AUDITED = True
    AUDIENCE_CHOICES = [("all", "Everyone"), ("institution", "Institutions"),
                        ("resource_person", "Resource persons"), ("staff", "NBTE staff")]

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    summary = models.CharField(max_length=300, blank=True)
    body = models.TextField()
    keywords = models.CharField(max_length=300, blank=True, help_text="Extra words people search with.")
    layer = models.ForeignKey(ComplaintLayer, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="articles")
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default="all")
    helpful = models.PositiveIntegerField(default=0)
    not_helpful = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title
