"""B10 / T-SEC-3: `accounts.access_all_departments` is no longer a director
right. It is removed from the director-level roles and default groups; the
ES and super admin keep it and hold the explicit tasks.view_all_tasks,
accounts.view_all_users and inspection.view_all_ssq (0002)."""
from django.db import migrations


def forward(apps, schema_editor):
    from org import seed
    Role = apps.get_model("accounts", "Role")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    perm = Permission.objects.filter(content_type__app_label="accounts", codename="access_all_departments").first()
    if perm is None:
        return
    for role in Role.objects.filter(code__in=seed.DIRECTOR_LEVEL_ROLES):
        role.permissions.remove(perm)
    for group in Group.objects.filter(name__in=seed.DIRECTOR_LEVEL_GROUPS):
        group.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [("org", "0005_single_director")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
