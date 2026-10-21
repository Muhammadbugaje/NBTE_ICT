"""Attendance report permissions.

Adds two permissions to AttendanceRecord:

    view_all_attendance          every department's attendance + dashboards
    view_department_attendance   the holder's own department only

Why: attendance used `accounts.access_all_departments` as its "see everyone"
switch, and every director role carries that permission, so every director
could open every department's attendance. The new permissions make that a
deliberate grant.

The data step creates the permission rows right away (Django normally does it
in post_migrate, after all migrations) and gives them to the standard roles:

    executive-secretary, super-admin   -> view_all_attendance
    director, deputy-director          -> view_department_attendance

Anyone else (for example HR) is granted view_all_attendance from the
permission screens. Reverse removes the rows again.
"""
from django.db import migrations

PERMS = {
    "view_all_attendance":
        "Can view attendance reports and dashboards for ALL departments",
    "view_department_attendance":
        "Can view attendance reports and dashboards for their own department",
}
ROLE_GRANTS = {
    "view_all_attendance": ["executive-secretary", "super-admin"],
    "view_department_attendance": ["director", "deputy-director"],
}
CONFIG_KEY = "attendance.all_access_department_codes"


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    ConfigEntry = apps.get_model("config", "ConfigEntry")

    ct, _ = ContentType.objects.get_or_create(
        app_label="attendance", model="attendancerecord",
    )
    for codename, name in PERMS.items():
        perm, _ = Permission.objects.get_or_create(
            content_type=ct, codename=codename, defaults={"name": name},
        )
        for role in Role.objects.filter(code__in=ROLE_GRANTS[codename]):
            role.permissions.add(perm)

    ConfigEntry.objects.get_or_create(
        key=CONFIG_KEY, scope="global",
        defaults={
            "value": '["hr"]',
            "value_type": "json",
            "group": "Attendance",
            "description": (
                "Department codes whose directors / office heads may view "
                "attendance for ALL departments (default: Human Resources). "
                "Anyone else is given the 'view_all_attendance' permission "
                "explicitly."
            ),
        },
    )


def backwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    Permission.objects.filter(
        content_type__app_label="attendance", codename__in=list(PERMS),
    ).delete()
    ConfigEntry.objects.filter(key=CONFIG_KEY, scope="global").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0004_seed_manual_checkin_config"),
        ("accounts", "0012_create_assign_groups_permission"),
        ("config", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="attendancerecord",
            options={
                "ordering": ["-date", "user__first_name", "user__last_name"],
                "permissions": [
                    ("view_all_attendance",
                     "Can view attendance reports and dashboards for ALL departments"),
                    ("view_department_attendance",
                     "Can view attendance reports and dashboards for their own department"),
                ],
                "verbose_name": "Attendance record",
                "verbose_name_plural": "Attendance records",
            },
        ),
        migrations.RunPython(forwards, backwards),
    ]
