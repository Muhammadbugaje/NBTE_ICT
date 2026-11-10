"""Phase B v2.1 — B.8 roles on the staff profile (C1-C6) and B.11 staff ID (E1-E4)."""
import io

import pytest
from django.urls import reverse

from accounts.models import Role, User
from staff.models import Department, Designation, Staff
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


@pytest.fixture
def dept():
    return Department.objects.create(name="ICT unit", code="ict-x")


@pytest.fixture
def roles():
    return {"officer": Role.objects.create(code="b8-officer", name="B8 Officer", rank=20),
            "head": Role.objects.create(code="b8-head", name="B8 Head", rank=50),
            "top": Role.objects.create(code="b8-top", name="B8 Top", rank=900)}


@pytest.fixture
def hr(dept):
    u = user_with_perms("hr@x.org", "accounts.change_user", "accounts.view_user", "staff.view_staff",
                        "staff.add_staff", "staff.change_staff", "staff.print_staff_id",
                        "accounts.access_all_departments")
    Role.objects.filter(pk=u.role_id).update(rank=60)
    return User.objects.get(pk=u.pk)


@pytest.fixture
def person(dept, roles):
    u = User.objects.create_user("p@x.org", "pw-12345-x", first_name="Pat", last_name="Person", role=roles["officer"],
                                 must_change_password=False)
    s = Staff.all_objects.filter(user=u).first() or Staff.objects.create(
        user=u, employee_no="P-1", first_name="Pat", last_name="Person")
    s.department = dept
    s.email = "p@x.org"
    s.gender = "F"
    s.save()
    return s


def test_c1_role_chip_and_access_tab(client, hr, person):
    client.force_login(hr)
    body = client.get(reverse("staff:detail", args=[person.pk])).content.decode()
    assert 'id="staff-role-chip"' in body and "B8 Officer" in body
    assert "?tab=access" in body
    tab = client.get(reverse("staff:access", args=[person.pk]), HTTP_HX_REQUEST="true").content.decode()
    assert 'id="staff-access"' in tab and "Effective permissions" in tab and "<html" not in tab
    full = client.get(reverse("staff:detail", args=[person.pk]) + "?tab=access").content.decode()
    assert 'id="staff-access"' in full


