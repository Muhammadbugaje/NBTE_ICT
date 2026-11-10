from django.db import migrations


def forwards(apps, schema_editor):
    from payments.fee_table_seed import seed_type_fees
    # Only a database already in use gets the table switched on (so its bills
    # follow the SOP Annex A figures per category); a fresh database gets the
    # rows from seed_nbte.
    live = apps.get_model("institutions", "Institution").objects.exists()
    if not live:
        return
    seed_type_fees(apps.get_model("institutions", "InstitutionType"),
                   apps.get_model("payments", "InstitutionTypeFee"),
                   apps.get_model("config", "ConfigEntry"), activate=True)


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_v22_institution_type_fees"), ("config", "0004_brand_logo"),
                    ("institutions", "0023_alter_bodyprogrammerequest_options")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
