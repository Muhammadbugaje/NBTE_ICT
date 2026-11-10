"""v2.2: add the settings introduced in v2.2 to an existing database (never
changes an existing value). Only the v2.2 keys: older keys whose default
depends on the environment (e.g. qa.payment.*.enabled) are left to seed_nbte."""
from django.db import migrations


def forwards(apps, schema_editor):
    from qa_core.settings_catalogue import SETTINGS_V22

    ConfigEntry = apps.get_model("config", "ConfigEntry")
    if not ConfigEntry.objects.filter(key__startswith="qa.").exists():
        return  # fresh database: seed_nbte creates every setting
    for key, value, vtype, group, desc, editable_by, dual in SETTINGS_V22:
        value = value() if callable(value) else value
        ConfigEntry.objects.get_or_create(
            key=key, scope="global",
            defaults={"value": value, "value_type": vtype, "group": group, "description": desc,
                      "editable_by": editable_by, "dual_control": dual})


class Migration(migrations.Migration):
    dependencies = [("qa_core", "0005_x47_three_layer"), ("config", "0004_brand_logo")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
