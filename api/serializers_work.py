"""Request validation and response shapes for the staff "Work" endpoints:
tasks, tickets, requests (workflow) and leave.

Request serializers only check types/shapes; the business validation is
done by the website's own forms and services (see api/services/*.py), whose
errors come back as the contract's ``fields``.
"""
from rest_framework import serializers


from drf_spectacular.types import OpenApiTypes as _OAT
from drf_spectacular.utils import extend_schema_field as _esf


@_esf(_OAT.STR)
class PlainChoiceField(serializers.ChoiceField):
    """Validated like a ChoiceField, documented as a plain string (avoids
    enum-name collisions with other API domains in the schema)."""

from .serializers import RefSerializer

PRIORITIES = ["urgent", "high", "normal", "low"]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
class UserRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    initials = serializers.CharField()


class CodeRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()


class WorkAttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    size = serializers.IntegerField(allow_null=True, help_text="Bytes; null if the file is missing.")
    uploaded_by = UserRefSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    download_url = serializers.CharField(help_text="GET with the Bearer token; add ?inline=1 to preview.")


class WorkActivitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    actor = UserRefSerializer(allow_null=True)
    message = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class AttachmentUploadSerializer(serializers.Serializer):
    """multipart/form-data. Allowed types and size: the website's
    core.uploads.validate_attachment_upload."""
    file = serializers.FileField()
    description = serializers.CharField(max_length=255, required=False, allow_blank=True)


class CommentRequestSerializer(serializers.Serializer):
    body = serializers.CharField(trim_whitespace=False, allow_blank=True)
    is_internal = serializers.BooleanField(required=False, default=False)


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class RequiredNoteSerializer(serializers.Serializer):
    note = serializers.CharField(allow_blank=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class RequiredReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=True)


class TaskPartSerializer(serializers.Serializer):
    """Phase C (H14, H15): one person's part; ``reason`` when returning it."""
    user = serializers.IntegerField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class TaskDivisionRouteSerializer(serializers.Serializer):
    """Phase C (H23): route to a division (optionally naming members)."""
    division = serializers.IntegerField()
    members = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class PeopleQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
TASK_SCOPES = ["assigned", "created", "review", "all"]
TASK_STATE_FILTERS = ["active", "open", "overdue", "completed", "cancelled", "all", "draft", "in_progress",
                      "awaiting_response", "returned", "awaiting_review"]


class TaskListQuerySerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=TASK_SCOPES, required=False, default="assigned")
    state = serializers.ChoiceField(choices=TASK_STATE_FILTERS, required=False, default="active")
    priority = PlainChoiceField(choices=PRIORITIES, required=False)
    category = serializers.CharField(required=False, help_text="Category code.")
    due = serializers.ChoiceField(choices=["overdue", "today", "week", "none"], required=False)
    q = serializers.CharField(required=False, allow_blank=True)
    sort = serializers.ChoiceField(choices=["created", "updated", "due", "priority", "state", "title", "reference"],
                                   required=False)
    direction = serializers.ChoiceField(choices=["asc", "desc"], required=False)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class TaskCategorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    handling_department = RefSerializer(allow_null=True)
    default_priority = serializers.CharField()
    default_due_days = serializers.IntegerField()
    requires_review = serializers.BooleanField()
    icon = serializers.CharField(allow_blank=True)
    colour = serializers.CharField(allow_blank=True)


class TaskListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    title = serializers.CharField()
    state = serializers.CharField()
    state_display = serializers.CharField()
    priority = serializers.CharField()
    priority_display = serializers.CharField()
    category = CodeRefSerializer(allow_null=True)
    due_date = serializers.DateField(allow_null=True)
    due_label = serializers.CharField(allow_blank=True)
    is_overdue = serializers.BooleanField()
    created_by = UserRefSerializer()
    owner_department = RefSerializer(allow_null=True)
    progress_percent = serializers.IntegerField()
    assignee_count = serializers.IntegerField()
    my_state = serializers.CharField(allow_null=True, help_text="My assignee row's state, if I am an assignee.")
    updated_at = serializers.DateTimeField()


class TaskAssigneeSerializer(serializers.Serializer):
    user = UserRefSerializer()
    state = serializers.CharField()
    state_display = serializers.CharField()
    note = serializers.CharField(allow_blank=True)
    assigned_by = UserRefSerializer(allow_null=True)
    assigned_at = serializers.DateTimeField()
    accepted_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)
    review_state = serializers.CharField(help_text="pending / confirmed / returned (Phase C, H14-H15)")
    review_note = serializers.CharField(allow_blank=True)


class TaskRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    title = serializers.CharField()
    state = serializers.CharField()
    state_display = serializers.CharField()


class TaskDependencySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Dependency link id.")
    depends_on = TaskRefSerializer()
    note = serializers.CharField(allow_blank=True)
    met = serializers.BooleanField()


class TaskCommentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    author = UserRefSerializer()
    body = serializers.CharField()
    is_internal = serializers.BooleanField()
    is_pinned = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    can_delete = serializers.BooleanField()


class TimeEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    user = UserRefSerializer()
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.IntegerField()
    hours = serializers.FloatField()
    note = serializers.CharField(allow_blank=True)
    is_running = serializers.BooleanField()


class TaskActionsSerializer(serializers.Serializer):
    """Which buttons the website shows this user (tasks.services.available_actions)."""
    is_creator = serializers.BooleanField()
    is_assignee = serializers.BooleanField()
    can_edit = serializers.BooleanField()
    can_open = serializers.BooleanField()
    can_cancel = serializers.BooleanField()
    can_delete = serializers.BooleanField()
    can_review = serializers.BooleanField()
    can_assign = serializers.BooleanField()
    can_accept = serializers.BooleanField()
    can_start = serializers.BooleanField()
    can_reopen = serializers.BooleanField()
    can_start_timer = serializers.BooleanField()
    can_stop_timer = serializers.BooleanField()
    can_log_time = serializers.BooleanField()
    can_ask = serializers.BooleanField()
    can_resume = serializers.BooleanField()
    can_return = serializers.BooleanField()
    can_mark_done = serializers.BooleanField()
    can_resend = serializers.BooleanField()
    can_comment = serializers.BooleanField()
    can_attach = serializers.BooleanField()
    # Phase C (v2.1)
    can_accept_start = serializers.BooleanField()
    can_confirm_parts = serializers.BooleanField()
    can_confirm_all = serializers.BooleanField()
    can_route_division = serializers.BooleanField()
    can_division_accept = serializers.BooleanField()
    can_division_complete = serializers.BooleanField()
    can_download_report = serializers.BooleanField()


class TaskDivisionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    state = serializers.CharField(help_text="pending / accepted / declined")


class TaskLinkedRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()


class TaskDetailSerializer(TaskListItemSerializer):
    description = serializers.CharField(allow_blank=True)
    start_date = serializers.DateField(allow_null=True)
    estimated_hours = serializers.CharField(allow_null=True, help_text="Decimal as string.")
    logged_hours = serializers.FloatField()
    requires_review = serializers.BooleanField()
    reviewer = UserRefSerializer(allow_null=True)
    reviewed_by = UserRefSerializer(allow_null=True)
    reviewed_at = serializers.DateTimeField(allow_null=True)
    review_notes = serializers.CharField(allow_blank=True)
    tags = serializers.ListField(child=serializers.CharField())
    recurrence_type = serializers.CharField()
    recurrence_rule = serializers.CharField(allow_blank=True)
    parent = TaskRefSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    assignees = TaskAssigneeSerializer(many=True)
    subtasks = TaskListItemSerializer(many=True)
    dependencies = TaskDependencySerializer(many=True)
    dependencies_met = serializers.BooleanField()
    comments = TaskCommentSerializer(many=True)
    attachments = WorkAttachmentSerializer(many=True)
    time_entries = TimeEntrySerializer(many=True)
    running_timer = TimeEntrySerializer(allow_null=True)
    activity = WorkActivitySerializer(many=True)
    qa_link_url = serializers.CharField(allow_blank=True,
                                        help_text="Set when the task is driven from the QA screens.")
    division = TaskDivisionSerializer(allow_null=True, help_text="Group task: the division it is routed to.")
    linked_request = TaskLinkedRequestSerializer(allow_null=True,
                                                 help_text="Cross-department subtask handled as this request.")
    actions = TaskActionsSerializer()


class TaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    category = serializers.IntegerField(required=False, allow_null=True, help_text="Id from tasks/categories/.")
    priority = PlainChoiceField(choices=PRIORITIES, required=False,
                                       help_text="Defaults to the category's default priority.")
    start_date = serializers.DateField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    requires_review = serializers.BooleanField(required=False)
    reviewer = serializers.IntegerField(required=False, allow_null=True)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False)
    recurrence_type = serializers.ChoiceField(choices=["none", "daily", "weekly", "monthly", "custom"],
                                              required=False)
    recurrence_rule = serializers.CharField(max_length=100, required=False, allow_blank=True)
    assignees = serializers.ListField(child=serializers.IntegerField(), required=False,
                                      help_text="User ids from tasks/people/.")
    open_now = serializers.BooleanField(required=False, default=False,
                                        help_text="Open the task (notify assignees) right after creating it.")
    from_message = serializers.IntegerField(required=False, allow_null=True,
                                            help_text="Chat message id: links the message to the new task.")


class TaskUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    category = serializers.IntegerField(required=False, allow_null=True)
    priority = PlainChoiceField(choices=PRIORITIES, required=False)
    start_date = serializers.DateField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    requires_review = serializers.BooleanField(required=False)
    reviewer = serializers.IntegerField(required=False, allow_null=True)
    tags = serializers.ListField(child=serializers.CharField(max_length=60), required=False)
    recurrence_type = serializers.ChoiceField(choices=["none", "daily", "weekly", "monthly", "custom"],
                                              required=False)
    recurrence_rule = serializers.CharField(max_length=100, required=False, allow_blank=True)


class TaskReviewSerializer(serializers.Serializer):
    approve = serializers.BooleanField()
    notes = serializers.CharField(required=False, allow_blank=True, default="",
                                  help_text="Required (5+ characters) when approve is false.")


class TaskLogTimeSerializer(serializers.Serializer):
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TaskCommentRequestSerializer(CommentRequestSerializer):
    pinned = serializers.BooleanField(required=False, default=False)


class TaskAssigneesAddSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)


class TaskAssigneesResultSerializer(serializers.Serializer):
    added = serializers.ListField(child=serializers.IntegerField())
    skipped = serializers.ListField(child=serializers.CharField())
    task = TaskDetailSerializer()


class TaskPeopleQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True)
    category = serializers.IntegerField(required=False)
    task = serializers.IntegerField(required=False)


class WorkPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    initials = serializers.CharField()
    email = serializers.CharField(allow_blank=True)
    role = serializers.CharField(allow_blank=True)
    department = serializers.CharField(allow_blank=True)
    used = serializers.IntegerField(help_text="Open tasks now.")
    max = serializers.IntegerField()
    remaining = serializers.IntegerField()
    at_capacity = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------
TICKET_STATES = ["new", "assigned", "in_progress", "awaiting_response", "escalated", "resolved", "closed",
                 "reopened", "cancelled", "awaiting_director"]


class TicketListQuerySerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["my", "assigned", "all"], required=False, default="my")
    state = serializers.ChoiceField(choices=["open", "closed", *TICKET_STATES], required=False)
    priority = PlainChoiceField(choices=PRIORITIES, required=False)
    category = serializers.CharField(required=False, help_text="Category code.")
    q = serializers.CharField(required=False, allow_blank=True)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class TicketCategorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    icon = serializers.CharField(allow_blank=True)
    handling_department = RefSerializer(allow_null=True)
    minimum_priority = serializers.CharField()
    priorities = serializers.ListField(child=serializers.CharField(), help_text="Allowed, lowest first.")


class TicketListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    subject = serializers.CharField()
    state = serializers.CharField()
    state_display = serializers.CharField()
    priority = serializers.CharField()
    priority_display = serializers.CharField()
    category = CodeRefSerializer(allow_null=True)
    raised_by = UserRefSerializer()
    assigned_to = UserRefSerializer(allow_null=True)
    handling_department = RefSerializer(allow_null=True)
    resolution_due_at = serializers.DateTimeField(allow_null=True)
    sla_overdue = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class TicketAssetSerializer(serializers.Serializer):
    tag = serializers.CharField()
    name = serializers.CharField()
    can_open = serializers.BooleanField(help_text="May this user open assets/<tag>/?")


class TicketCommentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    author = UserRefSerializer()
    body = serializers.CharField()
    is_internal = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class TicketActionsSerializer(serializers.Serializer):
    is_raiser = serializers.BooleanField()
    is_assignee = serializers.BooleanField()
    can_assign = serializers.BooleanField()
    can_start = serializers.BooleanField()
    can_await = serializers.BooleanField()
    can_resolve = serializers.BooleanField()
    can_signoff = serializers.BooleanField()
    can_close = serializers.BooleanField()
    can_reopen = serializers.BooleanField()
    can_cancel = serializers.BooleanField()
    can_comment = serializers.BooleanField()
    can_internal = serializers.BooleanField()
    can_attach = serializers.BooleanField()
    can_route = serializers.BooleanField()
    can_accept_division = serializers.BooleanField()
    can_request_funds = serializers.BooleanField()
    can_rate = serializers.BooleanField()


class TicketDivisionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    state = serializers.CharField(allow_blank=True)


class TicketFundsSerializer(serializers.Serializer):
    state = serializers.CharField()
    amount = serializers.CharField(allow_null=True)
    request = serializers.CharField(allow_null=True)
    note = serializers.CharField(allow_blank=True)


class TicketRouteSerializer(serializers.Serializer):
    division = serializers.IntegerField()
    note = serializers.CharField(required=False, allow_blank=True, default="")


class TicketFundsRequestSerializer(serializers.Serializer):
    amount = serializers.CharField()
    purpose = serializers.CharField()


class TicketRateSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class TicketDetailSerializer(TicketListItemSerializer):
    body = serializers.CharField()
    current_location = serializers.CharField(allow_blank=True)
    asset = TicketAssetSerializer(allow_null=True)
    resolution_notes = serializers.CharField(allow_blank=True)
    collection_location = serializers.CharField(allow_blank=True)
    collection_timeframe = serializers.CharField(allow_blank=True)
    response_due_at = serializers.DateTimeField(allow_null=True)
    first_response_at = serializers.DateTimeField(allow_null=True)
    resolved_at = serializers.DateTimeField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    escalation_level = serializers.IntegerField()
    reopen_count = serializers.IntegerField()
    satisfaction_rating = serializers.IntegerField(allow_null=True)
    satisfaction_comment = serializers.CharField(allow_blank=True)
    director_signoff_required = serializers.BooleanField()
    comments = TicketCommentSerializer(many=True)
    attachments = WorkAttachmentSerializer(many=True)
    activity = WorkActivitySerializer(many=True)
    actions = TicketActionsSerializer()
    division = TicketDivisionSerializer(allow_null=True)
    funds = TicketFundsSerializer(allow_null=True)
    report_pdf_url = serializers.CharField()


class TicketCreateSerializer(serializers.Serializer):
    """JSON, or multipart/form-data when sending ``photo``."""
    category = serializers.CharField(help_text="Category code from tickets/categories/.")
    subject = serializers.CharField(max_length=255, allow_blank=True)
    body = serializers.CharField(allow_blank=True)
    priority = PlainChoiceField(choices=PRIORITIES, required=False,
                                       help_text="Defaults to the category's minimum priority.")
    current_location = serializers.CharField(max_length=200, required=False, allow_blank=True)
    asset_tag = serializers.CharField(max_length=60, required=False, allow_blank=True)
    photo = serializers.FileField(required=False, help_text="Optional photo or file (attachment rules apply).")


class TicketAssignSerializer(serializers.Serializer):
    assignee = serializers.IntegerField(help_text="User id from tickets/<id>/assignees/.")
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class TicketResolveSerializer(serializers.Serializer):
    resolution_notes = serializers.CharField(allow_blank=True)
    collection_location = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    collection_timeframe = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class TicketSignoffSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class TicketCloseSerializer(serializers.Serializer):
    rating = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class TicketPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    initials = serializers.CharField()
    email = serializers.CharField(allow_blank=True)


# ---------------------------------------------------------------------------
# Requests (workflow)
# ---------------------------------------------------------------------------
REQUEST_STATUSES = ["draft", "awaiting_approval", "awaiting_receiving", "returned", "in_progress",
                    "awaiting_fulfillment", "ready_for_handover", "completed", "rejected", "cancelled"]


class RequestListQuerySerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["mine", "awaiting", "assigned"], required=False, default="mine")
    status = PlainChoiceField(choices=["all", "active", *REQUEST_STATUSES], required=False, default="all")
    type = serializers.CharField(required=False, help_text="Request type code.")
    q = serializers.CharField(required=False, allow_blank=True)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class FieldOptionSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class FormFieldSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    type = serializers.ChoiceField(choices=["text", "textarea", "number", "date", "select", "checkbox",
                                            "asset_picker", "user_picker"])
    required = serializers.BooleanField()
    help = serializers.CharField(allow_blank=True)
    max_length = serializers.IntegerField(allow_null=True)
    options = FieldOptionSerializer(many=True, allow_null=True,
                                    help_text="select / asset_picker / user_picker choices (type detail only).")


class RequestTypeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    icon = serializers.CharField(allow_blank=True)
    category = CodeRefSerializer(allow_null=True)
    handling_department = RefSerializer(allow_null=True)
    is_leave = serializers.BooleanField(help_text="annual-leave: use leave/ for a friendlier flow.")
    fields = FormFieldSerializer(many=True)


class RequestCreateSerializer(serializers.Serializer):
    request_type = serializers.CharField(help_text="Type code.")
    subject = serializers.CharField(max_length=255, allow_blank=True)
    body = serializers.CharField(allow_blank=True)
    priority = PlainChoiceField(choices=["low", "normal", "high", "urgent"], required=False,
                                       default="normal")
    fields = serializers.DictField(required=False, default=dict,
                                   help_text="Values of the type's form fields, by key. Dates YYYY-MM-DD, "
                                             "checkbox true/false, pickers the option value.")


class RequestResubmitSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=255, allow_blank=True)
    body = serializers.CharField(allow_blank=True)
    comment = serializers.CharField(required=False, allow_blank=True, default="")
    fields = serializers.DictField(required=False, default=dict)


class RequestListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    subject = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField()
    priority = serializers.CharField()
    request_type = CodeRefSerializer()
    requester = UserRefSerializer()
    receiving_department = RefSerializer(allow_null=True)
    assigned_to = UserRefSerializer(allow_null=True)
    current_step = serializers.CharField(allow_blank=True, help_text="Name of the step it is waiting at.")
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class StepSerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.ChoiceField(choices=["approval", "assignment", "fulfillment", "closure"])
    state = serializers.ChoiceField(choices=["done", "current", "upcoming"])


class WorkCurrentStepSerializer(serializers.Serializer):
    name = serializers.CharField()
    kind = serializers.CharField()
    instructions = serializers.CharField(allow_blank=True)
    actor = UserRefSerializer(allow_null=True)
    is_me = serializers.BooleanField()


class ApprovalSerializer(serializers.Serializer):
    step = serializers.CharField(allow_blank=True)
    actor = UserRefSerializer(allow_null=True)
    actor_role = serializers.CharField(allow_blank=True)
    decision = serializers.ChoiceField(choices=["approve", "return", "reject"])
    decision_display = serializers.CharField()
    comment = serializers.CharField(allow_blank=True)
    decided_at = serializers.DateTimeField()


class FormValueSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    value = serializers.JSONField(allow_null=True)


class RequestCommentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    author = UserRefSerializer()
    body = serializers.CharField()
    is_internal = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class LeaveYearSerializer(serializers.Serializer):
    year = serializers.IntegerField()
    requested_days = serializers.CharField()
    remaining = serializers.CharField()
    is_set = serializers.BooleanField()
    over_balance_days = serializers.CharField()
    is_over_balance = serializers.BooleanField()


class LeaveSummarySerializer(serializers.Serializer):
    leave_type = serializers.CharField(required=False, help_text="v2.1: leave type code.")
    leave_type_name = serializers.CharField(required=False)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    requested_days = serializers.CharField()
    has_over_balance = serializers.BooleanField()
    years = LeaveYearSerializer(many=True)


class RequestActionsSerializer(serializers.Serializer):
    can_approve = serializers.BooleanField()
    can_return = serializers.BooleanField(help_text="Return to the previous step.")
    can_return_to_requester = serializers.BooleanField()
    can_reject = serializers.BooleanField()
    can_assign = serializers.BooleanField()
    can_mark_fulfilled = serializers.BooleanField()
    can_confirm_handover = serializers.BooleanField()
    can_cancel = serializers.BooleanField()
    can_resubmit = serializers.BooleanField()
    can_comment = serializers.BooleanField()
    can_comment_internal = serializers.BooleanField()
    can_attach = serializers.BooleanField()


class RequestDetailSerializer(RequestListItemSerializer):
    body = serializers.CharField()
    completed_at = serializers.DateTimeField(allow_null=True)
    closed_at = serializers.DateTimeField(allow_null=True)
    revision_count = serializers.IntegerField()
    returned_by = UserRefSerializer(allow_null=True)
    returned_at = serializers.DateTimeField(allow_null=True)
    form_data = FormValueSerializer(many=True)
    step = WorkCurrentStepSerializer(allow_null=True)
    chain = StepSerializer(many=True)
    approvals = ApprovalSerializer(many=True)
    comments = RequestCommentSerializer(many=True)
    attachments = WorkAttachmentSerializer(many=True)
    activity = WorkActivitySerializer(many=True)
    leave = LeaveSummarySerializer(allow_null=True)
    has_record_pdf = serializers.BooleanField()
    actions = RequestActionsSerializer()


class CommentOnlySerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class RequiredCommentSerializer(serializers.Serializer):
    comment = serializers.CharField(allow_blank=True)


class RequestAssignSerializer(serializers.Serializer):
    assignee = serializers.IntegerField()
    comment = serializers.CharField(required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------
class LeaveBalanceSerializer(serializers.Serializer):
    leave_type = serializers.CharField()
    name = serializers.CharField()
    year = serializers.IntegerField()
    entitlement = serializers.CharField(help_text="Working days, decimal string.")
    used = serializers.CharField()
    pending = serializers.CharField()
    remaining = serializers.CharField()
    is_set = serializers.BooleanField(help_text="False when no entitlement is configured for you.")
    entitlement_source = serializers.ChoiceField(choices=["override", "role", "type"], allow_null=True)


class LeaveApplicationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    request_id = serializers.IntegerField()
    reference = serializers.CharField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    working_days = serializers.IntegerField()
    leave_type = serializers.CharField(help_text="Leave type code (v2.1), e.g. annual, maternity.")
    leave_type_name = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField()
    current_step = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    can_cancel = serializers.BooleanField()


class LeaveOverviewSerializer(serializers.Serializer):
    staff_linked = serializers.BooleanField(help_text="False: no staff record, applying is refused.")
    can_apply = serializers.BooleanField()
    balances = LeaveBalanceSerializer(many=True)
    current = LeaveApplicationSerializer(allow_null=True)
    upcoming = LeaveApplicationSerializer(allow_null=True)
    resumption_date = serializers.DateField(allow_null=True)
    days_until_upcoming = serializers.IntegerField(allow_null=True)


class LeaveYearQuerySerializer(serializers.Serializer):
    year = serializers.IntegerField(required=False, min_value=1900, max_value=9999)


class LeaveListQuerySerializer(serializers.Serializer):
    status = PlainChoiceField(choices=["all", "pending", "approved", "closed"], required=False,
                                     default="all")
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class LeaveApplySerializer(serializers.Serializer):
    leave_type = serializers.CharField(required=False, allow_blank=True, max_length=40,
                                       help_text="Leave type code (default annual). See Settings -> Leave types.")
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, help_text="Dates-mode types.")
    days = serializers.IntegerField(required=False, min_value=1, max_value=366,
                                    help_text="Days-mode types: the end date is computed (weekends and holidays "
                                              "skipped for working-day types).")

    def validate(self, attrs):
        if not attrs.get("end_date") and not attrs.get("days"):
            raise serializers.ValidationError({"end_date": ["Give end_date, or days for a days-mode type."]})
        return attrs
    subject = serializers.CharField(max_length=255, required=False, allow_blank=True)
    body = serializers.CharField(required=False, allow_blank=True)
    priority = PlainChoiceField(choices=["low", "normal", "high", "urgent"], required=False,
                                       default="normal")


class LeavePreviewQuerySerializer(serializers.Serializer):
    leave_type = serializers.CharField(required=False, allow_blank=True, max_length=40)
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False)
    days = serializers.IntegerField(required=False, min_value=1, max_value=366)

    def validate(self, attrs):
        if not attrs.get("end_date") and not attrs.get("days"):
            raise serializers.ValidationError({"end_date": ["Give end_date, or days for a days-mode type."]})
        return attrs


class LeavePreviewSerializer(serializers.Serializer):
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    working_days = serializers.IntegerField()
    resumption_date = serializers.DateField()
    remaining_before = serializers.CharField(allow_null=True)
    remaining_after = serializers.CharField(allow_null=True)
    is_over_balance = serializers.BooleanField()
    leave_type = serializers.CharField()
    leave_type_name = serializers.CharField()


class LeaveApprovalSerializer(serializers.Serializer):
    request_id = serializers.IntegerField()
    reference = serializers.CharField()
    requester = UserRefSerializer()
    department = RefSerializer(allow_null=True)
    start_date = serializers.DateField(allow_null=True)
    end_date = serializers.DateField(allow_null=True)
    requested_days = serializers.CharField(allow_null=True)
    has_over_balance = serializers.BooleanField()
    step = serializers.CharField()
    waiting_since = serializers.DateTimeField()


class HolidaySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    kind = serializers.CharField()
    date_start = serializers.DateField()
    date_end = serializers.DateField()
    is_recurring = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
