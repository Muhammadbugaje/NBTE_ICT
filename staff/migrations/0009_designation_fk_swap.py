"""Nominal roll, step 3 of 4 — the FK becomes `Staff.designation`.

Forward: drop the old text column `designation`, rename `designation_fk` to
`designation`. Reverse: rename back to `designation_fk` and re-add the text
column (empty); 0008's reverse then refills it from `designation_legacy`.
Kept separate from 0008 so each step reverses independently.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("staff", "0008_designation_fk_data")]

    operations = [
        migrations.RemoveField(model_name="staff", name="designation"),
        migrations.RenameField(model_name="staff", old_name="designation_fk", new_name="designation"),
    ]
