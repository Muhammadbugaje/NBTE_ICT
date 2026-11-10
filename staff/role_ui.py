"""Role assignment from the HR screens (B.8: C1-C4) — a thin wrapper.

    can_manage_roles(actor) -> bool
    grantable_roles(actor)  -> Role queryset the actor may hand out
    role_assign(user, role, actor, reason="") -> user     (raises RoleAssignError)
    access_summary(user)    -> dict for the profile's Access tab

Every HR path that sets a role (staff profile "Manage role" sheet, the staff
create form, user management "Edit roles", and later the import) calls
`role_assign`, so the rules live in one place.

MERGE HOOK (pb-org, B.7 / C9-C12): `org.assign_role(user, role, actor)` is the
full rule set (grade check against Role.min_grade, "only what you hold"
subset rule, director department scope, audited ES override). When pb-org is
merged, replace the body of `role_assign` below with:

    from org.services import assign_role          # or wherever pb-org exposes it
    return assign_role(user, role, actor, reason=reason)

and make `grantable_roles` call org's equivalent. Search for "TODO(pb-org)".
"""
from __future__ import annotations

from django.db import transaction

from accounts import access
from accounts.permissions import can_act_on_user, rank_of


class RoleAssignError(Exception):
    pass


def can_manage_roles(actor) -> bool:
    """HR (holders of accounts.change_user), the Executive Secretary, super admins."""
    if not (actor and getattr(actor, "is_authenticated", False)):
        return False
    return bool(access.is_super_admin(actor) or access.is_executive_secretary(actor)
                or actor.has_perm("accounts.change_user"))


def grantable_roles(actor, target=None):
    """Roles the actor may hand out (org rules: rank, subset, department)."""
    from accounts.models import Role
    import org
    return Role.objects.filter(pk__in=[r.pk for r in org.grantable_roles(actor, target)]).order_by("-rank", "name")


def role_assign(user, role, actor, *, reason="", override=False, source="hr"):
    """Set `user.role` to `role` (None clears it) on behalf of `actor`.
    All rules live in org.assign_role (B.7, C9-C12)."""
    import org

    if not can_manage_roles(actor) and not org.is_director(actor):
        raise RoleAssignError("Only HR, a director or the Executive Secretary can change roles.")
    if user is None or not getattr(user, "pk", None):
        raise RoleAssignError("This staff member has no user account yet.")
    try:
        org.assign_role(actor, user, role, reason or "", override=override, source=source)
    except org.OrgRuleError as exc:
        raise RoleAssignError(str(exc)) from exc
    return user


def access_summary(user):
    """Role, groups and effective permissions grouped by app (read only)."""
    from django.contrib.auth.models import Permission

    from accounts import permissions_service as ps

    if user is None:
        return None
    labels = sorted(ps.effective_permissions(user)) if not user.is_superuser else []
    names = {}
    if labels:
        for app, code, name in Permission.objects.filter(
                codename__in=[lbl.split(".", 1)[1] for lbl in labels]).values_list(
                "content_type__app_label", "codename", "name"):
            names[f"{app}.{code}"] = name
    grouped = {}
    for label in labels:
        app = label.split(".", 1)[0]
        grouped.setdefault(app, []).append({"label": label, "name": names.get(label, label)})
    return {
        "role": user.role if user.role_id else None,
        "groups": list(ps.effective_groups(user)) if hasattr(ps, "effective_groups") else [],
        "superuser": user.is_superuser,
        "count": len(labels),
        "by_app": sorted(grouped.items()),
    }
