from django.contrib import admin

from .models import (
    Announcement, AnnouncementCategory, AnnouncementLink,
    Event, EventCategory, EventAttendee, Document, DocumentCategory, DocumentVersion,
    Reminder, ReminderRecipient, AwardCategory, Award, Holiday, GalleryCategory, GalleryAlbum, GalleryItem,
)


# ============================================================================
# ANNOUNCEMENTS
# ============================================================================
class AnnouncementLinkInline(admin.TabularInline):
    model = AnnouncementLink
    extra = 1
    fields = ("kind", "label", "url", "sort_order")


@admin.register(AnnouncementCategory)
class AnnouncementCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "visibility", "priority",
                    "is_pinned", "publish_at", "expires_at", "author")
    list_filter = ("visibility", "priority", "is_pinned", "is_draft",
                   "category", "department")
    search_fields = ("title", "summary", "body")
    date_hierarchy = "publish_at"
    readonly_fields = ("notified_at", "archived_at", "auto_archived", "image_original_name")
    inlines = [AnnouncementLinkInline]
    filter_horizontal = ("roles", "recipients")

    def save_model(self, request, obj, form, change):
        if not obj.author_id:
            obj.author = request.user
        super().save_model(request, obj, form, change)


# ============================================================================
# EVENTS
# ============================================================================
class EventAttendeeInline(admin.TabularInline):
    model = EventAttendee
    extra = 0
    fields = ("user", "rsvp", "responded_at", "note")


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "starts_at", "ends_at",
                    "location_name", "visibility", "is_cancelled", "is_draft")
    list_filter = ("visibility", "is_cancelled", "is_draft", "category", "department")
    search_fields = ("title", "summary", "description", "location_name")
    date_hierarchy = "starts_at"
    readonly_fields = ("notified_at", "reminder_sent_at", "image_original_name")
    inlines = [EventAttendeeInline]
    filter_horizontal = ("roles", "recipients")
    fieldsets = (
        (None, {"fields": ("title", "summary", "description", "image", "image_original_name")}),
        ("Classification", {"fields": ("category", "visibility", "department", "roles", "recipients")}),
        ("Timing", {"fields": ("starts_at", "ends_at", "all_day", "timezone_name", "reminder_lead_minutes")}),
        ("Location", {"fields": ("location_name", "location_address", "virtual_url")}),
        ("Organizer", {"fields": ("organizer",)}),
        ("Status", {"fields": ("is_cancelled", "cancellation_reason", "is_draft")}),
        ("Audit", {"classes": ("collapse",),
                   "fields": ("notified_at", "reminder_sent_at")}),
    )

    def save_model(self, request, obj, form, change):
        if not obj.organizer_id:
            obj.organizer = request.user
        super().save_model(request, obj, form, change)


# ============================================================================
# REMINDERS
# ============================================================================
class ReminderRecipientInline(admin.TabularInline):
    model = ReminderRecipient
    extra = 0
    readonly_fields = ("user", "delivered_at", "is_read", "read_at")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("title", "creator", "kind", "remind_at",
                    "is_fired", "is_dismissed", "fired_at")
    list_filter = ("kind", "is_fired", "is_dismissed", "department")
    search_fields = ("title", "body")
    date_hierarchy = "remind_at"
    filter_horizontal = ("roles", "recipients")
    inlines = [ReminderRecipientInline]
    readonly_fields = ("is_fired", "fired_at")


from .models import (
    Document, DocumentCategory, DocumentVersion, DocumentDownloadLog,
)


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    fields = ("version_number", "is_current", "file", "original_name",
              "uploaded_by", "change_note", "file_size")
    readonly_fields = ("version_number", "file_size", "original_name")
    can_delete = False  # NR-08: never delete versions


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "visibility", "source_type",
                    "status", "is_featured", "owner", "published_at", "download_count")
    list_filter = ("status", "visibility", "source_type", "is_featured",
                   "category", "department")
    search_fields = ("title", "summary", "description")
    date_hierarchy = "published_at"
    readonly_fields = ("published_at", "notified_at", "download_count",
                       "cover_image_original_name", "review_reminder_sent_at")
    inlines = [DocumentVersionInline]
    filter_horizontal = ("roles", "recipients")

    def save_model(self, request, obj, form, change):
        if not obj.owner_id:
            obj.owner = request.user
        super().save_model(request, obj, form, change)


@admin.register(DocumentDownloadLog)
class DocumentDownloadLogAdmin(admin.ModelAdmin):
    list_display = ("document", "user", "is_external", "ip_address", "downloaded_at")
    list_filter = ("is_external", "downloaded_at")
    search_fields = ("document__title", "user__email", "ip_address")
    date_hierarchy = "downloaded_at"
    readonly_fields = ("document", "version", "user", "ip_address",
                       "user_agent", "is_external", "downloaded_at")

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False


@admin.register(AwardCategory)
class AwardCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Award)
class AwardAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "kind", "category",
                    "awarded_on", "visibility", "is_featured")
    list_filter = ("kind", "visibility", "is_featured", "category")
    search_fields = ("title", "description",
                     "recipient__first_name", "recipient__last_name",
                     "recipient__employee_no")
    date_hierarchy = "awarded_on"
    autocomplete_fields = ("recipient",)
    fieldsets = (
        (None, {"fields": ("kind", "title", "description", "image",
                            "certificate", "category")}),
        ("Recipient", {"fields": ("recipient", "department", "awarded_by")}),
        ("Timing", {"fields": ("awarded_on", "years", "is_recurring")}),
        ("Visibility", {"fields": ("visibility", "roles", "recipients",
                                    "is_featured")}),
    )

@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "date_start", "date_end",
                    "department", "visibility", "is_active")
    list_filter = ("kind", "visibility", "is_active", "department")
    search_fields = ("name", "description")
    date_hierarchy = "date_start"

@admin.register(GalleryCategory)
class GalleryCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    prepopulated_fields = {"slug": ("name",)}


class GalleryItemInline(admin.TabularInline):
    model = GalleryItem
    extra = 0
    fields = ("file", "file_kind", "caption", "credit", "sort_order")
    ordering = ("sort_order", "id")


@admin.register(GalleryAlbum)
class GalleryAlbumAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "event_date", "visibility",
                    "is_featured", "is_published", "item_count")
    list_filter = ("visibility", "is_featured", "is_published", "category")
    search_fields = ("title", "description")
    date_hierarchy = "event_date"
    inlines = [GalleryItemInline]
    autocomplete_fields = ("owner",)

    def item_count(self, obj):
        return obj.items.count()
    item_count.short_description = "Items"

