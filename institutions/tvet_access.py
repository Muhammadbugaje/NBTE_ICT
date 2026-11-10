"""Who may add, edit or drop TVET institutions, programmes and professional
bodies (v2.1 Part 1b / X47). Rank is not a permission.

Three action groups, each with initiate / confirm / approve permissions:

* ``institutions`` — Add TVET, bulk import, contacts, addresses, state & LGA,
  name, category, ownership, delist / relist, programme edit / drop;
* ``programmes``  — the programme master list and mapping programmes to bodies;
* ``bodies``      — professional bodies (add, edit, accounts, import / export).

Held only by the ``executive-secretary`` and ``super-admin`` roles (and
superusers) and by the three seeded groups "TVET Support – Initiators /
Confirmers / Approvers" (empty: admins add named people). Every other role
and group loses them — director roles included; ``enforce`` runs after the
seeds and in the data migrations. Direct per-user grants are left alone.
Seeing institutions and bodies follows ``assignments.access`` (need-to-know)
or the read-only ``view_all_*`` permissions.
"""
from __future__ import annotations

KEEP_ROLES = ("executive-secretary", "super-admin")

_ROUTINE = ("add_tvet", "bulk_import", "update_contact", "update_address", "update_state_lga",
            "update_programme", "drop_programmes")
_SENIOR = ("update_name", "update_category", "update_ownership", "delist", "relist")
INSTITUTION_ACTIONS = _ROUTINE + _SENIOR

INITIATE = tuple(f"initiate_{n}" for n in INSTITUTION_ACTIONS) + (
    "initiate_edit_professional_bodies", "initiate_edit_programmes")
CONFIRM = tuple(f"confirm_{n}" for n in INSTITUTION_ACTIONS) + (
    "confirm_edit_professional_bodies", "confirm_edit_programmes")
APPROVE = tuple(f"approve_{n}" for n in INSTITUTION_ACTIONS) + (
    "approve_edit_professional_bodies", "approve_edit_programmes")
LEGACY = ("manage_professional_bodies", "manage_programme_master")  # old single "edit" rights
CODENAMES = INITIATE + CONFIRM + APPROVE + LEGACY

GROUPS = {
    "TVET Support – Initiators": INITIATE,
    "TVET Support – Confirmers": CONFIRM,
    "TVET Support – Approvers": APPROVE,
}
GROUP_DESCRIPTIONS = {
    "TVET Support – Initiators": "Start changes to TVET institutions, programmes and professional bodies.",
    "TVET Support – Confirmers": "Confirm (second layer) TVET institution, programme and professional body changes.",
    "TVET Support – Approvers": "Approve (third layer) TVET institution, programme and professional body changes.",
}

# Action code -> group (the ActionPolicy codes the pending-action engine sees).
ACTION_GROUPS = {f"institutions.{n}": "institutions" for n in INSTITUTION_ACTIONS}
ACTION_GROUPS.update({
    "institutions.edit_professional_body": "bodies",
    "institutions.edit_programme_master": "programmes",
    "institutions.edit_programme_mapping": "programmes",
})


def group_of(action_code):
    return ACTION_GROUPS.get(action_code)


def labels(codenames=CODENAMES):
    return [f"institutions.{c}" for c in codenames]


def is_tvet_label(label):
    app, _, codename = str(label).partition(".")
    return app == "institutions" and codename in CODENAMES


def apply(Role, Group, Permission, Department=None):
    """Narrow the permissions to the keepers (real or historical models)."""
    perms = list(Permission.objects.filter(content_type__app_label="institutions", codename__in=CODENAMES))
    if not perms:
        return {"roles": 0, "groups": 0}
    by_code = {p.codename: p for p in perms}
    removed_roles = 0
    for role in Role.objects.filter(permissions__in=perms).exclude(code__in=KEEP_ROLES).distinct():
        role.permissions.remove(*perms)
        removed_roles += 1
    for code in KEEP_ROLES:
        role = Role.objects.filter(code=code).first()
        if role is not None:
            role.permissions.add(*perms)
    removed_groups = 0
    for group in Group.objects.filter(permissions__in=perms).exclude(name__in=list(GROUPS)).distinct():
        group.permissions.remove(*perms)
        removed_groups += 1
    groups = []
    for name, codenames in GROUPS.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.add(*[by_code[c] for c in codenames if c in by_code])
        groups.append(group)
    if Department is not None and hasattr(Department, "director_groups"):
        for dept in Department.objects.filter(director_groups__in=groups).distinct():
            dept.director_groups.remove(*groups)  # rank / seat never carries these
    return {"roles": removed_roles, "groups": removed_groups}


def enforce():
    """After seeding (seed_nbte / seed_startup): keep the permissions narrowed."""
    from django.contrib.auth.models import Group, Permission

    from accounts.models import Role
    from staff.models import Department

    out = apply(Role, Group, Permission, Department)
    try:
        from accounts.models import GroupProfile
        for name, desc in GROUP_DESCRIPTIONS.items():
            group = Group.objects.filter(name=name).first()
            if group is not None:
                GroupProfile.objects.get_or_create(group=group, defaults={"description": desc})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Checks used by views and the API
# ---------------------------------------------------------------------------
def is_top(user):
    import org
    return bool(getattr(user, "is_authenticated", False) and user.is_active
                and (user.is_superuser or org.is_es(user) or org.is_super_admin(user)))


def can_initiate(user, group):
    """May ``user`` start a change in ``group`` ("institutions", "programmes", "bodies")?"""
    if is_top(user):
        return True
    codes = {"bodies": ("initiate_edit_professional_bodies",), "programmes": ("initiate_edit_programmes",),
             "institutions": tuple(f"initiate_{n}" for n in INSTITUTION_ACTIONS)}[group]
    return any(user.has_perm(f"institutions.{c}") for c in codes)


def can_view_bodies(user):
    """Professional bodies list: anyone who may change them, read-only holders,
    or staff who can see institutions (bodies are reference data for QA)."""
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "") != "staff":
        return False
    from assignments import access
    return bool(is_top(user) or access.full_viewer_everywhere(user)
                or user.has_perm("institutions.view_all_professional_bodies")
                or can_initiate(user, "bodies") or can_initiate(user, "programmes"))
