from django.contrib import admin

from .models import ConfigEntry


@admin.register(ConfigEntry)
class ConfigEntryAdmin(admin.ModelAdmin):
    list_display = ("key", "scope", "value", "value_type", "group", "updated_at")
    list_filter = ("group", "value_type", "scope")
    search_fields = ("key", "description")
