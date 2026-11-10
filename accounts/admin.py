from django.contrib import admin, messages
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme
from core.admin_base import RangeDateTimeFilter, RelatedDropdownFilter, ReadOnlyModelAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db import transaction
from django.utils.html import format_html
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UnfoldReadOnlyPasswordHashWidget
from unfold.widgets import UnfoldAdminPasswordWidget

from .forms import AdminUserChangeForm, AdminUserCreationForm


class ThemedUserChangeForm(AdminUserChangeForm):
    """The project's change form with unfold's read-only password widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "password" in self.fields:
            self.fields["password"].widget = UnfoldReadOnlyPasswordHashWidget()
            self.fields["password"].widget.template_name = "auth/widgets/read_only_password_hash_new.html"


class ThemedUserCreationForm(AdminUserCreationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["temporary_password"].widget = UnfoldAdminPasswordWidget(
            attrs={"autocomplete": "new-password"})
from .models import Role, User, UserPermissionOverride
from .services import generate_temporary_password, send_credentials_email


class OverrideInline(TabularInline):
    model = UserPermissionOverride
    fk_name = "user"
    extra = 0
    autocomplete_fields = ("permission",)
    readonly_fields = ("granted_by",)


class EmailLogInline(TabularInline):
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
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    form = ThemedUserChangeForm
    add_form = ThemedUserCreationForm
    change_password_form = AdminPasswordChangeForm
    ordering = ("email",)
    list_display = ("person", "role", "department", "account_type", "active_badge", "must_change_password",
                    "last_login")
    list_filter = (("role", RelatedDropdownFilter), ("department", RelatedDropdownFilter), "user_type", "is_active",
                   "is_staff", "is_superuser", "must_change_password", ("last_login", RangeDateTimeFilter),
                   ("date_joined", RangeDateTimeFilter))
    search_fields = ("email", "first_name", "last_name")
    list_select_related = ("role", "department")
    date_hierarchy = "date_joined"

    @display(description="User", header=True, ordering="email")
    def person(self, obj):
        name = obj.get_full_name() or obj.email
        initials = ((obj.first_name[:1] + obj.last_name[:1]) or obj.email[:2]).upper()
        return [name, obj.email, initials]

    @display(description="Type", label={"staff": "info", "institution": "warning",
                                        "resource_person": "success"})
    def account_type(self, obj):
        value = getattr(obj, "user_type", "staff") or "staff"
        return value, value.replace("_", " ").capitalize()

    @display(description="Active", ordering="is_active", label={"Active": "success", "Off": "danger"})
    def active_badge(self, obj):
        return "Active" if obj.is_active else "Off"
    inlines = [OverrideInline, EmailLogInline]

    def get_inlines(self, request, obj=None):
        # Email history only makes sense for an existing user.
        return [OverrideInline, EmailLogInline] if obj else [OverrideInline]

    actions = ["reset_and_email_password", "activate_users", "deactivate_users"]
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
        # Phase B (B.7): a role change goes through org.assign_role, so the
        # grade, "only what you hold" and department rules apply here too.
        # A superuser's change is recorded as an audited override.
        role_changed = "role" in getattr(form, "changed_data", ())
        wanted_role = obj.role if role_changed else None
        if role_changed:
            obj.role = User.objects.get(pk=obj.pk).role if change else None
        super().save_model(request, obj, form, change)
        if role_changed:
            import org
            target = User.objects.get(pk=obj.pk)
            try:
                org.assign_role(request.user, target, wanted_role, "Changed in Django admin",
                                override=request.user.is_superuser, source="admin")
            except org.OrgRuleError as exc:
                messages.error(request, f"Role not changed: {exc}")
            obj.role = User.objects.get(pk=obj.pk).role
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

    @admin.action(description="Activate selected users", permissions=["change"])
    def activate_users(self, request, queryset):
        n = 0
        for user in queryset.filter(is_active=False):
            user.is_active = True
            user.save(update_fields=["is_active"])  # save() so audit signals fire
            n += 1
        self.message_user(request, f"Activated {n} user(s).", messages.SUCCESS)

    @admin.action(description="Deactivate selected users", permissions=["change"])
    def deactivate_users(self, request, queryset):
        # Never lock yourself out from here.
        n = 0
        for user in queryset.filter(is_active=True).exclude(pk=request.user.pk):
            user.is_active = False
            user.save(update_fields=["is_active"])
            n += 1
        skipped = queryset.filter(pk=request.user.pk, is_active=True).exists()
        msg = f"Deactivated {n} user(s)."
        if skipped:
            msg += " Your own account was left active."
        self.message_user(request, msg, messages.SUCCESS if n else messages.WARNING)


def _email_credentials(user_id, email, temporary_password):
    """Deferred credentials email - re-fetches the user so the callback isn't
    holding a stale form instance."""
    from .models import User as _User
    from .services import send_credentials_email as _send

    user = _User.objects.filter(pk=user_id).first()
    if user is not None:
        _send(user, temporary_password)


@admin.register(Role)
class RoleAdmin(ModelAdmin):
    list_display = ("name", "code", "rank", "user_count", "requires_2fa", "is_system")
    list_filter = ("requires_2fa", "is_system")
    search_fields = ("name", "code")

    @admin.display(description="Active users")
    def user_count(self, obj):
        return obj.users.filter(is_active=True).count()
    # Stage 3: `groups` uses Django's own filter_horizontal widget, same as
    # `permissions`, so the admin and the groups UI edit the same data.
    filter_horizontal = ("permissions", "groups")
    prepopulated_fields = {"code": ("name",)}

    def has_delete_permission(self, request, obj=None):
        return not (obj and obj.is_system) and super().has_delete_permission(request, obj)


from django.contrib.auth.models import Permission  # noqa: E402


@admin.register(Permission)
class PermissionAdmin(ModelAdmin):
    """Needed for autocomplete in overrides; read-only (permissions come from models)."""
    search_fields = ("codename", "name", "content_type__app_label")
    list_display = ("__str__", "codename")
    list_filter = (("content_type", RelatedDropdownFilter),)

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


class GroupProfileInline(StackedInline):
    model = GroupProfile
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("description",)


class RoleInline(TabularInline):
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
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
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


# ===========================================================================
# Phase 9: account deletion requests, signed-in devices, passkeys
# ===========================================================================
from core.admin_base import ChoicesDropdownFilter, badge  # noqa: E402

from . import deletion as _deletion  # noqa: E402
from .models import AccountDeletionRequest, Passkey, UserSession  # noqa: E402


@admin.register(AccountDeletionRequest)
class AccountDeletionRequestAdmin(ModelAdmin):
    """Users ask from inside the website/app; an administrator decides.
    "Mark completed" deactivates the account and blanks personal fields —
    the user row and statutory records are kept."""

    list_display = ("id", "user", "user_type", "status_badge", "created_at", "handled_by", "handled_at")
    list_filter = (("status", ChoicesDropdownFilter), "user__user_type", ("created_at", RangeDateTimeFilter))
    search_fields = ("user__email", "user__first_name", "user__last_name", "reason", "note")
    readonly_fields = ("user", "reason", "status", "created_at", "handled_by", "handled_at")
    fields = ("user", "reason", "status", "created_at", "handled_by", "handled_at", "note")
    actions = ["approve_requests", "decline_requests", "complete_requests"]
    date_hierarchy = "created_at"
    status_badge = badge("status", "Status", {"pending": "warning", "approved": "info", "declined": "danger",
                                              "completed": "success", "withdrawn": "neutral"})

    def has_add_permission(self, request):
        return False  # requests are made by the users themselves

    @admin.display(description="Account type", ordering="user__user_type")
    def user_type(self, obj):
        return obj.user.get_user_type_display()

    def _run(self, request, queryset, fn, verb):
        done = sum(1 for req in queryset.select_related("user") if fn(req, request.user))
        skipped = queryset.count() - done
        self.message_user(request, f"{done} request(s) {verb}." + (f" {skipped} skipped (wrong status)." if skipped
                                                                  else ""),
                          messages.SUCCESS if done else messages.WARNING)

    @admin.action(description="Approve selected requests", permissions=["change"])
    def approve_requests(self, request, queryset):
        self._run(request, queryset, _deletion.approve, "approved")

    @admin.action(description="Decline selected requests", permissions=["change"])
    def decline_requests(self, request, queryset):
        self._run(request, queryset, _deletion.decline, "declined")

    @admin.action(description="Mark completed (deactivate account, blank personal fields)", permissions=["change"])
    def complete_requests(self, request, queryset):
        self._run(request, queryset, _deletion.complete, "completed")


@admin.register(UserSession)
class UserSessionAdmin(ReadOnlyModelAdmin):
    list_display = ("user", "device_label", "kind", "door", "ip", "created_at", "last_seen_at", "revoked_at")
    list_filter = ("kind", "door", ("last_seen_at", RangeDateTimeFilter))
    search_fields = ("user__email", "device_label", "ip")
    exclude = ("session_key",)


@admin.register(Passkey)
class PasskeyAdmin(ReadOnlyModelAdmin):
    list_display = ("user", "name", "created_at", "last_used_at", "sign_count")
    search_fields = ("user__email", "name")
    exclude = ("public_key", "credential_id")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("accounts.delete_passkey")  # remove a lost device's passkey
