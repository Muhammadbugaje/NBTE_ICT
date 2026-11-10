"""Organisation data sets for ``seed_nbte`` (X51) and the Phase B migrations.

Every function is an idempotent upsert that never overwrites an edit: it
creates what is missing and fills empty fields only. Each returns
``{"created": n, "updated": n, "unchanged": n}``. Pass ``get_model`` (a
migration's ``apps.get_model``) to run against historical models; without it
the live models are used. ``dry_run=True`` counts without writing.

    from org import seed
    seed.run_all(stdout=print)             # every org data set
    seed.DATA_SETS                         # [(name, function), ...] for seed_nbte

Values marked "to confirm" are placeholders (MASTER_PLAN §4.5, §4.6, §18).
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
# CONTEDISS 01-17. Level 11 is skipped on many federal scales (HR to confirm):
# it is created inactive. Years in grade are left empty for HR to fill.
GRADE_LEVELS = [(n, "CONTEDISS", n != 11,
                 "Level 11 is skipped on many federal scales — to confirm" if n == 11
                 else "Placeholder — designations and years in grade to confirm by HR")
                for n in range(1, 18)]

# Proposed minimum grade per role (MASTER_PLAN §4.6 Table 13) — to confirm.
ROLE_MIN_GRADES = {
    "director": 17, "deputy-director": 16, "division-head": 14,
    "office-head-admin": 14, "office-head-ts": 14, "office-head-dev": 14,
    "officer": 8, "qa-finance-officer": 8, "qa-audit-officer": 8, "ict-qa-monitor": 8,
}

# Permissions created by Phase B (Meta permissions; listed so migrations can
# create them before Django's post_migrate step does).
NEW_PERMISSIONS = [
    ("tasks", "task", "view_all_tasks", "Can see tasks of every department"),
    ("accounts", "role", "view_all_users", "Can see user accounts of every department"),
    ("inspection", "ssqreviewassignment", "view_all_ssq", "QA: can see SSQs of every department"),
    ("org", "grantablepermission", "assign_director", "Can assign and vacate department directors"),
    ("org", "grantablepermission", "manage_offices", "Can manage offices and their permission groups"),
    ("org", "division", "manage_all_divisions", "Can manage divisions of any department"),
    ("org", "gradelevel", "manage_grade_levels", "Can manage grade levels and designations"),
]

# Held by the ES and super admin (B10, H1, H3, T-SEC-3).
ES_PERMISSIONS = [
    "tasks.view_all_tasks", "accounts.view_all_users", "inspection.view_all_ssq",
    "org.assign_director", "org.manage_offices", "org.manage_all_divisions", "org.manage_grade_levels",
]
ES_ROLES = ["executive-secretary", "super-admin"]

# Director-level roles and default groups that lose the cross-department switch.
DIRECTOR_LEVEL_ROLES = ["director", "deputy-director", "division-head",
                        "office-head-admin", "office-head-ts", "office-head-dev"]
DIRECTOR_LEVEL_GROUPS = ["Director", "Deputy Director", "Office Head"]

DIVISION_HEAD_GROUP = ("Division Head",
                       "Given automatically to the head of an active division; removed when they stop heading one.",
                       ["staff.view_staff", "assets.view_asset", "accounts.approve_requests",
                        "accounts.view_department_requests", "attendance.view_department_attendance"])

# The Director of Finance's payment powers come with the Finance seat (B2).
FINANCE_SEAT_GROUP = ("Finance – Approvals",
                      "Director of Finance: final confirmation of payments, waivers, refunds and bank details. "
                      "Inherited by whoever sits in the Finance director seat (qa.payment.finance_department).",
                      ["qa_core.edit_qa_dual_control_settings", "qa_core.view_pending_actions",
                       "payments.second_verify_payment", "payments.final_confirm_payment", "payments.confirm_waiver",
                       "payments.confirm_refund", "payments.manage_fee_schedule", "payments.manage_bank_accounts",
                       "payments.confirm_cancel_obligation", "payments.view_obligation", "payments.view_proof",
                       "payments.view_payment_reports", "qa_core.view_all_institutions",
                       "accreditation.manage_rate_table"])

# X31 allow-list: what a director may grant to own staff (days = max duration).
GRANTABLE_PERMISSIONS = [
    ("assignments.assign_work", 30, "Delegate: assign the department's work (Phase P)"),
    ("assignments.view_department_qa", 30, "Delegate: see the department's QA work (Phase P)"),
    ("accounts.approve_requests", 90, "Approve requests on the director's behalf"),
    ("accounts.view_department_requests", 90, "See the department's requests and tickets"),
    ("attendance.view_department_attendance", 90, "See the department's attendance"),
    ("assets.view_department_assets", 90, "See the department's assets"),
    ("staff.view_staff", 90, "See full staff records of the department"),
    ("qa_core.reassign_qa_task", 30, "Reassign QA tasks in the department"),
    ("accreditation.act_on_qa_stage", 30, "Act on QA stages for the department"),
    ("complaints.assign_complaint", 30, "Assign complaints in the department"),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _models(get_model):
    if get_model is None:
        from django.apps import apps
        get_model = apps.get_model
    return get_model


def _stats():
    return {"created": 0, "updated": 0, "unchanged": 0}


def _perm(get_model, label):
    Permission = get_model("auth", "Permission")
    app, _, codename = label.partition(".")
    return Permission.objects.filter(content_type__app_label=app, codename=codename).first()


# --------------------------------------------------------------------------
# data sets
# --------------------------------------------------------------------------
def seed_permissions(get_model=None, dry_run=False):
    """Create the Phase B permission rows if missing."""
    get_model = _models(get_model)
    Permission = get_model("auth", "Permission")
    ContentType = get_model("contenttypes", "ContentType")
    st = _stats()
    for app, model, codename, name in NEW_PERMISSIONS:
        if Permission.objects.filter(content_type__app_label=app, codename=codename).exists():
            st["unchanged"] += 1
            continue
        st["created"] += 1
        if not dry_run:
            ct, _ = ContentType.objects.get_or_create(app_label=app, model=model)
            Permission.objects.get_or_create(content_type=ct, codename=codename, defaults={"name": name})
    return st


def seed_grade_levels(get_model=None, dry_run=False):
    get_model = _models(get_model)
    GradeLevel = get_model("org", "GradeLevel")
    st = _stats()
    for number, scale, active, notes in GRADE_LEVELS:
        if GradeLevel.objects.filter(number=number).exists():
            st["unchanged"] += 1
            continue
        st["created"] += 1
        if not dry_run:
            GradeLevel.objects.create(number=number, scale=scale, code=f"{scale.lower()}-{number:02d}",
                                      label=f"{scale} {number:02d}", is_active=active, notes=notes)
    return st


def seed_role_min_grades(get_model=None, dry_run=False):
    get_model = _models(get_model)
    Role = get_model("accounts", "Role")
    st = _stats()
    for code, grade in ROLE_MIN_GRADES.items():
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        if role.min_grade is not None:
            st["unchanged"] += 1
            continue
        st["updated"] += 1
        if not dry_run:
            Role.objects.filter(pk=role.pk).update(min_grade=grade)
    return st


def seed_es_permissions(get_model=None, dry_run=False):
    """ES and super admin hold the explicit view_all_* and org permissions."""
    get_model = _models(get_model)
    Role = get_model("accounts", "Role")
    st = _stats()
    for code in ES_ROLES:
        role = Role.objects.filter(code=code).first()
        if role is None:
            continue
        have = set(role.permissions.values_list("pk", flat=True))
        for label in ES_PERMISSIONS:
            perm = _perm(get_model, label)
            if perm is None:
                continue
            if perm.pk in have:
                st["unchanged"] += 1
            else:
                st["created"] += 1
                if not dry_run:
                    role.permissions.add(perm)
    return st


def _group(get_model, name, description, labels, dry_run, st):
    Group = get_model("auth", "Group")
    GroupProfile = get_model("accounts", "GroupProfile")
    group = Group.objects.filter(name=name).first()
    if group is not None:
        # A group this seed created before all its permissions existed (fresh
        # install: Meta permissions appear after migrate) is completed; a group
        # holding anything outside the seed list has been edited and is left alone.
        have = {f"{a}.{c}" for a, c in group.permissions.values_list("content_type__app_label", "codename")}
        if have <= set(labels):
            missing = [p for p in (_perm(get_model, label) for label in labels if label not in have)
                       if p is not None]
            if missing:
                st["updated"] += 1
                if not dry_run:
                    group.permissions.add(*missing)
                return group
        st["unchanged"] += 1
        return group
    st["created"] += 1
    if dry_run:
        return None
    group = Group.objects.create(name=name)
    GroupProfile.objects.get_or_create(group=group, defaults={"description": description})
    perms = [p for p in (_perm(get_model, label) for label in labels) if p is not None]
    group.permissions.set(perms)
    return group


def seed_division_head_group(get_model=None, dry_run=False):
    get_model = _models(get_model)
    st = _stats()
    _group(get_model, *DIVISION_HEAD_GROUP, dry_run, st)
    return st


def seed_finance_seat_group(get_model=None, dry_run=False, finance_code=None):
    """Create the Finance approvals group (if missing) and attach it to the
    Finance department's director seat (qa.payment.finance_department)."""
    get_model = _models(get_model)
    Department = get_model("staff", "Department")
    st = _stats()
    group = _group(get_model, *FINANCE_SEAT_GROUP, dry_run, st)
    if finance_code is None:
        ConfigEntry = get_model("config", "ConfigEntry")
        row = ConfigEntry.objects.filter(key="qa.payment.finance_department").first()
        finance_code = (str(row.value).strip().strip('"') if row is not None and row.value else "") or "finance"
    dept = Department.objects.filter(code=finance_code).first()
    if dept is None:
        try:  # QA alias ("finance" may be stored under another code)
            from qa_core.seed_data import DEPARTMENTS
            for code, name, aliases in DEPARTMENTS:
                if code == finance_code:
                    dept = (Department.objects.filter(code__in=[code, *aliases]).first()
                            or Department.objects.filter(name__iexact=name).first())
                    break
        except Exception:
            pass
    if dept is not None and group is not None:
        if dept.director_groups.filter(pk=group.pk).exists():
            st["unchanged"] += 1
        else:
            st["updated"] += 1
            if not dry_run:
                dept.director_groups.add(group)
    return st


