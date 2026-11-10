"""Part 1a: flag (never fix) duplicated single-holder roles.

Counts departments with more than one active ``director`` role holder and
more than one ``executive-secretary``, and writes one audit entry saying so.
The removal is done by ``manage.py fix_duplicate_directors --apply`` after a
dry run, so nobody loses a role silently during ``migrate``.
"""
from django.db import migrations
from django.db.models import Count


def forwards(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    staff = User.objects.filter(is_active=True, user_type="staff")
    depts = (staff.filter(role__code="director").exclude(department=None).values("department_id")
             .annotate(n=Count("pk")).filter(n__gt=1).count())
    es = staff.filter(role__code="executive-secretary").count()
    if not depts and es <= 1:
        return
    note = (f"{depts} department(s) with more than one director; {es} Executive Secretary account(s). "
            "Run: python manage.py fix_duplicate_directors --dry-run")
    print(f"\n  ! {note}")
    try:
        AuditLogEntry = apps.get_model("core", "AuditLogEntry")
        AuditLogEntry.objects.create(action="other", model_label="org.single_holder", object_pk="",
                                     object_repr="Duplicate single-holder roles", changes={}, note=note[:255])
    except Exception:
        pass


class Migration(migrations.Migration):
    dependencies = [
        ("org", "0006_split_access_all_departments"),
        ("accounts", "0020_professional_body_user"),
        ("core", "0005_importrun_importrow_seedrecord"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
