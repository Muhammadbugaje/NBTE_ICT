"""Seed the standard roles. Idempotent.

Each role carries a `rank` (seniority) and a `max_open_tasks` (task
capacity). `--sync` re-applies both plus any missing permissions.
"""
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand

from accounts.models import Role


_VIEW_STAFF = ["staff.view_staff"]
_VIEW_HISTORY = ["staff.view_staffstatushistory"]
_AUDIT_READ = ["core.view_auditlogentry"]
_CROSS_DEPARTMENT = ["accounts.access_all_departments"]
_POWER = ["accounts.view_analytics_dashboard"]
_ASSETS_VIEW = ["assets.view_asset"]
_ASSETS_MANAGE = ["assets.view_asset", "assets.add_asset", "assets.change_asset"]
_WORKFLOW_VIEW_ALL = ["accounts.view_all_requests"]
_WORKFLOW_APPROVE = ["accounts.approve_requests"]


# code, name, requires_2fa, rank, max_open_tasks, permissions
ROLES = [
    (
        "executive-secretary",
        "Executive Secretary",
        True, 999, 100,
        _VIEW_STAFF + _VIEW_HISTORY + _AUDIT_READ + _CROSS_DEPARTMENT + _POWER
        + _ASSETS_MANAGE + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE,
    ),
    (
        "director",
        "Director of Department",
        True, 100, 50,
        _VIEW_STAFF + _VIEW_HISTORY + _AUDIT_READ + _CROSS_DEPARTMENT + _POWER
        + _ASSETS_MANAGE + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE,
    ),
    (
        "deputy-director",
        "Deputy Director",
        True, 80, 30,
        _VIEW_STAFF + _VIEW_HISTORY + _CROSS_DEPARTMENT + _POWER
        + _ASSETS_VIEW + _WORKFLOW_APPROVE,
    ),
    (
        "office-head-ts",
        "Office Head, Technical Support",
        False, 60, 20,
        _VIEW_STAFF + _POWER + _ASSETS_VIEW + _WORKFLOW_APPROVE,
    ),
    (
        "office-head-dev",
        "Office Head, Programming/Dev Support",
        False, 60, 20,
        _VIEW_STAFF + _POWER + _ASSETS_VIEW + _WORKFLOW_APPROVE,
    ),
    (
        "office-head-admin",
        "Office Head, General Administration",
        False, 60, 20,
        [
            "staff.view_staff", "staff.add_staff", "staff.change_staff",
            "staff.change_staff_state", "staff.view_staffstatushistory",
            "staff.view_staffattachment", "staff.add_staffattachment",
        ]
        + _CROSS_DEPARTMENT + _POWER + _ASSETS_MANAGE + _WORKFLOW_APPROVE,
    ),
    (
        "division-head",
        "Division/Department Head",
        False, 50, 12,
        _VIEW_STAFF + _ASSETS_VIEW + _WORKFLOW_APPROVE,
    ),
    (
        "officer",
        "Officer / Technician",
        False, 20, 6,
        _ASSETS_VIEW,
    ),
    (
        "admin-staff",
        "Administrative Staff",
        False, 15, 5,
        ["staff.view_staff", "staff.add_staff", "staff.change_staff"] + _ASSETS_VIEW,
    ),
    (
        "super-admin",
        "Super Admin",
        True, 0, 100,
        [
            "accounts.manage_roles", "accounts.manage_permission_overrides",
            "config.change_configentry", "config.view_configentry",
            "config.add_configentry", "notifications.view_emaillog",
            "staff.view_staff", "staff.add_staff", "staff.change_staff",
            "staff.change_staff_state", "staff.view_staffstatushistory",
        ]
        + _AUDIT_READ + _CROSS_DEPARTMENT + _POWER
        + _ASSETS_MANAGE + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE,
    ),
    (
        "staff",
        "General Staff",
        False, 10, 3,
        _ASSETS_VIEW,
    ),
]


class Command(BaseCommand):
    help = ("Seed the standard roles. --sync also re-applies rank, "
            "max_open_tasks, and adds any missing permissions.")

    def add_arguments(self, parser):
        parser.add_argument("--sync", action="store_true")

    def handle(self, *args, **opts):
        for code, name, twofa, rank, max_open, perms in ROLES:
            role, created = Role.objects.get_or_create(
                code=code,
                defaults={
                    "name": name, "requires_2fa": twofa,
                    "is_system": True, "rank": rank,
                    "max_open_tasks": max_open,
                },
            )
            if created or opts["sync"]:
                if role.rank != rank:
                    role.rank = rank
                    role.save(update_fields=["rank", "updated_at"])
                if role.max_open_tasks != max_open:
                    role.max_open_tasks = max_open
                    role.save(update_fields=["max_open_tasks", "updated_at"])
                for label in perms:
                    app, codename = label.split(".")
                    perm = Permission.objects.filter(
                        content_type__app_label=app, codename=codename
                    ).first()
                    if perm:
                        role.permissions.add(perm)
                    else:
                        self.stderr.write(f"  missing permission {label}")
            self.stdout.write(
                f"{'created' if created else 'exists '} {code} "
                f"(rank {rank}, max_open_tasks {max_open})"
            )