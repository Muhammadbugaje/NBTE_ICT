"""F1: every existing entitlement and application becomes annual leave, and
the workflow request type is renamed "Leave application" (it now carries any
leave type; the code stays ``annual-leave`` for the mobile app and links)."""
from django.db import migrations


def forwards(apps, schema_editor):
    LeaveType = apps.get_model("leave", "LeaveType")
    annual, _ = LeaveType.objects.get_or_create(
        code="annual", defaults={"name": "Annual leave", "sort_order": 1,
                                 "description": "[TO CONFIRM] entitlement days"})
    apps.get_model("leave", "LeaveEntitlement").objects.filter(leave_type__isnull=True).update(leave_type=annual)
    apps.get_model("leave", "LeaveApplication").objects.filter(leave_type__isnull=True).update(leave_type=annual)
    RequestType = apps.get_model("workflow", "RequestType")
    RequestType.objects.filter(code="annual-leave", name="Annual leave application").update(name="Leave application")


def backwards(apps, schema_editor):
    RequestType = apps.get_model("workflow", "RequestType")
    RequestType.objects.filter(code="annual-leave", name="Leave application").update(name="Annual leave application")


class Migration(migrations.Migration):
    dependencies = [
        ("leave", "0002_leave_types"),
        ("workflow", "0007_record_pdf_restricted_storage"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
