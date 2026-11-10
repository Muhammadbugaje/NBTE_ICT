"""Part 1b (X47): rank is not a permission.

Adds the approve layer and the professional-body / programme permissions to
the TVET Support set, removes the whole set from every role but the ES and
super admin (director roles included) and from every other group and seat,
and seeds the three empty groups "TVET Support – Initiators / Confirmers /
Approvers". Direct per-user grants are untouched.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    try:
        create_permissions(apps.get_app_config("institutions"), verbosity=0, apps=apps)
    except Exception:
        pass
    from institutions.tvet_access import apply

    apply(apps.get_model("accounts", "Role"), apps.get_model("auth", "Group"), apps.get_model("auth", "Permission"),
          apps.get_model("staff", "Department"))


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0017_x47_three_layer"),
        ("accounts", "0020_professional_body_user"),
        ("staff", "0016_a4_tenure_without_staff"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
