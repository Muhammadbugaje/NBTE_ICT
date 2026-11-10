"""X45 / S-15 / S-16: import diff engine with the staff spec."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from openpyxl import Workbook, load_workbook

from core import importdiff
from core.models import ImportRow, ImportRun

pytestmark = pytest.mark.django_db
PW = "Imp-test-2026!"


@pytest.fixture
def hr_user(db):
    from django.contrib.auth.models import Permission

    from accounts.models import User

    u = User(email="hr.import@demo.nbte.local", first_name="Halima", last_name="Sani", user_type="staff", must_change_password=False)
    u.set_password(PW)
    u.save()
    u.user_permissions.add(Permission.objects.get(codename="import_nominal_roll"))
    return User.objects.get(pk=u.pk)


@pytest.fixture
def staff_rows(db):
    from staff.models import Department, Staff

    ict = Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]
    rows = []
    for i, (first, last) in enumerate([("Musa", "Ibrahim"), ("Ngozi", "Eze"), ("Yusuf", "Adamu")], start=1):
        rows.append(Staff.objects.create(employee_no=f"NBTE/0{400 + i}", first_name=first, last_name=last,
                                         email=f"{first.lower()}.{last.lower()}@nbte.gov.ng", phone=f"0803000000{i}",
                                         department=ict))
    return rows


def _spec():
    return importdiff.get_spec("staff")


def _export_rows():
    wb = load_workbook(io.BytesIO(importdiff.export_workbook(_spec())))
    ws = wb.worksheets[0]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    return wb, rows


def _upload(rows, name="staff.xlsx"):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


def _find(rows, emp):
    i = rows[0].index("Employee no.")
    return next(r for r in rows[1:] if r[i] == emp)


def _row(header, **values):
    return [values.get(h, "") for h in header]


def test_x45_roundtrip_new_only(hr_user, staff_rows):
    from staff.models import Staff

    _wb, rows = _export_rows()
    header = rows[0]
    assert header[-1] == "__key"
    rows.append(_row(header, **{"Employee no.": "NBTE/0533", "First name": "Sadiq", "Last name": "Lawan",
                                "Email": "sadiq.lawan@nbte.gov.ng"}))
    rows.append(_row(header, **{"Employee no.": "NBTE/0534", "First name": "Amaka", "Last name": "Obi",
                                "Email": "amaka.obi@nbte.gov.ng"}))
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    assert run.counts == {"new": 2, "unchanged": len(rows) - 3}
    before = Staff.objects.count()
    result = importdiff.apply(run, user=hr_user)
    assert result == {"applied": 2, "conflicts": 0, "failed": 0}
    assert Staff.objects.count() == before + 2
    run.refresh_from_db()
    assert run.status == ImportRun.APPLIED


def test_x45_changed_field_level_accept(hr_user, staff_rows):
    from staff.models import Staff

    _wb, rows = _export_rows()
    header = rows[0]
    phone_i, last_i = header.index("Phone"), header.index("Last name")
    musa_row = _find(rows, "NBTE/0401")
    musa_row[phone_i] = "08120009999"
    musa_row[last_i] = "Ibrahim-Bello"
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    assert run.counts == {"changed": 1, "unchanged": len(rows) - 2}
    row = run.rows.get(action="changed")
    assert set(row.diff) == {"phone", "last_name"}
    assert row.diff["phone"] == {"old": "08030000001", "new": "08120009999", "label": "Phone"}
    importdiff.set_field(run, row.pk, "phone", False)                 # untick the phone change
    importdiff.apply(run, user=hr_user)
    musa = Staff.objects.get(employee_no="NBTE/0401")
    assert musa.last_name == "Ibrahim-Bello" and musa.phone == "08030000001"


def test_x45_missing_never_deleted(hr_user, staff_rows):
    from staff.models import Staff

    _wb, rows = _export_rows()
    rows.remove(_find(rows, "NBTE/0403"))                              # drop Yusuf
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    assert run.counts == {"unchanged": len(rows) - 1, "missing": 1}
    importdiff.set_all(run, "missing", True)                          # cannot select "missing"
    importdiff.apply(run, user=hr_user)
    assert Staff.objects.filter(employee_no="NBTE/0403").exists()
    assert _spec().allow_deactivate_missing is False


def test_x45_conflict_optimistic(hr_user, staff_rows):
    from staff.models import Staff

    _wb, rows = _export_rows()
    _find(rows, "NBTE/0401")[rows[0].index("Phone")] = "08120001111"
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    musa = Staff.objects.get(employee_no="NBTE/0401")
    musa.phone = "07000000000"                                         # someone edits after the preview
    musa.save()
    result = importdiff.apply(run, user=hr_user)
    assert result["conflicts"] == 1 and result["applied"] == 0
    musa.refresh_from_db()
    assert musa.phone == "07000000000"
    run.refresh_from_db()
    assert run.status == ImportRun.PARTIAL and run.rows.filter(action="conflict").count() == 1


def test_x45_duplicate_keys_in_file(hr_user, staff_rows):
    _wb, rows = _export_rows()
    rows.append(list(_find(rows, "NBTE/0401")))
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    err = run.rows.get(action="error")
    assert "Duplicate key" in err.error


def test_x45_header_signature_and_wrong_template(hr_user, staff_rows):
    _wb, rows = _export_rows()
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    assert run.header_signature == importdiff.header_signature(rows[0])
    with pytest.raises(importdiff.ImportRejected):
        importdiff.preview(_spec(), _upload([["Totally", "Other"], ["a", "b"]]), user=hr_user)


def test_import_limits(hr_user, staff_rows, settings):
    from config.models import ConfigEntry

    ConfigEntry.objects.create(key="qa.import.max_rows", value="2", value_type="int")
    _wb, rows = _export_rows()
    with pytest.raises(importdiff.ImportRejected) as exc:
        importdiff.preview(_spec(), _upload(rows), user=hr_user)
    assert "more than 2 rows" in str(exc.value)


def test_export_formula_injection_safe(hr_user, staff_rows):
    from staff.models import Staff

    s = staff_rows[0]
    s.duties = '=HYPERLINK("http://evil.example","x")'
    s.save()
    wb, rows = _export_rows()
    duties = _find(rows, s.employee_no)[rows[0].index("Duties")]
    assert duties.startswith("'=")
    # and the round trip reads the original text back (no change detected)
    run = importdiff.preview(_spec(), _upload(rows), user=hr_user)
    assert run.counts.get("changed", 0) == 0
    from core import excel

    assert excel.neutralise("+2348030000000") == "'+2348030000000" and excel.neutralise(5) == 5


def test_x45_requires_confirmation_specs():
    for name, cls in importdiff.DIFF_REGISTRY.items():
        spec = cls()
        if spec.requires_confirmation:
            pytest.fail(f"{name}: confirmation specs arrive with X47 (Phase F)")


@pytest.mark.parametrize("name", sorted(importdiff.DIFF_REGISTRY))
def test_x45_spec_contract(name):
    spec = importdiff.get_spec(name)
    assert spec.key_fields and spec.compare_fields and spec.permission
    fields = {c.field for c in spec.columns}
    assert set(spec.key_fields) <= fields
    assert set(spec.compare_fields) <= fields
    for alt in spec.alt_keys:
        assert set(alt) <= fields


def test_x45_preview_page_owner_only_and_htmx(hr_user, staff_rows):
    _wb, rows = _export_rows()
    _find(rows, "NBTE/0401")[rows[0].index("Phone")] = "08120001111"
    c = Client()
    c.force_login(hr_user)
    r = c.post("/imports/new/staff/", {"file": _upload(rows)})
    assert r.status_code == 302
    url = r["Location"]
    html = c.get(url).content.decode()
    assert "Check before anything changes" in html and "0803" in html and "08120001111" in html
    run = ImportRun.objects.get(uploaded_by=hr_user)
    row = run.rows.get(action="changed")
    r = c.post(f"/imports/{run.pk}/toggle/", {"row": row.pk, "field": "phone"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "0 of 1 change" in r.content.decode()
    tab = c.get(f"/imports/{run.pk}/tab/unchanged/", HTTP_HX_REQUEST="true").content.decode()
    assert 'id="idiff-tab"' in tab and "<html" not in tab
    full = c.get(f"/imports/{run.pk}/?tab=unchanged").content.decode()
    assert "<html" in full
    # another user cannot see the preview
    from accounts.models import User

    other = User.objects.create_user(email="other.import@demo.nbte.local", password=PW, must_change_password=False)
    c2 = Client()
    c2.force_login(other)
    assert c2.get(url).status_code == 404
