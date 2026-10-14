from django.contrib import admin

from .models import LeaveApplication, LeaveEntitlement


@admin.register(LeaveEntitlement)
class LeaveEntitlementAdmin(admin.ModelAdmin):
    list_display = ("staff", "year", "days", "updated_by", "updated_at")
    list_filter = ("year", "staff__department")
    search_fields = ("staff__first_name", "staff__last_name", "staff__employee_no")
    autocomplete_fields = ("staff", "updated_by")


@admin.register(LeaveApplication)
class LeaveApplicationAdmin(admin.ModelAdmin):
    list_display = ("staff", "request", "start_date", "end_date")
    list_filter = ("start_date", "request__status")
    search_fields = ("staff__first_name", "staff__last_name", "request__reference")
    autocomplete_fields = ("request", "staff")
    readonly_fields = ("request", "staff", "start_date", "end_date")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
