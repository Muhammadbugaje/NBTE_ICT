"""v2.3 (owner decision 1): "Cleared for QA" after SOP-01; "Approved" only at the
end of the first SOP-03. Back-fill: a proposed / under-review institution that
already has a QA case is cleared for QA."""
from django.db import migrations, models


def backfill(apps, schema_editor):
    Institution = apps.get_model("institutions", "Institution")
    QACase = apps.get_model("accreditation", "QACase")
    ids = set(QACase.objects.values_list("institution_id", flat=True))
    for inst in Institution.objects.filter(pk__in=ids, status__in=("proposed", "under_review"),
                                           cleared_for_qa_at__isnull=True):
        first = QACase.objects.filter(institution_id=inst.pk).order_by("created_at").first()
        inst.cleared_for_qa_at = first.created_at if first else None
        inst.save(update_fields=["cleared_for_qa_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0024_professionalbodymember"),
        ("accreditation", "0030_remove_cqac_shared_mode"),
    ]
    operations = [
        migrations.AddField(model_name="institution", name="cleared_for_qa_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
