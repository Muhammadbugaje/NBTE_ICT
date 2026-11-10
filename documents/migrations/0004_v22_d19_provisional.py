from django.db import migrations


def forwards(apps, schema_editor):
    from documents.catalogue import ensure

    ensure(apps)


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_seed_rules_and_backfill")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
