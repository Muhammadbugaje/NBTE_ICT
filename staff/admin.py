from django.contrib import admin
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme

from django.utils.html import format_html

from accounts.scoping import scope_to_department

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone

from .models import Department, Office, Staff, StaffAttachment, StaffCategory, StaffState, StaffStatusHistory, ExecutivePosition, ExecutiveTenure
from .models import Designation, ImportSession, Promotion, SavedQuery, StaffTransfer, StateOfOrigin


class LookupAdmin(ModelAdmin):
    list_display = ("name", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("name",)


@admin.register(Department)
class DepartmentAdmin(LookupAdmin):
    list_display = ("name", "code", "head", "is_active", "sort_order")
    search_fields = ("name", "code")
    raw_id_fields = ("head",)
admin.site.register(StaffCategory, LookupAdmin)
admin.site.register(StaffState, LookupAdmin)


@admin.register(Office)
class OfficeAdmin(LookupAdmin):
    list_display = ("name", "head", "is_active", "sort_order")
    autocomplete_fields = ("head",)


class StatusHistoryInline(TabularInline):
    model = StaffStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in StaffStatusHistory._meta.fields if f.name != "staff"]

    def has_add_permission(self, request, obj=None):
        return False


class AttachmentInline(TabularInline):
    model = StaffAttachment
    extra = 0
    fields = ("file", "description", "original_name", "download")
    readonly_fields = ("original_name", "download")

    @admin.display(description="Download")
    def download(self, obj):
        # Via the permission-checked view - never a raw file URL.
        return format_html('<a href="{}">{}</a>', obj.download_url(), obj.original_name) if obj.pk else "-"


@admin.register(Staff)
class StaffAdmin(ModelAdmin):
    list_display = ("employee_no", "last_name", "first_name", "department", "designation",
                    "grade_level", "state_of_origin", "state")
    list_filter = ("department", "designation", "state_of_origin", "gender", "state", "office", "category")
    search_fields = ("employee_no", "first_name", "last_name", "email",
                     "designation__name", "department__name")
    autocomplete_fields = ("reports_to", "user")
    list_select_related = ("department", "designation", "state_of_origin", "state")
    readonly_fields = ("photo_original_name", "created_at", "updated_at")
    inlines = [StatusHistoryInline, AttachmentInline]
    actions = ["export_nominal_roll", "promote_selected"]
    fieldsets = (
        ("Identity", {"fields": ("employee_no", "user", "first_name", "last_name", "gender",
                                 "email", "phone", "photo", "photo_original_name")}),
        ("Position", {"fields": ("department", "office", "subsection", "designation", "grade_level",
                                 "office_location", "category", "reports_to", "duties")}),
        ("Nominal roll", {"fields": ("state_of_origin", "lga", "date_of_birth",
                                     "date_first_appointment", "date_joined_board",
                                     "date_present_appointment", "date_confirmation",
                                     "qualifications")}),
        ("State", {"fields": ("state", "state_start", "state_end")}),
        ("Personal", {"fields": ("contact_preference", "emergency_contact_name", "emergency_contact_phone",
                                 "date_joined_service")}),
        ("Legacy", {"classes": ("collapse",),
                    "description": "Kept for backward compatibility — use Date of birth.",
                    "fields": ("birth_month", "birth_day")}),
        ("Record", {"fields": ("is_deleted", "deleted_at", "created_at", "updated_at")}),
    )

    @admin.action(description="Export selected as nominal roll (XLSX)")
    def export_nominal_roll(self, request, queryset):
        if not request.user.has_perm("staff.export_nominal_roll"):
            raise PermissionDenied
        from core import audit

        from .exporters import export_nominal_roll
        from .lifecycle_views import _xlsx_response
        content, count = export_nominal_roll(queryset.filter(is_deleted=False))
        audit.log("export_nominal_roll", request.user, actor=request.user,
                  changes={"rows": count, "layout": "admin selection"}, note="Admin export")
        return _xlsx_response(content, f"NBTE_Nominal_Roll_selection_{timezone.localdate():%Y%m%d}.xlsx")

    @admin.action(description="Promote selected staff")
    def promote_selected(self, request, queryset):
        if not request.user.has_perm("staff.promote_staff"):
            raise PermissionDenied
        designations = set(queryset.values_list("designation_id", flat=True))
        url = reverse("staff:promotions")
        if len(designations) == 1 and None not in designations:
            url += f"?designation={designations.pop()}"
        messages.info(request, "Promotions are made on the Promotions page so you can preview the "
                               "before-and-after list. The list there is filtered to the selected "
                               "staff's designation where possible.")
        return HttpResponseRedirect(url)

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
class ExecutivePositionAdmin(ModelAdmin):
    list_display = ("name", "scope", "department", "order", "is_active")
    list_filter = ("scope", "is_active", "department")
    list_editable = ("order", "is_active")
    prepopulated_fields = {"code": ("name",)}
    search_fields = ("name", "description")


