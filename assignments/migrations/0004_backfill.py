"""P.1 (X1): back-fill WorkAssignment rows from the assignment-like fields
already on QA records. Additive and idempotent; reversing is a no-op (rows
are never deleted). Counts: docs/master_plan_v2_1/reports/work_assignment_backfill.md
(``manage.py backfill_work_assignments --dry-run --write``)."""
from django.db import migrations


def forwards(apps, schema_editor):
    from assignments import backfill
    backfill.run(apps)


class Migration(migrations.Migration):
    dependencies = [
        ("assignments", "0003_rotation_and_chains"),
        ("accreditation", "0009_p0_access_changes"),
        ("inspection", "0003_view_all_ssq"),
        ("complaints", "0002_institution_media_folders"),
        ("institutions", "0007_institution_lga_ref_institutionaddress_lga_ref"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
