"""Introduce the Executive Secretary role (rank 999, above every director)
and rebalance the existing director-band ranks so they all sit strictly
below ES. Superusers are handled in code as rank 1000 — no DB change.

Ranks (higher = more senior):
    executive-secretary 999   (above every director)
    director            100   (ICT, Finance, Audit, HR — peers)
    deputy-director      80
    office-head-*        60
    division-head        50
    officer              20
    admin-staff          15
    staff                10
    super-admin           0   (system role — not in the hierarchy)
"""
from django.db import migrations


RANKS = {
    "executive-secretary": 999,
    "director": 100,
    "deputy-director": 80,
    "office-head-admin": 60,
    "office-head-ts": 60,
    "office-head-dev": 60,
    "division-head": 50,
    "officer": 20,
    "admin-staff": 15,
    "staff": 10,
    "super-admin": 0,
}


def apply(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    for code, rank in RANKS.items():
        Role.objects.filter(code=code).update(rank=rank)

    Role.objects.get_or_create(
        code="executive-secretary",
        defaults={
            "name": "Executive Secretary",
            "is_system": True,
            "rank": 999,
            "requires_2fa": True,
        },
    )


def undo(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(code="executive-secretary").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_role_rank"),
    ]

    operations = [
        migrations.RunPython(apply, reverse_code=undo),
    ]