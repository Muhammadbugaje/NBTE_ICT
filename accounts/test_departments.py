import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client

import config
from accounts.models import Role, User
from accounts.scoping import scope_to_department
from accounts.services import provision_user
from config.models import ConfigEntry
from core.models import AuditLogEntry
from staff.models import Department, Staff

pytestmark = pytest.mark.django_db


@pytest.fixture
def depts():
    return Department.objects.create(name="ICT"), Department.objects.create(name="Planning & Research")


def _role(code, *codenames):
    role = Role.objects.create(code=code, name=code)
    role.permissions.set(Permission.objects.filter(codename__in=codenames))
    return role


def _user(email, dept=None, role=None, **kw):
    return User.objects.create_user(email, "Pass-word-123", department=dept, role=role, must_change_password=False, **kw)


# ---------------- the Department class ----------------
def test_code_is_generated_unique_and_stable():
    a = Department.objects.create(name="Planning & Research")
    b = Department.objects.create(name="Planning  Research")      # slugs to the same thing
    assert a.code == "planning-research" and b.code == "planning-research-2"
    a.name = "Planning, Research & Statistics"
    a.save()
    assert a.code == "planning-research"                           # renaming never changes the handle
    assert a.config_scope == "department:planning-research"


def test_seed_departments_is_idempotent():
    call_command("seed_departments"); call_command("seed_departments")
    assert list(Department.objects.values_list("code", flat=True)) == ["ict"]


# ---------------- user belongs to a department ----------------
def test_provision_user_with_department(depts, django_capture_on_commit_callbacks):
    ict, _ = depts
    with django_capture_on_commit_callbacks(execute=True):
        u = provision_user("d@x.org", "D", "U", department=ict)
    assert User.objects.get(pk=u.pk).department == ict


def test_admin_add_user_form_has_department_field(client, depts, django_capture_on_commit_callbacks):
    from django.core import mail
    ict, _ = depts
    client.force_login(User.objects.create_superuser("su@x.org", "Pass-word-123"))
    page = client.get("/admin/accounts/user/add/").content
    assert b'name="department"' in page and b"ICT" in page
    with django_capture_on_commit_callbacks(execute=True):
      r = client.post("/admin/accounts/user/add/", {"email": "new@x.org", "first_name": "N", "last_name": "U",
                                                  "department": ict.pk, "temporary_password": "",
                                                  "permission_overrides-TOTAL_FORMS": 0, "permission_overrides-INITIAL_FORMS": 0})
    assert r.status_code == 302
    new = User.objects.get(email="new@x.org")
    assert new.department == ict and new.must_change_password is True
    assert len(mail.outbox) == 1 and "new@x.org" in mail.outbox[0].body      # credentials email went out


# ---------------- department-aware permissions ----------------
def test_can_access_department_rules(depts):
    ict, planning = depts
    me = _user("me@x.org", ict)
    assert me.can_access_department(ict) and not me.can_access_department(planning)
    assert me.can_access_department(ict.pk) and not me.can_access_department(None)
    assert not _user("nodept@x.org").can_access_department(ict)                       # fails closed
    assert User.objects.create_superuser("su@x.org", "Pass-word-123").can_access_department(planning)
    director = _user("dir@x.org", ict, _role("dir", "access_all_departments"))
    assert director.can_access_department(planning)                                   # cross-department right
    gone = _user("gone@x.org", ict, is_active=False)
    assert not gone.can_access_department(ict)


def test_permission_scoped_to_a_department(depts):
    ict, planning = depts
    head = _user("head@x.org", ict, _role("head", "change_staff_state"))
    assert head.has_perm_for_department("staff.change_staff_state", ict)
    assert not head.has_perm_for_department("staff.change_staff_state", planning)    # has the perm, wrong department
    plain = _user("plain@x.org", ict)
    assert not plain.has_perm_for_department("staff.change_staff_state", ict)        # right department, no perm


def test_scope_to_department_queryset(depts):
    ict, planning = depts
    for n, d in (("1", ict), ("2", ict), ("3", planning), ("4", None)):
        Staff.objects.create(employee_no=n, first_name="S", last_name=n, department=d)
    qs = Staff.objects.all()
    assert scope_to_department(qs, _user("a@x.org", ict)).count() == 2
    assert scope_to_department(qs, _user("b@x.org", planning)).count() == 1
    assert scope_to_department(qs, _user("c@x.org")).count() == 0                     # no department -> nothing
    assert scope_to_department(qs, _user("d@x.org", ict, _role("all", "access_all_departments"))).count() == 4
    assert scope_to_department(qs, User.objects.create_superuser("su@x.org", "Pass-word-123")).count() == 4
    from django.contrib.auth.models import AnonymousUser
    assert scope_to_department(qs, AnonymousUser()).count() == 0


