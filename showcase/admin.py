"""Admin registration for showcase images."""
from django.contrib import admin
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme
from django.utils.html import format_html

from .forms import ShowcaseImageForm
from .models import ShowcaseImage


class PlacementFilter(admin.SimpleListFilter):
    title = "placement"
    parameter_name = "placement"

    def lookups(self, request, model_admin):
        return ShowcaseImage.PLACEMENT_CHOICES

    def queryset(self, request, queryset):
        if self.value() in ShowcaseImage.PLACEMENT_CODES:
            return queryset.filter(placements__regex=r"(^|,)%s(,|$)" % self.value())
        return queryset


@admin.register(ShowcaseImage)
class ShowcaseImageAdmin(ModelAdmin):
    form = ShowcaseImageForm
    list_display = (
        "preview", "__str__", "placements_display",
        "order", "is_active", "start_at", "end_at",
    )
    list_display_links = ("preview", "__str__")
    list_editable = ("order", "is_active")
    search_fields = ("title", "caption", "alt_text")
    readonly_fields = ("created_by", "created_at", "updated_at", "preview_large")
    fieldsets = (
        (None, {"fields": ("image", "preview_large", "alt_text", "title", "caption")}),
        ("Where and when", {"fields": ("placements", "order", "is_active", "start_at", "end_at")}),
        ("History", {"fields": ("created_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_list_filter(self, request):
        return (PlacementFilter, "is_active")

    @admin.display(description="Image")
    def preview(self, obj):
        if not obj.pk or not obj.image:
            return "—"
        return format_html(
            '<img src="{}" alt="" width="96" height="54" '
            'style="object-fit:cover;border-radius:6px">',
            obj.get_image_url(),
        )

    @admin.display(description="Preview")
    def preview_large(self, obj):
        if not obj.pk or not obj.image:
            return "Save to see a preview."
        return format_html(
            '<img src="{}" alt="" style="max-width:480px;border-radius:8px">',
            obj.get_image_url(),
        )

    @admin.display(description="Placements")
    def placements_display(self, obj):
        return obj.get_placements_display()

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)