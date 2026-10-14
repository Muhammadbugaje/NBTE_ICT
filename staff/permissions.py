"""Staff visibility & action-scoping helpers.

Rules (doc 30 §5.2, §18.3, §19.6):

  - Superuser / `staff.view_staff`          → see everything, all departments
  - `staff.view_own_department_staff`       → see own department (colleague level)
  - Everyone                                → see their own staff record (full)
  - Editing follows the same scope, plus `staff.manage_own_department_staff`
    for the department-head case.
  - State changes additionally require `staff.change_staff_state`.

`view_level()` returns the strongest level a viewer has on a given record,
which the template uses to decide which sections to reveal:

  admin     — every field, every department
  manager   — every field, within their department (department head)
  self      — full own record
  colleague — public-facing fields only (name, photo, designation, contact)
"""
from __future__ import annotations

from django.db.models import Q

from .models import Staff


# ---------------------------------------------------------------------------
# Querysets
# ---------------------------------------------------------------------------
def staff_visible_to(user):
    qs = Staff.objects.select_related(
        "user", "department", "office", "state", "category", "reports_to",
    )
    if not user or not user.is_authenticated:
        return qs.none()
    if user.is_superuser or user.has_perm("staff.view_staff"):
        return qs
    if user.has_perm("staff.view_own_department_staff") and getattr(user, "department_id", None):
        return qs.filter(Q(department_id=user.department_id) | Q(user_id=user.pk))
    return qs.filter(user_id=user.pk)


# ---------------------------------------------------------------------------
# View level
# ---------------------------------------------------------------------------
def view_level(user, staff: Staff) -> str:
    """Return the highest applicable level string."""
    if not user or not user.is_authenticated:
        return ""
    if user.is_superuser or user.has_perm("staff.view_staff"):
        return "admin"
    if staff.user_id == user.pk:
        return "self"
    if user.has_perm("staff.manage_own_department_staff") and getattr(user, "department_id", None):
        if staff.department_id == user.department_id:
            return "manager"
    if user.has_perm("staff.view_own_department_staff") and getattr(user, "department_id", None):
        if staff.department_id == user.department_id:
            return "colleague"
    return ""


def can_view_staff(user, staff: Staff) -> bool:
    return bool(view_level(user, staff))


def can_view_hr_fields(user, staff: Staff) -> bool:
    """Emergency contact, birth date, state history, attachments."""
    return view_level(user, staff) in ("admin", "manager", "self")


def can_view_state_history(user, staff: Staff) -> bool:
    return can_view_hr_fields(user, staff)


def can_manage_staff(user, staff: Staff) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.has_perm("staff.change_staff"):
        return True
    if user.has_perm("staff.manage_own_department_staff") and getattr(user, "department_id", None):
        return staff.department_id == user.department_id
    # Own record: profile-ish fields only (view hides HR-only fields)
    if staff.user_id == user.pk:
        return True
    return False


def can_change_state(user, staff: Staff) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.has_perm("staff.change_staff_state"):
        return True
    if user.has_perm("staff.manage_own_department_staff") and getattr(user, "department_id", None):
        return staff.department_id == user.department_id
    return False


def can_create_staff(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return (
        user.has_perm("staff.add_staff")
        or user.has_perm("staff.manage_own_department_staff")
    )


def can_delete_staff(user, staff: Staff) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or user.has_perm("staff.delete_staff"):
        return True
    return False


def can_message_staff(user, staff: Staff) -> bool:
    """Any colleague in the same department, plus managers/admins."""
    if not user or not user.is_authenticated or staff.user_id == user.pk:
        return False
    lvl = view_level(user, staff)
    return lvl in ("admin", "manager", "colleague")


def can_notify_staff(user, staff: Staff) -> bool:
    """Send an in-app notification — restricted to managers and above."""
    return view_level(user, staff) in ("admin", "manager")


def scoped_department_for_new(user):
    if user.is_superuser or user.has_perm("staff.change_staff"):
        return None
    if user.has_perm("staff.manage_own_department_staff"):
        return getattr(user, "department", None)
    return None