from django.db import migrations


CONFIG_ENTRIES = [
    ("attendance.manual_checkin_enabled", "true", "bool",
     "Attendance",
     "Enable the code-based manual check-in flow."),
    ("attendance.manual_code_length", "6", "int",
     "Attendance",
     "Length of the rotating office check-in codes."),
    ("attendance.manual_code_rotation_minutes", "30", "int",
     "Attendance",
     "How often office check-in codes rotate, in minutes."),
    ("attendance.manual_code_grace_minutes_before_open", "30", "int",
     "Attendance",
     "Minutes before work start when codes are already valid."),
    ("attendance.manual_code_grace_minutes_after_close", "30", "int",
     "Attendance",
     "Minutes after work end when codes are still valid."),
    ("attendance.manual_code_max_attempts_per_hour", "10", "int",
     "Attendance",
     "Maximum failed code attempts per user per hour."),
]


def seed(apps, schema_editor):
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    for key, value, vtype, group, description in CONFIG_ENTRIES:
        ConfigEntry.objects.update_or_create(
            key=key, scope="global",
            defaults={
                "value": value,
                "value_type": vtype,
                "group": group,
                "description": description,
            },
        )


def unseed(apps, schema_editor):
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    keys = [k for k, _, _, _, _ in CONFIG_ENTRIES]
    ConfigEntry.objects.filter(key__in=keys, scope="global").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0003_locationcheckincode"),  # match your filename
        ("config", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, unseed)]