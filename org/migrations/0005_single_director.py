"""B1: seat one director per department (rule in org/director_plan.py; the
dry-run report is docs/master_plan_v2_1/reports/single_director_plan.md).
Other director-role holders keep their role but not the seat."""
from django.db import migrations


def forward(apps, schema_editor):
    from org import director_plan
    director_plan.apply(apps.get_model("staff", "Department"), apps.get_model("accounts", "User"),
                        apps.get_model("staff", "Staff"))


class Migration(migrations.Migration):
    dependencies = [("org", "0004_remove_qa_finance_director")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
