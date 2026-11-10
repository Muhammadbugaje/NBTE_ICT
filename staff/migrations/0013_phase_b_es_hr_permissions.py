"""Phase B (B.9 / B.11): create the three new staff permissions now (Django
would only create them after all migrations) and give the Executive
Secretary and Super Admin roles the ES-only promotion powers.

    executive-secretary, super-admin: promote_staff, override_promotion_step,
                                      rollback_promotion, print_staff_id

Additive only: no permission is removed from anybody. Reverse removes the
role links (the permission rows go with 0012's reverse).
"""
from django.db import migrations

NEW = {
    "promote_staff": "Can promote staff to a new designation",   # may not exist yet on a fresh DB
    "override_promotion_step": "Can promote past the next step (ES override, with a reason)",
    "rollback_promotion": "Can roll back a promotion (ES only, audited)",
    "print_staff_id": "Can print staff ID cards for other staff",
}
GRANT = ["promote_staff", "override_promotion_step", "rollback_promotion", "print_staff_id"]
ROLES = ["executive-secretary", "super-admin"]


def forwards(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    ct, _ = ContentType.objects.get_or_create(app_label="staff", model="staff")
    for code, name in NEW.items():
        Permission.objects.get_or_create(content_type=ct, codename=code, defaults={"name": name})
    perms = list(Permission.objects.filter(content_type=ct, codename__in=GRANT))
    for role in Role.objects.filter(code__in=ROLES):
        role.permissions.add(*perms)


def backwards(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    perms = Permission.objects.filter(content_type__app_label="staff",
                                      codename__in=[c for c in NEW if c != "promote_staff"])
    for role in Role.objects.filter(code__in=ROLES):
        role.permissions.remove(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0012_phase_b_hr_permissions"),
        ("accounts", "0017_usersession_app_device"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
