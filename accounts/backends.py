from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission


class RolePermissionBackend(ModelBackend):
    """authenticate() is inherited (works with USERNAME_FIELD=email).
    Permissions = role baseline + per-user grants - per-user revokes.
    Django groups / user_permissions are deliberately ignored."""

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        if not hasattr(user_obj, "_rbac_perm_cache"):
            user_obj._rbac_perm_cache = self._compute(user_obj)
        return user_obj._rbac_perm_cache

    def get_user_permissions(self, user_obj, obj=None):
        return self.get_all_permissions(user_obj, obj)

    def get_group_permissions(self, user_obj, obj=None):
        return set()

    @staticmethod
    def _compute(user):
        def label(qs):
            return {f"{ct}.{code}" for ct, code in qs.values_list("content_type__app_label", "codename")}

        if user.is_superuser:
            return label(Permission.objects.all())
        perms = set()
        if user.role_id:
            perms |= label(Permission.objects.filter(roles=user.role_id))
        overrides = user.permission_overrides.all()
        perms |= label(Permission.objects.filter(pk__in=overrides.filter(effect="grant").values("permission")))
        perms -= label(Permission.objects.filter(pk__in=overrides.filter(effect="revoke").values("permission")))
        return perms
