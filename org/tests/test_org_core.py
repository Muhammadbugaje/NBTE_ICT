"""Phase B organisation core: one director per department, divisions, offices,
grade levels, role assignment rules and director grants (B1-B10, C7-C12, X31)."""
import datetime
import io
import pathlib
import re

import pytest
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

import org
from accounts.models import Role, User, UserPermissionOverride
from config.models import ConfigEntry
from core.models import AuditLogEntry
from org.models import Division, GradeLevel, GrantablePermission
from staff.models import Department, Designation, Office, Staff

pytestmark = pytest.mark.django_db
PW = "Org-test-pass-2026!"


@pytest.fixture
def roles():
    call_command("seed_roles", "--sync", stdout=io.StringIO())
    return {r.code: r for r in Role.objects.all()}


@pytest.fixture
def depts():
    return {
        "ict": Department.objects.create(name="ICT (t)", code="ict"),
        "hr": Department.objects.create(name="HR (t)", code="hr"),
        "fin": Department.objects.create(name="Finance (t)", code="finance"),
    }


_n = {"i": 0}


def mk(email, role=None, dept=None, grade=None, designation=None, superuser=False):
    user = User.objects.create_user(email, PW, first_name=email.split("@")[0].title(), last_name="T",
                                    role=Role.objects.filter(code=role).first() if role else None,
                                    department=dept, must_change_password=False,
                                    is_superuser=superuser, is_staff=superuser)
    _n["i"] += 1
    staff = Staff.objects.filter(user=user).first()  # accounts.signals may create it
    if staff is None:
        staff = Staff(user=user, employee_no=f"T{_n['i']:05d}", first_name=user.first_name, last_name="T")
    staff.department = dept
    staff.grade_level = str(grade) if grade else ""
    staff.designation = designation
    staff.save()
    return User.objects.get(pk=user.pk)


def fresh(u):
    return User.objects.get(pk=u.pk)


# ---------------------------------------------------------------------------
# B1 / B4: one director
# ---------------------------------------------------------------------------
def test_b1_unique_director_constraint(roles, depts):
    d = mk("d@t.local", "director", depts["ict"])
    assert depts["ict"].__class__.objects.get(pk=depts["ict"].pk).director_id == d.pk  # seat filled
    with pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.filter(pk=depts["hr"].pk).update(director=d)


def test_b1_second_director_role_holder_gets_no_seat(roles, depts):
    first = mk("d1@t.local", "director", depts["ict"])
    second = mk("d2@t.local", "director", depts["ict"])
    assert org.director_of(depts["ict"]) == first
    assert org.is_director(first, depts["ict"]) and not org.is_director(second, depts["ict"])
    assert org.director_of("ict") == first and org.director_of(depts["ict"].pk) == first


def test_b4_assign_refused_when_occupied(roles, depts):
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    first = mk("d1@t.local", "director", depts["ict"])
    second = mk("d2@t.local", "deputy-director", depts["ict"])
    with pytest.raises(org.OrgRuleError, match="Vacate the current director first"):
        org.assign_director(depts["ict"], second, es)
    assert org.director_of(depts["ict"]) == first


def test_b4_vacate_then_assign(roles, depts):
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    first = mk("d1@t.local", "director", depts["ict"])
    second = mk("d2@t.local", "deputy-director", depts["ict"])
    with pytest.raises(org.OrgRuleError, match="reason"):
        org.vacate_director(depts["ict"], es, "")
    org.vacate_director(depts["ict"], es, "Retired", new_role="deputy-director")
    assert org.director_of(depts["ict"]) is None and fresh(first).role.code == "deputy-director"
    org.assign_director(depts["ict"], second, es, "Appointed")
    second = fresh(second)
    assert org.director_of(depts["ict"]) == second and second.role.code == "director"
    actions = set(AuditLogEntry.objects.values_list("action", flat=True))
    assert {"director_assigned", "director_vacated"} <= actions


