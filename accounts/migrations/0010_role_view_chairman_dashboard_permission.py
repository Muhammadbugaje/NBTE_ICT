"""Review L1: define the permission the Chairman dashboard checks."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_role_view_all_requests_permission"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="role",
            options={
                "ordering": ["name"],
                "permissions": [
                    ("manage_roles", "Can manage roles and role-permission mappings"),
                    (
                        "manage_permission_overrides",
                        "Can grant/revoke per-user permission overrides",
                    ),
                    (
                        "access_all_departments",
                        "Can see and act across ALL departments",
                    ),
                    (
                        "view_analytics_dashboard",
                        "Can view the full cross-office analytics dashboard",
                    ),
                    ("approve_requests", "Can approve requests assigned to them"),
                    (
                        "raise_cross_department_requests",
                        "Can raise requests that route across departments",
                    ),
                    (
                        "view_all_requests",
                        "Can view every workflow request across all offices",
                    ),
                    ("view_chairman_dashboard", "Can view the Chairman dashboard"),
                ],
            },
        ),
    ]
