"""Seed the default permission groups and link them to roles. Idempotent.

    python manage.py seed_default_groups

* Uses get_or_create by group name. A group that already exists is NEVER
  modified — not its permissions, description or role links ("already
  exists" is printed instead). Delete or rename it first to re-seed.
* Permission sets mirror the role baselines in seed_roles.py and the Stage 1
  review fixes (H8 asset rights, M1 view_all_requests, L1 chairman dashboard),
  so linking a group to its role adds nothing a role-holder didn't already
  have — it makes the access visible and editable as a group.
* Prints a table of what was created or skipped.

Role mapping (there is no Chairman role and no Superuser role in this
project — see GROUPS_PERMISSIONS.md):

  Staff               -> staff, officer, admin-staff
  Director            -> director
  Deputy Director     -> deputy-director
  Office Head         -> office-head-ts, office-head-dev, office-head-admin
  Executive Secretary -> executive-secretary
  Chairman            -> (no role; assign the group to the Chairman's account)
  Superuser           -> super-admin   (is_superuser accounts already hold everything)
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.group_signals import views_are_logging
from accounts.models import GroupProfile, Role

_VIEW_STAFF = ["staff.view_staff"]
_VIEW_HISTORY = ["staff.view_staffstatushistory"]
_AUDIT_READ = ["core.view_auditlogentry"]
_CROSS_DEPARTMENT = ["accounts.access_all_departments"]
_POWER = ["accounts.view_analytics_dashboard"]
_ASSETS_VIEW = ["assets.view_asset"]
_ASSETS_MANAGE = ["assets.view_asset", "assets.add_asset", "assets.change_asset"]
_WORKFLOW_VIEW_ALL = ["accounts.view_all_requests"]
_WORKFLOW_APPROVE = ["accounts.approve_requests"]
_GROUP_ADMIN = ["auth.view_group", "auth.add_group", "auth.change_group",
                "auth.delete_group", "accounts.assign_groups"]

# name, description, permissions, role codes
DEFAULT_GROUPS = [
    ("Staff",
     "Everyday access every member of staff needs.",
     _ASSETS_VIEW,
     ["staff", "officer", "admin-staff"]),
    ("Director",
     "Department director: staff records, audit read, cross-department view, asset management, approvals.",
     _VIEW_STAFF + _VIEW_HISTORY + _AUDIT_READ + _CROSS_DEPARTMENT + _POWER
     + _ASSETS_MANAGE + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE,
     ["director"]),
    ("Deputy Director",
     "Deputy director: staff records, cross-department view, analytics, approvals.",
     _VIEW_STAFF + _VIEW_HISTORY + _CROSS_DEPARTMENT + _POWER + _ASSETS_VIEW + _WORKFLOW_APPROVE,
     ["deputy-director"]),
    ("Office Head",
     "Head of an ICT office: staff directory, analytics, approvals.",
     _VIEW_STAFF + _POWER + _ASSETS_VIEW + _WORKFLOW_APPROVE,
     ["office-head-ts", "office-head-dev", "office-head-admin"]),
    ("Executive Secretary",
     "Executive Secretary: everything a director sees, across the whole Board.",
     _VIEW_STAFF + _VIEW_HISTORY + _AUDIT_READ + _CROSS_DEPARTMENT + _POWER
     + _ASSETS_MANAGE + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE,
     ["executive-secretary"]),
    ("Chairman",
     "Governing Council Chairman: the Chairman dashboard and analytics. Assign to the Chairman's account directly.",
     ["accounts.view_chairman_dashboard"] + _POWER,
     []),
    ("Superuser",
     "System administration: roles, overrides, settings, email log, groups and permissions.",
     ["accounts.manage_roles", "accounts.manage_permission_overrides",
      "accounts.view_user", "accounts.change_user",
      "config.change_configentry", "config.view_configentry", "config.add_configentry",
      "notifications.view_emaillog",
      "staff.view_staff", "staff.add_staff", "staff.change_staff",
      "staff.change_staff_state", "staff.view_staffstatushistory"]
     + _AUDIT_READ + _CROSS_DEPARTMENT + _POWER + _ASSETS_MANAGE
     + _WORKFLOW_VIEW_ALL + _WORKFLOW_APPROVE + _GROUP_ADMIN,
     ["super-admin"]),
]


def _perm(label):
    app, code = label.split(".", 1)
    return Permission.objects.filter(content_type__app_label=app, codename=code).first()


class Command(BaseCommand):
    help = "Create the default permission groups and link them to roles (never overwrites)."

    def handle(self, *args, **opts):
        rows = []
        for name, description, perms, role_codes in DEFAULT_GROUPS:
            if Group.objects.filter(name=name).exists():
                rows.append((name, "already exists", "-", "-"))
                continue
            with transaction.atomic(), views_are_logging():
                group = Group.objects.create(name=name)
                GroupProfile.objects.create(group=group, description=description)
                found, missing = [], []
                for label in dict.fromkeys(perms):
                    p = _perm(label)
                    (found if p else missing).append(p or label)
                group.permissions.set(found)
                roles = list(Role.objects.filter(code__in=role_codes))
                for role in roles:
                    role.groups.add(group)
                from core import audit
                audit.log("create", group, changes={
                    "before": [], "after": sorted(perms), "added": sorted(perms), "removed": [],
                    "roles": [r.code for r in roles],
                }, note="seed_default_groups")
            for label in missing:
                self.stderr.write(f"  {name}: missing permission {label}")
            rows.append((name, "created", str(len(found)),
                         ", ".join(r.code for r in roles) or "(none)"))

        widths = [max(len(r[i]) for r in rows + [("Group", "Status", "Perms", "Roles")]) for i in range(4)]
        line = "  ".join("{:<%d}" % w for w in widths)
        self.stdout.write(line.format("Group", "Status", "Perms", "Roles"))
        self.stdout.write(line.format(*("-" * w for w in widths)))
        for r in rows:
            self.stdout.write(line.format(*r))
