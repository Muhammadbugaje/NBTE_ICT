"""Regression tests for review finding H7 (self-service staff edits)."""
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department, Staff

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def test_self_edit_cannot_change_department(client, ict, roles):
    u = mk("self7@x.org", None, roles["staff"])
    client.force_login(u)
    staff = u.staff_profile
    client.post(reverse("staff:update", args=[staff.pk]), {
        "employee_no": staff.employee_no, "first_name": "A", "last_name": "B",
        "email": u.email, "department": ict.pk, "contact_preference": "email",
        "grade_level": "17", "designation": "Director",
    })
    u.refresh_from_db()
    staff.refresh_from_db()
    assert u.department_id is None
    assert staff.grade_level == "" and staff.designation == ""


def test_self_edit_can_change_contact_fields(client, ict, roles):
    u = mk("self7b@x.org", ict, roles["staff"])
    client.force_login(u)
    staff = u.staff_profile
    resp = client.post(reverse("staff:update", args=[staff.pk]), {
        "phone": "08000000000", "contact_preference": "phone",
        "emergency_contact_name": "Kin", "emergency_contact_phone": "0800",
        "office_location": "Block A",
    })
    assert resp.status_code == 302
    staff.refresh_from_db()
    assert staff.phone == "08000000000" and staff.office_location == "Block A"


def test_self_edit_page_shows_contact_form_only(client, ict, roles):
    u = mk("self7c@x.org", ict, roles["staff"])
    client.force_login(u)
    html = client.get(reverse("staff:update", args=[u.staff_profile.pk])).content.decode()
    assert 'name="phone"' in html
    assert 'name="department"' not in html and 'name="grade_level"' not in html


def test_hr_still_gets_full_form(client, ict, roles):
    su = mk("su7@x.org", is_superuser=True)
    target = mk("t7@x.org", ict, roles["staff"])
    client.force_login(su)
    html = client.get(reverse("staff:update", args=[target.staff_profile.pk])).content.decode()
    assert 'name="department"' in html and 'name="grade_level"' in html


def test_departmentless_manager_gets_no_department_choices(roles):
    from django.contrib.auth.models import Permission
    from accounts.models import UserPermissionOverride
    from staff.forms import StaffForm
    u = mk("mgr7@x.org", None, roles["staff"])
    UserPermissionOverride.objects.create(
        user=u, effect="grant",
        permission=Permission.objects.get(codename="manage_own_department_staff"))
    u = User.objects.get(pk=u.pk)
    form = StaffForm(user=u)
    assert form.fields["department"].queryset.count() == 0
