"""Sign-in accounts from the nominal roll, login by file number, and merging
with existing account records (nominal-roll follow-up)."""
import io

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import User
from staff.models import ImportSession, Staff
from staff.nominal_roll import NominalRollImporter
from staff.testing import build_sample_workbook, user_with_perms

pytestmark = pytest.mark.django_db


def run(**kw):
    return NominalRollImporter(io.BytesIO(build_sample_workbook()), filename="s.xlsx", **kw).run(dry_run=False)


def test_surname_fileno_accounts_and_login_by_file_number(client):
    res = run(accounts={"scheme": "surname_fileno"})
    assert res.accounts_created == 9
    jabir = Staff.objects.select_related("user").get(employee_no="1413")
    u = jabir.user
    assert u.email == "1413@staff-login.invalid" and u.must_change_password
    assert u.check_password("jabir1413") and u.department == jabir.department
    assert Staff.objects.filter(user=u).count() == 1                 # linked, not duplicated
    resp = client.post(reverse("accounts:login"), {"username": "1413", "password": "jabir1413"})
    assert resp.status_code == 302
    assert client.get(reverse("staff:list")).status_code in (200, 302)
    client.logout()
    bad = client.post(reverse("accounts:login"), {"username": "1413", "password": "wrong"})
    assert bad.status_code == 200 and "_auth_user_id" not in client.session


def test_email_login_still_works(client):
    u = user_with_perms("someone@x.org")
    resp = client.post(reverse("accounts:login"), {"username": "someone@x.org", "password": "x-pass-12345"})
    assert resp.status_code == 302


def test_shared_and_dry_run():
    with pytest.raises(ValueError):
        NominalRollImporter(io.BytesIO(b""), accounts={"scheme": "shared", "password": "short"})
    dry = NominalRollImporter(io.BytesIO(build_sample_workbook()), filename="s.xlsx",
                              accounts={"scheme": "shared", "password": "Welcome2026"}).run(dry_run=True)
    assert dry.accounts_planned == 9 and not User.objects.exists()
    res = run(accounts={"scheme": "shared", "password": "Welcome2026"})
    assert res.accounts_created == 9
    assert all(s.user.check_password("Welcome2026") for s in Staff.objects.select_related("user"))
    again = run(accounts={"scheme": "shared", "password": "Welcome2026"})
    assert again.accounts_created == 0                            # nobody without an account left


