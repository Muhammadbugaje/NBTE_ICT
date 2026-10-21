"""Nominal-roll importer (staff.nominal_roll) — Phase 12.

The synthetic workbook (staff.testing) always runs. The real HR file runs
when NBTE_NOMINAL_ROLL_XLSX points at it (it holds personal data, so it is
not bundled with the code):

    NBTE_NOMINAL_ROLL_XLSX="/path/NOMINAL ROLL AS AT OCTOBER, 2026.xlsx" pytest staff/test_nominal_roll_importer.py
"""
import io
import os
from datetime import date

import pytest
from django.core.management import call_command

from core.models import AuditLogEntry
from staff.models import Department, Designation, ImportSession, Staff, StaffState, StateOfOrigin
from staff.nominal_roll import NominalRollImporter, parse_nbte_date
from staff.testing import JABIR_QUALS, build_sample_workbook

pytestmark = pytest.mark.django_db
REAL_FILE = os.environ.get("NBTE_NOMINAL_ROLL_XLSX", "")


def run(dry_run=False, **kw):
    return NominalRollImporter(io.BytesIO(build_sample_workbook()), filename="sample.xlsx", **kw).run(dry_run=dry_run)


# ---------------------------------------------------------------- dates
@pytest.mark.parametrize("raw, expected, level", [
    ("7th Mar. 58", date(1958, 3, 7), ""),
    ("1st - MAY- 88", date(1988, 5, 1), ""),
    ("1st- JULY- 22", date(2022, 7, 1), ""),
    ("07-Apr.76", date(1976, 4, 7), ""),
    ("29-3-23", date(2023, 3, 29), ""),
    ("29-10-97", date(1997, 10, 29), ""),
    ("1-AUG-99", date(1999, 8, 1), ""),
    ("16- 03-21", date(2021, 3, 16), ""),
    ("2-0CT-26", date(2026, 10, 2), ""),
    ("3-Oct.-19", date(2019, 10, 3), ""),
    ("3-Oct, 2019", date(2019, 10, 3), ""),
    ("16th Mar., 21", date(2021, 3, 16), ""),
    ("13- Aprl-2018", date(2018, 4, 13), ""),
    ("2024-01-08 00:00:00", date(2024, 1, 8), ""),
    ("18-03-92", date(1992, 3, 18), ""),
    ("05-03-92", date(1992, 3, 5), "info"),      # ambiguous -> day first
    ("30-3-234", date(2024, 3, 30), "warning"),  # typo year
    ("5/13/96", date(1996, 5, 13), "warning"),   # month/day
    (45000, date(2023, 3, 15), ""),               # Excel serial
])
def test_parse_nbte_date(raw, expected, level):
    res = parse_nbte_date(raw)
    assert res.value == expected
    assert res.level == level


@pytest.mark.parametrize("raw", [None, "", "-", "#REF!", "SECONDMENT TO THE BOARD"])
def test_parse_nbte_date_empty_or_invalid(raw):
    assert parse_nbte_date(raw).value is None


# ---------------------------------------------------------------- sample workbook
def test_dry_run_saves_nothing():
    before = Staff.objects.count()
    res = run(dry_run=True)
    assert res.new == 9 and not res.aborted
    assert Staff.objects.count() == before
    assert not ImportSession.objects.exists()


def test_import_creates_staff_and_is_idempotent():
    first = run()
    assert first.status == "ok", first.aborted
    assert first.new == 9 and first.updated == 0
    assert Staff.objects.count() == 9
    second = run()
    assert (second.new, second.updated, second.unchanged) == (0, 0, 9)
    assert ImportSession.objects.count() == 2
    assert ImportSession.objects.first().report_file


def test_sheet_shapes_and_values():
    res = run()
    names = [s.name for s in res.sheets]
    assert names == [" ", "NEW STAFF 2023", "NOMINAL ROLL WITH QUALIFICATION"]   # oldest first
    assert "Sheet1" in res.ignored_sheets
    jabir = Staff.objects.select_related("designation", "department", "state_of_origin", "state").get(
        employee_no="1413")
    # newer sheet wins
    assert jabir.designation.name.startswith("SNR. PROG. OFFR")
    assert jabir.grade_level == "9"
    assert jabir.department.name == "ICT"
    assert (jabir.gender, jabir.state_of_origin.name, jabir.lga) == ("M", "KANO", "MADOBI")
    assert jabir.date_of_birth == date(1996, 6, 10)
    assert jabir.date_first_appointment == date(2020, 3, 17)
    assert jabir.date_joined_board == date(2023, 9, 28)
    assert jabir.date_present_appointment == date(2025, 1, 1)
    assert jabir.date_confirmation == date(2022, 3, 17)
    assert jabir.state.name == "Confirmed"
    assert jabir.qualifications == JABIR_QUALS
    assert (jabir.birth_month, jabir.birth_day) == (6, 10)       # legacy pair kept in step
    # no-header sheet read positionally
    assert Staff.objects.get(employee_no="1600").department.name == "FINANCE"
    # normalisation
    assert Staff.objects.get(employee_no="1500").state_of_origin.name == "KANO"
    assert Staff.objects.get(employee_no="1500").state.name == "Confirmed"
    assert Staff.objects.get(employee_no="1500").department.code == "hr"
    assert Staff.objects.get(employee_no="1501").state_of_origin.name == "AKWA IBOM"
    assert Staff.objects.get(employee_no="1503").department.name == "ZONAL OFFICE AWKA"
    assert Staff.objects.get(employee_no="1503").grade_level == "6"
    assert Staff.objects.get(employee_no="1487").state.name == "Probation"
    # retired person only on the old sheet is flagged
    assert "900" in [m[0] for m in res.missing_from_latest]


