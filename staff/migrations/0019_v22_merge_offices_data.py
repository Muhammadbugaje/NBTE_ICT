from django.db import migrations


def forwards(apps, schema_editor):
    from staff.office_merge import merge_offices
    merge_offices(apps.get_model("staff", "Department"), apps.get_model("staff", "Office"),
                  apps.get_model("staff", "Staff"))


class Migration(migrations.Migration):
    dependencies = [("staff", "0018_v22_unify_departments_offices")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
