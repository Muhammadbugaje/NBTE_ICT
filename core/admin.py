from django.contrib import admin
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme
from core.admin_base import ChoicesDropdownFilter, RangeDateFilter, RangeDateTimeFilter, RelatedDropdownFilter, badge
from django.utils import timezone

from .models import AuditLogEntry, ContactMessage


@admin.register(AuditLogEntry)
class AuditLogEntryAdmin(ModelAdmin):
    list_display = ("timestamp", "actor_repr", "action", "model_label", "object_pk", "object_repr")
    list_filter = (("action", ChoicesDropdownFilter), "model_label", ("timestamp", RangeDateTimeFilter))
    date_hierarchy = "timestamp"
    list_select_related = ("actor",)
    search_fields = ("actor_repr", "object_repr", "object_pk", "model_label")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactMessage)
class ContactMessageAdmin(ModelAdmin):
    list_display = (
        "created_at", "name", "email", "subject_short",
        "handling_department", "assigned_to", "is_read", "is_handled",
    )
    list_filter = ("is_read", "is_handled", ("handling_department", RelatedDropdownFilter),
                   ("created_at", RangeDateTimeFilter))
    date_hierarchy = "created_at"
    actions = ["mark_read", "mark_handled"]

    @admin.action(description="Mark selected as read", permissions=["change"])
    def mark_read(self, request, queryset):
        n = queryset.filter(is_read=False).update(is_read=True)
        self.message_user(request, f"Marked {n} message(s) as read.")

    @admin.action(description="Mark selected as handled", permissions=["change"])
    def mark_handled(self, request, queryset):
        n = queryset.filter(is_handled=False).update(is_handled=True, is_read=True, handled_at=timezone.now(),
                                                    handled_by=request.user)
        self.message_user(request, f"Marked {n} message(s) as handled.")
    search_fields = ("name", "email", "subject", "message")
    readonly_fields = ("name", "email", "subject", "message", "ip_address", "user_agent", "created_at", "updated_at")
    fieldsets = (
        ("From", {"fields": ("name", "email", "subject")}),
        ("Message", {"fields": ("message",)}),
        ("Metadata", {"fields": ("ip_address", "user_agent", "created_at", "updated_at")}),
        ("Routing", {"fields": ("handling_department", "assigned_to")}),
        ("Handling", {"fields": ("is_read", "is_handled", "handled_by", "handled_at", "notes")}),
    )

    @admin.display(description="Subject")
    def subject_short(self, obj):
        return (obj.subject or "(no subject)")[:50]

    def save_model(self, request, obj, form, change):
        # Auto-mark handled when notes get saved by an admin.
        if obj.is_handled and not obj.handled_at:
            obj.handled_at = timezone.now()
            if not obj.handled_by_id:
                obj.handled_by = request.user
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return False  # messages come from the public form