def seed_grantable_permissions(get_model=None, dry_run=False):
    get_model = _models(get_model)
    GrantablePermission = get_model("org", "GrantablePermission")
    st = _stats()
    for label, days, description in GRANTABLE_PERMISSIONS:
        perm = _perm(get_model, label)
        if perm is None:
            continue  # its app arrives in a later phase; picked up on the next run
        if GrantablePermission.objects.filter(permission=perm).exists():
            st["unchanged"] += 1
            continue
        st["created"] += 1
        if not dry_run:
            GrantablePermission.objects.create(permission=perm, max_duration_days=days, description=description)
    return st


DATA_SETS = [
    ("org.permissions", seed_permissions),
    ("org.grade_levels", seed_grade_levels),
    ("org.role_min_grades", seed_role_min_grades),
    ("org.es_permissions", seed_es_permissions),
    ("org.division_head_group", seed_division_head_group),
    ("org.finance_seat_group", seed_finance_seat_group),
    ("org.grantable_permissions", seed_grantable_permissions),
]


# Data sets that need permission rows, re-run after every migrate (post_migrate)
# because on a fresh install Django creates Meta permissions after migrations.
POST_MIGRATE_SETS = [seed_permissions, seed_es_permissions, seed_division_head_group,
                     seed_finance_seat_group, seed_grantable_permissions, seed_role_min_grades]


def run_all(get_model=None, dry_run=False, stdout=None):
    """Run every org data set; returns {name: stats}."""
    out = {}
    for name, func in DATA_SETS:
        out[name] = func(get_model=get_model, dry_run=dry_run)
        if stdout:
            s = out[name]
            stdout(f"{name}: created {s['created']}, updated {s['updated']}, unchanged {s['unchanged']}")
    return out
