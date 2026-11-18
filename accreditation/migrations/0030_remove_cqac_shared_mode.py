"""v2.3 Phase 2 (owner decision 10): the CQAC "shared" recommendation mode is
removed for good. Every member always votes on their own (CQACVote); the
setting row is deleted so it can no longer be switched back."""
from django.db import migrations


def forwards(apps, schema_editor):
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    ConfigEntry.objects.filter(key="qa.cqac.recommendation_mode").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accreditation", "0029_alter_casechangerequest_status"),
        ("config", "0004_brand_logo"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