def test_b4_only_managers_assign_and_person_must_be_in_department(roles, depts):
    officer = mk("o@t.local", "officer", depts["ict"])
    outsider = mk("x@t.local", "officer", depts["hr"])
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    with pytest.raises(org.OrgRuleError):
        org.assign_director(depts["ict"], officer, officer)
    with pytest.raises(org.OrgRuleError, match="Transfer"):
        org.assign_director(depts["ict"], outsider, es)


def test_b1_plan_picks_senior_most_and_lists_others(roles, depts):
    from org import director_plan

    junior = Designation.objects.create(name="Asst Director (t)", rank=85)
    senior = Designation.objects.create(name="Director (t)", rank=95)
    ConfigEntry.objects.create(key="org.auto_seat_director", value="false", value_type="bool")
    a = mk("a@t.local", "director", depts["ict"], designation=junior)
    b = mk("b@t.local", "director", depts["ict"], designation=senior)
    rows = {r["code"]: r for r in director_plan.plan(Department, User, Staff)}
    assert rows["ict"]["chosen"]["user_id"] == b.pk
    assert [o["user_id"] for o in rows["ict"]["others"]] == [a.pk]
    assert Department.objects.get(pk=depts["ict"].pk).director_id is None  # dry run
    out = io.StringIO()
    call_command("plan_single_director", "--apply", "--report", str(pathlib.Path("/tmp") / "sdp_test.md"),
                 stdout=out)
    assert Department.objects.get(pk=depts["ict"].pk).director_id == b.pk
    assert "Staff #" in out.getvalue()  # non-demo names are hidden in the report


# ---------------------------------------------------------------------------
# B2: the Finance seat, not a role
# ---------------------------------------------------------------------------
def test_b2_seat_groups_follow_the_seat(roles, depts):
    group = Group.objects.create(name="Seat perms (t)")
    perm = Permission.objects.get(codename="view_all_tasks")
    group.permissions.add(perm)
    depts["fin"].director_groups.add(group)
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    fd = mk("fd@t.local", "director", depts["fin"])
    other = mk("od@t.local", "director", depts["ict"])
    assert fresh(fd).has_perm("tasks.view_all_tasks")
    assert not fresh(other).has_perm("tasks.view_all_tasks")
    org.vacate_director(depts["fin"], es, "Moved")
    assert not fresh(fd).has_perm("tasks.view_all_tasks")


