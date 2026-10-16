"""Regression tests for review findings H5 (account provisioning) and H6 (staff import)."""
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
import io

from django.core import mail
from openpyxl import Workbook, load_workbook

from notifications.models import EmailLog


def _post_create(client, ict, email="new5@x.org", **extra):
    data = {"employee_no": "E5-" + email, "first_name": "Ada", "last_name": "Obi",
            "email": email, "department": ict.pk, "contact_preference": "email",
            "create_account": "on"}
    data.update(extra)
    return client.post(reverse("staff:create"), data, follow=True)


def test_create_with_account_links_the_same_record(client, ict):
    client.force_login(mk("su5@x.org", is_superuser=True))
    resp = _post_create(client, ict)
    assert resp.status_code == 200
    staff = Staff.objects.get(email="new5@x.org")
    assert staff.user is not None and staff.user.email == "new5@x.org"
    assert Staff.objects.filter(user=staff.user).count() == 1
    assert staff.user.must_change_password


def test_password_never_shown_and_not_stored_in_email_log(client, ict):
    client.force_login(mk("su5b@x.org", is_superuser=True))
    resp = _post_create(client, ict, email="new5b@x.org", initial_password="Typed-Pass-2026")
    assert b"Typed-Pass-2026" not in resp.content
    log = EmailLog.objects.get(recipient_text="new5b@x.org")
    assert log.category == "credentials" and "Typed-Pass-2026" not in log.body_html


def test_login_url_resolves():
    assert reverse("accounts:login")


def test_conflicting_existing_account_is_reported_not_crashed(client, ict):
    mk("dup5@x.org")  # has its own auto-created directory record
    client.force_login(mk("su5c@x.org", is_superuser=True))
    resp = _post_create(client, ict, email="dup5@x.org")
    assert resp.status_code == 200
    staff = Staff.objects.get(employee_no="E5-dup5@x.org")
    assert staff.user_id is None
    assert b"already belongs to another directory" in resp.content


def test_user_created_first_links_matching_unlinked_staff(ict):
    s = Staff.objects.create(employee_no="E5X", first_name="A", last_name="B", email="match5@x.org")
    u = mk("match5@x.org")
    s.refresh_from_db()
    assert s.user_id == u.pk
    assert Staff.objects.filter(user=u).count() == 1


def test_import_create_account(ict):
    from staff.imports import StaffImport
    res = StaffImport().run([{"employee_no": "E5I", "first_name": "A", "last_name": "B",
                              "email": "imp5@x.org", "create_account": "yes"}])
    assert res.created == 1, res.rejected
    assert Staff.objects.get(employee_no="E5I").user.email == "imp5@x.org"


# ------------------------------------------------------------------ H6
def test_import_blank_optional_cells_accepted(ict):
    from staff.imports import StaffImport
    res = StaffImport().run([{"employee_no": "E6A", "first_name": "A", "last_name": "B",
                              "email": "e6a@x.org", "phone": "", "designation": ""}])
    assert res.created == 1, res.rejected
    s = Staff.objects.get(employee_no="E6A")
    assert s.phone == "" and s.contact_preference == "email"


def test_import_sets_department(ict):
    from staff.imports import StaffImport
    res = StaffImport().run([{"employee_no": "E6B", "first_name": "A", "last_name": "B",
                              "email": "e6b@x.org", "department": "ICT"}])
    assert res.created == 1, res.rejected
    assert Staff.objects.get(employee_no="E6B").department_id == ict.pk


def test_blank_department_leaves_existing_unchanged(ict):
    from staff.imports import StaffImport
    Staff.objects.create(employee_no="E6C", first_name="A", last_name="B", email="e6c@x.org",
                         department=ict)
    res = StaffImport().run([{"employee_no": "E6C", "first_name": "A", "last_name": "B",
                              "email": "e6c@x.org", "department": ""}])
    assert res.updated == 1, res.rejected
    assert Staff.objects.get(employee_no="E6C").department_id == ict.pk


def test_export_then_import_round_trip(ict):
    from core import excel
    from staff.imports import StaffExport, StaffImport
    boss = Staff.objects.create(employee_no="E6BOSS", first_name="B", last_name="Oss",
                                email="boss6@x.org", department=ict)
    Staff.objects.create(employee_no="E6W", first_name="W", last_name="Orker",
                         email="w6@x.org", department=ict, reports_to=boss)
    data = StaffExport().export_xlsx()
    res = StaffImport().run_from_file(io.BytesIO(data))
    assert not res.rejected, res.rejected
    assert Staff.objects.get(employee_no="E6W").reports_to_id == boss.pk


def test_dead_import_module_removed():
    import importlib.util
    assert importlib.util.find_spec("staff.importexport") is None
