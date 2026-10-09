"""Accounts app models.

- `Role` — organisational role with an admin-editable permission baseline.
- `User` — custom user, email-only login, department-scoped.
- `UserPermissionOverride` — per-user grant/revoke on top of the role baseline.
- Profile completeness is computed from User + linked Staff record; see
  `User.profile_completeness()`.
"""
from django.contrib.auth.models import AbstractUser, BaseUserManager, Permission
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
        ]

    def __str__(self):
        return self.name

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

    must_change_password = models.BooleanField(default=True)
    # 2FA scaffold (enforced in Phase 5).
    two_factor_enabled = models.BooleanField(default=False)
    totp_secret = models.CharField(max_length=64, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

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