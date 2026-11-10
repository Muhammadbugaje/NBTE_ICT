from django.db import migrations


DESCRIPTION = (
    "Stage 14: shared lets one member submit for the CQAC panel; individual requires every assigned member to vote "
    "on every programme. Outcome is by full-panel majority; approval details by majority of approving votes. Ties "
    "return for review."
)


def shorten_description(apps, schema_editor):
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    ConfigEntry.objects.filter(key="qa.cqac.recommendation_mode", scope="global").update(description=DESCRIPTION)


class Migration(migrations.Migration):
    dependencies = [
        ("accreditation", "0026_cqacvote"),
        ("config", "0004_brand_logo"),
    ]

    operations = [
        migrations.RunPython(shorten_description, migrations.RunPython.noop),
    ]