def test_warnings_dates_and_duplicates():
    res = run(dry_run=True)
    msgs = " | ".join(w.message for w in res.date_warnings)
    assert "3-digit year '234' read as 2024" in msgs
    assert "read as month/day" in msgs
    assert "not a plausible birth date" in msgs                  # 2026 DOB
    dups = res.duplicate_warnings
    assert len(dups) == 1 and "1487" in dups[0].message and "MUSA" in dups[0].message
    assert Staff.objects.count() == 0


def test_empty_incoming_never_clobbers():
    run()
    s = Staff.objects.get(employee_no="1503")
    s.email = "okeke@nbte.gov.ng"
    s.lga = "AWKA NORTH"           # will be overwritten by a non-empty value
    s.save()
    run()
    s.refresh_from_db()
    assert s.email == "okeke@nbte.gov.ng"
    assert s.lga == "AWKA SOUTH"


def test_skip_and_fail_modes():
    run()
    Staff.objects.filter(employee_no="1413").update(grade_level="1")
    skipped = run(mode="skip")
    assert skipped.new == 0 and skipped.updated == 0 and skipped.skipped >= 9
    assert Staff.objects.get(employee_no="1413").grade_level == "1"
    failed = run(mode="fail")
    assert failed.aborted and failed.status == "failed"
    assert Staff.objects.get(employee_no="1413").grade_level == "1"
    assert ImportSession.objects.filter(status="failed").exists()


def test_lookups_created_and_existing_kept():
    hr = Department.objects.create(name="Human Resources", code="hr", sort_order=7)
    run()
    hr.refresh_from_db()
    assert hr.name == "Human Resources" and hr.sort_order == 7     # never modified
    for name in ("ES OFFICE", "ICT", "FINANCE", "ZONAL OFFICE AWKA"):
        assert Department.objects.filter(name=name).exists(), name
    assert Designation.objects.filter(name="EXECUTIVE SECRETARY", rank=0).exists()
    assert StateOfOrigin.objects.filter(name="AKWA IBOM", zone="SS").exists()
    assert set(StaffState.objects.values_list("name", flat=True)) >= {"Confirmed", "Probation"}


def test_audit_entries_per_staff():
    run()
    assert AuditLogEntry.objects.filter(action="import_nominal_roll").count() == 9
    e = AuditLogEntry.objects.filter(action="import_nominal_roll", object_repr__contains="1413").first()
    assert e.changes["source"] == "NOMINAL ROLL WITH QUALIFICATION" and e.changes["row"] == 8


def test_male_and_female():
    run()
    assert Staff.objects.filter(gender="M").exists() and Staff.objects.filter(gender="F").exists()


def test_seeded_designations_are_matched_not_duplicated():
    call_command("seed_nbte", "--quiet", verbosity=0)
    n = Designation.objects.count()
    run()
    jabir = Staff.objects.get(employee_no="1413")
    assert jabir.designation.rank > 0          # the seeded, ranked record
    assert Designation.objects.count() == n    # every spelling matched by alias


def test_command_writes_report(tmp_path):
    path = tmp_path / "roll.xlsx"
    path.write_bytes(build_sample_workbook())
    call_command("import_nominal_roll", str(path), "--dry-run", stdout=io.StringIO())
    report = (tmp_path / "roll.xlsx.report.txt").read_text()
    assert "Sheet: NOMINAL ROLL WITH QUALIFICATION" in report and "DRY RUN" in report
    assert Staff.objects.count() == 0


# ---------------------------------------------------------------- the real HR file
@pytest.mark.skipif(not (REAL_FILE and os.path.isfile(REAL_FILE)),
                    reason="set NBTE_NOMINAL_ROLL_XLSX to the HR nominal roll to run")
def test_real_nominal_roll():
    def go():
        with open(REAL_FILE, "rb") as fh:
            return NominalRollImporter(fh, filename=os.path.basename(REAL_FILE)).run(dry_run=False)
    first = go()
    assert not first.aborted
    assert first.new >= 400
    assert len(first.date_warnings) >= 10
    assert len(first.duplicate_warnings) >= 2
    assert Staff.objects.filter(gender="M").count() > 100 and Staff.objects.filter(gender="F").count() > 100
    second = go()
    assert (second.new, second.updated) == (0, 0)
    import openpyxl
    wb = openpyxl.load_workbook(REAL_FILE, read_only=True, data_only=True)
    from staff.nominal_roll import LookupResolver, scan_workbook
    resolver = LookupResolver()
    wanted = set()
    for scan in scan_workbook(wb):
        for rec in scan.rows:
            name = resolver.department_name(rec.data.get("department"), rec.section)
            if name:
                wanted.add(name)
    for name in wanted:
        assert Department.objects.filter(name__iexact=name).exists() or resolver.department(name), name
    jabir = Staff.objects.get(employee_no="1413")
    assert (jabir.last_name, jabir.date_of_birth) == ("JABIR", date(1996, 6, 10))
