"""v2.4: honoraria batches now go to the ES before the DoF pays, and a payee
can report that a recorded payment never reached them.

Also creates the ``honoraria_teller`` reference format used for the
auto-generated teller number, on databases seeded before it existed.
"""
from django.conf import settings
from django.db import migrations, models


def forwards(apps, schema_editor):
    from honoraria.references import ensure_formats

    ensure_formats(apps.get_model("qa_core", "ReferenceFormat"))


class Migration(migrations.Migration):

    dependencies = [
        ("honoraria", "0010_v23_reference_formats"),
        ("qa_core", "0007_v23_settings"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentschedule",
            name="sent_to_es_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentschedule",
            name="sent_to_es_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL,
                                    related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="paymentschedule",
            name="es_decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentschedule",
            name="es_decided_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL,
                                    related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="paymentschedule",
            name="es_remark",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="paymentscheduleline",
            name="disputed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="paymentscheduleline",
            name="disputed_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL,
                                    related_name="+", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="paymentscheduleline",
            name="dispute_note",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="paymentschedule",
            name="status",
            field=models.CharField(choices=[("draft", "Draft"), ("awaiting_es", "Awaiting ES approval"),
                                            ("approved", "Approved — ready to pay"), ("paid", "Paid"),
                                            ("cancelled", "Cancelled")],
                                   db_index=True, default="draft", max_length=12),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
