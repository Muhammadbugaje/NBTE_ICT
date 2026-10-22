"""Accounts app models.

- `Role` — organisational role with an admin-editable permission baseline.
- `User` — custom user, email-only login, department-scoped.
- `UserPermissionOverride` — per-user grant/revoke on top of the role baseline.
- Profile completeness is computed from User + linked Staff record; see
  `User.profile_completeness()`.
"""
from django.contrib.auth.models import AbstractUser, BaseUserManager, Group, Permission
from django.db import models

from core.models import TimeStampedModel


# ---------------------------------------------------------------------------
# Profile completeness weights
# ---------------------------------------------------------------------------
# key -> (human label, default weight)
# Admins can override any weight via the config registry:
#   key: profile.weight.<key>
#   default: 70
# See config/defaults.py for the seeded rows.
PROFILE_FIELD_WEIGHTS = {
    "first_name":              ("First name",              2),
    "last_name":               ("Last name",               2),
    "email":                   ("Email",                   2),
    "phone":                   ("Phone number",            3),
    "department":              ("Department",              3),
    "role":                    ("Role",                    2),
    "photo":                   ("Profile photo",           4),
    "designation":             ("Designation",             3),
    "grade_level":             ("Grade level",             3),
    "subsection":              ("Subsection / unit",       2),
    "office_location":         ("Office location",         2),
    "duties":                  ("Duties",                  2),
    "emergency_contact_name":  ("Emergency contact name",  3),
    "emergency_contact_phone": ("Emergency contact phone", 3),
    "contact_preference":      ("Contact preference",      2),
}

# Fields whose value lives on the User model. Everything else lives on Staff.
_USER_LEVEL_FIELDS = {"first_name", "last_name", "email", "department", "role"}


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------
class Role(TimeStampedModel):
    """Organisational role with an admin-editable default permission set.

    `rank` establishes seniority within a department. Higher = more senior.
    Used by the workflow engine to skip approval steps that would route a
    request to someone less senior than the requester. System roles that
    don't sit in the hierarchy (super-admin) use rank 0.
    """

    AUDITED = True

    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")
    # Stage 3 (groups & permissions): Django groups this role carries. Every
    # user holding the role inherits the groups' permissions through
    # RolePermissionBackend. Additive to `permissions` above - both apply.
    groups = models.ManyToManyField(
        Group, blank=True, related_name="roles",
        help_text="Django groups every holder of this role inherits.",
    )
    # 2FA enforcement flag (wired in Phase 5).
    requires_2fa = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False, help_text="Seeded role; do not delete.")
    rank = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Seniority within a department. Higher = more senior. "
            "Used to skip approval steps that would otherwise route a request "
            "to someone below the requester. 0 = not in the hierarchy (system roles)."
        ),
    )
    max_open_tasks = models.PositiveIntegerField(
        default=5,
        help_text=(
            "Maximum simultaneously-open tasks this role can be assigned. "
            "Higher ranks usually get higher caps."
        ),
    )

    class Meta:
        ordering = ["name"]
        permissions = [
            ("manage_roles", "Can manage roles and role-permission mappings"),
            ("manage_permission_overrides", "Can grant/revoke per-user permission overrides"),
            ("access_all_departments", "Can see and act across ALL departments"),
            ("view_analytics_dashboard", "Can view the full cross-office analytics dashboard"),
            ("approve_requests", "Can approve requests assigned to them"),
            ("raise_cross_department_requests", "Can raise requests that route across departments"),
            ("view_all_requests", "Can view every workflow request and ticket across all offices"),
            ("view_department_requests",
             "Can view all workflow requests and tickets raised by or addressed to their own department"),
            # Review L1: the Chairman dashboard checked a permission that did
            # not exist. There is no Chairman role; grant this per user.
            ("view_chairman_dashboard", "Can view the Chairman dashboard"),
            # Stage 3: assign groups/permissions to roles and users.
            ("assign_groups", "Can assign groups and direct permissions to roles and users"),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# GroupProfile (Stage 3)
# ---------------------------------------------------------------------------
class GroupProfile(TimeStampedModel):
    """Optional description for a Django `auth.Group`.

    Django's Group has only a name. Admins asked for a sentence explaining
    what each group is for; a one-to-one side table keeps `auth.Group`
    itself untouched (no swapped model, no parallel permission table).
    """

    AUDITED = True

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="profile")
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "group profile"

    def __str__(self):
        return f"Profile of {self.group.name}"

