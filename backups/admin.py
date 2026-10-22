from django.contrib import admin

from .models import BackupCopy, BackupDestination, BackupRun, RestoreDrill


@admin.register(BackupDestination)
class BackupDestinationAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "is_active", "credential_env_prefix", "last_success_at")
    list_filter = ("kind", "is_active")


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BackupRun)
class BackupRunAdmin(ReadOnlyAdmin):
    list_display = ("label", "status", "trigger", "started_at", "finished_at", "size_bytes")
    list_filter = ("status", "trigger")


@admin.register(BackupCopy)
class BackupCopyAdmin(ReadOnlyAdmin):
    list_display = ("run", "destination", "status", "written_at", "deleted_at")


@admin.register(RestoreDrill)
class RestoreDrillAdmin(ReadOnlyAdmin):
    list_display = ("backup_run", "result", "started_at", "finished_at", "automatic")
