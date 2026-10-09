from django.contrib import admin
from django.utils.html import format_html, format_html_join
from django.utils.html import strip_tags

from .models import EmailLog, EmailQuotaTracker, Notification


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EmailLog)
class EmailLogAdmin(ReadOnly):
    list_display = ("created_at", "to", "subject", "status", "provider", "category", "attempts")
    list_filter = ("status", "provider", "category", "created_at")
    search_fields = ("recipient_text", "subject", "user__email", "user__first_name", "user__last_name")
    date_hierarchy = None
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
    list_display = ("created_at", "recipient", "category", "level", "title", "is_read")
    list_filter = ("category", "level", "is_read", "created_at")
    search_fields = ("title", "body", "recipient__email", "recipient__first_name", "recipient__last_name")
    date_hierarchy = None
    fields = ("created_at", "recipient", "actor", "category", "level",
              "title", "body", "url", "is_read", "read_at", "metadata")