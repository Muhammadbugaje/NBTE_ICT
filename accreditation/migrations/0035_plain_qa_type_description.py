"""Institutions saw internal spec jargon ("(Spec §6.3)") in the Institutional
Administration description: replace the seeded text only (an edited one is
left alone)."""
from django.db import migrations

OLD = "The non-academic Institutional Administration 'programme' (Spec §6.3) on the same expiry cycle."
NEW = ("A review of how the institution is run (governance, administration and support services), on the same "
       "cycle as its programmes.")


def fix(apps, schema_editor):
    apps.get_model("accreditation", "QAType").objects.filter(description=OLD).update(description=NEW)


class Migration(migrations.Migration):
    dependencies = [("accreditation", "0034_programme_clearance_task")]
    operations = [migrations.RunPython(fix, migrations.RunPython.noop)]
