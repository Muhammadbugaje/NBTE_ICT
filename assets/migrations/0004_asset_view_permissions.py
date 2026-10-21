"""Asset read-scope permissions.

Adds two permissions to Asset:

    view_all_assets          every asset in every department
    view_department_assets   every asset of the holder's own department

Why: the assets pages used `accounts.access_all_departments` as their "see
everything" switch and every director role carries it, so every director saw
every department's assets, and regular staff saw their whole department's
assets. The rule now lives in assets/access.py and these permissions make
"see everything" a deliberate grant.

The data step creates the permission rows right away (Django normally does it
in post_migrate, after all migrations) and gives them to the standard roles and
to the seeded default groups (a group that does not exist is simply skipped):

    executive-secretary, super-admin
        / groups "Executive Secretary", "Superuser"      -> view_all_assets
    director, deputy-director, office-head-admin/ts/dev
        / groups "Director", "Deputy Director",
          "Office Head"                                  -> view_department_assets

Super admin passes automatically in code; the grant is only so the permission
shows up ticked on the permission screens. Anyone else (HR, an ICT head, ...)
is granted view_all_assets from the permission screens. Reverse removes the
rows again.
"""
from django.db import migrations

PERMS = {
    "view_all_assets": "Can view assets of ALL departments",
    "view_department_assets": "Can view all assets of their own department",
}
ROLE_GRANTS = {
    "view_all_assets": ["executive-secretary", "super-admin"],
    "view_department_assets": [
        "director", "deputy-director",
        "office-head-admin", "office-head-ts", "office-head-dev",
    ],
}
GROUP_GRANTS = {
    "view_all_assets": ["Executive Secretary", "Superuser"],
    "view_department_assets": ["Director", "Deputy Director", "Office Head"],
}


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    Role = apps.get_model("accounts", "Role")

    ct, _ = ContentType.objects.get_or_create(app_label="assets", model="asset")
    for codename, name in PERMS.items():
        perm, _ = Permission.objects.get_or_create(
            content_type=ct, codename=codename, defaults={"name": name},
        )
        for role in Role.objects.filter(code__in=ROLE_GRANTS[codename]):
            role.permissions.add(perm)
        for group in Group.objects.filter(name__in=GROUP_GRANTS[codename]):
            group.permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(
        content_type__app_label="assets", codename__in=list(PERMS),
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0003_attachment_restricted_storage"),
        ("accounts", "0012_create_assign_groups_permission"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="asset",
            options={
                "ordering": ["asset_tag"],
                "permissions": [
                    ("view_all_assets", "Can view assets of ALL departments"),
                    ("view_department_assets",
                     "Can view all assets of their own department"),
                ],
            },
        ),
        migrations.RunPython(forwards, backwards),
    ]
