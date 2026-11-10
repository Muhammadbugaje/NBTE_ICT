"""P.4 (N13, S-17): ``qa_core.view_all_institutions`` only for the ES, the
super admin and the Director of ICT.

In ``department`` mode the permission is how Finance, the ICT QA monitor and
the demo walkthrough group see every institution — removing it there would
break today's queues. It is therefore removed **when the privacy mode is
switched to ``assignment``** (``manage.py privacy_mode --set assignment``),
after a dry run (``plan()`` / the report). In assignment mode
``assignments.access`` does not read the permission at all (full viewers are
decided by role, seat and ``assignments.view_all_journeys``), and seeding
stops adding it back (``enforce()`` runs after the role and group seeds).

Who keeps it: role ``executive-secretary``, role ``super-admin``,
superusers (implicit), the Director of ICT (holds it as a direct permission
so department-mode screens keep working after a switch back).
"""
from __future__ import annotations

PERM = ("qa_core", "view_all_institutions")
KEEP_ROLES = ("executive-secretary", "super-admin")


def _perm():
    from django.contrib.auth.models import Permission
    return Permission.objects.filter(content_type__app_label=PERM[0], codename=PERM[1]).first()


def _ict_director():
    import org

    from .access import ict_department_code
    return org.director_of(ict_department_code())


def holders():
    """{"roles": [(code, active users)], "groups": [(name, members)], "users": [user]}
    of everyone holding the permission other than the keepers."""
    from accounts.models import Role, User
    from django.contrib.auth.models import Group
    perm = _perm()
    out = {"roles": [], "groups": [], "users": []}
    if perm is None:
        return out
    for role in Role.objects.filter(permissions=perm).exclude(code__in=KEEP_ROLES).order_by("code"):
        out["roles"].append((role.code, User.objects.filter(role=role, is_active=True).count()))
    for group in Group.objects.filter(permissions=perm).order_by("name"):
        out["groups"].append((group.name, group.user_set.filter(is_active=True).count()))
    ict_dir = _ict_director()
    for user in User.objects.filter(user_permissions=perm).order_by("pk"):
        if ict_dir is not None and user.pk == ict_dir.pk:
            continue
        out["users"].append(user)
    return out


def plan_text(*, show_names=False):
    from django.utils import timezone
    h = holders()
    lines = ["# `qa_core.view_all_institutions` — dry run (P.4)", "",
             f"Run: {timezone.now():%Y-%m-%d %H:%M}. Report only; nothing was changed. Applied by "
             "`manage.py privacy_mode --set assignment` (confirmed through dual control).", "",
             "Kept by: role `executive-secretary`, role `super-admin`, superusers, and the Director of ICT "
             "(direct permission).", "", "## Removed from roles", "", "| Role | Active users |", "|---|---:|"]
    lines += [f"| `{c}` | {n} |" for c, n in h["roles"]] or ["| (none) | |"]
    lines += ["", "## Removed from groups", "", "| Group | Active members |", "|---|---:|"]
    lines += [f"| {g} | {n} |" for g, n in h["groups"]] or ["| (none) | |"]
    lines += ["", f"## Removed as a direct permission: {len(h['users'])} account(s)", ""]
    if show_names:
        lines += [f"- {u.email}" for u in h["users"] if u.email.endswith(("@demo.nbte.local", "@demo.nbte.test"))]
    lines += ["", "In assignment mode these people see institutions through assignments, grants and their "
              "department's part (Finance: every institution with a bill).", ""]
    return "\n".join(lines)


def apply(actor=None):
    """Remove the permission from everyone but the keepers. Returns counts."""
    from core import audit
    from accounts.models import Role
    from django.contrib.auth.models import Group
    perm = _perm()
    if perm is None:
        return {"roles": 0, "groups": 0, "users": 0}
    h = holders()
    for code, _n in h["roles"]:
        Role.objects.get(code=code).permissions.remove(perm)
    for name, _n in h["groups"]:
        Group.objects.get(name=name).permissions.remove(perm)
    for user in h["users"]:
        user.user_permissions.remove(perm)
    ict_dir = _ict_director()
    if ict_dir is not None:
        ict_dir.user_permissions.add(perm)
    for code in KEEP_ROLES:
        role = Role.objects.filter(code=code).first()
        if role is not None:
            role.permissions.add(perm)
    counts = {"roles": len(h["roles"]), "groups": len(h["groups"]), "users": len(h["users"])}
    audit.log("other", perm, actor=actor, changes={"removed": {
        "roles": [c for c, _ in h["roles"]], "groups": [g for g, _ in h["groups"]],
        "users": [u.pk for u in h["users"]]}}, note="P.4 view_all_institutions narrowed")
    return counts


def enforce():
    """After seeding: in assignment mode keep the permission narrowed."""
    from .access import assignment_mode
    if assignment_mode():
        h = holders()
        if h["roles"] or h["groups"] or h["users"]:
            apply()
