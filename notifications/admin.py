from django.contrib import admin
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme
from core.admin_base import ChoicesDropdownFilter, RangeDateFilter, RangeDateTimeFilter, RelatedDropdownFilter, badge
from django.utils.html import format_html, format_html_join
from django.utils.html import strip_tags

from .models import EmailLog, EmailQuotaTracker, Notification


class ReadOnly(ModelAdmin):
    warn_unsaved_form = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailLog)
class EmailLogAdmin(ReadOnly):
    list_display = ("created_at", "to", "subject", "status_badge", "provider", "category", "attempts")
    list_filter = (("status", ChoicesDropdownFilter), "provider", "category", ("created_at", RangeDateTimeFilter))
    status_badge = badge("status", "Status", {"queued": "warning", "sent": "success", "failed": "danger",
                                              "skipped": "neutral"})
    search_fields = ("recipient_text", "subject", "user__email", "user__first_name", "user__last_name")
    date_hierarchy = "created_at"
    fields = ("created_at", "scheduled_for", "sent_at", "status", "provider", "category", "subject",
              "recipient_text", "user", "attempts", "last_error", "trail", "message")

    @admin.display(description="To")
    def to(self, obj):
        return obj.recipient_text

    @admin.display(description="What happened (each provider tried)")
    def trail(self, obj):
        if not obj.attempt_trail:
            return "-"
        return format_html_join(
            "", "<div>#{} {} - {}{}</div>",
            ((t.get("attempt", ""), t["provider"], "OK" if t["ok"] else "FAILED",
              f": {t['error']}" if t.get("error") else "") for t in obj.attempt_trail))

    @admin.display(description="Message (text)")
    def message(self, obj):
        if obj.category in EmailLog.sensitive_categories():
            return "(not stored - this email type contains credentials or reset links)"
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", strip_tags(obj.body_html).strip())


@admin.register(EmailQuotaTracker)
class EmailQuotaTrackerAdmin(ReadOnly):
    list_display = ("date", "provider", "sent_count")
    list_filter = ("provider",)


@admin.register(Notification)
class NotificationAdmin(ReadOnly):
    list_display = ("created_at", "recipient", "category", "level_badge", "title", "is_read")
    list_filter = ("is_read", ("level", ChoicesDropdownFilter), "category", ("created_at", RangeDateTimeFilter))
    list_select_related = ("recipient",)
    level_badge = badge("level", "Level", {"info": "info", "success": "success", "warning": "warning",
                                           "error": "danger", "danger": "danger"})
    actions = ["mark_read", "mark_unread"]
    search_fields = ("title", "body", "recipient__email", "recipient__first_name", "recipient__last_name")
    date_hierarchy = "created_at"
    fields = ("created_at", "recipient", "actor", "category", "level",
              "title", "body", "url", "is_read", "read_at", "metadata")

    # Notifications are read-only records, but support staff may clear a
    # backlog for someone. Only these two flags change, through update().
    def _may_mark(self, request):
        return request.user.is_superuser or request.user.has_perm("notifications.change_notification")

    @admin.action(description="Mark selected as read")
    def mark_read(self, request, queryset):
        from django.contrib import messages
        from django.utils import timezone
        if not self._may_mark(request):
            self.message_user(request, "You don't have permission to change notifications.", messages.ERROR)
            return
        n = queryset.filter(is_read=False).update(is_read=True, read_at=timezone.now())
        self.message_user(request, f"Marked {n} notification(s) as read.", messages.SUCCESS)

    @admin.action(description="Mark selected as unread")
    def mark_unread(self, request, queryset):
        from django.contrib import messages
        if not self._may_mark(request):
            self.message_user(request, "You don't have permission to change notifications.", messages.ERROR)
            return
        n = queryset.filter(is_read=True).update(is_read=False, read_at=None)
        self.message_user(request, f"Marked {n} notification(s) as unread.", messages.SUCCESS)


# Phase 9: per-user notification preferences and quiet hours
from .models import NotificationPreference  # noqa: E402


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(ModelAdmin):
    list_display = ("user", "email_digest", "quiet_hours_enabled", "quiet_from", "quiet_to", "timezone", "updated_at")
    list_filter = ("email_digest", "quiet_hours_enabled")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    raw_id_fields = ("user",)
