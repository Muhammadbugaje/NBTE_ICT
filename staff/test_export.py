"""Nominal-roll XLSX export (staff.exporters) — Phase 12."""
import io
from datetime import date, datetime

import openpyxl
import pytest
from django.urls import reverse

from core.models import AuditLogEntry
from staff.exporters import export_nominal_roll
from staff.models import Department, Designation, Staff, StaffState, StateOfOrigin
from staff.exporters import STANDARD_HEADERS
from staff.nominal_roll import NominalRollImporter
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


@pytest.fixture
def five():
    ict = Department.objects.create(name="ICT", code="ict", sort_order=1)
    fin = Department.objects.create(name="FINANCE", code="finance", sort_order=2)
    kano = StateOfOrigin.objects.create(name="KANO", code="kn", zone="NW")
    conf = StaffState.objects.create(name="Confirmed")
    spo = Designation.objects.create(name="SNR. PROG. OFFR.", rank=57)
    po = Designation.objects.create(name="PROG. OFF. II", rank=35)
    rows = [("1413", "JABIR", "JIBRIL ABDULKADIR", ict, spo, "9", date(1996, 6, 10)),
            ("1500", "SANI", "RABI", ict, po, "7", date(1990, 1, 2)),
            ("1600", "MOHAMMED", "SAMBO", fin, po, "7", None),
            ("1601", "ALIYU", "UMAR", fin, spo, "9", None),
            ("1602", "BELLO", "ZAINAB", ict, po, "7", None)]
    for no, last, first, dept, desig, gl, dob in rows:
        Staff.objects.create(employee_no=no, last_name=last, first_name=first, department=dept, designation=desig,
                             grade_level=gl, date_of_birth=dob, gender="M", state_of_origin=kano, lga="MADOBI",
                             state=conf, date_present_appointment=date(2025, 1, 1), qualifications="BSc 2017")
    Staff.objects.create(employee_no="X1", last_name="OTHER", first_name="ONE")   # not in the subset
    return Staff.objects.exclude(employee_no="X1")


def _read(content):
    return openpyxl.load_workbook(io.BytesIO(content))


def test_five_row_export(five):
    content, count = export_nominal_roll(five)
    assert count == 5
    ws = _read(content)["NOMINAL ROLL"]
    assert [c.value for c in ws[5]] == STANDARD_HEADERS
    first = [c.value for c in ws[6]]
    # ICT first (sort order), seniority (rank) first, then surname
    assert first[:10] == [1, "JABIR", "JIBRIL ABDULKADIR", "M", 1413, "KANO", "MADOBI", "SNR. PROG. OFFR.", 9, "ICT"]
    assert first[10] == datetime(1996, 6, 10) and ws.cell(6, 11).number_format == "d-mmm-yy"
    assert first[13] == datetime(2025, 1, 1)
    assert first[15:17] == ["CONFIRMED", "BSc 2017"]
    assert ws.cell(5, 18).value == "EMAIL" and ws.cell(5, 20).value == "OFFICE"
    data_rows = [r for r in ws.iter_rows(min_row=6, values_only=True) if r[0]]
    assert len(data_rows) == 5
    assert [r[1] for r in data_rows] == ["JABIR", "BELLO", "SANI", "ALIYU", "MOHAMMED"]


def test_per_department_sheets(five):
    content, count = export_nominal_roll(five, per_department=True)
    wb = _read(content)
    assert wb.sheetnames == ["ICT", "FINANCE"] and count == 5
    assert [c.value for c in wb["FINANCE"][5]] == STANDARD_HEADERS


def test_export_round_trips_through_importer(five):
    content, _ = export_nominal_roll(five)
    NominalRollImporter(io.BytesIO(content), filename="export.xlsx").run(dry_run=False)  # fills legacy birth day
    content, _ = export_nominal_roll(five)
    res = NominalRollImporter(io.BytesIO(content), filename="export.xlsx").run(dry_run=True)
    assert not res.aborted and (res.new, res.updated, res.unchanged) == (0, 0, 5)


def test_export_view_permission_and_audit(client, five):
    client.force_login(user_with_perms("v@x.org", "staff.view_staff"))
    assert client.get(reverse("staff:nominal_roll_export")).status_code == 403
    u = user_with_perms("x@x.org", "staff.export_nominal_roll", "accounts.access_all_departments")
    client.force_login(u)
    resp = client.get(reverse("staff:nominal_roll_export"))
    assert resp.status_code == 200
    ws = _read(resp.content)["NOMINAL ROLL"]
    file_nos = {r[4] for r in ws.iter_rows(min_row=6, values_only=True) if r[0]}
    assert {1413, 1500, 1600, 1601, 1602, "X1"} <= file_nos
    assert AuditLogEntry.objects.filter(action="export_nominal_roll", actor=u).exists()


def test_export_is_department_scoped(client, five):
    ict = Department.objects.get(code="ict")
    client.force_login(user_with_perms("h@x.org", "staff.export_nominal_roll", department=ict))
    ws = _read(client.get(reverse("staff:nominal_roll_export")).content)["NOMINAL ROLL"]
    depts = {r[9] for r in ws.iter_rows(min_row=6, values_only=True) if r[0]}
    assert depts == {"ICT"}
