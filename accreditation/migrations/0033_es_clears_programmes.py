"""The Executive Secretary clears new programmes too (the fallback when the
handling department has no Director): grant accreditation.clear_qa_programmes
to the ES role. Additive; nothing is removed."""
from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("auth", "Permission")
    perm = Permission.objects.filter(content_type__app_label="accreditation", codename="clear_qa_programmes").first()
    role = Role.objects.filter(code="executive-secretary").first()
    if perm is not None and role is not None:
        role.permissions.add(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("accreditation", "0032_v23_visit_finding_not_working"),
        ("accounts", "0020_professional_body_user"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
