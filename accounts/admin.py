from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db import transaction

from .forms import AdminUserChangeForm, AdminUserCreationForm
from .models import Role, User, UserPermissionOverride
from .services import generate_temporary_password, send_credentials_email


class OverrideInline(admin.TabularInline):
    model = UserPermissionOverride
    fk_name = "user"
    extra = 0
    autocomplete_fields = ("permission",)
    readonly_fields = ("granted_by",)


class EmailLogInline(admin.TabularInline):
    """Read-only history of emails sent to this user."""
    from notifications.models import EmailLog as _EmailLog
    model = _EmailLog
    fk_name = "user"
    extra = 0
    can_delete = False
    show_change_link = True
    fields = readonly_fields = ("created_at", "subject", "status", "provider", "category")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = AdminUserChangeForm
    add_form = AdminUserCreationForm
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "department", "is_active", "must_change_password")
    list_filter = ("role", "department", "is_active", "is_staff", "must_change_password")
    search_fields = ("email", "first_name", "last_name")
    inlines = [OverrideInline, EmailLogInline]

    def get_inlines(self, request, obj=None):
        # Email history only makes sense for an existing user.
        return [OverrideInline, EmailLogInline] if obj else [OverrideInline]

    actions = ["reset_and_email_password"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("first_name", "last_name")}),
        ("Access", {"fields": ("role", "department", "is_active", "is_staff", "is_superuser", "must_change_password")}),
        ("2FA (enforced in Phase 5)", {"fields": ("two_factor_enabled",)}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "role", "department",
                           "is_staff", "temporary_password")}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        pw = getattr(obj, "_temporary_password", None)
        if not change and pw:
            # Capture pk + email in the closure so the callback doesn't hold a
            # reference to a possibly-stale form instance.
            uid = obj.pk
            email = obj.email
            transaction.on_commit(lambda: _email_credentials(uid, email, pw))
            messages.success(request, f"Credentials emailed to {obj.email}.")

    def save_formset(self, request, form, formset, change):
        """Persist inline overrides, setting granted_by on new rows, and apply
        deletions. Calling formset.save(commit=False) skips deletions by design,
        so we handle them explicitly here (the previous version didn't, which
        meant an admin clicking 'delete' on an override row had no effect)."""
        instances = formset.save(commit=False)
        for deleted in formset.deleted_objects:
            deleted.delete()
        for inst in instances:
            if isinstance(inst, UserPermissionOverride) and not inst.granted_by_id:
                inst.granted_by = request.user
            inst.save()
        formset.save_m2m()

    @admin.action(description="Reset to a new temporary password and email it")
    def reset_and_email_password(self, request, queryset):
        for user in queryset:
            pw = generate_temporary_password()
            user.set_password(pw)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password"])
            send_credentials_email(user, pw)
        self.message_user(request, f"Reset and emailed {queryset.count()} user(s).")


def _email_credentials(user_id, email, temporary_password):
    """Deferred credentials email - re-fetches the user so the callback isn't
    holding a stale form instance."""
    from .models import User as _User
    from .services import send_credentials_email as _send

    user = _User.objects.filter(pk=user_id).first()
    if user is not None:
        _send(user, temporary_password)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "requires_2fa", "is_system")
    search_fields = ("name", "code")
    # Stage 3: `groups` uses Django's own filter_horizontal widget, same as
    # `permissions`, so the admin and the groups UI edit the same data.
    filter_horizontal = ("permissions", "groups")
    prepopulated_fields = {"code": ("name",)}

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_system) and super().has_delete_permission(request, obj)


from django.contrib.auth.models import Permission  # noqa: E402


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    """Needed for autocomplete in overrides; read-only (permissions come from models)."""
    search_fields = ("codename", "name", "content_type__app_label")
    list_display = ("__str__", "codename")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# ---------------------------------------------------------------------------
# Stage 3: Django's built-in Group admin, extended (not replaced)
# ---------------------------------------------------------------------------
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin  # noqa: E402
from django.contrib.auth.models import Group  # noqa: E402

from .models import GroupProfile  # noqa: E402


class GroupProfileInline(admin.StackedInline):
    model = GroupProfile
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("description",)


class RoleInline(admin.TabularInline):
    """Read-only list of roles carrying this group (edit on the Role page)."""
    model = Role.groups.through
    extra = 0
    can_delete = False
    verbose_name = "role carrying this group"
    verbose_name_plural = "roles carrying this group"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin):
    """Django's GroupAdmin keeps its permission picker (filter_horizontal);
    this only adds the description inline and the roles that carry it."""
    inlines = [GroupProfileInline, RoleInline]
    list_display = ("name", "permission_count", "member_count")

    @admin.display(description="Permissions")
    def permission_count(self, obj):
        return obj.permissions.count()

    @admin.display(description="Direct members")
    def member_count(self, obj):
        return obj.user_set.count()
