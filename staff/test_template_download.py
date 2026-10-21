"""Blank nominal-roll template + the nominal roll page — Phase 12."""
import io

import openpyxl
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from staff.models import Department, Designation, ImportSession, Staff, StaffState
from staff.exporters import STANDARD_HEADERS
from staff.nominal_roll import NominalRollImporter
from staff.testing import build_sample_workbook, user_with_perms

pytestmark = pytest.mark.django_db


def test_template_download(client):
    Department.objects.create(name="ICT", code="ict")
    Designation.objects.create(name="PROG. OFF. II", rank=35)
    StaffState.objects.create(name="Confirmed")
    client.force_login(user_with_perms("i@x.org", "staff.import_nominal_roll"))
    resp = client.get(reverse("staff:nominal_roll_template"))
    assert resp.status_code == 200
    assert "NBTE_Nominal_Roll_Template.xlsx" in resp["Content-Disposition"]
    wb = openpyxl.load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["NOMINAL ROLL", "REFERENCE", "README"]
    ws = wb["NOMINAL ROLL"]
    assert [c.value for c in ws[1]] == STANDARD_HEADERS
    assert all(c.comment is not None for c in ws[1])
    assert ws["A2"].value == "e.g."
    formulas = {dv.formula1 for dv in ws.data_validations.dataValidation}
    assert any("REFERENCE!$A$" in f for f in formulas)        # departments
    assert any("REFERENCE!$B$" in f for f in formulas)        # designations
    ref = wb["REFERENCE"]
    assert [c.value for c in ref[1]] == ["DEPARTMT", "DESIGNATION", "ST_ORIGIN", "REMARKS", "SEX"]
    assert ref["A2"].value == "ICT" and ref["B2"].value == "PROG. OFF. II" and ref["D2"].value == "CONFIRMED"


def test_filled_template_imports_and_example_row_is_skipped(client):
    client.force_login(user_with_perms("i@x.org", "staff.import_nominal_roll"))
    wb = openpyxl.load_workbook(io.BytesIO(client.get(reverse("staff:nominal_roll_template")).content))
    wb["NOMINAL ROLL"].append([1, "NEW", "RECRUIT", "F", 1700, "KADUNA", "ZARIA", "PROG. OFF. II", 7, "ICT",
                               "7-Mar-90", "2-Jan-26", "2-Jan-26", "2-Jan-26", None, "PROBATION", "BSc"])
    buf = io.BytesIO()
    wb.save(buf)
    res = NominalRollImporter(io.BytesIO(buf.getvalue()), filename="t.xlsx").run(dry_run=False)
    assert res.new == 1 and not Staff.objects.filter(employee_no="1520").exists()   # example skipped


def test_page_permissions(client):
    client.force_login(user_with_perms("v@x.org", "staff.view_staff"))
    assert client.get(reverse("staff:nominal_roll")).status_code == 403
    assert client.get(reverse("staff:nominal_roll_template")).status_code == 403
    client.force_login(user_with_perms("e@x.org", "staff.export_nominal_roll"))
    page = client.get(reverse("staff:nominal_roll"))
    assert page.status_code == 200 and b"nbr-panel-import" not in page.content
    assert client.post(reverse("staff:nominal_roll_upload")).status_code == 403


def test_upload_preview_confirm_flow(client):
    u = user_with_perms("i@x.org", "staff.import_nominal_roll", "accounts.access_all_departments")
    client.force_login(u)
    upload = SimpleUploadedFile("roll.xlsx", build_sample_workbook())
    resp = client.post(reverse("staff:nominal_roll_upload"), {"file": upload})
    assert resp.status_code == 302 and resp["Location"] == reverse("staff:nominal_roll_preview")
    preview = client.get(reverse("staff:nominal_roll_preview"))
    assert preview.status_code == 200
    assert preview.context["result"].new == 9 and Staff.objects.filter(user__isnull=True).count() == 0
    # untick the old sheet, re-run, then confirm
    keep = ["NOMINAL ROLL WITH QUALIFICATION", "NEW STAFF 2023"]
    again = client.post(reverse("staff:nominal_roll_preview"),
                        {"sheets_submitted": "1", "sheet": keep, "mode": "update"})
    assert again.context["result"].new == 8
    done = client.post(reverse("staff:nominal_roll_confirm"),
                       {"sheets_submitted": "1", "sheet": keep, "mode": "update"})
    assert done.status_code == 302 and "tab=history" in done["Location"]
    assert Staff.objects.filter(user__isnull=True).count() == 8
    session = ImportSession.objects.get()
    assert session.uploaded_by == u and session.new_count == 8
    report = client.get(reverse("staff:nominal_roll_report", args=[session.pk]))
    assert report.status_code == 200 and b"NBTE nominal roll import" in b"".join(report.streaming_content)
    history = client.get(reverse("staff:nominal_roll") + "?tab=history")
    assert b"roll.xlsx" in history.content


def test_upload_rejects_non_excel(client):
    client.force_login(user_with_perms("i@x.org", "staff.import_nominal_roll"))
    resp = client.post(reverse("staff:nominal_roll_upload"),
                       {"file": SimpleUploadedFile("roll.csv", b"a,b")}, follow=True)
    assert resp.status_code == 200 and not ImportSession.objects.exists()
