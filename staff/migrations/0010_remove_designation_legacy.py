"""Nominal roll, step 4 of 4 — remove the `designation_legacy` safety column.

Shipped after the FK migration was verified (forward, reverse and forward
again on a fresh database and on a copy of the live database).
Reverse re-adds the column and refills it from each staff member's
Designation name, so 0009/0008 can still be reversed afterwards.
"""
from django.db import migrations, models


def refill_legacy(apps, schema_editor):
    Staff = apps.get_model("staff", "Staff")
    for s in Staff._base_manager.select_related("designation").only("pk", "designation__name"):
        name = s.designation.name if s.designation_id else ""
        Staff._base_manager.filter(pk=s.pk).update(designation_legacy=name)


class Migration(migrations.Migration):

    dependencies = [("staff", "0009_designation_fk_swap")]

    operations = [
        # Listed first so that, in reverse, the column is re-added before the refill runs.
        migrations.RunPython(migrations.RunPython.noop, refill_legacy),
        migrations.RemoveField(model_name="staff", name="designation_legacy"),
    ]
