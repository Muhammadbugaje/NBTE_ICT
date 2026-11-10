"""v2.1 X45: import diff specs for institutions, programmes, management roles
and the curriculum catalogue.

Each spec round-trips through the diff engine: export → edit → preview →
apply, with new rows created, changed fields listed old → new and rows missing
from the file never deleted.
"""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook

from core import importdiff

pytestmark = pytest.mark.django_db


class NS:
    pass


@pytest.fixture
def boss(qa_seed, make_user):
    """A super admin: passes every spec's ``can_use``."""
    return make_user("x45.boss@nbte.local", superuser=True)


def _spec(name):
    return importdiff.get_spec(name)


def _rows(name):
    data = importdiff.export_workbook(_spec(name))
    wb = load_workbook(io.BytesIO(data))
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def _upload(rows, name="import.xlsx"):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


def _row(header, **values):
    return [values.get(h, "") for h in header]


@pytest.fixture
def lookups(db):
    from institutions.models import InstitutionCategory, InstitutionOwnership
    from staff.models import StateOfOrigin

    ns = NS()
    ns.ownership = (InstitutionOwnership.objects.filter(code="public").first()
                    or InstitutionOwnership.objects.create(code="public", name="Public"))
    ns.category = (InstitutionCategory.objects.filter(name="Polytechnic").first()
                   or InstitutionCategory.objects.create(name="Polytechnic"))
    # Programme and catalogue rows take a ProgrammeCategory, not an institution one.
    from institutions.models import ProgrammeCategory

    ns.pcategory = (ProgrammeCategory.objects.filter(name="Polytechnic").first()
                    or ProgrammeCategory.objects.create(name="Polytechnic"))
    ns.state = (StateOfOrigin.objects.filter(code="KD").first()
                or StateOfOrigin.objects.create(code="KD", name="Kaduna"))
    return ns


def test_institution_import_creates_and_updates(boss, lookups, make_institution):
    from institutions.models import Institution, InstitutionAddress, InstitutionContact

    inst = make_institution("IMA", name="Ima Polytechnic")
    InstitutionAddress.unscoped.create(institution=inst, kind="permanent_site", line1="1 Old Road",
                                       town="Kaduna", state=lookups.state)
    InstitutionContact.unscoped.create(institution=inst, kind="phone", value="08030000001")
    InstitutionContact.unscoped.create(institution=inst, kind="official_email", value="info@ima.edu.ng")

    rows = _rows("institutions")
    header = rows[0]
    target = next(r for r in rows[1:] if r[header.index("Code")] == "IMA")
    target[header.index("Phone")] = "08120009999"
    target[header.index("Name")] = "Ima Polytechnic (Renamed)"
    rows.append(_row(header, **{
        "Code": "IMB", "Name": "Imb Polytechnic", "Ownership": "Public", "Category": "Polytechnic",
        "State": "Kaduna", "Address": "2 New Road", "Town": "Zaria", "Official e-mail": "info@imb.edu.ng",
        "Phone": "08030000002", "Status": "Approved",
    }))
    run = importdiff.preview(_spec("institutions"), _upload(rows), user=boss)
    assert run.counts.get("new") == 1
    assert run.counts.get("changed") == 1

    assert importdiff.apply(run, user=boss) == {"applied": 2, "conflicts": 0, "failed": 0}
    inst.refresh_from_db()
    assert inst.name == "Ima Polytechnic (Renamed)"
    assert InstitutionContact.unscoped.get(institution=inst, kind="phone", is_current=True).value == "08120009999"
    new = Institution.unscoped.get(code="IMB")
    assert new.status == "approved"
    assert InstitutionAddress.unscoped.get(institution=new, kind="permanent_site").town == "Zaria"


