"""Split "see every request" from "see my department's requests".

Before: directors carried `accounts.view_all_requests`, so every director could
open every workflow request in every office. After:

  * `view_all_requests`        the true "all" permission (Executive Secretary,
                               super admin, anyone granted explicitly). It now
                               also covers tickets/complaints. REMOVED from the
                               `director` role and the "Director" group.
  * `view_department_requests` NEW. Requests and tickets raised by people in the
                               holder's department or addressed to it. Given to
                               director, deputy-director and the office heads
                               (roles) and to the "Director", "Deputy Director"
                               and "Office Head" groups.

Only the seeded role/group names are touched; groups that do not exist are
skipped. The permission row is created here because Django only creates rows in
post_migrate, after all migrations. Reverse deletes the new permission and puts
`view_all_requests` back on the director role/group.
"""
from django.db import migrations

NEW_CODENAME = "view_department_requests"
NEW_NAME = ("Can view all workflow requests and tickets raised by or "
            "addressed to their own department")
DEPT_ROLES = ["director", "deputy-director",
              "office-head-admin", "office-head-ts", "office-head-dev"]
DEPT_GROUPS = ["Director", "Deputy Director", "Office Head"]


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    Role = apps.get_model("accounts", "Role")

    ct, _ = ContentType.objects.get_or_create(app_label="accounts", model="role")
    new, _ = Permission.objects.get_or_create(
        content_type=ct, codename=NEW_CODENAME, defaults={"name": NEW_NAME},
    )
    old = Permission.objects.filter(content_type=ct, codename="view_all_requests").first()

    for role in Role.objects.filter(code__in=DEPT_ROLES):
        role.permissions.add(new)
    for group in Group.objects.filter(name__in=DEPT_GROUPS):
        group.permissions.add(new)

    if old is not None:
        for role in Role.objects.filter(code="director"):
            role.permissions.remove(old)
        for group in Group.objects.filter(name="Director"):
            group.permissions.remove(old)


def backwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    Role = apps.get_model("accounts", "Role")

    old = Permission.objects.filter(
        content_type__app_label="accounts", codename="view_all_requests",
    ).first()
    if old is not None:
        for role in Role.objects.filter(code="director"):
            role.permissions.add(old)
        for group in Group.objects.filter(name="Director"):
            group.permissions.add(old)
    Permission.objects.filter(
        content_type__app_label="accounts", codename=NEW_CODENAME,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0012_create_assign_groups_permission"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="role",
            options={
                "ordering": ["name"],
                "permissions": [
                    ("manage_roles", "Can manage roles and role-permission mappings"),
                    ("manage_permission_overrides", "Can grant/revoke per-user permission overrides"),
                    ("access_all_departments", "Can see and act across ALL departments"),
                    ("view_analytics_dashboard", "Can view the full cross-office analytics dashboard"),
                    ("approve_requests", "Can approve requests assigned to them"),
                    ("raise_cross_department_requests", "Can raise requests that route across departments"),
                    ("view_all_requests", "Can view every workflow request and ticket across all offices"),
                    ("view_department_requests", "Can view all workflow requests and tickets raised by or addressed to their own department"),
                    ("view_chairman_dashboard", "Can view the Chairman dashboard"),
                    ("assign_groups", "Can assign groups and direct permissions to roles and users"),
                ],
            },
        ),
        migrations.RunPython(forwards, backwards),
    ]
