"""SOP-02 programme clearance becomes a task in the clearer's My work (like the
SOP-03 stages). Published flows are not re-seeded, so the stage flag is set on
every existing qa_request flow version here. Additive."""
from django.db import migrations


def flag(apps, schema_editor):
    FlowStage = apps.get_model("qa_core", "FlowStage")
    FlowStage.objects.filter(flow__code="qa_request", code="programme_clearance").update(creates_task=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accreditation", "0033_es_clears_programmes"),
        ("qa_core", "0007_v23_settings"),
    ]

    operations = [migrations.RunPython(flag, migrations.RunPython.noop)]
