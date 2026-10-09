import pytest

from accounts.models import Role, User
from core.models import AuditLogEntry
from django.contrib.auth.models import Permission
from staff.models import Staff, StaffState, StaffStatusHistory

pytestmark = pytest.mark.django_db


def _user_with_state_perm():
    role = Role.objects.create(code="hr", name="HR")
    role.permissions.add(Permission.objects.get(codename="change_staff_state"))
    return User.objects.create_user("hr@x.org", "x-pass-1234", role=role)


def test_state_change_requires_permission_and_writes_history_and_audit():
    active, leave = StaffState.objects.create(name="Active"), StaffState.objects.create(name="On Leave")
    s = Staff.objects.create(employee_no="S1", first_name="A", last_name="B", state=active)
    assert s.status_history.count() == 1  # initial state recorded

    nobody = User.objects.create_user("n@x.org", "x-pass-1234")
    with pytest.raises(PermissionError):
        s.change_state(leave, nobody)
    assert s.status_history.count() == 1

    hr = _user_with_state_perm()
    s = Staff.objects.get(pk=s.pk)
    s.change_state(leave, hr, reason="annual leave")
    h = s.status_history.first()
    assert (h.from_state, h.to_state, h.changed_by, h.reason) == (active, leave, hr, "annual leave")
    assert AuditLogEntry.objects.filter(object_pk=s.pk, action="state_change", actor=hr).exists()


def test_unrelated_edit_does_not_create_history():
    st = StaffState.objects.create(name="Active")
    s = Staff.objects.create(employee_no="S2", first_name="A", last_name="B", state=st)
    s = Staff.objects.get(pk=s.pk)
    s.designation = "Officer"
    s.save()
    assert s.status_history.count() == 1


def test_direct_state_assignment_also_writes_history():
    a, b = StaffState.objects.create(name="Active"), StaffState.objects.create(name="Suspended")
    s = Staff.objects.create(employee_no="S3", first_name="A", last_name="B", state=a)
    s = Staff.objects.get(pk=s.pk)
    s.state = b
    s.save()  # e.g. from an import or shell - still must be recorded (NR-09)
    assert s.status_history.count() == 2


def test_reporting_line_and_employee_no_unique():
    boss = Staff.objects.create(employee_no="B1", first_name="Boss", last_name="One")
    s = Staff.objects.create(employee_no="S4", first_name="A", last_name="B", reports_to=boss)
    assert list(boss.direct_reports.all()) == [s]
    from django.db import IntegrityError, transaction
    with pytest.raises(IntegrityError), transaction.atomic():
        Staff.objects.create(employee_no="S4", first_name="X", last_name="Y")


def test_admin_changelist_and_add_pages_render(client):
    su = User.objects.create_superuser("su@x.org", "x-pass-1234")
    client.force_login(su)
    for url in ("/admin/staff/staff/", "/admin/staff/staff/add/", "/admin/accounts/user/add/",
                "/admin/config/configentry/", "/admin/core/auditlogentry/", "/admin/accounts/role/add/"):
        assert client.get(url).status_code == 200, url