@admin.register(ExecutiveTenure)
class ExecutiveTenureAdmin(ModelAdmin):
    list_display = ("person_name", "position", "started_on", "ended_on",
                    "is_acting", "is_current", "is_public", "is_featured")
    list_filter = ("is_acting", "is_public", "is_featured", "position")
    search_fields = ("display_name", "staff__first_name", "staff__last_name",
                     "staff__employee_no", "position__name")
    date_hierarchy = "started_on"
    autocomplete_fields = ("staff", "position")
    fieldsets = (
        (None, {"fields": ("position", "staff", "display_name", "title", "is_placeholder",
                            "started_on", "ended_on",
                            "is_acting", "appointment_reference")}),
        ("Public profile", {"fields": ("photo", "photo_original_name",
                                        "qualifications", "bio")}),
        ("Public page", {"fields": ("is_public", "is_featured", "display_order")}),
    )





# ---------------------------------------------------------------------------
# Nominal roll release
# ---------------------------------------------------------------------------
@admin.register(Designation)
class DesignationAdmin(ModelAdmin):
    list_display = ("name", "rank", "contediss_min", "contediss_max", "next_designation",
                    "staff_count", "is_active", "sort_order")
    list_editable = ("rank", "is_active", "sort_order")
    list_filter = ("is_active", "rank")
    search_fields = ("name", "code", "aliases")
    prepopulated_fields = {"code": ("name",)}
    autocomplete_fields = ("next_designation",)
    readonly_fields = ("staff_count",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("next_designation").annotate(
            _staff_count=Count("staff", filter=Q(staff__is_deleted=False)))

    @admin.display(description="Staff", ordering="_staff_count")
    def staff_count(self, obj):
        if hasattr(obj, "_staff_count"):
            return obj._staff_count
        return obj.staff.filter(is_deleted=False).count() if obj.pk else 0


@admin.register(StateOfOrigin)
class StateOfOriginAdmin(LookupAdmin):
    list_display = ("name", "code", "zone", "travel_mode", "distance_from_hq_km", "is_active", "sort_order")
    list_filter = ("zone", "travel_mode", "is_active")
    search_fields = ("name", "code")


class ReadOnlyAdmin(ModelAdmin):
    """Rows are written by the app (promotions / imports) only."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Promotion)
class PromotionAdmin(ReadOnlyAdmin):
    list_display = ("staff", "from_designation", "to_designation", "effective_date", "approved_by",
                    "is_reversal")
    list_filter = ("effective_date", "to_designation")
    search_fields = ("staff__first_name", "staff__last_name", "staff__employee_no", "reason")
    date_hierarchy = "effective_date"
    list_select_related = ("staff", "from_designation", "to_designation", "approved_by")

    @admin.display(boolean=True, description="Rollback")
    def is_reversal(self, obj):
        return obj.is_reversal


@admin.register(ImportSession)
class ImportSessionAdmin(ReadOnlyAdmin):
    list_display = ("source_filename", "created_at", "uploaded_by", "new_count", "updated_count",
                    "skipped_count", "error_count", "status_pill")
    list_filter = ("status",)
    search_fields = ("source_filename",)
    exclude = ("report_file", "credentials_file")
    readonly_fields = ("report",)

    @admin.display(description="Status", ordering="status")
    def status_pill(self, obj):
        symbol = {"ok": "✔", "partial": "⚠", "failed": "✖"}.get(obj.status, "•")
        return format_html('<strong data-status="{}">{} {}</strong>', obj.status, symbol,
                           obj.get_status_display())

    @admin.display(description="Report")
    def report(self, obj):
        if not obj.report_file:
            return "-"
        return format_html('<a href="{}">Download report</a>',
                           reverse("staff:nominal_roll_report", args=[obj.pk]))


@admin.register(SavedQuery)
class SavedQueryAdmin(ModelAdmin):
    list_display = ("name", "owner", "is_shared", "logic", "updated_at")
    list_filter = ("is_shared", "logic")
    search_fields = ("name", "description", "owner__email")


@admin.register(StaffTransfer)
class StaffTransferAdmin(ReadOnlyAdmin):
    list_display = ("staff", "from_department", "to_department", "from_office", "to_office",
                    "effective_date", "source", "recorded_by")
    list_filter = ("source", "to_department")
    search_fields = ("staff__first_name", "staff__last_name", "staff__employee_no", "reason")
    date_hierarchy = "effective_date"
    list_select_related = ("staff", "from_department", "to_department", "from_office", "to_office",
                           "recorded_by")
