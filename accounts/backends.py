from django.contrib.auth.backends import ModelBackend


class RolePermissionBackend(ModelBackend):
    """authenticate() is inherited (works with USERNAME_FIELD=email).

    Permissions come from accounts.permissions_service.effective_permissions:
      role baseline + role groups + direct groups + direct permissions
      + per-user grants, minus per-user revokes (a revoke always wins).

    Stage 3 change: role groups, direct groups and `user_permissions` used to
    be ignored here; they now add permissions. Nothing is taken away from
    anyone — the only difference for an existing user is that a Django group
    or direct permission assigned to them (none were, at the time of the
    change) now counts. See GROUPS_PERMISSIONS.md.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        """Email, or (nominal-roll release) a staff FILE NO linked to an account."""
        if username and "@" not in username:
            from staff.models import Staff
            staff = (Staff.objects.filter(employee_no__iexact=username.strip(), user__isnull=False)
                     .select_related("user").first())
            if staff is None:
                # Same work as a real check, so a missing file number isn't faster.
                return super().authenticate(request, username="unknown@invalid", password=password,
                                            **kwargs)
            username = staff.user.email
        return super().authenticate(request, username=username, password=password, **kwargs)

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        if not hasattr(user_obj, "_rbac_perm_cache"):
            user_obj._rbac_perm_cache = self._compute(user_obj)
        return user_obj._rbac_perm_cache

    def get_user_permissions(self, user_obj, obj=None):
        # Unchanged contract: callers of get_user_permissions() in this
        # project expect the full effective set.
        return self.get_all_permissions(user_obj, obj)

    def get_group_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        from .permissions_service import permission_sources
        if user_obj.is_superuser:
            return self.get_all_permissions(user_obj, obj)
        s = permission_sources(user_obj)
        return (s["role_groups"] | s["user_groups"]) - s["revokes"]

    @staticmethod
    def _compute(user):
        from .permissions_service import effective_permissions
        return effective_permissions(user)