def test_random_passwords_one_time_sheet(client):
    hr = user_with_perms("hr@x.org", "staff.import_nominal_roll", "accounts.add_user",
                         "accounts.access_all_departments")
    client.force_login(hr)
    client.post(reverse("staff:nominal_roll_upload"), {"file": SimpleUploadedFile("r.xlsx", build_sample_workbook())})
    opts = {"sheets_submitted": "1", "mode": "update", "create_accounts": "on", "password_scheme": "random",
            "sheet": ["NOMINAL ROLL WITH QUALIFICATION", " ", "NEW STAFF 2023"]}
    preview = client.post(reverse("staff:nominal_roll_preview"), opts)
    assert preview.context["result"].accounts_planned == 9
    assert client.post(reverse("staff:nominal_roll_confirm"), opts).status_code == 302
    session = ImportSession.objects.get()
    assert session.accounts_created == 9 and session.credentials_file
    sheet = client.get(reverse("staff:nominal_roll_credentials", args=[session.pk]))
    ws = openpyxl.load_workbook(io.BytesIO(sheet.content))["SIGN-IN DETAILS"]
    row = {r[0]: r for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
    jabir = Staff.objects.get(employee_no="1413").user
    assert jabir.check_password(row["1413"][5])
    session.refresh_from_db()
    assert not session.credentials_file                           # deleted after one download
    again = client.get(reverse("staff:nominal_roll_credentials", args=[session.pk]))
    assert again.status_code == 302


def test_accounts_option_needs_add_user(client):
    client.force_login(user_with_perms("i@x.org", "staff.import_nominal_roll"))
    client.post(reverse("staff:nominal_roll_upload"), {"file": SimpleUploadedFile("r.xlsx", build_sample_workbook())})
    resp = client.post(reverse("staff:nominal_roll_confirm"),
                       {"sheets_submitted": "1", "sheet": ["NOMINAL ROLL WITH QUALIFICATION"],
                        "create_accounts": "on", "password_scheme": "surname_fileno"})
    assert resp.status_code == 403
    page = client.get(reverse("staff:nominal_roll_preview"))
    assert b"create_accounts" not in page.content


def test_existing_account_record_is_merged_not_duplicated():
    u = User.objects.create_user("bugaje@nbte.gov.ng", "x-pass-12345", first_name="Muhammad", last_name="Bugaje")
    placeholder = u.staff_profile
    assert placeholder.employee_no.startswith("USR-")
    res = run()
    assert res.linked_accounts and res.linked_accounts[0][1] == "1270"
    placeholder.refresh_from_db()
    assert placeholder.employee_no == "1270" and placeholder.user == u
    assert placeholder.designation.name == "EXECUTIVE SECRETARY"
    assert Staff.objects.filter(last_name__iexact="BUGAJE").count() == 1
    assert placeholder.email == "bugaje@nbte.gov.ng"               # account email untouched


def test_email_column_matches_and_fills():
    wb = openpyxl.load_workbook(io.BytesIO(build_sample_workbook()))
    ws = wb["NOMINAL ROLL WITH QUALIFICATION"]
    ws.cell(5, 18, "EMAIL"); ws.cell(5, 19, "PHONE"); ws.cell(5, 20, "OFFICE")
    ws.cell(8, 18, "jabir@nbte.gov.ng"); ws.cell(8, 19, "08030000000"); ws.cell(8, 20, "Software Unit")
    buf = io.BytesIO(); wb.save(buf)
    NominalRollImporter(io.BytesIO(buf.getvalue()), filename="e.xlsx").run(dry_run=False)
    j = Staff.objects.get(employee_no="1413")
    assert (j.email, j.phone, j.office.name) == ("jabir@nbte.gov.ng", "08030000000", "Software Unit")


def test_old_wizard_staff_module_opens_new_page(client):
    client.force_login(user_with_perms("a@x.org", superuser=True))
    assert client.get(reverse("core:import_export_spec", args=["staff"]))["Location"] == reverse("staff:nominal_roll")
    assert client.get(reverse("core:ie_panel", args=["staff"]))["HX-Redirect"] == reverse("staff:nominal_roll")
    landing = client.get(reverse("core:import_export"))
    assert reverse("staff:nominal_roll").encode() in landing.content


def test_sidebar_link_under_administration(client):
    client.force_login(user_with_perms("i@x.org", "staff.import_nominal_roll"))
    html = client.get(reverse("staff:nominal_roll")).content.decode()
    admin_block = html[html.index('data-group="administration"'):]
    assert "Staff import &amp; export" in admin_block[:3000]
    assert "Import / Export" not in admin_block[:3000]               # still needs config permission


def test_email_is_the_username_and_welcome_email_is_queued(django_capture_on_commit_callbacks):
    from notifications.models import EmailLog
    wb = openpyxl.load_workbook(io.BytesIO(build_sample_workbook()))
    ws = wb["NOMINAL ROLL WITH QUALIFICATION"]
    ws.cell(5, 18, "EMAIL")
    ws.cell(8, 18, "Jabir.Abdulkadir@nbte.gov.ng")
    buf = io.BytesIO(); wb.save(buf)
    with django_capture_on_commit_callbacks(execute=False):
        res = NominalRollImporter(io.BytesIO(buf.getvalue()), filename="e.xlsx",
                                  accounts={"scheme": "random", "email": True}).run(dry_run=False)
    assert res.accounts_emailed == 1
    jabir = Staff.objects.select_related("user").get(employee_no="1413")
    assert jabir.user.email == "jabir.abdulkadir@nbte.gov.ng"           # email is the sign-in name
    log = EmailLog.objects.get(category="credentials")
    assert log.recipients == ["jabir.abdulkadir@nbte.gov.ng"] and log.body_html == ""   # password not stored
    assert res.credentials and {c["sign_in"] for c in res.credentials} >= {"jabir.abdulkadir@nbte.gov.ng"}


def test_adding_an_email_later_becomes_the_sign_in(client):
    run(accounts={"scheme": "surname_fileno"})
    s = Staff.objects.select_related("user").get(employee_no="1500")
    assert s.user.email.endswith("@staff-login.invalid")
    s.email = "rabi.sani@nbte.gov.ng"
    s.save()
    s.user.refresh_from_db()
    assert s.user.email == "rabi.sani@nbte.gov.ng"
    resp = client.post(reverse("accounts:login"), {"username": "rabi.sani@nbte.gov.ng", "password": "sani1500"})
    assert resp.status_code == 302
