"""Django admin for the QA engines: flows, two-step confirmations,
references, notifications and the timeline.

All of these change only through Settings → QA (with a second officer where
the catalogue says so), so the admin shows them read-only for support,
search and export. Unscoped by design (Master Plan §4).
"""
from django.contrib import admin

from core.admin_base import (
    ChoicesDropdownFilter, RangeDateTimeFilter, RelatedDropdownFilter, TabularInline, UnscopedReadOnlyModelAdmin,
    badge,
)

from .models import (
    ActionPolicy, FlowDefinition, FlowEvent, FlowInstance, FlowStage, FlowTransition, NotificationRule,
    NotificationTemplate, OutboundEmail, PendingAction, QATaskLink, ReferenceCounter, ReferenceFormat, Suspension,
    TaskLinkDriftReport, TimelineEvent,
)


class FlowStageInline(TabularInline):
    model = FlowStage
    extra = 0
    can_delete = False
    fields = ("sort_order", "code", "label", "sop_stage_no", "kind", "actor_kind", "sla_hours", "creates_task")
    readonly_fields = fields
    ordering = ("sort_order",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(FlowDefinition)
class FlowDefinitionAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("code", "name", "version", "subject_model", "is_active", "published_at")
    list_filter = ("is_active",)
    search_fields = ("code", "name", "description")
    inlines = [FlowStageInline]


@admin.register(FlowTransition)
class FlowTransitionAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("flow", "from_stage", "action_code", "to_stage", "requires_confirmation", "requires_remark")
    list_filter = (("flow", RelatedDropdownFilter), "requires_confirmation")
    search_fields = ("action_code", "label")


@admin.register(PendingAction)
class PendingActionAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("reference", "policy", "target_label", "status_badge", "initiated_by", "decided_by",
                    "expires_at")
    list_filter = (("status", ChoicesDropdownFilter), ("policy", RelatedDropdownFilter),
                   ("initiated_at", RangeDateTimeFilter))
    search_fields = ("reference", "target_label", "reason")
    date_hierarchy = "created_at"
    status_badge = badge("status", "Status", {"applied": "success", "confirmed": "success"})


@admin.register(ActionPolicy)
class ActionPolicyAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("action_code", "label", "menu_group", "requires_second_officer", "expires_after_hours",
                    "is_enabled")
    list_filter = ("requires_second_officer", "is_enabled")
    search_fields = ("action_code", "label", "description")


@admin.register(TimelineEvent)
class TimelineEventAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("at", "institution", "event_code", "visibility", "actor", "public_text")
    list_filter = (("visibility", ChoicesDropdownFilter), ("at", RangeDateTimeFilter))
    search_fields = ("event_code", "public_text", "internal_text", "institution__code")
    date_hierarchy = "at"

    def get_queryset(self, request):
        return TimelineEvent.unscoped.select_related("institution", "actor").order_by("-at", "-id")


@admin.register(QATaskLink)
class QATaskLinkAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("task", "stage_code", "kind", "opened_at", "due_at", "closed_at", "close_reason")
    list_filter = (("kind", ChoicesDropdownFilter), ("due_at", RangeDateTimeFilter))
    search_fields = ("stage_code",)


for _model, _cols in (
        (FlowStage, ("flow", "sort_order", "code", "label", "kind", "actor_kind", "sla_hours")),
        (FlowInstance, ("flow_version", "content_type", "object_id", "current_stages", "started_at",
                        "completed_at", "outcome")),
        (FlowEvent, ("instance", "from_stage", "to_stage", "action_code", "actor", "at")),
        (ReferenceFormat, ("code", "name", "pattern", "reset_period", "is_active")),
        (ReferenceCounter, ("format", "period_key", "scope_key", "last_value")),
        (TaskLinkDriftReport, ("run_at", "checked", "opened", "closed", "dry_run")),
        (Suspension, ("content_type", "object_id", "reason", "placed_by", "placed_at", "lifted_at")),
        (NotificationTemplate, ("code", "channel", "subject", "is_active")),
        (NotificationRule, ("event_code", "flow", "stage", "recipient_kind", "template", "is_active")),
        (OutboundEmail, ("subject", "recipients", "category", "created_at")),
):
    admin.site.register(_model, type(f"{_model.__name__}Admin", (UnscopedReadOnlyModelAdmin,),
                                     {"list_display": _cols}))
