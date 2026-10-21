from django.contrib import admin

from .models import ConfigEntry


@admin.register(ConfigEntry)
class ConfigEntryAdmin(admin.ModelAdmin):
    list_display = ("key", "scope", "value", "value_type", "group", "dual_control", "updated_at")
    list_filter = ("group", "value_type", "scope", "dual_control")
    search_fields = ("key", "description")

    # QA (Master Plan §14): dual-control rows change only through a second
    # officer (Settings → QA), and locked rows never change. The admin shows
    # both read-only; ConfigEntry.save() enforces the lock regardless.
    def get_readonly_fields(self, request, obj=None):
        if obj is not None and (obj.is_locked or obj.dual_control):
            return [f.name for f in obj._meta.concrete_fields]
        return ("dual_control", "editable_by")

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_locked:
            return False
        return super().has_delete_permission(request, obj)
