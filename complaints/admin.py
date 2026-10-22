"""Django admin for complaints (Phase 7).

Complaints move only through the complaint engine (complaints.services) and
their flow, so the records are read-only here. Layers, routing rules,
escalation rules and knowledge articles are configured in Settings →
Complaints; the admin lists them for support and export.
"""
from django.contrib import admin

from core.admin_base import (
    ChoicesDropdownFilter, RangeDateTimeFilter, RelatedDropdownFilter, StatusFilter, TabularInline,
    UnscopedReadOnlyModelAdmin, badge,
)

from .models import (
    Complaint, ComplaintAttachment, ComplaintLayer, ComplaintMessage, ComplaintStage, Escalation, EscalationRule,
    KnowledgeArticle, RoutingRule,
)


class StageInline(TabularInline):
    model = ComplaintStage
    extra = 0
    can_delete = False
    fields = ("stage_label", "assigned_department", "assigned_to", "entered_at", "due_at", "breached_at", "left_at")
    readonly_fields = fields
    ordering = ("entered_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Complaint)
class ComplaintAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("reference", "subject", "layer", "priority_badge", "status_badge", "current_department",
                    "current_assignee", "created_at")
    list_filter = (StatusFilter, ("priority", ChoicesDropdownFilter), ("layer", RelatedDropdownFilter),
                   ("raiser_type", ChoicesDropdownFilter), ("created_at", RangeDateTimeFilter))
    search_fields = ("reference", "subject", "body", "raised_by__email")
    date_hierarchy = "created_at"
    inlines = [StageInline]
    status_badge = badge("status", "Status")
    priority_badge = badge("priority", "Priority", {"low": "neutral", "normal": "info", "high": "warning",
                                                     "critical": "danger"})

    def get_queryset(self, request):
        return Complaint.unscoped.select_related("layer", "current_department", "current_assignee")


@admin.register(ComplaintStage)
class ComplaintStageAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("complaint", "stage_label", "assigned_department", "assigned_to", "entered_at", "due_at",
                    "breached_at", "left_at")
    list_filter = (("assigned_department", RelatedDropdownFilter), ("entered_at", RangeDateTimeFilter))
    search_fields = ("complaint__reference", "stage_label")


@admin.register(ComplaintLayer)
class ComplaintLayerAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("name", "code", "parent", "default_priority", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "description")


@admin.register(KnowledgeArticle)
class KnowledgeArticleAdmin(UnscopedReadOnlyModelAdmin):
    list_display = ("title", "layer", "audience", "is_published", "helpful", "not_helpful", "updated_at")
    list_filter = ("is_published", ("audience", ChoicesDropdownFilter))
    search_fields = ("title", "summary", "keywords")


for _model, _cols in (
        (RoutingRule, ("layer", "stage_code", "target_kind", "target_department", "order", "is_active")),
        (EscalationRule, ("flow", "stage_code", "level", "after_breach_minutes", "target_kind", "is_active")),
        (Escalation, ("complaint_stage", "level", "escalated_to", "at", "acknowledged_at")),
        (ComplaintMessage, ("complaint", "author", "visibility", "is_system", "created_at")),
        (ComplaintAttachment, ("complaint", "original_name", "uploaded_by", "scan_status", "created_at")),
):
    admin.site.register(_model, type(f"{_model.__name__}Admin", (UnscopedReadOnlyModelAdmin,),
                                     {"list_display": _cols}))
