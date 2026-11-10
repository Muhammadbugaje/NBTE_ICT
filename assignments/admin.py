from django.contrib import admin

from .models import AccessEvent, AccessGrant


@admin.register(AccessGrant)
class AccessGrantAdmin(admin.ModelAdmin):
    list_display = ("pk", "grantee", "scope", "object_ct", "object_id", "granted_at", "expires_at", "revoked_at",
                    "revoke_reason")
    list_filter = ("scope", "revoke_reason")
    search_fields = ("grantee__email", "object_id", "reason")
    raw_id_fields = ("grantee", "granted_by", "revoked_by", "institution")
    readonly_fields = [f.name for f in AccessGrant._meta.fields]

    def has_add_permission(self, request):  # created only by assignments.grants
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccessEvent)
class AccessEventAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "action", "object_ct", "object_id", "via_grant")
    list_filter = ("action",)
    readonly_fields = [f.name for f in AccessEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