def test_programme_import_derives_expiry(boss, lookups, make_institution):
    import datetime

    from accreditation.models import ApprovalType
    from institutions.models import Programme, ProgrammeLevel

    inst = make_institution("PMA")
    nd = ProgrammeLevel.objects.get(code="nd")
    programme = Programme.unscoped.create(institution=inst, name="Computer Science", level=nd,
                                          status="proposed", category=lookups.pcategory)
    approval = (ApprovalType.objects.filter(code="full").first()
                or ApprovalType.objects.create(code="full", name="Full accreditation", duration_months=60,
                                               programme_status="accredited"))

    rows = _rows("programmes")
    header = rows[0]
    target = next(r for r in rows[1:] if r[header.index("Institution code")] == "PMA")
    target[header.index("Status")] = "Accredited"
    target[header.index("Approval type")] = "Full accreditation"
    target[header.index("Commencement date")] = "2026-10-01"
    rows.append(_row(header, **{
        "Institution code": "PMA", "Programme": "Electrical Engineering", "Level": "ND",
        "Category": "Polytechnic", "Status": "Approval to commence", "Approval type": "Full accreditation",
        "Approved streams": 2, "Commencement date": "2026-10-01",
    }))
    run = importdiff.preview(_spec("programmes"), _upload(rows), user=boss)
    assert run.counts.get("new") == 1
    assert run.counts.get("changed") == 1
    assert importdiff.apply(run, user=boss) == {"applied": 2, "conflicts": 0, "failed": 0}

    programme.refresh_from_db()
    assert programme.status == "accredited"
    assert programme.approval_type_id == approval.pk
    assert programme.expiry_date == approval.expiry_for(datetime.date(2026, 10, 1))
    new = Programme.unscoped.get(institution=inst, name="Electrical Engineering", level=nd)
    assert new.approved_streams == 2
    assert new.expiry_date == approval.expiry_for(datetime.date(2026, 10, 1))


def test_management_roles_import(boss):
    from institutions.models import ManagementRole

    role = (ManagementRole.objects.filter(code="registrar").first()
            or ManagementRole.objects.create(code="registrar", name="Registrar", max_holders=1))
    rows = _rows("management_roles")
    header = rows[0]
    target = next(r for r in rows[1:] if r[header.index("Code")] == "registrar")
    target[header.index("Max holders")] = 2
    rows.append(_row(header, **{"Code": "bursar", "Name": "Bursar", "Sort order": 20, "Mandatory": "Yes",
                                "Max holders": 1, "Requires e-mail": "Yes", "Requires phone": "Yes",
                                "Chief executive": "No", "Head of ICT": "No", "Active": "Yes"}))
    run = importdiff.preview(_spec("management_roles"), _upload(rows), user=boss)
    assert run.counts.get("new") == 1
    assert run.counts.get("changed") == 1
    assert importdiff.apply(run, user=boss) == {"applied": 2, "conflicts": 0, "failed": 0}
    role.refresh_from_db()
    assert role.max_holders == 2
    assert ManagementRole.objects.get(code="bursar").name == "Bursar"


def test_curriculum_requirements_import(boss, lookups):
    from accreditation.models import CatalogueItem

    item = CatalogueItem.objects.create(name="Microscope", kind="equipment", programme_name="Science Laboratory",
                                        category=lookups.pcategory, required_quantity=5)
    rows = _rows("curriculum_requirements")
    header = rows[0]
    target = next(r for r in rows[1:] if r[header.index("Item")] == "Microscope")
    target[header.index("Required quantity")] = 8
    rows.append(_row(header, **{
        "Category": "Polytechnic", "Item": "Autoclave", "Kind": "equipment", "Required quantity": 2,
        "Unit": "no.", "Weight": 2, "Critical": "Yes", "Active": "Yes",
    }))
    run = importdiff.preview(_spec("curriculum_requirements"), _upload(rows), user=boss)
    assert run.counts.get("new") == 1
    assert run.counts.get("changed") == 1
    assert importdiff.apply(run, user=boss) == {"applied": 2, "conflicts": 0, "failed": 0}
    item.refresh_from_db()
    assert item.required_quantity == 8
    new = CatalogueItem.objects.get(name="Autoclave")
    assert new.is_critical is True and new.required_quantity == 2


def test_missing_rows_are_never_deleted(boss, make_institution):
    from institutions.models import Institution

    keep = make_institution("MIS")
    make_institution("MIS2")
    rows = _rows("institutions")
    rows = [r for r in rows if r[0] != "MIS2"]
    run = importdiff.preview(_spec("institutions"), _upload(rows), user=boss)
    assert run.counts.get("missing") == 1
    importdiff.apply(run, user=boss)
    assert Institution.unscoped.filter(code="MIS2").exists()
    assert Institution.unscoped.filter(pk=keep.pk).exists()
