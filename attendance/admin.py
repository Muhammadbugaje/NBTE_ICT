"""Attendance admin."""
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.utils import timezone

from .models import AttendanceLocation, AttendanceRecord


@admin.register(AttendanceLocation)
class AttendanceLocationAdmin(admin.ModelAdmin):
    list_display = (
        "name", "code", "latitude", "longitude",
        "radius_meters", "gps_offset_meters",
        "work_start_time", "grace_minutes",
        "department_list", "is_active",
    )
    list_filter = ("is_active", "departments")
    search_fields = ("code", "name", "description")
    ordering = ("name",)
    prepopulated_fields = {"code": ("name",)}
    filter_horizontal = ("departments",)
    fieldsets = (
        ("Identity", {"fields": ("code", "name", "description")}),
        ("Geofence", {
            "fields": (
                "latitude", "longitude",
                "radius_meters", "gps_offset_meters",
            ),
            "description": (
                "Copy latitude and longitude from Google Maps by right-clicking "
                "the exact building on the map and choosing the coordinates."
            ),
        }),
        ("Working hours", {
            "fields": ("work_start_time", "work_end_time", "grace_minutes"),
        }),
        ("Scope", {
            "fields": ("departments",),
            "description": (
                "Which departments use this location. Leave blank to apply "
                "to everyone (e.g. a shared HQ reception)."
            ),
        }),
        ("Status", {"fields": ("is_active", "notes")}),
    )

    @admin.display(description="Departments")
    def department_list(self, obj):
        names = list(obj.departments.values_list("name", flat=True))
        return ", ".join(names) if names else "Any"


@admin.action(description="Mark selected as Present")
def bulk_mark_present(modeladmin, request, queryset):
    n = queryset.update(
        status=AttendanceRecord.PRESENT,
        source=AttendanceRecord.SOURCE_MANUAL,
        recorded_by=request.user,
    )
    messages.success(request, f"{n} record(s) marked Present.")


@admin.action(description="Mark selected as Late")
def bulk_mark_late(modeladmin, request, queryset):
    n = queryset.update(
        status=AttendanceRecord.LATE,
        source=AttendanceRecord.SOURCE_MANUAL,
        recorded_by=request.user,
    )
    messages.success(request, f"{n} record(s) marked Late.")


@admin.action(description="Mark selected as Absent")
def bulk_mark_absent(modeladmin, request, queryset):
    n = queryset.update(
        status=AttendanceRecord.ABSENT,
        source=AttendanceRecord.SOURCE_MANUAL,
        recorded_by=request.user,
    )
    messages.success(request, f"{n} record(s) marked Absent.")


@admin.action(description="Mark selected as Excused")
def bulk_mark_excused(modeladmin, request, queryset):
    n = queryset.update(
        status=AttendanceRecord.EXCUSED,
        source=AttendanceRecord.SOURCE_MANUAL,
        recorded_by=request.user,
    )
    messages.success(request, f"{n} record(s) marked Excused.")


@admin.action(description="Mark selected as Holiday")
def bulk_mark_holiday(modeladmin, request, queryset):
    n = queryset.update(
        status=AttendanceRecord.HOLIDAY,
        source=AttendanceRecord.SOURCE_MANUAL,
        recorded_by=request.user,
    )
    messages.success(request, f"{n} record(s) marked Holiday.")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "date", "user_link", "status_badge", "source",
        "check_in_at", "distance_display", "location",
    )
    list_filter = ("status", "source", "date", "location")
    search_fields = (
        "user__email", "user__first_name", "user__last_name",
        "notes",
    )
    date_hierarchy = "date"
    ordering = ("-date",)
    readonly_fields = ("created_at", "updated_at")
    list_per_page = 50
    actions = [
        bulk_mark_present, bulk_mark_late, bulk_mark_absent,
        bulk_mark_excused, bulk_mark_holiday,
    ]
    fieldsets = (
        ("Identity", {"fields": ("user", "date")}),
        ("Result", {"fields": ("status", "notes")}),
        ("Check-in", {
            "fields": (
                "check_in_at", "latitude", "longitude",
                "distance_meters", "location",
            ),
        }),
        ("Provenance", {"fields": ("source", "recorded_by")}),
        ("Audit", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "location", "recorded_by")

    @admin.display(description="User", ordering="user__first_name")
    def user_link(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html
        url = reverse("admin:accounts_user_change", args=[obj.user_id])
        return format_html('<a href="{}">{}</a>', url, obj.user.get_full_name() or obj.user.email)

    @admin.display(description="Status")
    def status_badge(self, obj):
        from django.utils.html import format_html
        colors = {
            "present": ("#065f46", "#d1fae5"),
            "late":    ("#92400e", "#fef3c7"),
            "absent":  ("#991b1b", "#fee2e2"),
            "excused": ("#3730a3", "#e0e7ff"),
            "holiday": ("#5b21b6", "#ede9fe"),
        }
        fg, bg = colors.get(obj.status, ("#334155", "#f1f5f9"))
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            'background:{};color:{};font-size:11px;font-weight:600;">{}</span>',
            bg, fg, obj.get_status_display(),
        )

    @admin.display(description="Distance", ordering="distance_meters")
    def distance_display(self, obj):
        if obj.distance_meters is None:
            return "—"
        return f"{obj.distance_meters} m"