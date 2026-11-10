import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("leave", "0003_existing_leave_is_annual")]
    operations = [
        migrations.AlterField(
            model_name="leaveapplication", name="leave_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="applications",
                                    to="leave.leavetype")),
        migrations.AlterField(
            model_name="leaveentitlement", name="leave_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entitlements",
                                    to="leave.leavetype")),
    ]
