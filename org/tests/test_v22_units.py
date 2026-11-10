"""v2.2: Departments and Offices are one list (a unit has a kind)."""
import pytest

from qa_core.tests.conftest import make_user  # noqa: F401

pytestmark = pytest.mark.django_db


def test_offices_merge_into_departments_once():
    from staff.models import Department, Office, Staff
    from staff.office_merge import merge_offices

    es = Department.objects.create(name="ES OFFICE", code="es-office")
    zonal = Department.objects.create(name="ZONAL OFFICE", code="zonal-office")
    Department.objects.create(name="Legal Unit", code="legal")
    Office.objects.create(name="Legal Unit")
    Office.objects.create(name="ZONAL OFFICE LAGOS")
    Office.objects.create(name="Media Unit")
    before = Department.objects.count()
    merge_offices(Department, Office, Staff)
    assert Department.objects.count() == before + 2          # Lagos + Media; Legal merged
    lagos = Department.objects.get(name="ZONAL OFFICE LAGOS")
    assert lagos.kind == "zonal_office" and lagos.parent == zonal
    assert Department.objects.get(code="legal").kind == "unit"
    assert Department.objects.get(name="Media Unit").parent == es
    assert not Office.objects.filter(is_active=True).exists()
    merge_offices(Department, Office, Staff)                 # idempotent
    assert Department.objects.count() == before + 2


def test_member_groups_give_permissions(make_user):
    from django.contrib.auth.models import Group, Permission
    from accounts.permissions_service import effective_permissions
    from staff.models import Department, Staff

    unit = Department.objects.create(name="JAMB Desk", kind="unit")
    g = Group.objects.create(name="JAMB desk group")
    perm = Permission.objects.get(codename="view_department", content_type__app_label="staff")
    g.permissions.add(perm)
    unit.member_groups.add(g)
    user = make_user("unit.member@t.local", role="officer")
    staff = Staff.objects.filter(user=user).first() or Staff(user=user, employee_no="U-1", first_name="U",
                                                             last_name="M")
    staff.department = unit
    staff.save()
    user = type(user).objects.get(pk=user.pk)
    assert "staff.view_department" in effective_permissions(user)
