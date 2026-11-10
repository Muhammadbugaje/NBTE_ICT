"""Shared building blocks for the per-module visibility rules.

`assets/access.py`, `tickets/access.py` and `workflow/access.py` all answer
the same three questions about a user, so the answers live here once:

    is_super_admin(user) ........ superuser flag or the `super-admin` role.
    is_executive_secretary(user)  the `executive-secretary` role (informational
                                  only: the ES gets the "all" permissions
                                  through the permissions, never by role code,
                                  so an admin can remove them).
    is_department_lead(user) .... director, deputy director or an office head
                                  that belongs to a department.

None of these look at `accounts.access_all_departments`. Every director role
carries that permission, so using it as a "see everything" switch made every
director a viewer of every department (see attendance/access.py for the first
time this was fixed).
"""
from __future__ import annotations

DEPARTMENT_LEAD_ROLE_CODES = (
    "director", "deputy-director",
    "office-head-admin", "office-head-ts", "office-head-dev",
)
SUPER_ROLE_CODES = ("super-admin",)
EXECUTIVE_SECRETARY_ROLE = "executive-secretary"


def is_super_admin(user):
    """Superuser flag, or the `super-admin` role. Always passes every check.
    (Phase B: delegates to org.)"""
    import org
    return org.is_super_admin(user)


def is_executive_secretary(user):
    import org
    return bool(getattr(user, "is_authenticated", False) and org.has_role(user, EXECUTIVE_SECRETARY_ROLE))


def is_department_lead(user):
    """Director-level post (director seat, deputy, office head) inside a department.
    (Phase B: delegates to org.is_department_lead.)"""
    import org
    return bool(getattr(user, "is_authenticated", False) and user.department_id
                and org.is_department_lead(user, user.department_id))