def test_staff_admin_only_lists_own_department(depts):
    ict, planning = depts
    Staff.objects.create(employee_no="ICT-001", first_name="I", last_name="Ctperson", department=ict)
    Staff.objects.create(employee_no="PLN-001", first_name="P", last_name="Lnperson", department=planning)
    role = _role("viewer", "view_staff")
    mine = _user("mine@x.org", ict, role, is_staff=True)
    wide = _user("wide@x.org", ict, _role("wide", "view_staff", "access_all_departments"), is_staff=True)
    c = Client(); c.force_login(mine)
    page = c.get("/admin/staff/staff/").content
    assert b"ICT-001" in page and b"PLN-001" not in page
    c2 = Client(); c2.force_login(wide)
    both = c2.get("/admin/staff/staff/").content
    assert b"ICT-001" in both and b"PLN-001" in both
    # and guessing another department's record by URL is refused
    other = Staff.objects.get(employee_no="PLN-001")
    assert c.get(f"/admin/staff/staff/{other.pk}/change/", follow=True).status_code in (200, 404)
    assert b"PLN-001" not in c.get(f"/admin/staff/staff/{other.pk}/change/", follow=True).content


# ---------------- ICT does things differently ----------------
def test_department_specific_config_overrides_global(depts):
    ict, planning = depts
    ConfigEntry.objects.create(key="request.approval_levels", value="4", value_type="int")
    ConfigEntry.objects.create(key="request.approval_levels", scope=ict.config_scope, value="2", value_type="int")
    assert config.get_for_user("request.approval_levels", _user("i@x.org", ict)) == 2        # ICT: its own rule
    assert config.get_for_user("request.approval_levels", _user("p@x.org", planning)) == 4   # others: global rule
    assert config.get_for_user("request.approval_levels", _user("n@x.org")) == 4             # no department: global
    assert config.get_for_department("request.approval_levels", ict) == 2
    assert config.get_for_department("request.approval_levels", None, 9) == 4
    assert config.get_for_department("nothing.here", ict, 9) == 9


# ---------------- Staff <-> User department stay in step ----------------
def test_staff_department_flows_to_linked_user_and_back(depts):
    ict, planning = depts
    u = _user("link@x.org")
    s = Staff.objects.create(employee_no="L1", first_name="L", last_name="K", user=u, department=ict)
    assert User.objects.get(pk=u.pk).department == ict                  # directory -> account
    u = User.objects.get(pk=u.pk); u.department = planning; u.save()
    assert Staff.objects.get(pk=s.pk).department == planning            # account -> directory
    s = Staff.objects.get(pk=s.pk); s.department = ict; s.save()
    assert User.objects.get(pk=u.pk).department == ict
    assert AuditLogEntry.objects.filter(model_label="accounts.User", object_pk=u.pk, changes__has_key="department").exists() \
        or any("department" in e.changes for e in AuditLogEntry.objects.filter(model_label="accounts.User", object_pk=u.pk))


def test_blank_side_is_filled_from_the_other_never_wiped(depts):
    ict, _ = depts
    u = _user("fill@x.org", ict)
    s = Staff.objects.create(employee_no="F1", first_name="F", last_name="L", user=u)   # no department given
    assert Staff.objects.get(pk=s.pk).department == ict                                  # filled from the account
    s = Staff.objects.get(pk=s.pk); s.department = None; s.save()
    assert User.objects.get(pk=u.pk).department == ict                                   # blank doesn't wipe the account


# ---------------- roles ----------------
def test_seeded_roles_get_cross_department_right_only_where_intended():
    call_command("seed_roles")
    def can(code): return Role.objects.get(code=code).permissions.filter(codename="access_all_departments").exists()
    assert can("director") and can("deputy-director") and can("super-admin") and can("office-head-admin")
    assert not can("office-head-ts") and not can("officer") and not can("staff")


def test_seed_roles_sync_adds_missing_perms_to_existing_roles_without_removing():
    call_command("seed_roles")
    role = Role.objects.get(code="director")
    role.permissions.clear()
    extra = Permission.objects.get(codename="view_staffattachment"); role.permissions.add(extra)
    call_command("seed_roles")                                        # without --sync: untouched
    assert not role.permissions.filter(codename="access_all_departments").exists()
    call_command("seed_roles", "--sync")
    assert role.permissions.filter(codename="access_all_departments").exists()
    assert role.permissions.filter(pk=extra.pk).exists()              # nothing removed
