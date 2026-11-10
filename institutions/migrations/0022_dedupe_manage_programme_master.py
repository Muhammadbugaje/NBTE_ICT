"""`manage_programme_master` was declared in the Meta of *two* institution
models (ProgrammeMaster and BodyProgrammeRequest), so Django created two
Permission rows for the same codename. Product code (institutions/pb_requests)
and tests use Permission.objects.get(codename=...), which then blew up with
MultipleObjectsReturned. This migration keeps the ProgrammeMaster row and
re-points every User / Group grant of the duplicate onto it first, so nobody
loses the right."""
from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    try:
        master_ct = ContentType.objects.get(app_label="institutions", model="programmemaster")
        dupes = list(Permission.objects.filter(
            codename="manage_programme_master", content_type__app_label="institutions"
        ).exclude(content_type=master_ct))
    except Exception:
        return  # content types not ready (first migrate) — ModelPermission syncs later
    canonical, _created = Permission.objects.get_or_create(
        codename="manage_programme_master", content_type=master_ct,
        defaults={"name": "QA: can edit the programme master list and body scopes"})
    if canonical is None:
        return
    for perm in dupes:
        for user in perm.user_set.all():
            user.user_permissions.add(canonical)
            user.user_permissions.remove(perm)
        for group in perm.group_set.all():
            group.permissions.add(canonical)
            group.permissions.remove(perm)
        perm.delete()


def backwards(apps, schema_editor):
    pass  # the duplicate row stays gone


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0021_institution_change_requests"),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
