from django.contrib import admin
from django import forms
from django.db import models
from django.utils.html import format_html

from .models import (
    Asset,
    AssetAssignment,
    AssetAttachment,
    AssetCategory,
    AssetIssue,
)


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order", "is_active", "asset_count")
    list_editable = ("sort_order", "is_active")
    search_fields = ("name", "code", "description")
    prepopulated_fields = {"code": ("name",)}
    fieldsets = (
        ("Identity", {"fields": ("name", "code", "description", "icon",
                                 "sort_order", "is_active")}),
        ("Spec schema", {"fields": ("spec_fields",),
                         "description": "Optional structured spec fields for this category. "
                                        "Format: [{\"key\": \"ram_gb\", \"label\": \"RAM (GB)\", \"type\": \"number\"}, …]"}),
    )

    @admin.display(description="Assets")
    def asset_count(self, obj):
        return obj.assets.count()


class AssetAssignmentInline(admin.TabularInline):
    model = AssetAssignment
    extra = 0
    can_delete = False
    autocomplete_fields = ("assigned_to", "assigned_by", "returned_to")
    readonly_fields = ("assigned_at", "returned_at", "duration_days_display")
    fields = ("assigned_to", "assigned_by", "assigned_at", "returned_at",
              "condition_on_assign", "condition_on_return",
              "location_on_assign", "notes", "duration_days_display")
    ordering = ("-assigned_at",)

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Days held")
    def duration_days_display(self, obj):
        if not obj.pk:
            return "—"
        return obj.duration_days


class AssetAttachmentInline(admin.TabularInline):
    model = AssetAttachment
    extra = 0
    fields = ("file", "kind", "description", "original_name", "uploaded_by", "link")
    readonly_fields = ("original_name", "link")
    # Restricted storage has no public URL, so the stock widget (which links
    # to file.url) cannot render; downloads go through `link` instead (H1).
    formfield_overrides = {models.FileField: {"widget": forms.FileInput}}

    @admin.display(description="File")
    def link(self, obj):
        if obj.pk and obj.file:
            return format_html('<a href="{}">{}</a>',
                               obj.download_url(), obj.original_name or "download")
        return "—"


class AssetIssueInline(admin.TabularInline):
    model = AssetIssue
    extra = 0
    can_delete = False
    fields = ("summary", "reported_by", "reported_at", "resolved_at",
              "resolved_by", "resolution_notes", "request")
    readonly_fields = ("reported_at",)
    ordering = ("-reported_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("asset_tag", "display_name", "category", "state",
                    "current_custodian", "owner_department", "current_location")
    list_filter = ("state", "category", "owner_department")
    search_fields = ("asset_tag", "serial_number", "brand", "model",
                     "description", "current_location",
                     "current_custodian__email", "current_custodian__first_name",
                     "current_custodian__last_name")
    readonly_fields = ("asset_tag", "created_at", "updated_at",
                       "current_custodian", "qr_preview")
    autocomplete_fields = ("category", "owner_department", "current_custodian")
    inlines = [AssetAssignmentInline, AssetIssueInline, AssetAttachmentInline]

    fieldsets = (
        ("Identity", {"fields": ("asset_tag", "serial_number", "category")}),
        ("Description", {"fields": ("brand", "model", "color", "description", "spec")}),
        ("Ownership & location", {"fields": ("owner_department", "current_location",
                                             "current_custodian")}),
        ("Lifecycle", {"fields": ("state", "acquired_at", "cost", "warranty_until")}),
        ("Notes", {"fields": ("notes",)}),
        ("QR code", {"fields": ("qr_preview",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "is_deleted", "deleted_at")}),
    )

    @admin.display(description="Name")
    def display_name(self, obj):
        return obj.display_name

    @admin.display(description="QR code")
    def qr_preview(self, obj):
        if not obj.pk:
            return "—"
        svg = obj.qr_svg(box_size=4, border=1)
        if not svg:
            return "Install `qrcode` to render."
        return format_html(
            '<div style="background:#fff;padding:12px;border:1px solid #eee;'
            'border-radius:8px;max-width:180px;">{}</div>'
            '<p style="margin-top:8px;font-size:12px;color:#666;">'
            'Payload: <code>{}</code></p>',
            format_html(svg),
            obj.qr_payload(),
        )

    def get_queryset(self, request):
        return Asset.all_objects.select_related(
            "category", "owner_department", "current_custodian",
        )

    def save_model(self, request, obj, form, change):
        # Auto-generate the asset tag if it's missing on creation.
        if not change and not obj.asset_tag:
            from .services import create_asset
            # create_asset handles the tag; delegate the save to it.
            # But save_model gets a ModelForm-bound instance. Instead we
            # generate a tag here directly.
            obj.asset_tag = _generate_tag_on_save(obj)
        super().save_model(request, obj, form, change)


def _generate_tag_on_save(asset):
    """Simplified tag generator for admin saves (no transaction concerns,
    since admin saves are already inside a transaction)."""
    from .services import _unique_asset_tag
    return _unique_asset_tag(asset.category, asset.owner_department)


@admin.register(AssetAssignment)
class AssetAssignmentAdmin(admin.ModelAdmin):
    list_display = ("asset", "assigned_to", "assigned_by", "assigned_at",
                    "expected_return_at", "returned_at", "is_open_display")
    list_filter = ("assigned_at", "expected_return_at", "returned_at")
    search_fields = ("asset__asset_tag", "assigned_to__email",
                     "assigned_to__first_name", "assigned_to__last_name")
    autocomplete_fields = ("asset", "assigned_to", "assigned_by", "returned_to")
    date_hierarchy = "assigned_at"

    @admin.display(description="Status", boolean=True)
    def is_open_display(self, obj):
        return obj.is_open


@admin.register(AssetIssue)
class AssetIssueAdmin(admin.ModelAdmin):
    list_display = ("asset", "summary_short", "reported_by", "reported_at",
                    "resolved_at", "is_resolved_display")
    list_filter = ("reported_at", "resolved_at")
    search_fields = ("asset__asset_tag", "summary", "description")
    autocomplete_fields = ("asset", "reported_by", "resolved_by", "request")
    date_hierarchy = "reported_at"

    @admin.display(description="Summary")
    def summary_short(self, obj):
        return obj.summary[:60]

    @admin.display(description="Resolved", boolean=True)
    def is_resolved_display(self, obj):
        return obj.is_resolved


@admin.register(AssetAttachment)
class AssetAttachmentAdmin(admin.ModelAdmin):
    list_display = ("asset", "original_name", "kind", "uploaded_by", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("asset__asset_tag", "original_name", "description")