# ---------------------------------------------------------------------------
# User manager
# ---------------------------------------------------------------------------
class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        extra.setdefault("must_change_password", True)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("must_change_password", False)
        return self._create(email, password, **extra)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class User(AbstractUser):
    """Email is the login identifier. The redundant `username` column is
    removed so createsuperuser and create_user never need a duplicate value."""

    AUDITED = True
    AUDIT_EXCLUDE_FIELDS = ("password", "totp_secret")
    AUDIT_IGNORE_FIELDS = ("last_login",)

    username = None
    email = models.EmailField(unique=True)

    role = models.ForeignKey(
        Role, null=True, blank=True,
        on_delete=models.PROTECT, related_name="users",
    )
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.PROTECT, related_name="users",
    )

    # --- QA (Master Plan §3) -------------------------------------------
    # One user table for staff, institution users and resource persons.
    # `user_type` is the hard discriminator ExternalUserGuardMiddleware checks
    # on every request; `institution` makes scope_to_institution one indexed
    # filter that fails closed when NULL.
    STAFF, INSTITUTION, RESOURCE_PERSON = "staff", "institution", "resource_person"
    USER_TYPE_CHOICES = [
        (STAFF, "NBTE staff"),
        (INSTITUTION, "Institution user"),
        (RESOURCE_PERSON, "Resource person"),
    ]
    user_type = models.CharField(
        max_length=20, choices=USER_TYPE_CHOICES, default=STAFF, db_index=True,
        help_text="Staff use the internal system; institutions and resource persons use the QA portal.",
    )
    institution = models.ForeignKey(
        "institutions.Institution", null=True, blank=True,
        on_delete=models.PROTECT, related_name="users",
        help_text="Required for institution users; optional for resource persons "
                  "(conflict-of-interest checks); always empty for staff.",
    )

    must_change_password = models.BooleanField(default=True)
    # Phase 6: which sidebar a staff member works in (the workspace switcher).
    WORKSPACE_CHOICES = [("main", "Main system"), ("qa", "Quality Assurance")]
    workspace = models.CharField(max_length=10, choices=WORKSPACE_CHOICES, default="main",
                                 help_text="Sidebar shown by default: the main NBTE system or the QA workspace.")
    # 2FA scaffold (enforced in Phase 5).
    two_factor_enabled = models.BooleanField(default=False)
    totp_secret = models.CharField(max_length=64, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            # Master Plan §3: institution users must belong to an institution,
            # staff never do; resource persons may (conflict-of-interest).
            models.CheckConstraint(
                condition=(
                    models.Q(user_type="institution", institution__isnull=False)
                    | models.Q(user_type="staff", institution__isnull=True)
                    | models.Q(user_type="resource_person")
                ),
                name="accounts_user_type_institution_consistent",
            ),
        ]

    @property
    def is_staff_user(self):
        """NBTE staff (internal system). Not Django's `is_staff` (admin access)."""
        return self.user_type == self.STAFF

    @property
    def is_external_user(self):
        """Institution users and resource persons — QA portal only."""
        return self.user_type != self.STAFF

    def __str__(self):
        name = self.get_full_name()
        return f"{name} ({self.email})" if name else self.email

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def save(self, *args, **kwargs):
        self.email = (self.email or "").lower()
        super().save(*args, **kwargs)
        # Only sync when department might have changed (or on a full save).
        update_fields = kwargs.get("update_fields")
        department_touched = (
            update_fields is None
            or "department" in update_fields
            or "department_id" in update_fields
        )
        if department_touched:
            self._sync_department_to_staff()

    # ------------------------------------------------------------------
    # Department-aware access
    # ------------------------------------------------------------------
    def can_access_department(self, department):
        """True for superusers and anyone holding `accounts.access_all_departments`;
        otherwise only for their own department. Fails closed."""
        if not self.is_active:
            return False
        if self.is_superuser or self.has_perm("accounts.access_all_departments"):
            return True
        dept_id = getattr(department, "pk", department)
        return dept_id is not None and self.department_id == dept_id

    def has_perm_for_department(self, perm, department):
        """A permission that applies only inside a department: needs the
        permission AND access to that department."""
        return self.has_perm(perm) and self.can_access_department(department)

    @property
    def config_scope(self):
        return self.department.config_scope if self.department_id else "global"

    @property
    def is_power_user(self):
        """True if the user sees the full analytics dashboard link."""
        return self.has_perm("accounts.view_analytics_dashboard")

    # ------------------------------------------------------------------
    # Department sync with the linked Staff record
    # ------------------------------------------------------------------
    def _sync_department_to_staff(self):
        if not self.department_id or getattr(self, "_dept_syncing", False):
            return
        try:
            staff = self.staff_profile
        except Exception:
            return
        if staff.department_id != self.department_id:
            self._dept_syncing = True
            staff.department_id = self.department_id
            staff.save()
            self._dept_syncing = False

    # ------------------------------------------------------------------
    # Profile completeness
    # ------------------------------------------------------------------
    def profile_completeness(self):
        """Return {percent, filled_weight, total_weight, items}.

        `items` is a list of {key, label, filled, weight}. Values come from
        the User and (if linked) the Staff record — a field counts as filled
        if the correct source has a non-empty value.
        """
        staff = None
        try:
            staff = self.staff_profile
        except Exception:
            staff = None

        import config as _cfg
        items = []
        total = 0
        filled_weight = 0

        for key, (label, default_weight) in PROFILE_FIELD_WEIGHTS.items():
            try:
                weight = int(_cfg.get(f"profile.weight.{key}", default_weight))
            except (TypeError, ValueError):
                weight = default_weight
            value = self._profile_value(key, staff)
            filled = value not in (None, "", 0)
            if filled:
                filled_weight += weight
            total += weight
            items.append({
                "key": key,
                "label": label,
                "filled": filled,
                "weight": weight,
            })

        percent = round((filled_weight / total) * 100) if total else 0
        return {
            "percent": percent,
            "filled_weight": filled_weight,
            "total_weight": total,
            "items": items,
        }

    def _profile_value(self, key, staff):
        """Resolve a profile field's current value.

        - User-level fields (first_name, last_name, email, department, role)
          come from the User model.
        - Everything else comes from the linked Staff record; if no Staff
          record exists, the field is treated as unfilled.
        """
        if key == "department":
            return self.department_id
        if key == "role":
            return self.role_id
        if key in ("first_name", "last_name", "email"):
            return getattr(self, key, None)
        if staff is None:
            return None
        return getattr(staff, key, None)

    def profile_percent(self):
        return self.profile_completeness()["percent"]

    @property
    def profile_threshold(self):
        """Minimum percentage required to unlock gated actions."""
        import config as _cfg
        try:
            return int(_cfg.get("profile.completion_threshold", 70))
        except (TypeError, ValueError):
            return 70

    @property
    def profile_complete(self):
        return self.profile_percent() >= self.profile_threshold

    def missing_profile_items(self):
        return [i for i in self.profile_completeness()["items"] if not i["filled"]]


