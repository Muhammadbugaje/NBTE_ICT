"""Tasks admin — read-focused. Actions live in services layer."""
from django.contrib import admin
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme
from core.admin_base import ChoicesDropdownFilter, RangeDateFilter, RangeDateTimeFilter, RelatedDropdownFilter, badge
from django import forms
from django.db import models
from django.utils.html import format_html

from .models import (
    Task, TaskActivity, TaskAssignee, TaskAttachment,
    TaskCategory, TaskComment, TaskDependency, TaskTimeEntry,
)


@admin.register(TaskCategory)
class TaskCategoryAdmin(ModelAdmin):
    list_display = (
        "name", "code", "handling_department", "default_priority",
        "default_due_days", "requires_review", "is_reusable",
        "min_rank_to_assign", "is_active", "sort_order",
    )
    list_filter = (
        "is_active", "requires_review", "is_reusable", "handling_department",
    )
    search_fields = ("code", "name", "description")
    ordering = ("sort_order", "name")
    prepopulated_fields = {"code": ("name",)}


class TaskSubtaskInline(TabularInline):
    model = Task
    fk_name = "parent"
    extra = 0
    fields = ("reference", "title", "state", "priority", "due_date")
    readonly_fields = ("reference", "title", "state", "priority", "due_date")
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class TaskAssigneeInline(TabularInline):
    model = TaskAssignee
    extra = 0
    fields = (
        "user", "state", "assigned_by",
        "assigned_at", "accepted_at", "started_at", "completed_at",
    )
    readonly_fields = ("assigned_at", "accepted_at", "started_at", "completed_at")


class TaskCommentInline(TabularInline):
    model = TaskComment
    extra = 0
    fields = ("author", "body", "is_internal", "is_pinned", "created_at")
    readonly_fields = ("author", "created_at")


class TaskAttachmentInline(TabularInline):
    model = TaskAttachment
    extra = 0
    fields = ("uploaded_by", "file", "original_name", "description", "created_at", "link")
    readonly_fields = ("uploaded_by", "original_name", "created_at", "link")
    # Restricted storage has no public URL, so the stock widget (which links
    # to file.url) cannot render; downloads go through `link` instead (H1).
    formfield_overrides = {models.FileField: {"widget": forms.FileInput}}

    @admin.display(description="Download")
    def link(self, obj):
        if obj.pk and obj.file:
            return format_html('<a href="{}">{}</a>', obj.download_url(), obj.original_name or "download")
        return "—"


class TaskActivityInline(TabularInline):
    model = TaskActivity
    extra = 0
    can_delete = False
    fields = ("kind", "actor", "message", "created_at")
    readonly_fields = ("kind", "actor", "message", "created_at")
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


class TaskDependencyInline(TabularInline):
    model = TaskDependency
    fk_name = "task"
    extra = 0
    autocomplete_fields = ("depends_on",)
    fields = ("depends_on", "note")


class TaskTimeEntryInline(TabularInline):
    model = TaskTimeEntry
    extra = 0
    fields = ("user", "started_at", "ended_at", "duration_seconds", "note")
    readonly_fields = ("started_at", "ended_at", "duration_seconds")


@admin.register(Task)
class TaskAdmin(ModelAdmin):
    list_display = (
        "reference", "title", "state_badge", "priority_badge",
        "created_by", "due_date", "owner_department",
    )
    list_filter = (
        ("state", ChoicesDropdownFilter), ("priority", ChoicesDropdownFilter),
        ("owner_department", RelatedDropdownFilter), ("category", RelatedDropdownFilter),
        ("due_date", RangeDateFilter), "requires_review", "is_recurring", "recurrence_type",
    )
    list_select_related = ("created_by", "owner_department")
    state_badge = badge("state", "State")
    priority_badge = badge("priority", "Priority", {'low': 'neutral', 'normal': 'info', 'high': 'warning', 'urgent': 'danger', 'critical': 'danger'})
    search_fields = ("reference", "title", "description")
    readonly_fields = (
        "reference", "created_at", "updated_at",
        "completed_at", "cancelled_at", "reviewed_at",
        "last_reminder_at", "recurrence_next_at",
    )
    fieldsets = (
        ("Identity", {"fields": ("reference", "title", "description", "category")}),
        ("Ownership", {"fields": ("created_by", "owner_department", "parent")}),
        ("Classification", {"fields": ("priority", "state", "tags")}),
        ("Timing", {
            "fields": (
                "start_date", "due_date", "completed_at", "cancelled_at",
                "estimated_hours",
            ),
        }),
        ("Review", {
            "fields": (
                "requires_review", "reviewer", "reviewed_by",
                "reviewed_at", "review_notes",
            ),
        }),
        ("Reuse & Recurrence", {
            "fields": (
                "cloned_from", "is_recurring", "recurrence_type",
                "recurrence_rule", "recurrence_next_at",
            ),
        }),
        ("Audit", {"fields": ("created_at", "updated_at", "last_reminder_at")}),
    )
    inlines = [
        TaskSubtaskInline, TaskAssigneeInline, TaskDependencyInline,
        TaskCommentInline, TaskAttachmentInline, TaskTimeEntryInline,
        TaskActivityInline,
    ]
    date_hierarchy = "created_at"
    raw_id_fields = (
        "created_by", "reviewer", "reviewed_by", "parent", "cloned_from",
    )


@admin.register(TaskAssignee)
class TaskAssigneeAdmin(ModelAdmin):
    list_display = ("task", "user", "state_badge", "assigned_at", "completed_at")
    list_filter = (("state", ChoicesDropdownFilter), ("assigned_at", RangeDateTimeFilter))
    state_badge = badge("state", "State")
    search_fields = ("task__reference", "user__email")


@admin.register(TaskActivity)
class TaskActivityAdmin(ModelAdmin):
    list_display = ("task", "kind", "actor", "created_at")
    list_filter = ("kind",)
    search_fields = ("task__reference", "message")
    ordering = ("-created_at",)


@admin.register(TaskComment)
class TaskCommentAdmin(ModelAdmin):
    list_display = ("task", "author", "is_internal", "is_pinned", "created_at")
    list_filter = ("is_internal", "is_pinned")
    search_fields = ("body", "task__reference")


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(ModelAdmin):
    list_display = ("task", "original_name", "uploaded_by", "created_at")
    search_fields = ("original_name", "task__reference")
    readonly_fields = ("link",)
    # Restricted storage has no public URL, so the stock widget (which links
    # to file.url) cannot render; downloads go through `link` instead (H1).
    formfield_overrides = {models.FileField: {"widget": forms.FileInput}}

    @admin.display(description="Download")
    def link(self, obj):
        if obj.pk and obj.file:
            return format_html('<a href="{}">{}</a>', obj.download_url(), obj.original_name or "download")
        return "—"


@admin.register(TaskTimeEntry)
class TaskTimeEntryAdmin(ModelAdmin):
    list_display = ("task", "user", "started_at", "ended_at", "hours")
    list_filter = ("user",)
    search_fields = ("task__reference", "user__email")


@admin.register(TaskDependency)
class TaskDependencyAdmin(ModelAdmin):
    list_display = ("task", "depends_on", "created_at")
    search_fields = ("task__reference", "depends_on__reference")