"""seed_nbte data sets owned by core (v2.1 X51): settings, roles, groups,
HR reference lists, backups, the transitional QA startup set and the demo.

Order numbers follow seed spec §5 (set number x 10)."""
import importlib
import io

from django.apps import apps
from django.contrib.auth.models import Group, Permission

from .seeding import load_json, seed_set, upsert


def _perm(label):
    app, codename = label.split(".", 1)
    return Permission.objects.filter(content_type__app_label=app, codename=codename).first()


# ---------------------------------------------------------------------------
# 1 settings
# ---------------------------------------------------------------------------
def settings_rows():
    """(key, value, type, group, description, editable_by, dual) from every
    catalogue: config.defaults.DEFAULTS and each app's settings_catalogue.SETTINGS."""
    from config.defaults import DEFAULTS

    rows = [(k, v, t, g, d, "", False) for k, v, t, g, d in DEFAULTS]
    for cfg in apps.get_app_configs():
        try:
            mod = importlib.import_module(f"{cfg.name}.settings_catalogue")
        except ImportError:
            continue
        rows += list(getattr(mod, "SETTINGS", []))
    seen, out = set(), []
    for row in rows:
        if row[0] in seen:
            continue
        seen.add(row[0])
        out.append(row)
    return out


@seed_set("settings", group="base", order=10)
def seed_settings(ctx):
    from config.models import ConfigEntry

    for key, value, vtype, group, desc, editable_by, dual in settings_rows():
        value = value() if callable(value) else value
        upsert(ConfigEntry, {"key": key, "scope": "global"},
               {"value": value, "value_type": vtype, "group": group, "description": (desc or "")[:255],
                "editable_by": editable_by or "", "dual_control": bool(dual)}, ctx=ctx, create_only=True)


# ---------------------------------------------------------------------------
# 2 roles (accounts.seed_roles semantics without --sync: a role an admin has
# configured is never changed) + the QA roles (create only)
# ---------------------------------------------------------------------------
@seed_set("roles", group="base", depends=("settings",), order=20)
def seed_roles(ctx):
    from accounts.management.commands.seed_roles import _MIGRATION_GRANTED, ROLES
    from accounts.models import Role
    from qa_core import seed_data

    deferred = set()
    for code, name, twofa, rank, max_open, perms in ROLES:
        role = Role.objects.filter(code=code).first()
        created = role is None
        if created:
            role = Role(code=code, name=name, requires_2fa=twofa, is_system=True, rank=rank,
                        max_open_tasks=max_open)
            role.save()
        fresh = created or not role.permissions.exclude(codename__in=_MIGRATION_GRANTED).exists()
        added = 0
        if fresh:
            for label in perms:
                p = _perm(label)
                if p is None:
                    deferred.add(label)
                elif not role.permissions.filter(pk=p.pk).exists():
                    role.permissions.add(p)
                    added += 1
        if created:
            ctx.res.created += 1
        elif added:
            ctx.res.filled += 1
        else:
            ctx.res.unchanged += 1
    for code, name, rank, max_open, _user_type, description in seed_data.NEW_ROLES:
        upsert(Role, {"code": code}, {"name": name, "rank": rank, "max_open_tasks": max_open,
                                      "description": description, "is_system": True}, ctx=ctx, create_only=True)
    if deferred:
        ctx.note(f"{len(deferred)} permission(s) deferred until their app exists")


# ---------------------------------------------------------------------------
# 3 groups (default groups; QA groups are in qa_core.startup)
# ---------------------------------------------------------------------------
@seed_set("groups", group="base", depends=("roles",), order=30)
def seed_groups(ctx):
    from accounts.management.commands.seed_default_groups import DEFAULT_GROUPS
    from accounts.models import GroupProfile, Role

    for name, description, perms, role_codes in DEFAULT_GROUPS:
        if Group.objects.filter(name=name).exists():
            ctx.res.unchanged += 1
            continue
        group = Group.objects.create(name=name)
        GroupProfile.objects.get_or_create(group=group, defaults={"description": description})
        group.permissions.set([p for p in (_perm(label) for label in dict.fromkeys(perms)) if p is not None])
        for role in Role.objects.filter(code__in=role_codes):
            role.groups.add(group)
        ctx.res.created += 1


# 12-13 HR reference lists (hr.leave_types, hr.holidays) live in leave/seed_sets.py (Phase D).


# ---------------------------------------------------------------------------
# Transitional: the existing QA startup seed (flows, templates, policies,
# lookups, fees, phase 3-8 data). Later phases split it into sets 14-32.
# ---------------------------------------------------------------------------
@seed_set("qa_core.startup", group="qa", depends=("settings", "roles", "groups", "org.departments", "geo.states"),
          order=140)
def seed_qa_startup(ctx):
    from qa_core.management.commands.seed_startup import LegacyStartupCommand

    cmd = LegacyStartupCommand(stdout=io.StringIO(), stderr=io.StringIO())
    stats = cmd.run_sets(with_demo=False)
    ctx.res.created += sum(stats.created.values())
    ctx.res.filled += stats.grants_added
    if not stats.changes:
        ctx.res.unchanged += sum(stats.totals.values()) if stats.totals else 1
    if stats.deferred_perms:
        ctx.note(f"{len(stats.deferred_perms)} QA permission(s) deferred to later phases")
    for note in stats.notes:
        if "up to date" not in note:
            ctx.note(note)


# ---------------------------------------------------------------------------
# 33 backups
# ---------------------------------------------------------------------------
@seed_set("backups", group="base", depends=("settings",), order=330)
def seed_backups(ctx):
    from backups import seed as backup_seed

    result = backup_seed.seed()
    ctx.res.created += result.get("destinations", 0)
    ctx.res.filled += result.get("grants", 0)
    if not (result.get("destinations") or result.get("grants")):
        ctx.res.unchanged += 1
    try:
        warning = backup_seed.warning()
    except Exception:
        warning = ""
    if warning:
        ctx.note(str(warning))


# ---------------------------------------------------------------------------
# 34 demo (only with --with-demo)
# ---------------------------------------------------------------------------
@seed_set("demo", group="demo", depends=("qa_core.startup",), order=900, demo_only=True)
def seed_demo(ctx):
    from qa_core.management.commands.seed_startup import LegacyStartupCommand

    out = io.StringIO()
    cmd = LegacyStartupCommand(stdout=out, stderr=io.StringIO())
    stats = cmd.run_sets(with_demo=True, demo_only=True)
    ctx.res.created += sum(v for k, v in stats.created.items() if k.lower().startswith("demo"))
    for line in stats.credentials:
        ctx.note(line)
    if not ctx.res.created:
        ctx.res.unchanged += 1
    try:
        from documents.seed_sets import demo_signatures

        demo_signatures(ctx)
    except ImportError:
        pass
