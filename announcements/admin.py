from django.contrib import admin

from core.admin_base import ModelAdmin, ReadOnlyModelAdmin, TabularInline, badge

from .models import (
    Announcement,
    AnnouncementAck,
    AnnouncementAttachment,
    AnnouncementPoll,
    AnnouncementRead,
    PollOption,
    PollVote,
)


class AttachmentInline(TabularInline):
    model = AnnouncementAttachment
    extra = 0
    fields = ("file", "original_name", "is_deleted")
    readonly_fields = ("original_name",)


class PollOptionInline(TabularInline):
    model = PollOption
    extra = 0
    fields = ("position", "text")


@admin.register(Announcement)
class AnnouncementAdmin(ModelAdmin):
    list_display = ("title", "kind_badge", "audience", "department", "requires_ack", "pinned",
                    "published_at", "expires_at", "created_by")
    list_filter = ("kind", "audience", "requires_ack", "pinned")
    search_fields = ("title", "body", "created_by__email")
    autocomplete_fields = ("created_by",)
    filter_horizontal = ("roles",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")
    inlines = [AttachmentInline]

    kind_badge = badge("kind", "Kind", tones={"notice": "info", "action_needed": "warning",
                                              "urgent": "danger"})


@admin.register(AnnouncementPoll)
class AnnouncementPollAdmin(ModelAdmin):
    list_display = ("question", "announcement", "allow_multiple", "closes_at")
    search_fields = ("question", "announcement__title")
    raw_id_fields = ("announcement",)
    inlines = [PollOptionInline]


@admin.register(AnnouncementAck)
class AnnouncementAckAdmin(ReadOnlyModelAdmin):
    list_display = ("announcement", "user", "acknowledged_at")
    search_fields = ("announcement__title", "user__email")


@admin.register(AnnouncementRead)
class AnnouncementReadAdmin(ReadOnlyModelAdmin):
    list_display = ("announcement", "user", "read_at")
    search_fields = ("announcement__title", "user__email")


@admin.register(PollVote)
class PollVoteAdmin(ReadOnlyModelAdmin):
    list_display = ("poll", "option", "user", "voted_at")
    search_fields = ("poll__question", "user__email")
