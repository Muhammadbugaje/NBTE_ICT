"""Phase B v2.1 HR API mirrors the web rules (B.8 Access, B.9 promotions)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from staff.models import Department, Designation, Staff
from staff.testing import user_with_perms

from .conftest import BASE, bearer

pytestmark = pytest.mark.django_db


@pytest.fixture
def ladder():
    po1 = Designation.objects.create(name="PROG. OFF. I", rank=40, contediss_min="8")
    po2 = Designation.objects.create(name="PROG. OFF. II", rank=35, contediss_min="7", next_designation=po1)
    d = Department.objects.create(name="ICT api", code="ict-api")
    old = timezone.localdate() - timedelta(days=5 * 366)
    new = timezone.localdate() - timedelta(days=200)
    a = Staff.objects.create(employee_no="API-1", first_name="A", last_name="One", designation=po2,
                             grade_level="7", department=d, date_present_appointment=old)
    b = Staff.objects.create(employee_no="API-2", first_name="B", last_name="Two", designation=po2,
                             grade_level="7", department=d, date_present_appointment=new)
    return a, b


def test_d5_api_promotions_tabs_and_permission(staff_api, ladder):
    hr = user_with_perms("hr-api@x.org", "staff.promote_staff", "staff.view_staff", "accounts.access_all_departments")
    hr.must_change_password = False
    hr.save()
    r = bearer(staff_api, hr).get(BASE + "staff/promotions/")
    assert r.status_code == 200, r.content
    rows = r.json()["results"]
    assert [x["employee_no"] for x in rows] == ["API-1"]
    assert rows[0]["next_designation"] == "PROG. OFF. I" and rows[0]["eligible"]
    all_rows = staff_api.get(BASE + "staff/promotions/?tab=all&q=API-").json()["results"]
    assert {x["employee_no"] for x in all_rows} == {"API-1", "API-2"}
    nobody = user_with_perms("nobody-api@x.org", "staff.view_staff")
    nobody.must_change_password = False
    nobody.save()
    assert bearer(staff_api, nobody).get(BASE + "staff/promotions/").status_code == 403


def test_c1_api_access(staff_api):
    hr = user_with_perms("hr2-api@x.org", "accounts.change_user", "staff.view_staff", "accounts.access_all_departments")
    hr.must_change_password = False
    hr.save()
    target = user_with_perms("t-api@x.org", "staff.view_staff")
    st = Staff.all_objects.filter(user=target).first() or Staff.objects.create(
        user=target, employee_no="T-API", first_name="T", last_name="Api")
    r = bearer(staff_api, hr).get(BASE + f"staff/{st.pk}/access/")
    assert r.status_code == 200, r.content
    assert r.json()["role"] and any(p["label"] == "staff.view_staff" for p in r.json()["permissions"])
