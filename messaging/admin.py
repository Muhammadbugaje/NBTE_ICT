from django.contrib import admin

from .models import Message, Thread, ThreadParticipant


class ThreadParticipantInline(admin.TabularInline):
    model = ThreadParticipant
    extra = 0
    autocomplete_fields = ("user",)
    readonly_fields = ("joined_at", "last_read_at")


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("sender", "body", "sent_at", "is_system")
    readonly_fields = ("sent_at",)
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "display_title", "thread_type", "created_by", "last_message_at")
    list_filter = ("thread_type",)
    search_fields = ("subject", "participants__user__email", "messages__body")
    readonly_fields = ("created_at", "updated_at", "last_message_at")
    inlines = [ThreadParticipantInline, MessageInline]

    @admin.display(description="Title")
    def display_title(self, obj):
        return str(obj)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "thread", "sender", "sent_at", "is_system", "short_body")
    list_filter = ("is_system",)
    search_fields = ("body", "sender__email")
    readonly_fields = ("thread", "sender", "body", "sent_at", "is_system")

    @admin.display(description="Body")
    def short_body(self, obj):
        return obj.body[:60] + ("…" if len(obj.body) > 60 else "")

    def has_add_permission(self, request):
        return False