def test_c2_access_tab_htmx_save(client, hr, person, roles):
    client.force_login(hr)
    resp = client.post(reverse("staff:role", args=[person.pk]), {"role": roles["head"].pk}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert 'hx-swap-oob="true"' in body and "B8 Head" in body and "qa:toast" in resp["HX-Trigger"]
    person.user.refresh_from_db()
    assert person.user.role == roles["head"]
    # A role above the actor's own is refused (C9-C11 hook: org.assign_role at the pb-org merge).
    resp = client.post(reverse("staff:role", args=[person.pk]), {"role": roles["top"].pk}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 422
    person.user.refresh_from_db()
    assert person.user.role == roles["head"]


def test_c2_non_hr_cannot_assign(client, person, roles, dept):
    viewer = user_with_perms("v@x.org", "staff.view_staff", "accounts.access_all_departments")
    client.force_login(viewer)
    resp = client.post(reverse("staff:role", args=[person.pk]), {"role": roles["officer"].pk}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 422
    assert "Manage role" not in client.get(reverse("staff:detail", args=[person.pk])).content.decode()


def test_c3_manage_role_in_directory(client, hr, person):
    client.force_login(hr)
    body = client.get(reverse("staff:list")).content.decode()
    assert "Manage role" in body and "manage=1" in body


def test_c4_create_with_role(client, hr, dept, roles):
    client.force_login(hr)
    form = client.get(reverse("staff:create")).content.decode()
    assert 'name="role"' in form and "B8 Top" not in form            # only grantable roles
    resp = client.post(reverse("staff:create"), {
        "employee_no": "NEW-1", "first_name": "Ngozi", "last_name": "New", "email": "new1@x.org",
        "department": dept.pk, "contact_preference": "email", "create_account": "on", "role": roles["head"].pk,
    })
    assert resp.status_code == 302, resp.content.decode()[:2000]
    s = Staff.objects.get(employee_no="NEW-1")
    assert s.user and s.user.role == roles["head"]


def test_c5_import_role_and_grade_with_errors(hr, dept, roles):
    from staff.imports import StaffImport
    spec = StaffImport()
    rows = [
        {"employee_no": "IMP-1", "first_name": "A", "last_name": "One", "email": "imp1@x.org",
         "create_account": "yes", "role": "b8-head", "grade_level": "GL08"},
        {"employee_no": "IMP-2", "first_name": "B", "last_name": "Two", "email": "imp2@x.org",
         "role": "nonsense"},
        {"employee_no": "IMP-3", "first_name": "C", "last_name": "Three", "grade_level": "42"},
        {"employee_no": "IMP-4", "first_name": "D", "last_name": "Four", "email": "imp4@x.org",
         "create_account": "yes", "role": "B8 Top"},
    ]
    from core import audit
    audit.set_current_actor(hr)        # the middleware sets this for the wizard request
    result = spec.run(rows)
    assert result.created == 1
    s = Staff.objects.get(employee_no="IMP-1")
    assert s.grade_level == "8" and s.user.role == roles["head"]
    bad = {r.row_number: " ".join(r.errors) for r in result.rejected}
    assert "Unknown role" in bad[2] and "Grade must be" in bad[3] and "cannot grant" in bad[4]
    assert not Staff.objects.filter(employee_no="IMP-4").exists()


def test_e2_placeholder_by_gender(client, hr, person):
    client.force_login(hr)
    body = client.get(reverse("staff:id_card", args=[person.pk])).content.decode()
    assert "idc-ph--female" in body and "NO PHOTO YET" in body
    person.gender = "M"
    person.save()
    body = client.get(reverse("staff:id_card", args=[person.pk])).content.decode()
    assert "idc-ph--male" in body


def test_e3_styles_registry(client, hr, person):
    from staff import staff_id_styles as reg
    assert {s.key for s in reg.STYLES} == {"cr80", "a4x8", "badge"}
    cr80, a4, badge = reg.get_style("cr80"), reg.get_style("a4x8"), reg.get_style("badge")
    assert (cr80.width_mm, cr80.height_mm) == (85.6, 54) and cr80.page_size_css == "85.6mm 54mm"
    assert a4.per_page == 8 and a4.page_size_css == "210mm 297mm"
    # 2 x 85.6 + gap + margins fit A4 exactly; 4 x 54 + gaps + margins too.
    assert abs(2 * a4.margin_left_mm + 2 * a4.width_mm + a4.gap_mm - 210) < 0.01
    assert abs(2 * a4.margin_top_mm + 4 * a4.height_mm + 3 * a4.gap_mm - 297) < 0.01
    assert (badge.width_mm, badge.height_mm) == (54, 86)
    client.force_login(hr)
    for st in ("cr80", "a4x8", "badge"):
        body = client.get(reverse("staff:id_print") + f"?ids={person.pk}&style={st}").content.decode()
        assert f"size: {reg.get_style(st).page_size_css}" in body


def test_e1_printed_qr_verifies_until_deactivated(client, person):
    from accounts import staff_id
    token = staff_id.make_print_token(person.user)
    assert staff_id.verify_staff_token(token).is_valid
    person.user.is_active = False
    person.user.save()
    assert not staff_id.verify_staff_token(token).is_valid


def test_e4_bulk_print_permission(client, hr, person, dept):
    other = Staff.objects.create(employee_no="O-2", first_name="Olu", last_name="Other", department=dept)
    url = reverse("staff:id_print") + f"?ids={person.pk}&ids={other.pk}&style=a4x8"
    client.force_login(hr)
    body = client.get(url).content.decode()
    assert body.count('data-id-face="front"') == 2
    # Without staff.print_staff_id: only your own card.
    client.force_login(person.user)
    assert client.get(url).status_code == 403
    assert client.get(reverse("staff:id_print") + f"?ids={person.pk}").status_code == 200
