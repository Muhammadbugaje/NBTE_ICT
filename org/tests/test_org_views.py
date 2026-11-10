"""Phase B organisation pages: department tabs (HTMX + full page), director
seat actions, divisions, Manage access, Settings → Offices / Grade levels /
Designations (B4, B6, B9, C7, C8, X31)."""
import io

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command

import org
from accounts.models import Role, User
from org.models import Division, GradeLevel
from staff.models import Department, Designation, Office, Staff

pytestmark = pytest.mark.django_db
HX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def setup():
    call_command("seed_roles", "--sync", stdout=io.StringIO())
    ict = Department.objects.create(name="ICT (v)", code="ict")
    hr = Department.objects.create(name="HR (v)", code="hr")

    def mk(email, role, dept):
        u = User.objects.create_user(email, "View-test-2026!", first_name=email[:3], last_name="V",
                                     role=Role.objects.get(code=role), department=dept, must_change_password=False)
        if not Staff.objects.filter(user=u).exists():
            Staff.objects.create(user=u, employee_no=f"V-{u.pk}", first_name="A", last_name="B", department=dept)
        return User.objects.get(pk=u.pk)

    ns = type("NS", (), {})()
    ns.ict, ns.hr = ict, hr
    ns.director = mk("dir@v.local", "director", ict)
    ns.officer = mk("off@v.local", "officer", ict)
    ns.hr_director = mk("hrd@v.local", "director", hr)
    ns.es = mk("es@v.local", "executive-secretary", hr)
    return ns


def test_b6_divisions_tab_full_page_and_fragment(setup, client):
    client.force_login(setup.director)
    r = client.get("/org/departments/ict/?tab=divisions")
    assert r.status_code == 200 and b"<html" in r.content.lower() and b"New division" in r.content
    r = client.get("/org/departments/ict/?tab=divisions", **HX)
    assert r.status_code == 200 and b"<html" not in r.content.lower() and b"New division" in r.content


def test_b6_director_creates_division_and_appoints_head_via_htmx(setup, client):
    client.force_login(setup.director)
    r = client.post("/org/departments/ict/divisions/new/", {"name": "Dev Support", "max_open_tasks": "3",
                                                           "head": setup.officer.pk}, **HX)
    assert r.status_code == 200 and b"Dev Support" in r.content
    div = Division.objects.get(department=setup.ict, name="Dev Support")
    assert div.head == setup.officer and div.max_open_tasks == 3
    assert User.objects.get(pk=setup.officer.pk).groups.filter(name=org.DIVISION_HEAD_GROUP).exists()


def test_b6_other_director_cannot_see_or_change_department(setup, client):
    client.force_login(setup.hr_director)
    assert client.get("/org/departments/ict/").status_code == 404
    assert client.post("/org/departments/ict/divisions/new/", {"name": "X"}).status_code == 404
    assert not Division.objects.filter(name="X").exists()


def test_b4_assign_refused_when_occupied_in_ui(setup, client):
    client.force_login(setup.es)
    r = client.post("/org/departments/ict/director/assign/", {"user": setup.officer.pk}, **HX)
    assert b"Vacate the current director first" in r.content
    assert org.director_of(setup.ict) == setup.director
    r = client.post("/org/departments/ict/director/vacate/", {"reason": "Retired"}, **HX)
    assert org.director_of(setup.ict) is None
    client.post("/org/departments/ict/director/assign/", {"user": setup.officer.pk, "reason": "Letter"}, **HX)
    assert org.director_of(setup.ict) == User.objects.get(pk=setup.officer.pk)


def test_x31_manage_access_panel(setup, client):
    from django.contrib.auth.models import Permission

    client.force_login(setup.director)
    perm = Permission.objects.get(codename="approve_requests")
    r = client.get("/org/departments/ict/?tab=access")
    assert r.status_code == 200 and b"Grant a permission" in r.content
    r = client.post("/org/departments/ict/access/grant/", {"user": setup.officer.pk, "permission": perm.pk,
                                                          "days": "7", "reason": "Cover"}, **HX)
    assert b"granted" in r.content
    assert User.objects.get(pk=setup.officer.pk).has_perm("accounts.approve_requests")
    grant = org.active_grants(user=setup.officer).get()
    client.post(f"/org/departments/ict/access/{grant.pk}/revoke/", {}, **HX)
    assert not User.objects.get(pk=setup.officer.pk).has_perm("accounts.approve_requests")
    client.force_login(setup.officer)
    assert client.get("/org/departments/ict/?tab=access").status_code == 403


def test_b9_settings_offices(setup, client):
    group = Group.objects.create(name="Desk perms (v)")
    client.force_login(setup.es)
    assert client.get("/org/settings/offices/").status_code == 200
    r = client.post("/org/settings/offices/new/", {"name": "JAMB Desk (v)", "groups": [group.pk]}, **HX)
    assert r.status_code == 200 and b"JAMB Desk (v)" in r.content
    # v2.2: offices are units in the one Department list.
    unit = Department.objects.get(name="JAMB Desk (v)")
    assert unit.kind != Department.KIND_DEPARTMENT and list(unit.member_groups.all()) == [group]
    client.force_login(setup.director)
    assert client.get("/org/settings/offices/").status_code == 403


def test_c7_c8_settings_grade_levels_and_designations(setup, client):
    client.force_login(setup.es)
    g8 = GradeLevel.objects.get(number=8)
    assert client.get("/org/settings/grade-levels/").status_code == 200
    client.post(f"/org/settings/grade-levels/{g8.pk}/", {"label": "CONTEDISS 08", "min_years_in_grade": "3",
                                                        "is_active": "on"}, **HX)
    assert GradeLevel.objects.get(pk=g8.pk).min_years_in_grade == 3
    d = Designation.objects.create(name="Programme Officer (v)", rank=20)
    assert client.get("/org/settings/designations/?q=Programme").status_code == 200
    g10 = GradeLevel.objects.get(number=10)
    r = client.post(f"/org/settings/designations/{d.pk}/", {"default_role": Role.objects.get(code="officer").pk,
                                                            "grade_min": g8.pk, "grade_max": g10.pk, "rank": "20"}, **HX)
    d.refresh_from_db()
    assert r.status_code == 200 and d.default_role.code == "officer" and d.grade_max == g10
    r = client.post(f"/org/settings/designations/{d.pk}/", {"grade_min": g10.pk, "grade_max": g8.pk}, **HX)
    assert b"lowest grade is above" in r.content
    client.force_login(setup.officer)
    assert client.get("/org/settings/grade-levels/").status_code == 403
