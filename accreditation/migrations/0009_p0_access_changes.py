"""Phase 0 access changes (data only). Dry-run report first:
``python manage.py plan_p0_access`` → docs/master_plan_v2_1/reports/p0_access_dry_run.md.

* O10 / T-SEC-7: the ``officer`` role no longer holds
  ``accreditation.decide_departmental_clearance``; the Executive Secretary
  role gets it (``can_decide`` = director of the clearance department, a
  ClearanceDelegate, the ES or a superuser).
* X12 / decision 32 (D-3, default "yes"): ``qa.ssq.submit_roles`` drops
  ``inst-admin`` on existing installs — only the Chief Executive submits.

Reversible: the reverse puts both back exactly.
"""
import json

from django.db import migrations

PERM = ("accreditation", "decide_departmental_clearance")
SSQ_KEY = "qa.ssq.submit_roles"


def _perm(apps):
    Permission = apps.get_model("auth", "Permission")
    return Permission.objects.filter(content_type__app_label=PERM[0], codename=PERM[1]).first()


def _roles(apps, code):
    Role = apps.get_model("accounts", "Role")
    return Role.objects.filter(code=code).first()


def _ssq_rows(apps):
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    return ConfigEntry.objects.filter(key=SSQ_KEY)


def forwards(apps, schema_editor):
    perm = _perm(apps)
    if perm is not None:
        officer = _roles(apps, "officer")
        if officer is not None:
            officer.permissions.remove(perm)
        es = _roles(apps, "executive-secretary")
        if es is not None:
            es.permissions.add(perm)
    for row in _ssq_rows(apps):
        try:
            roles = json.loads(row.value or "[]")
        except ValueError:
            continue
        if isinstance(roles, list) and "inst-admin" in roles:
            row.value = json.dumps([r for r in roles if r != "inst-admin"] or ["inst-chief-executive"])
            row.save(update_fields=["value"])


def backwards(apps, schema_editor):
    perm = _perm(apps)
    if perm is not None:
        officer = _roles(apps, "officer")
        if officer is not None:
            officer.permissions.add(perm)
        es = _roles(apps, "executive-secretary")
        if es is not None:
            es.permissions.remove(perm)
    for row in _ssq_rows(apps):
        try:
            roles = json.loads(row.value or "[]")
        except ValueError:
            continue
        if isinstance(roles, list) and "inst-admin" not in roles:
            row.value = json.dumps(roles + ["inst-admin"])
            row.save(update_fields=["value"])


class Migration(migrations.Migration):
    dependencies = [
        ("accreditation", "0008_p0_clearance_delegate_programme_reviews"),
        ("accounts", "0017_usersession_app_device"),
        ("config", "0003_qa_value_types_dual_control"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
