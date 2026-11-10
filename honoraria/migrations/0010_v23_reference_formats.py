"""v2.3: create the honoraria reference formats on databases seeded before
they existed (fixes "No active reference format 'honoraria'")."""
from django.db import migrations


def forwards(apps, schema_editor):
    from honoraria.references import ensure_formats

    ensure_formats(apps.get_model("qa_core", "ReferenceFormat"))


class Migration(migrations.Migration):

    dependencies = [
        ("honoraria", "0009_v23_honorarium_files"),
        ("qa_core", "0007_v23_settings"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
