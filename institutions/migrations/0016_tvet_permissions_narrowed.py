"""v2.1 X47: adding / editing / dropping TVET institutions and programmes is an
explicit permission (institutions.tvet_access).

Removes the permissions from every role except ``executive-secretary`` and
``super-admin``, gives them to the "QA Desk" / "QA Confirmers" / "QA Senior
Confirmers" groups and attaches those groups to the NTIRD director seat.
Direct per-user grants are untouched. Not reversed automatically: re-grant
through the roles screen if needed.
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
        ("institutions", "0015_document_review"),
        ("accounts", "0020_professional_body_user"),
        ("staff", "0016_a4_tenure_without_staff"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
