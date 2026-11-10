"""v2.1 X9: fill ComplaintLayer.handling_department from each layer's first
department-type routing rule; layers without one take the plan's default
(portal access and workflow delays → ICT, payments → Finance) when that
department exists. Layers routed to the institution's handling department
stay blank (blank means exactly that)."""
from django.db import migrations

PLAN_DEFAULTS = {"payment-issues": "finance", "portal-access": "ict", "workflow-delays": "ict"}


def fill(apps, schema_editor):
    Layer = apps.get_model("complaints", "ComplaintLayer")
    Rule = apps.get_model("complaints", "RoutingRule")
    Department = apps.get_model("staff", "Department")
    for layer in Layer.objects.filter(handling_department__isnull=True):
        rule = (Rule.objects.filter(layer=layer, target_kind="department", target_department__isnull=False)
                .order_by("order", "id").first())
        dept_id = rule.target_department_id if rule is not None else None
        if dept_id is None and layer.code in PLAN_DEFAULTS:
            dept = Department.objects.filter(code=PLAN_DEFAULTS[layer.code]).first()
            dept_id = dept.pk if dept is not None else None
        if dept_id is not None:
            Layer.objects.filter(pk=layer.pk).update(handling_department_id=dept_id)


class Migration(migrations.Migration):
    dependencies = [("complaints", "0003_x9_x11_routing")]
    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
