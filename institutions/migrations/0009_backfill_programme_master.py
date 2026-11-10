"""v2.1 R.4 (X44): back-fill the programme master list from existing
programmes and body mappings; link each programme to its master row."""
from django.db import migrations


def forwards(apps, schema_editor):
    from institutions.programme_master import backfill

    ambiguous = backfill(apps.get_model("institutions", "ProgrammeMaster"),
                         apps.get_model("institutions", "Programme"),
                         apps.get_model("institutions", "ProgrammeBodyMapping"),
                         apps.get_model("institutions", "ProgrammeLevel"))
    if ambiguous:
        print(f"\n  Programme master back-fill: {len(ambiguous)} ambiguous (several bodies), left without a body: "
              + "; ".join(ambiguous[:20]))


class Migration(migrations.Migration):
    dependencies = [("institutions", "0008_programme_master")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
