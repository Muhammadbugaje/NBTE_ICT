"""C7: map the free-text Staff.grade_level onto GradeLevel rows. Values that
cannot be mapped stay as text (grade empty); `manage.py plan_grade_levels`
lists them."""
from django.db import migrations


def forward(apps, schema_editor):
    from org.services import parse_grade_number
    Staff = apps.get_model("staff", "Staff")
    GradeLevel = apps.get_model("org", "GradeLevel")
    levels = {g.number: g.pk for g in GradeLevel.objects.all()}
    for s in Staff.objects.filter(grade__isnull=True).exclude(grade_level=""):
        n = parse_grade_number(s.grade_level)
        if n in levels:
            Staff.objects.filter(pk=s.pk).update(grade_id=levels[n])


class Migration(migrations.Migration):
    dependencies = [("org", "0002_seed_org_reference")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
