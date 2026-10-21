"""Django admin for SOP-01 (system screens; unscoped by design, §4)."""
from django.contrib import admin

from .models import Application, ApplicationCycle, DepartmentalClearance


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("reference", "institution", "status", "round", "submitted_at")
    list_filter = ("status",)
    search_fields = ("reference", "institution__code", "institution__name")
    readonly_fields = ("status",)

    def get_queryset(self, request):
        return Application.unscoped.select_related("institution")


@admin.register(DepartmentalClearance)
class DepartmentalClearanceAdmin(admin.ModelAdmin):
    list_display = ("application", "department", "round", "status", "decided_by", "decided_at")
    list_filter = ("status", "department")

    def get_queryset(self, request):
        return DepartmentalClearance.unscoped.select_related("application", "department")


admin.site.register(ApplicationCycle, list_display=("name", "opens_at", "closes_at", "is_open"))
