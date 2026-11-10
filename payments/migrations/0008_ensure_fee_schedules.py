"""Make sure every fee schedule the workflows bill exists.

A database seeded before onboarding v3/v4 had no ``institutional_processing_v3``
schedule, so the moment the last SOP-01 document was approved the flow effect
``create_payment_obligation:institutional_processing_v3`` failed ("Fee schedule
... is missing or inactive") and the application stayed in document review.
Rows that exist are never changed (same defaults as seed_startup)."""
from django.db import migrations

FEE_SCHEDULES = [  # code, name, slip description, formula key, confirmed setting (qa_core.seed_data_phase2)
    ("institutional_processing", "Institutional processing fee", "Institutional processing fee (SOP-01)",
     "qa.fee.institutional_processing", ""),
    ("institutional_processing_v3", "Institutional fee", "Institutional fee (SOP-01)",
     "qa.fee.institutional_processing_v3", ""),
    ("programme_processing", "Programme processing fee", "Programme processing fee",
     "qa.fee.programme_processing", ""),
    ("qa_request_processing", "QA request processing fee", "QA request processing fee (SOP-02)",
     "qa.fee.qa_request_processing", "qa.fee.qa_request_processing.confirmed"),
    ("visit_fine", "Visit report fine", "Fine for falsified items on a visit report (X40)",
     "qa.fee.visit_fine", ""),
]


def forwards(apps, schema_editor):
    FeeSchedule = apps.get_model("payments", "FeeSchedule")
    BankAccount = apps.get_model("payments", "BankAccount")
    # Only a database already in use: a fresh one gets the rows from seed_startup.
    if not FeeSchedule.objects.exists():
        return
    # The old internal name ("… (v3 SOP-01)") showed on institutions' payment pages.
    FeeSchedule.objects.filter(code="institutional_processing_v3", name="Institutional fee (v3 SOP-01)"
                               ).update(name="Institutional fee")
    placeholder = BankAccount.objects.filter(is_placeholder=True).first()
    for code, name, description, formula, confirmed in FEE_SCHEDULES:
        FeeSchedule.objects.get_or_create(
            code=code, defaults={"name": name, "description": description, "formula": formula,
                                 "confirmed_setting": confirmed, "revenue_account": placeholder})


class Migration(migrations.Migration):
    dependencies = [("payments", "0007_v22_indexes")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
