"""Admin registration for the tickets app."""
from django.contrib import admin

from .models import (
    Ticket,
    TicketActivity,
    TicketAttachment,
    TicketCategory,
    TicketComment,
)


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name", "code", "handling_department",
        "minimum_priority", "is_active", "sort_order",
    )
    list_filter = ("is_active", "handling_department", "minimum_priority")
    search_fields = ("code", "name", "description")
    ordering = ("sort_order", "name")
    prepopulated_fields = {"code": ("name",)}


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0
    readonly_fields = ("author", "created_at", "updated_at")
    fields = ("author", "body", "is_internal", "created_at", "updated_at")


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0
    readonly_fields = ("uploaded_by", "original_name", "created_at")
    fields = ("uploaded_by", "file", "original_name", "description", "created_at")


class TicketActivityInline(admin.TabularInline):
    model = TicketActivity
    extra = 0
    can_delete = False
    readonly_fields = ("kind", "actor", "message", "metadata", "created_at")
    fields = ("kind", "actor", "message", "created_at")
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "subject", "state", "priority",
        "category", "assigned_to", "handling_department",
        "created_at",
    )
    list_filter = (
        "state", "priority", "category",
        "handling_department", "sla_response_breached",
        "sla_resolution_breached",
    )
    search_fields = ("reference", "subject", "body")
    readonly_fields = (
        "reference", "created_at", "updated_at",
        "first_response_at", "resolved_at", "closed_at",
        "escalated_at", "escalation_level",
        "sla_response_breached", "sla_resolution_breached",
        "reopen_count", "last_reopened_at",
    )
    fieldsets = (
        ("Identity", {
            "fields": ("reference", "subject", "body", "current_location"),
        }),
        ("Classification", {
            "fields": ("category", "priority", "asset"),
        }),
        ("Ownership", {
            "fields": ("raised_by", "assigned_to", "handling_department"),
        }),
        ("State", {
            "fields": ("state", "resolution_notes"),
        }),
        ("SLA", {
            "fields": (
                "response_due_at", "resolution_due_at",
                "first_response_at", "resolved_at", "closed_at",
                "sla_response_breached", "sla_resolution_breached",
            ),
        }),
        ("Escalation", {
            "fields": ("escalated_at", "escalation_level"),
        }),
        ("Reopen", {
            "fields": ("reopen_count", "last_reopened_at"),
        }),
        ("Satisfaction", {
            "fields": ("satisfaction_rating", "satisfaction_comment"),
        }),
        ("Audit", {
            "fields": ("created_at", "updated_at"),
        }),
    )
    inlines = [TicketActivityInline, TicketCommentInline, TicketAttachmentInline]
    date_hierarchy = "created_at"


@admin.register(TicketComment)
class TicketCommentAdmin(admin.ModelAdmin):
    list_display = ("ticket", "author", "is_internal", "created_at")
    list_filter = ("is_internal",)
    search_fields = ("body", "ticket__reference")


@admin.register(TicketAttachment)
class TicketAttachmentAdmin(admin.ModelAdmin):
    list_display = ("ticket", "original_name", "uploaded_by", "created_at")
    search_fields = ("original_name", "ticket__reference")


@admin.register(TicketActivity)
class TicketActivityAdmin(admin.ModelAdmin):
    list_display = ("ticket", "kind", "actor", "created_at")
    list_filter = ("kind",)
    search_fields = ("ticket__reference", "message")
    ordering = ("-created_at",)