def test_org_director_of_used_everywhere():
    """Static check (B3/T-CONS-1): no inline director role checks or the old
    finance-director role outside org, tests, seeds and migrations."""
    root = pathlib.Path(__file__).resolve().parents[2]
    bad = re.compile(r"role\.code ==|role__code=\"director\"|role__code=\"qa-finance-director\"|qa-finance-director")
    hits = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if (rel.startswith(("org/", "docs/", ".venv")) or "/migrations/" in rel or "/tests/" in rel
                or "/test_" in rel or rel.startswith("test_") or "seed" in rel or rel.endswith("conftest.py")
                or "/management/commands/" in rel or rel.endswith("/demo.py")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if bad.search(line):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, "\n".join(hits)


# ---------------------------------------------------------------------------
# B10 / H1: directors see their own department only
# ---------------------------------------------------------------------------
def test_b10_director_roles_lose_access_all_departments(roles):
    perm = Permission.objects.get(codename="access_all_departments")
    for code in ("director", "deputy-director", "division-head", "office-head-admin"):
        assert not roles[code].permissions.filter(pk=perm.pk).exists(), code
    for code in ("executive-secretary", "super-admin"):
        role = Role.objects.get(code=code)
        for label in ("view_all_tasks", "view_all_users", "view_all_ssq"):
            assert role.permissions.filter(codename=label).exists(), (code, label)


def test_b10_director_user_list_scoped(roles, depts):
    from accounts.user_views import _users_visible_to

    d = mk("d@t.local", "director", depts["ict"])
    mk("o1@t.local", "officer", depts["ict"])
    mk("o2@t.local", "officer", depts["hr"])
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    assert set(_users_visible_to(d).values_list("department__code", flat=True)) == {"ict"}
    assert {"ict", "hr"} <= set(_users_visible_to(fresh(es)).values_list("department__code", flat=True))


def test_h1_director_sees_only_department_tasks(roles, depts):
    from tasks.models import Task
    from tasks.views import _visible_tasks_qs

    d = mk("d@t.local", "director", depts["ict"])
    hr_officer = mk("o@t.local", "officer", depts["hr"])
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    mine = Task.objects.create(reference="T-ORG-1", title="ICT work", created_by=d, owner_department=depts["ict"])
    theirs = Task.objects.create(reference="T-ORG-2", title="HR work", created_by=hr_officer,
                                 owner_department=depts["hr"])
    seen = set(_visible_tasks_qs(d).values_list("pk", flat=True))
    assert mine.pk in seen and theirs.pk not in seen
    assert {mine.pk, theirs.pk} <= set(_visible_tasks_qs(fresh(es)).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# B6-B8: divisions
# ---------------------------------------------------------------------------
def test_b6_director_creates_division_in_own_department_only(roles, depts):
    d = mk("d@t.local", "director", depts["ict"])
    div = org.create_division(depts["ict"], "Technical Support", d, max_open_tasks=4)
    assert div.department == depts["ict"] and div.code == "technical-support" and div.max_open_tasks == 4
    with pytest.raises(org.OrgRuleError):
        org.create_division(depts["hr"], "Payroll", d)
    with pytest.raises(org.OrgRuleError, match="already has"):
        org.create_division(depts["ict"], "technical support", d)


def test_b7_head_gets_and_loses_group(roles, depts):
    d = mk("d@t.local", "director", depts["ict"])
    officer = mk("o@t.local", "officer", depts["ict"])
    outsider = mk("x@t.local", "officer", depts["hr"])
    div = org.create_division(depts["ict"], "Dev Support", d)
    with pytest.raises(org.OrgRuleError, match="member of this department"):
        org.set_division_head(div, outsider, d)
    org.set_division_head(div, officer, d)
    officer = fresh(officer)
    assert officer.groups.filter(name=org.DIVISION_HEAD_GROUP).exists()
    assert officer.has_perm("accounts.approve_requests")
    assert org.division_of(officer) == div and officer in org.heads_of(depts["ict"])
    org.set_division_head(div, None, d)
    officer = fresh(officer)
    assert not officer.groups.filter(name=org.DIVISION_HEAD_GROUP).exists()
    assert not officer.has_perm("accounts.approve_requests")


def test_b8_division_capacity(roles, depts):
    from tasks.models import Task, TaskAssignee

    d = mk("d@t.local", "director", depts["ict"])
    officer = mk("o@t.local", "officer", depts["ict"])
    div = org.create_division(depts["ict"], "Ops", d, max_open_tasks=1)
    org.move_to_division(officer.staff_profile, div, d)
    assert org.division_has_capacity(div)
    t = Task.objects.create(reference="T-ORG-3", title="One", created_by=d, owner_department=depts["ict"])
    TaskAssignee.objects.create(task=t, user=officer)
    # Phase C: capacity counts group tasks routed to the division, not members' own tasks.
    assert org.division_open_task_count(div) == 0 and org.division_has_capacity(div)
    t.division, t.division_state, t.state = div, Task.DIV_ACCEPTED, Task.OPEN
    t.save()
    assert org.division_open_task_count(div) == 1 and not org.division_has_capacity(div)


# ---------------------------------------------------------------------------
# B9: offices carry permission groups
# ---------------------------------------------------------------------------
def test_b9_office_groups_in_effective_permissions(roles, depts):
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    officer = mk("o@t.local", "officer", depts["ict"])
    office = Office.objects.create(name="JAMB Desk (t)")
    group = Group.objects.create(name="JAMB desk perms (t)")
    group.permissions.add(Permission.objects.get(codename="view_department_requests"))
    org.set_office_groups(office, [group], es)
    assert not fresh(officer).has_perm("accounts.view_department_requests")
    staff = officer.staff_profile
    staff.office = office
    staff.save()
    assert fresh(officer).has_perm("accounts.view_department_requests")
    staff.office = None
    staff.save()  # transfer out
    assert not fresh(officer).has_perm("accounts.view_department_requests")


def test_b9_office_groups_subset_rule(roles, depts):
    hr = mk("hr@t.local", "officer", depts["hr"])
    hr.user_permissions.add(Permission.objects.get(codename="manage_offices"))
    office = Office.objects.create(name="Desk (t)")
    group = Group.objects.create(name="Big (t)")
    group.permissions.add(Permission.objects.get(codename="view_all_tasks"))
    with pytest.raises(org.OrgRuleError, match="do not hold"):
        org.set_office_groups(office, [group], fresh(hr))


# ---------------------------------------------------------------------------
# C7: grade levels
# ---------------------------------------------------------------------------
def test_c7_grade_levels_seeded_as_placeholders():
    assert GradeLevel.objects.filter(scale="CONTEDISS").count() == 17
    assert not GradeLevel.objects.get(number=11).is_active
    assert "to confirm" in GradeLevel.objects.get(number=8).notes


def test_c7_grade_text_migrates_to_fk(roles, depts):
    u = mk("o@t.local", "officer", depts["ict"], grade="GL 08")
    assert u.staff_profile.grade.number == 8
    assert org.parse_grade_number("CONTEDISS 15") == 15 and org.parse_grade_number("n/a") is None
    s = Staff.objects.create(employee_no="TX1", first_name="A", last_name="B", grade_level="Level ??")
    assert s.grade is None
    out = io.StringIO()
    call_command("plan_grade_levels", stdout=out)
    assert "`Level ??`" in out.getvalue()


# ---------------------------------------------------------------------------
# C9-C12: assign_role
# ---------------------------------------------------------------------------
def _hr_officer(depts):
    hr = mk("hr@t.local", "officer", depts["hr"])
    hr.user_permissions.add(Permission.objects.get(codename="change_user"))
    hr.user_permissions.add(Permission.objects.get(codename="view_all_users"))
    return fresh(hr)


def test_c9_role_refused_for_low_grade(roles, depts):
    ConfigEntry.objects.create(key="hr.grade_rules_enabled", value="true", value_type="bool")
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    target = mk("o@t.local", "officer", depts["ict"], grade=8)
    with pytest.raises(org.OrgRuleError, match="grade level 17"):
        org.assign_role(es, target, "director")
    assert fresh(target).role.code == "officer"


def test_c10_hr_cannot_grant_unheld_role(roles, depts):
    hr = _hr_officer(depts)
    target = mk("o@t.local", "staff", depts["ict"])
    problems = org.role_problems(hr, target, roles["admin-staff"])
    assert any("do not hold" in p for p in problems)
    with pytest.raises(org.OrgRuleError):
        org.assign_role(hr, target, "admin-staff")
    # A role whose permissions HR holds is fine.
    hr.user_permissions.add(*Permission.objects.filter(roles=roles["admin-staff"]))
    org.assign_role(fresh(hr), target, "admin-staff", source="test")
    assert fresh(target).role.code == "admin-staff"


def test_c11_director_scope(roles, depts):
    d = mk("d@t.local", "director", depts["ict"])
    own = mk("o@t.local", "staff", depts["ict"])
    other = mk("x@t.local", "staff", depts["hr"])
    org.assign_role(d, own, "officer")
    assert fresh(own).role.code == "officer"
    with pytest.raises(org.OrgRuleError, match="own department"):
        org.assign_role(d, other, "officer")


def test_c12_es_override_audited(roles, depts):
    ConfigEntry.objects.create(key="hr.grade_rules_enabled", value="true", value_type="bool")
    es = mk("es@t.local", "executive-secretary", depts["hr"])
    target = mk("o@t.local", "officer", depts["ict"], grade=8)
    with pytest.raises(org.OrgRuleError, match="reason"):
        org.assign_role(es, target, "deputy-director", override=True)
    org.assign_role(es, target, "deputy-director", "Acting appointment", override=True)
    assert fresh(target).role.code == "deputy-director"
    entry = AuditLogEntry.objects.filter(action="role_override").latest("timestamp")
    assert entry.note == "Acting appointment" and entry.changes["overridden"]
    # A non-ES actor cannot override.
    hr = _hr_officer(depts)
    with pytest.raises(org.OrgRuleError):
        org.assign_role(hr, target, "director", "x", override=True)


def test_c9_admin_and_role_form_use_assign_role(roles, depts, client):
    hr = _hr_officer(depts)
    target = mk("o@t.local", "staff", depts["ict"])
    client.force_login(hr)
    r = client.post(f"/accounts/users/{target.pk}/roles/", {"role": roles["director"].pk})
    assert r.status_code in (302, 200)
    assert fresh(target).role.code == "staff"  # refused: HR does not hold the director's permissions


# ---------------------------------------------------------------------------
# X31: director grants
# ---------------------------------------------------------------------------
def test_x31_grant_subset_and_expiry(roles, depts):
    d = mk("d@t.local", "director", depts["ict"])
    officer = mk("o@t.local", "officer", depts["ict"])
    outsider = mk("x@t.local", "officer", depts["hr"])
    label = "accounts.approve_requests"
    gp = GrantablePermission.objects.get(permission__codename="approve_requests")
    gp.max_duration_days = 30
    gp.save()
    assert d.has_perm(label) and not officer.has_perm(label)
    with pytest.raises(org.OrgRuleError, match="at most 30 days"):
        org.grant_permission(d, officer, label, days=31, reason="Cover")
    with pytest.raises(org.OrgRuleError):
        org.grant_permission(d, outsider, label, days=5, reason="Cover")
    # Subset rule: the director does not hold view_all_tasks (and it is not allow-listed).
    GrantablePermission.objects.create(permission=Permission.objects.get(codename="view_all_tasks"))
    with pytest.raises(org.OrgRuleError, match="you hold yourself"):
        org.grant_permission(d, officer, "tasks.view_all_tasks", days=5, reason="x")
    grant = org.grant_permission(d, officer, label, days=7, reason="Leave cover")
    assert grant.granted_scope == "department" and fresh(officer).has_perm(label)
    assert list(org.active_grants(department=depts["ict"])) == [grant]
    # Expiry: once the time passes the grant stops counting at once, and the
    # nightly job records it.
    UserPermissionOverride.objects.filter(pk=grant.pk).update(expires_at=timezone.now() - datetime.timedelta(minutes=1))
    assert not fresh(officer).has_perm(label)
    out = io.StringIO()
    call_command("expire_permission_grants", stdout=out)
    assert "Expired 1" in out.getvalue()
    assert UserPermissionOverride.objects.get(pk=grant.pk).revoked_at is not None


def test_x31_not_allow_listed_and_revoke(roles, depts):
    d = mk("d@t.local", "director", depts["ict"])
    officer = mk("o@t.local", "officer", depts["ict"])
    with pytest.raises(org.OrgRuleError, match="not on the list"):
        org.grant_permission(d, officer, "staff.view_staffstatushistory", days=5, reason="x")
    grant = org.grant_permission(d, officer, "accounts.approve_requests", days=5, reason="Cover")
    org.revoke_permission(d, grant, "Back from leave")
    assert not fresh(officer).has_perm("accounts.approve_requests")
    assert {"permission_granted", "permission_revoked"} <= set(AuditLogEntry.objects.values_list("action", flat=True))


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------
def test_seed_org_idempotent(roles):
    out = io.StringIO()
    call_command("seed_org", stdout=out)
    out = io.StringIO()
    call_command("seed_org", stdout=out)
    assert "Changed 0 item(s)." in out.getvalue()
    out = io.StringIO()
    call_command("seed_org", "--dry-run", stdout=out)
    assert "Would change 0" in out.getvalue()
