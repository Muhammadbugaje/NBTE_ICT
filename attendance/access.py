"""Who may see attendance reports and dashboards.

Single source of truth, used by the attendance views, global search and the
dashboards, so the rules can never drift apart.

    Super admin ............ everything, automatically (superuser flag or the
                             `super-admin` role).
    All departments ........ needs `attendance.view_all_attendance`. Seeded on
                             the Executive Secretary role; grant it to anyone
                             else (HR, a director, ...) from the permissions
                             screens. Directors of the departments listed in
                             the config key `attendance.all_access_department_codes`
                             (default: HR) get it by virtue of their post.
    Own department ......... a director / deputy / office head of that
                             department, or anyone holding
                             `attendance.view_department_attendance`.
    Everyone else .......... their own records only.

Note this deliberately does NOT use `accounts.access_all_departments`: that
permission is a general "act across departments" switch that every director
role carries, so it made every director a viewer of every department's
attendance.
"""
from __future__ import annotations

import json

PERM_ALL = "attendance.view_all_attendance"
PERM_DEPARTMENT = "attendance.view_department_attendance"

DIRECTOR_ROLE_CODES = (
    "director", "deputy-director",
    "office-head-admin", "office-head-ts", "office-head-dev",
)
SUPER_ROLE_CODES = ("super-admin",)


def _cfg_codes():
    try:
        import config as _c
        raw = _c.get("attendance.all_access_department_codes", '["hr"]')
    except Exception:
        raw = '["hr"]'
    if isinstance(raw, (list, tuple)):
        codes = raw
    else:
        try:
            codes = json.loads(raw)
        except Exception:
            codes = ["hr"]
    return {str(c).strip().lower() for c in codes if str(c).strip()}


def is_director(user):
    """Director-level post (director, deputy, office head) inside a department."""
    return bool(
        user.is_authenticated
        and user.department_id
        and user.role_id
        and user.role.code in DIRECTOR_ROLE_CODES
    )


def _in_all_access_department(user):
    """Director-level staff of a department that is allowed to see everyone
    (HR by default). Rank-and-file staff in that department are not."""
    if not is_director(user):
        return False
    code = (getattr(user.department, "code", "") or "").strip().lower()
    return bool(code) and code in _cfg_codes()


def can_view_all_attendance(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.role_id and user.role.code in SUPER_ROLE_CODES:
        return True
    if user.has_perm(PERM_ALL):
        return True
    return _in_all_access_department(user)


def can_view_department(user, department):
    if not user.is_authenticated or department is None:
        return False
    if can_view_all_attendance(user):
        return True
    if not user.department_id:
        return False
    if user.department_id != getattr(department, "pk", department):
        return False
    return is_director(user) or user.has_perm(PERM_DEPARTMENT)


def attendance_scope(user):
    """"all", "department" or "none" — what a dashboard may show this user."""
    if can_view_all_attendance(user):
        return "all"
    if user.is_authenticated and user.department_id and can_view_department(
        user, user.department,
    ):
        return "department"
    return "none"
