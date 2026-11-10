"""v2.2: a director's account department is the department they direct, and
the Finance seat group always carries the final payment confirmation."""
from django.db import migrations


def forwards(apps, schema_editor):
    Department = apps.get_model("staff", "Department")
    User = apps.get_model("accounts", "User")
    for d in Department.objects.exclude(director=None):
        User.objects.filter(pk=d.director_id).exclude(department_id=d.pk).update(department_id=d.pk)
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    perm = Permission.objects.filter(codename="final_confirm_payment", content_type__app_label="payments").first()
    if perm is None:
        return
    for g in Group.objects.filter(name="Finance – Approvals"):
        g.permissions.add(perm)
    fin = Department.objects.filter(code="finance").first()
    if fin is not None:
        for g in fin.director_groups.all():
            if g.name == "Finance – Approvals":
                g.permissions.add(perm)


class Migration(migrations.Migration):
    dependencies = [("org", "0007_flag_duplicate_single_holders"), ("staff", "0019_v22_merge_offices_data"),
                    ("payments", "0005_v22_seed_type_fees")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
