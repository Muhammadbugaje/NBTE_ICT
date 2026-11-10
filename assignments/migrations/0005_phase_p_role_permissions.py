"""P.4/P.5/P.7: Phase P permissions on roles (spec G1.e).

* executive-secretary: view_all_journeys, reopen_completed_step, view_grants_log,
  view_access_report, revoke_access_grant, assign_work, and
  qa_core.edit_qa_dual_control_settings (the ES confirms dual-control
  changes such as qa.privacy.mode).
* director: assign_work, reopen_completed_step (the service also requires the
  director seat of the owning department).
Directors delegate view_department_qa / assign_work to deputies and division
heads through org.grant_permission (X31 allow-list). Additive; reversible.
"""
from django.db import migrations

GRANTS = {
    "executive-secretary": [("assignments", "view_all_journeys"), ("assignments", "reopen_completed_step"),
                            ("assignments", "view_grants_log"), ("assignments", "view_access_report"),
                            ("assignments", "revoke_access_grant"), ("assignments", "assign_work"),
                            ("qa_core", "edit_qa_dual_control_settings")],
    "director": [("assignments", "assign_work"), ("assignments", "reopen_completed_step")],
}


def _ensure_perms(apps):
    from django.contrib.auth.management import create_permissions
    for label in ("assignments", "qa_core"):
        try:
            create_permissions(apps.get_app_config(label), verbosity=0, apps=apps)
        except Exception:
            pass


def forwards(apps, schema_editor):
    _ensure_perms(apps)
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("auth", "Permission")
    for code, perms in GRANTS.items():
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        for app_label, codename in perms:
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if perm is not None:
                role.permissions.add(perm)


def backwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("auth", "Permission")
    for code, perms in GRANTS.items():
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        for app_label, codename in perms:
            if (app_label, codename) == ("qa_core", "edit_qa_dual_control_settings"):
                continue  # may have been held before
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if perm is not None:
                role.permissions.remove(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("assignments", "0004_backfill"),
        ("accounts", "__first__"),
        ("qa_core", "0004_timeline_department"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