# ---------------------------------------------------------------------------
# Per-user permission overrides
# ---------------------------------------------------------------------------
class UserPermissionOverride(TimeStampedModel):
    """Per-user grant or revoke on top of the role baseline.
    Effective permissions = (role perms + grants) - revokes."""

    AUDITED = True
    GRANT, REVOKE = "grant", "revoke"
    EFFECT_CHOICES = [(GRANT, "Grant"), (REVOKE, "Revoke")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="permission_overrides")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="+")
    effect = models.CharField(max_length=6, choices=EFFECT_CHOICES)
    reason = models.CharField(max_length=255, blank=True)
    granted_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        unique_together = ("user", "permission")

    def __str__(self):
        return f"{self.effect} {self.permission.codename} -> {self.user_id}"

# ===========================================================================
# Phase 9: signed-in devices, account deletion requests and passkeys.
# Business logic lives in accounts/sessions_service.py, accounts/deletion.py
# and accounts/passkeys.py so the mobile-app API can call the same functions.
# ===========================================================================
class UserSession(models.Model):
    """One signed-in browser (or, later, app install) for a user.

    The Django session row is the real credential; this row is the record the
    user sees under "Signed-in devices". Revoking deletes the Django session
    from the session store and stamps `revoked_at`.
    """

    WEB, APP = "web", "app"
    KIND_CHOICES = [(WEB, "Website"), (APP, "Mobile app")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="device_sessions")
    session_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True, unpack_ipv4=True)
    user_agent = models.CharField(max_length=500, blank=True)
    device_label = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=WEB)
    door = models.CharField(max_length=10, blank=True,
                            help_text="Which front door the sign-in happened on: internal or portal.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Phase 9 API: mobile-app sessions (kind "app"). session_key is then
    # "app:<jti of the current refresh token>" and follows token rotation.
    platform = models.CharField(max_length=20, blank=True, help_text="android / ios (app sessions).")
    app_version = models.CharField(max_length=40, blank=True)
    device_id = models.CharField(max_length=100, blank=True,
                                 help_text="Install id the app generates on first launch.")
    push_token = models.TextField(blank=True, help_text="Push-notification token of this install.")

    class Meta:
        ordering = ["-last_seen_at", "-created_at"]
        indexes = [models.Index(fields=["user", "revoked_at"], name="acct_usersess_user_rev_idx")]
        verbose_name = "signed-in device"
        verbose_name_plural = "signed-in devices"

    def __str__(self):
        return f"{self.device_label or 'Device'} · {self.user_id}"

    @property
    def is_active(self):
        return self.revoked_at is None


class AccountDeletionRequest(models.Model):
    """A user's request to delete their account (app-store requirement).

    NBTE must keep statutory records, so this is a request an administrator
    handles; "completed" deactivates the account and blanks personal fields,
    it never deletes the user row.
    """

    AUDITED = True

    PENDING, APPROVED, DECLINED, COMPLETED, WITHDRAWN = (
        "pending", "approved", "declined", "completed", "withdrawn")
    STATUS_CHOICES = [
        (PENDING, "Pending"), (APPROVED, "Approved"), (DECLINED, "Declined"),
        (COMPLETED, "Completed"), (WITHDRAWN, "Withdrawn by user"),
    ]
    OPEN_STATUSES = (PENDING, APPROVED)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="deletion_requests")
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    handled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, help_text="Administrator's note (shown to the user).")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "account deletion request"

    def __str__(self):
        return f"Deletion request #{self.pk} ({self.get_status_display()})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class Passkey(models.Model):
    """A WebAuthn credential (fingerprint / face / device PIN) for one user.
    `credential_id` and `public_key` are stored base64url-encoded."""

    AUDITED = True
    AUDIT_EXCLUDE_FIELDS = ("public_key",)
    AUDIT_IGNORE_FIELDS = ("sign_count", "last_used_at")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passkeys")
    credential_id = models.CharField(max_length=1024, unique=True)
    public_key = models.TextField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    name = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name or f"Passkey #{self.pk}"
