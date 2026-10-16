from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ApprovalChain,
    ApprovalStep,
    Request,
    RequestActivity,
    RequestApproval,
    RequestAttachment,
    RequestCategory,
    RequestComment,
    RequestType,
)


# ---------------------------------------------------------------------------
# Categories & chains
# ---------------------------------------------------------------------------
@admin.register(RequestCategory)
class RequestCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


class ApprovalStepInline(admin.TabularInline):
    model = ApprovalStep
    extra = 1
    fields = ("position", "name", "kind", "approver_kind", "approver_role",
              "approver_user", "scope", "is_optional")
    ordering = ("position",)


@admin.register(ApprovalChain)
class ApprovalChainAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "step_count", "is_active")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}
    inlines = [ApprovalStepInline]

    @admin.display(description="Steps")
    def step_count(self, obj):
        return obj.steps.count()


@admin.register(ApprovalStep)
class ApprovalStepAdmin(admin.ModelAdmin):
    list_display = ("chain", "position", "name", "kind", "approver_kind", "scope", "is_optional")
    list_filter = ("chain", "kind", "approver_kind", "is_optional")
    search_fields = ("name", "chain__name", "chain__code")
    ordering = ("chain", "position")


# ---------------------------------------------------------------------------
# Request types
# ---------------------------------------------------------------------------
@admin.register(RequestType)
class RequestTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "handling_department",
                    "approval_chain", "is_cross_department", "is_active")
    list_filter = ("category", "handling_department", "is_cross_department", "is_active")
    search_fields = ("name", "code", "description")
    prepopulated_fields = {"code": ("name",)}
    autocomplete_fields = ("handling_department", "approval_chain", "category")
    fieldsets = (
        ("Identity", {"fields": ("name", "code", "category", "description",
                                 "icon", "sort_order", "is_active")}),
        ("Routing", {"fields": ("handling_department", "is_cross_department")}),
        ("Approval", {"fields": ("approval_chain",)}),
        ("Extra sign-offs", {"fields": ("requires_es_approval",
                                        "requires_finance_approval",
                                        "requires_audit_approval",
                                        "requires_hr_approval",
                                        "requires_records_filing")}),
    )


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class RequestApprovalInline(admin.TabularInline):
    model = RequestApproval
    extra = 0
    can_delete = False
    fields = ("step", "actor", "actor_role", "decision", "comment", "decided_at")
    readonly_fields = fields
    ordering = ("decided_at",)

    def has_add_permission(self, request, obj=None):
        return False


class RequestCommentInline(admin.TabularInline):
    model = RequestComment
    extra = 0
    can_delete = False
    fields = ("author", "body", "is_internal", "created_at")
    readonly_fields = fields
    ordering = ("created_at",)

    def has_add_permission(self, request, obj=None):
        return False


class RequestAttachmentInline(admin.TabularInline):
    model = RequestAttachment
    extra = 0
    can_delete = False
    fields = ("original_name", "description", "uploaded_by", "file_link")
    readonly_fields = ("file_link",)

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="File")
    def file_link(self, obj):
        if obj.pk and obj.file:
            return format_html('<a href="{}">{}</a>',
                               obj.download_url(), obj.original_name)
        return "—"


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("reference", "subject_short", "requester", "request_type",
                    "status", "priority", "created_at")
    list_filter = ("status", "priority", "request_type__category",
                   "receiving_department", "created_at")
    search_fields = ("reference", "subject", "requester__email",
                     "requester__first_name", "requester__last_name")
    readonly_fields = ("reference", "created_at", "updated_at",
                       "completed_at", "closed_at", "record_generated_at")
    autocomplete_fields = ("requester", "request_type", "receiving_department", "assigned_to")
    inlines = [RequestApprovalInline, RequestCommentInline, RequestAttachmentInline]
    date_hierarchy = "created_at"
    fieldsets = (
        ("Identity", {"fields": ("reference", "request_type", "requester", "priority")}),
        ("Content", {"fields": ("subject", "body")}),
        ("Routing", {"fields": ("receiving_department", "assigned_to")}),
        ("Status", {"fields": ("status", "current_step", "current_step_started_at")}),
        ("Dates", {"fields": ("created_at", "updated_at", "completed_at", "closed_at")}),
        ("Record", {"fields": ("record_pdf", "record_original_name", "record_generated_at")}),
        ("Metadata", {"fields": ("metadata",)}),
    )

    @admin.display(description="Subject")
    def subject_short(self, obj):
        return obj.subject[:60]


@admin.register(RequestActivity)
class RequestActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "request", "kind", "actor", "message_short")
    list_filter = ("kind", "created_at")
    search_fields = ("request__reference", "message", "actor__email")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Message")
    def message_short(self, obj):
        return (obj.message or "")[:80]


@admin.register(RequestApproval)
class RequestApprovalAdmin(admin.ModelAdmin):
    list_display = ("decided_at", "request", "step", "actor", "decision")
    list_filter = ("decision", "decided_at")
    search_fields = ("request__reference", "actor__email", "comment")
    date_hierarchy = "decided_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(RequestComment)
class RequestCommentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "request", "author", "is_internal", "body_short")
    list_filter = ("is_internal", "created_at")
    search_fields = ("request__reference", "author__email", "body")

    @admin.display(description="Comment")
    def body_short(self, obj):
        return obj.body[:80]


@admin.register(RequestAttachment)
class RequestAttachmentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "request", "original_name", "uploaded_by")
    search_fields = ("request__reference", "original_name")