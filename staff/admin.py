from django.contrib import admin

from django.utils.html import format_html

from accounts.scoping import scope_to_department

from .models import Department, Office, Staff, StaffAttachment, StaffCategory, StaffState, StaffStatusHistory, ExecutivePosition, ExecutiveTenure


class LookupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name",)


@admin.register(Department)
class DepartmentAdmin(LookupAdmin):
    list_display = ("name", "code", "is_active", "sort_order")
    search_fields = ("name", "code")
admin.site.register(StaffCategory, LookupAdmin)
admin.site.register(StaffState, LookupAdmin)


@admin.register(Office)
class OfficeAdmin(LookupAdmin):
    list_display = ("name", "head", "is_active", "sort_order")
    autocomplete_fields = ("head",)


class StatusHistoryInline(admin.TabularInline):
    model = StaffStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in StaffStatusHistory._meta.fields if f.name != "staff"]

    def has_add_permission(self, request, obj=None):
        return False


class AttachmentInline(admin.TabularInline):
    model = StaffAttachment
    extra = 0
    fields = ("file", "description", "original_name", "download")
    readonly_fields = ("original_name", "download")

    @admin.display(description="Download")
    def download(self, obj):
        # Via the permission-checked view - never a raw file URL.
        return format_html('<a href="{}">{}</a>', obj.download_url(), obj.original_name) if obj.pk else "-"


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("employee_no", "last_name", "first_name", "office", "designation", "state", "reports_to")
    list_filter = ("office", "department", "category", "state")
    search_fields = ("employee_no", "first_name", "last_name", "email", "designation")
    autocomplete_fields = ("reports_to", "user")
    readonly_fields = ("photo_original_name", "created_at", "updated_at")
    inlines = [StatusHistoryInline, AttachmentInline]
    fieldsets = (
        ("Identity", {"fields": ("employee_no", "user", "first_name", "last_name", "email", "phone", "photo", "photo_original_name")}),
        ("Position", {"fields": ("department", "office", "subsection", "designation", "grade_level",
                                 "office_location", "category", "reports_to", "duties")}),
        ("State", {"fields": ("state", "state_start", "state_end")}),
        ("Personal", {"fields": ("contact_preference", "emergency_contact_name", "emergency_contact_phone",
                                 "birth_month", "birth_day", "date_joined_service")}),
        ("Record", {"fields": ("is_deleted", "deleted_at", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        # Admins can see/restore soft-deleted rows, but only within their department
        # unless they hold accounts.access_all_departments.
        return scope_to_department(Staff.all_objects.all(), request.user)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        # State edits require the dedicated permission (doc 30 §3, NR-09).
        if not request.user.has_perm("staff.change_staff_state"):
            ro += ["state", "state_start", "state_end"]
        return ro

    def delete_model(self, request, obj):
        obj.delete()  # soft



@admin.register(ExecutivePosition)
class ExecutivePositionAdmin(admin.ModelAdmin):
    list_display = ("name", "scope", "department", "order", "is_active")
    list_filter = ("scope", "is_active", "department")
    list_editable = ("order", "is_active")
    prepopulated_fields = {"code": ("name",)}
    search_fields = ("name", "description")


@admin.register(ExecutiveTenure)
class ExecutiveTenureAdmin(admin.ModelAdmin):
    list_display = ("staff", "position", "started_on", "ended_on",
                    "is_acting", "is_current", "is_public", "is_featured")
    list_filter = ("is_acting", "is_public", "is_featured", "position")
    search_fields = ("staff__first_name", "staff__last_name",
                     "staff__employee_no", "position__name")
    date_hierarchy = "started_on"
    autocomplete_fields = ("staff", "position")
    fieldsets = (
        (None, {"fields": ("position", "staff",
                            "started_on", "ended_on",
                            "is_acting", "appointment_reference")}),
        ("Public profile", {"fields": ("photo", "photo_original_name",
                                        "qualifications", "bio")}),
        ("Public page", {"fields": ("is_public", "is_featured", "display_order")}),
    )



