"""Approved institutions still pointing at the onboarding department (NTIRD)
move to their category's department (Master Plan §5.2): NTIRD handles only
institutions that are not approved yet. Without this, SSQ review and the QA
case of a newly approved institution went to NTIRD."""
from django.db import migrations


def repair(apps, schema_editor):
    Institution = apps.get_model("institutions", "Institution")
    Department = apps.get_model("staff", "Department")
    ntird = Department.objects.filter(code__iexact="ntird").first()
    if ntird is None:
        return
    rows = (Institution.objects.filter(handling_department=ntird)
            .exclude(status__in=("proposed", "under_review"))
            .select_related("category", "institution_type"))
    for inst in rows:
        dept_id = None
        if inst.category_id and inst.category.routing_department_id:
            dept_id = inst.category.routing_department_id
        elif inst.institution_type_id and inst.institution_type.default_department_id:
            dept_id = inst.institution_type.default_department_id
        if dept_id and dept_id != ntird.pk:
            Institution.objects.filter(pk=inst.pk).update(handling_department_id=dept_id)


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0026_discipline_institution_types"),
    ]

    operations = [migrations.RunPython(repair, migrations.RunPython.noop)]
