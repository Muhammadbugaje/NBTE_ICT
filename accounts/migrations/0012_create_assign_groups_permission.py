"""Stage 3: make sure `accounts.assign_groups` exists as soon as 0012 runs.

Django normally creates Meta.permissions rows in a post_migrate signal, which
runs only after *all* migrations. Creating it here means a later data
migration, a test, or `seed_default_groups` run in the same deploy can rely
on it. Reverse removes the row (0011's reverse then drops it from Meta).
"""
from django.db import migrations

CODENAME = "assign_groups"
NAME = "Can assign groups and direct permissions to roles and users"


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    ct, _ = ContentType.objects.get_or_create(app_label="accounts", model="role")
    Permission.objects.get_or_create(content_type=ct, codename=CODENAME, defaults={"name": NAME})


def backwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.filter(content_type__app_label="accounts", codename=CODENAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_role_groups_groupprofile_assign_groups"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
