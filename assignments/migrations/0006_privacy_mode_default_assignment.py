"""X1: need-to-know (``qa.privacy.mode = assignment``) is the default for new installs.

Creates the setting row only when it does not exist yet (a fresh database).
An existing install keeps whatever it has — moving it from ``department`` to
``assignment`` stays a dual-control switch (``manage.py privacy_mode``).
"""
from django.db import migrations

KEY = "qa.privacy.mode"


def forwards(apps, schema_editor):
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    if ConfigEntry.objects.filter(key=KEY, scope="global").exists():
        return
    from qa_core.settings_catalogue import SETTINGS
    row = next((r for r in SETTINGS if r[0] == KEY), None)
    if row is None:
        return
    _key, value, vtype, group, desc, editable_by, dual = row
    ConfigEntry.objects.create(key=KEY, scope="global", value=value, value_type=vtype, group=group,
                               description=desc, editable_by=editable_by or "", dual_control=bool(dual))


class Migration(migrations.Migration):
    dependencies = [
        ("assignments", "0005_phase_p_role_permissions"),
        ("config", "0004_brand_logo"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
