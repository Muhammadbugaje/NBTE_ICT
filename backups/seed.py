"""Idempotent seed for backups, called from `seed_startup` (Master Plan §15, §22).

* Permissions: every backups permission to the super-admin role and to the
  "Backup administrators" group; view_backups to the ict-qa-monitor role.
  The group is given to every active Director of ICT (role "director" in the
  ICT department). Nothing is ever removed.
* A placeholder, inactive local destination so the page has something to
  edit. While no destination is active, seed_startup prints
  "No backup destination configured. Set one before going live."
The qa.backup.* settings come from qa_core.settings_catalogue (SETTINGS_BACKUPS).
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group, Permission

ALL_PERMS = ["view_backups", "run_backup", "manage_destinations", "run_restore_drill"]
ROLE_PERMISSIONS = {
    "super-admin": ALL_PERMS,
    "ict-qa-monitor": ["view_backups"],
}
ADMIN_GROUP = "Backup administrators"
ADMIN_GROUP_DESCRIPTION = ("Director of ICT: view backups, run them, manage destinations and the schedule, "
                           "and run restore drills.")
PLACEHOLDER_NAME = "Local folder (placeholder)"


def _perm(codename):
    return Permission.objects.filter(content_type__app_label="backups", codename=codename).first()


def placeholder_path():
    return str(Path(settings.BASE_DIR).parent / "nbte-backups")


def seed():
    """Returns a dict of counts: grants, groups, destinations, directors."""
    from accounts.models import GroupProfile, Role
    from qa_core.departments import qa_department

    from .models import BackupDestination

    out = {"grants": 0, "groups": 0, "destinations": 0, "directors": 0}
    for code, codenames in ROLE_PERMISSIONS.items():
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        have = set(role.permissions.values_list("pk", flat=True))
        for codename in codenames:
            perm = _perm(codename)
            if perm is not None and perm.pk not in have:
                role.permissions.add(perm)
                out["grants"] += 1

    group, created = Group.objects.get_or_create(name=ADMIN_GROUP)
    if created:
        out["groups"] += 1
        GroupProfile.objects.get_or_create(group=group, defaults={"description": ADMIN_GROUP_DESCRIPTION})
    have = set(group.permissions.values_list("pk", flat=True))
    for codename in ALL_PERMS:
        perm = _perm(codename)
        if perm is not None and perm.pk not in have:
            group.permissions.add(perm)
            out["grants"] += 1
    ict = qa_department("ict")
    if ict is not None:
        import org
        director = org.director_of(ict)
        for user in ([director] if director is not None else []):
            if not user.groups.filter(pk=group.pk).exists():
                user.groups.add(group)
                out["directors"] += 1

    if not BackupDestination.objects.exists():
        BackupDestination.objects.create(
            name=PLACEHOLDER_NAME, kind=BackupDestination.LOCAL, is_active=False, is_placeholder=True,
            config={"path": placeholder_path()})
        out["destinations"] += 1
    return out


def warning():
    from .models import BackupDestination

    if not BackupDestination.objects.filter(is_active=True).exists():
        from .engine import NO_DESTINATION_WARNING
        return NO_DESTINATION_WARNING
    return ""
