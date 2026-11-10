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
# Phase B (B10/T-SEC-3): explicit "see everything" rights. Only the ES and the
# super admin hold them (and _CROSS_DEPARTMENT); directors see their own department.
_SEE_ALL = ["tasks.view_all_tasks", "accounts.view_all_users", "inspection.view_all_ssq"]
_POWER = ["accounts.view_analytics_dashboard"]
_ASSETS_VIEW = ["assets.view_asset"]
_ASSETS_MANAGE = ["assets.view_asset", "assets.add_asset", "assets.change_asset"]
_WORKFLOW_VIEW_ALL = ["accounts.view_all_requests"]
# Department-scoped read access (assets, tickets, requests). Directors, deputies
# and office heads get these; "all" is only for the ES / super admin / explicit grants.
_WORKFLOW_DEPT = ["accounts.view_department_requests"]
_ASSETS_ALL = ["assets.view_all_assets"]
_ASSETS_DEPT = ["assets.view_department_assets"]
_WORKFLOW_APPROVE = ["accounts.approve_requests"]
_ATT_ALL = ["attendance.view_all_attendance"]
_ATT_DEPT = ["attendance.view_department_attendance"]


# code, name, requires_2fa, rank, max_open_tasks, permissions
ROLES = [
    (
        "executive-secretary",
        "Executive Secretary",
        True, 999, 100,
        _VIEW_STAFF + _VIEW_HISTORY + _AUDIT_READ + _CROSS_DEPARTMENT + _SEE_ALL + _POWER
        + _ASSETS_MANAGE + _ASSETS_ALL + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE + _ATT_ALL,
    ),
    (
        "director",
        "Director of Department",
        True, 100, 50,
        _VIEW_STAFF + _VIEW_HISTORY + _AUDIT_READ + _POWER
        + _ASSETS_MANAGE + _ASSETS_DEPT + _WORKFLOW_DEPT + _WORKFLOW_APPROVE + _ATT_DEPT,
    ),
    (
        "deputy-director",
        "Deputy Director",
        True, 80, 30,
        _VIEW_STAFF + _VIEW_HISTORY + _POWER
        + _ASSETS_VIEW + _ASSETS_DEPT + _WORKFLOW_DEPT + _WORKFLOW_APPROVE + _ATT_DEPT,
    ),
    (
        "office-head-ts",
        "Office Head, Technical Support",
        False, 60, 20,
        _VIEW_STAFF + _POWER + _ASSETS_VIEW + _ASSETS_DEPT + _WORKFLOW_DEPT + _WORKFLOW_APPROVE,
    ),
    (
        "office-head-dev",
        "Office Head, Programming/Dev Support",
        False, 60, 20,
        _VIEW_STAFF + _POWER + _ASSETS_VIEW + _ASSETS_DEPT + _WORKFLOW_DEPT + _WORKFLOW_APPROVE,
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
        + _POWER + _ASSETS_MANAGE + _ASSETS_DEPT + _WORKFLOW_DEPT
        + _WORKFLOW_APPROVE,
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
        + _AUDIT_READ + _CROSS_DEPARTMENT + _SEE_ALL + _POWER
        + _ASSETS_MANAGE + _ASSETS_ALL + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE + _ATT_ALL,
    ),
    (
        "staff",
        "General Staff",
        False, 10, 3,
        _ASSETS_VIEW,
    ),
]


# Phase 9: announcements — the Executive Secretary's office and super admins
# post announcements and see acknowledgement reports. Others get it per user.
_ANNOUNCE = ["announcements.post_announcement", "announcements.view_announcement_report"]
ROLES = [(c, n, t, r, m, p + _ANNOUNCE if c in ("executive-secretary", "super-admin") else p)
         for (c, n, t, r, m, p) in ROLES]


# Granted by data migrations, not by an admin. See the fresh-install check below.
_MIGRATION_GRANTED = (
    "view_all_assets", "view_department_assets",
    "view_all_requests", "view_department_requests",
    "view_all_attendance", "view_department_attendance",
    # Phase B: org.seed adds these to the ES / super admin after every migrate.
    "view_all_tasks", "view_all_users", "view_all_ssq",
    "assign_director", "manage_offices", "manage_all_divisions", "manage_grade_levels",
)


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
            # Review M5: a role that exists but has NO permissions yet (the
            # Executive Secretary role is created bare by migration 0007)
            # gets its baseline even without --sync. Roles an admin has
            # already configured are only touched with --sync.
            # Permissions that migrations attach to existing roles at install
            # time (attendance 0005, assets 0004, accounts 0013) do not count
            # as "an admin configured this role".
            configured = role.permissions.exclude(
                codename__in=_MIGRATION_GRANTED).exists()
            fresh = created or not configured
            if fresh or opts["sync"]:
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
        # Phase B (C9, B10): proposed minimum grades (empty ones only) and the
        # ES / super-admin explicit "see all" rights.
        from org import seed as org_seed
        org_seed.seed_role_min_grades()
        org_seed.seed_es_permissions()