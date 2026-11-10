"""Data Hub - Assets category: export -> import round trips, holding rules,
unknown links, generated tags, idempotent re-imports and the list-page buttons."""
import io
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from openpyxl import load_workbook

from core.datahub import engine

pytestmark = pytest.mark.django_db
PW = "Hub-assets-2026!"


def _user(email, superuser=False, department=None, staff_no=None):
    from accounts.models import User
    from staff.models import Staff
    u = User(email=email, first_name="Asset", last_name=email.split("@")[0], user_type="staff",
             is_superuser=superuser, is_staff=superuser, must_change_password=False, department=department)
    u.set_password(PW)
    u.save()
    if staff_no:
        Staff.objects.filter(user=u).update(employee_no=staff_no)
    return u


def _import(data, apply=True, actor=None):
    return engine.import_workbook(io.BytesIO(data), apply=apply, categories=["assets"], actor=actor)


def _errors(report):
    return [(s.title, s.errors) for s in report.sheets if s.errors]


def _sheet(report, label):
    return next(s for s in report.sheets if s.label == label)


@pytest.fixture
def world():
    from assets import services as asvc
    from assets.models import AssetAttachment, AssetCategory, AssetIssue
    from staff.models import Department

    ict = Department.objects.create(code="hub-ict", name="Hub ICT Department")
    reg = Department.objects.create(code="hub-reg", name="Hub Registry")
    admin = _user("hub.asset.admin@nbte.gov.ng", superuser=True, department=ict)
    alice = _user("alice.assets@nbte.gov.ng", department=ict, staff_no="NBTE/A-1")
    bob = _user("bob.assets@nbte.gov.ng", department=reg, staff_no="NBTE/B-2")
    laptop = AssetCategory.objects.create(name="Hub Laptop", code="hub-laptop",
                                          spec_fields=[{"key": "ram_gb", "label": "RAM", "type": "number"}])
    kettle = AssetCategory.objects.create(name="Hub Kettle", code="hub-kettle")

    personal = asvc.create_asset(category=laptop, owner_department=ict, asset_tag="HUB-LT-1",
                                 serial_number="SN-LT-1", brand="Dell", model="Latitude 5440", color="Black",
                                 description="Officer laptop", spec={"ram_gb": 16}, current_location="Block B, Room 12",
                                 state="in_service", acquired_at=date(2025, 3, 15), cost=Decimal("1250000.50"),
                                 warranty_until=date(2027, 3, 14), notes="Bought under 2025 budget")
    dept_asset = asvc.create_asset(category=kettle, owner_department=reg, asset_tag="HUB-KT-1", state="in_service",
                                   assignment_mode="department")
    shared = asvc.create_asset(category=kettle, owner_department=ict, asset_tag="HUB-KT-2", state="in_service",
                               assignment_mode="shared")
    store = asvc.create_asset(category=laptop, owner_department=None, asset_tag="HUB-LT-2", state="new")

    asvc.assign_asset(asset=personal, to_user=alice, by_user=admin, condition="good", location="Room 12",
                      expected_return_at=date(2026, 12, 31))
    asvc.assign_asset(asset=dept_asset, department=reg, by_user=admin)
    asvc.assign_asset(asset=shared, to_user=alice, by_user=admin)
    asvc.assign_asset(asset=shared, to_user=bob, by_user=admin)
    # A closed hand-off in the history.
    old = asvc.assign_asset(asset=store, to_user=bob, by_user=admin)
    asvc.return_asset(asset=store, by_user=admin, condition_on_return="fair", notes="Back in store", assignment=old)
    AssetIssue.objects.create(asset=personal, reported_by=alice, summary="Battery drains fast",
                              description="Two hours only")
    AssetAttachment.objects.create(asset=personal, uploaded_by=admin, file="assets/attachments/receipt-hub.pdf",
                                   original_name="receipt.pdf", kind="receipt")
    return {"ict": ict, "reg": reg, "admin": admin, "alice": alice, "bob": bob, "laptop": laptop,
            "kettle": kettle, "personal": personal, "dept": dept_asset, "shared": shared, "store": store}


def _snapshot():
    from assets.models import Asset, AssetAssignment, AssetAttachment, AssetCategory, AssetIssue
    assets = {a.asset_tag: (a.serial_number, a.category.code, a.brand, a.model, a.color, a.description, a.spec,
                            a.owner_department.code if a.owner_department else None, a.current_location, a.state,
                            a.acquired_at, a.cost, a.warranty_until, a.notes, a.assignment_mode,
                            a.current_custodian.email if a.current_custodian else None, a.is_deleted)
              for a in Asset.all_objects.select_related("category", "owner_department", "current_custodian")}
    rows = sorted(
        (r.asset.asset_tag, r.assigned_to.email if r.assigned_to else None,
         r.department.code if r.department else None, r.assigned_at, r.assigned_by.email, r.expected_return_at,
         r.returned_at, r.returned_to.email if r.returned_to else None, r.condition_on_assign,
         r.condition_on_return, r.location_on_assign, r.notes)
        for r in AssetAssignment.objects.select_related("asset", "assigned_to", "department", "assigned_by",
                                                        "returned_to"))
    issues = sorted((i.asset.asset_tag, i.summary, i.description, i.reported_at, i.reported_by.email)
                    for i in AssetIssue.objects.select_related("asset", "reported_by"))
    files = sorted((f.asset.asset_tag, f.file.name, f.original_name, f.kind, f.uploaded_by.email)
                   for f in AssetAttachment.objects.select_related("asset", "uploaded_by"))
    cats = sorted(AssetCategory.objects.values_list("name", "code", "spec_fields", "is_active"))
    return assets, rows, issues, files, cats


def _wipe_assets():
    from assets.models import Asset, AssetAssignment, AssetAttachment, AssetCategory, AssetIssue
    AssetAssignment.objects.all().delete()
    AssetIssue.objects.all().delete()
    AssetAttachment.objects.all().delete()
    for a in Asset.all_objects.all():
        a.hard_delete()
    AssetCategory.objects.all().delete()


def _rows(data, title):
    wb = load_workbook(io.BytesIO(data), read_only=True)
    rows = list(wb[title].iter_rows(values_only=True))
    wb.close()
    return rows[0], rows[1:]


def _edit(data, title, fn):
    """Load the workbook, call fn(ws, header) and return the new bytes."""
    wb = load_workbook(io.BytesIO(data))
    ws = wb[title]
    header = [c.value for c in ws[1]]
    fn(ws, header)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _append(ws, header, **cells):
    ws.append([cells.get(h) for h in header])


# ---------------------------------------------------------------------------
# Plan and workbook layout
# ---------------------------------------------------------------------------
def test_assets_category_sits_after_organisation_and_users():
    keys = [s.id for s in engine.plan()]
    cats = [s.category for s in engine.plan()]
    first_asset = cats.index("assets")
    assert cats.index("org") < first_asset and cats.index("users") < first_asset
    assert keys.index("assets.AssetCategory") < keys.index("assets.Asset") < keys.index("assets.AssetAssignment")
    asset_sheet = engine.sheet_by_id("assets.Asset")
    assert asset_sheet.key == ("asset_tag",) and "current_custodian" not in asset_sheet.columns
    for col in ("serial_number", "category", "brand", "model", "owner_department", "current_location", "state",
                "acquired_at", "cost", "warranty_until", "notes", "assignment_mode", "spec"):
        assert col in asset_sheet.columns


def test_export_has_labels_dropdowns_and_instructions(world):
    data = engine.export_workbook(["assets"])
    wb = load_workbook(io.BytesIO(data))
    assert "Instructions" in wb.sheetnames and wb["__lists"].sheet_state == "hidden"
    ws = wb["Assets"]
    header = [c.value for c in ws[1]]
    modes = {r[header.index("asset_tag")].value: r[header.index("assignment_mode")].value
             for r in ws.iter_rows(min_row=2)}
    assert modes["HUB-LT-1"] == "Individual (one holder)"
    assert modes["HUB-KT-1"] == "Department (whole department)"
    assert modes["HUB-KT-2"] == "Shared (several named people)"
    formulas = " ".join(str(dv.formula1) for dv in ws.data_validations.dataValidation)
    assert "Individual (one holder)" in formulas and "In service" in formulas and "__lists" in formulas
    wb.close()

    blank = engine.export_workbook(["assets"], include_rows=False)
    wb = load_workbook(io.BytesIO(blank))
    assert wb["Assets"].max_row == 1 and wb["Assets"].data_validations.dataValidation
    assert "Instructions" in wb.sheetnames
    wb.close()


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------
def test_round_trip_personal_department_and_shared_assets(world):
    from assets.models import Asset
    before = _snapshot()
    assert before[0]["HUB-LT-1"][15] == "alice.assets@nbte.gov.ng"       # custodian of the personal laptop
    data = engine.export_workbook(["assets"])
    _wipe_assets()

    preview = _import(data, apply=False, actor=world["admin"])
    assert not preview.fatal and preview.error_count == 0, _errors(preview)
    assert not Asset.all_objects.exists()                                # a preview saves nothing

    report = _import(data, actor=world["admin"])
    assert report.applied and report.error_count == 0, _errors(report)
    assert _sheet(report, "assets.Asset").created == 4
    assert _sheet(report, "assets.AssetAssignment").created == 5
    after = _snapshot()
    assert after == before

    again = _import(data, actor=world["admin"])
    assert again.error_count == 0 and again.created == 0 and again.updated == 0, _errors(again)
    assert _snapshot() == before


def test_reimport_over_existing_data_is_idempotent_and_updates_cells(world):
    from assets.models import Asset
    data = engine.export_workbook(["assets"])
    first = _import(data)
    assert first.created == 0 and first.updated == 0 and first.error_count == 0, _errors(first)

    def change(ws, header):
        for r in ws.iter_rows(min_row=2):
            if r[header.index("asset_tag")].value == "HUB-LT-2":
                r[header.index("cost")].value = "₦ 98,500.25"
                r[header.index("acquired_at")].value = "01/02/2024"
                r[header.index("current_location")].value = "Store room"
    report = _import(_edit(data, "Assets", change))
    sheet = _sheet(report, "assets.Asset")
    assert sheet.updated == 1 and sheet.created == 0 and report.error_count == 0, _errors(report)
    a = Asset.objects.get(asset_tag="HUB-LT-2")
    assert a.cost == Decimal("98500.25") and a.acquired_at == date(2024, 2, 1) and a.current_location == "Store room"
    assert Asset.all_objects.count() == 4


# ---------------------------------------------------------------------------
# Holding rules
# ---------------------------------------------------------------------------
def test_wrong_ownership_data_is_rejected(world):
    from assets.models import Asset, AssetAssignment
    tpl = engine.export_workbook(["assets"], include_rows=False)

    def assets(ws, header):
        _append(ws, header, asset_tag="HUB-BAD-1", category="Hub Kettle", state="In service",
                assignment_mode="Department (whole department)")              # no owner_department
        _append(ws, header, asset_tag="HUB-LT-1", category="Hub Laptop", owner_department="hub-ict",
                assignment_mode="Shared (several named people)")               # mode change while held

    def assignments(ws, header):
        _append(ws, header, asset="HUB-LT-1", department="hub-ict", assigned_at="2026-01-05")  # individual + dept
        _append(ws, header, asset="HUB-LT-1", assigned_to="bob.assets@nbte.gov.ng",
                assigned_at="2026-01-06")                                    # second open holder
        _append(ws, header, asset="HUB-KT-1", assigned_to="bob.assets@nbte.gov.ng",
                assigned_at="2026-01-07")                                    # department asset + person
        _append(ws, header, asset="HUB-KT-2", assigned_to="alice.assets@nbte.gov.ng",
                assigned_at="2026-01-08")                                    # shared: already holds a share
    data = _edit(_edit(tpl, "Assets", assets), "Asset assignments", assignments)
    report = _import(data, actor=world["admin"])
    a_errs = [m for _r, m in _sheet(report, "assets.Asset").errors]
    r_errs = [m for _r, m in _sheet(report, "assets.AssetAssignment").errors]
    assert len(a_errs) == 2 and "owner_department is required" in a_errs[0] and "while the asset is held" in a_errs[1]
    assert len(r_errs) == 4, r_errs
    assert "leave department blank" in r_errs[0] and "already held by" in r_errs[1]
    assert "give the department" in r_errs[2] and "already holds a share" in r_errs[3]
    assert not Asset.all_objects.filter(asset_tag="HUB-BAD-1").exists()
    assert Asset.objects.get(asset_tag="HUB-LT-1").assignment_mode == "individual"
    assert AssetAssignment.objects.count() == 5


def test_unknown_department_user_and_category_are_rejected(world):
    from assets.models import Asset, AssetAssignment
    tpl = engine.export_workbook(["assets"], include_rows=False)

    def assets(ws, header):
        _append(ws, header, asset_tag="HUB-U-1", category="Hub Laptop", owner_department="no-such-dept")
        _append(ws, header, asset_tag="HUB-U-2", category="Spaceship")
        _append(ws, header, asset_tag="HUB-U-3", owner_department="hub-ict")      # no category

    def assignments(ws, header):
        _append(ws, header, asset="HUB-LT-2", assigned_to="ghost@nbte.gov.ng")
        _append(ws, header, asset="NO-SUCH-TAG", assigned_to="alice.assets@nbte.gov.ng")
    data = _edit(_edit(tpl, "Assets", assets), "Asset assignments", assignments)
    report = _import(data, actor=world["admin"])
    a_errs = [m for _r, m in _sheet(report, "assets.Asset").errors]
    r_errs = [m for _r, m in _sheet(report, "assets.AssetAssignment").errors]
    assert "no NBTE department with code 'no-such-dept'" in a_errs[0]
    assert "no asset category named 'Spaceship'" in a_errs[1]
    assert "category is required" in a_errs[2]
    assert "no user account with e-mail (or staff number) 'ghost@nbte.gov.ng'" in r_errs[0]
    assert "no assets.Asset with key 'NO-SUCH-TAG'" in r_errs[1] or "NO-SUCH-TAG" in r_errs[1]
    assert not Asset.all_objects.filter(asset_tag__startswith="HUB-U-").exists()
    assert AssetAssignment.objects.count() == 5


# ---------------------------------------------------------------------------
# Generated tags, aliases, parsing
# ---------------------------------------------------------------------------
def test_blank_tag_is_generated_and_serial_makes_reimport_idempotent(world):
    from assets.models import Asset, AssetAssignment
    tpl = engine.export_workbook(["assets"], include_rows=False)

    def assets(ws, header):
        _append(ws, header, category="hub-laptop", owner_department="Hub ICT Department", serial_number="SN-NEW-9",
                brand="HP", state="New", cost="1,500,000", acquired_at="15/03/2025")
        _append(ws, header, category="Hub Laptop", owner_department="hub-ict", brand="Lenovo")

    def assignments(ws, header):
        _append(ws, header, asset="SN-NEW-9", assigned_to="NBTE/B-2", condition_on_assign="Good")
    data = _edit(_edit(tpl, "Assets", assets), "Asset assignments", assignments)

    report = _import(data, actor=world["admin"])
    assert report.error_count == 0, _errors(report)
    new = list(Asset.objects.filter(brand__in=("HP", "Lenovo")).order_by("asset_tag"))
    assert [a.asset_tag for a in new] == ["HUB-IC-HUB-LA-00001", "HUB-IC-HUB-LA-00002"]
    hp = next(a for a in new if a.brand == "HP")
    assert hp.cost == Decimal("1500000.00") and hp.acquired_at == date(2025, 3, 15)
    assert hp.owner_department == world["ict"] and hp.category == world["laptop"]
    row = AssetAssignment.objects.get(asset=hp, returned_at__isnull=True)
    assert row.assigned_to == world["bob"] and row.assigned_by == world["admin"]
    hp.refresh_from_db()
    assert hp.current_custodian == world["bob"]

    # The same file again: the serial number finds the HP laptop (no duplicate, no second
    # hand-off); the Lenovo row has neither tag nor serial, so it is a new asset each time.
    again = _import(data, actor=world["admin"])
    assert again.error_count == 0, _errors(again)
    assert Asset.objects.filter(brand="HP").count() == 1
    assert AssetAssignment.objects.filter(asset=hp).count() == 1
    assert _sheet(again, "assets.AssetAssignment").created == 0

    # The export carries the generated tags, so re-importing an export never duplicates.
    export = engine.export_workbook(["assets"])
    count = Asset.all_objects.count()
    third = _import(export, actor=world["admin"])
    assert third.created == 0 and third.updated == 0 and third.error_count == 0
    assert Asset.all_objects.count() == count


def test_existing_tag_spelling_is_kept_and_new_tags_are_uppercased(world):
    from assets.models import Asset
    tpl = engine.export_workbook(["assets"], include_rows=False)

    def assets(ws, header):
        _append(ws, header, asset_tag="hub-lt-1", category="Hub Laptop", owner_department="hub-ict",
                brand="Dell", model="Latitude 5440", serial_number="SN-LT-1", state="In service")
        _append(ws, header, asset_tag="hub-new-7", category="Hub Laptop")
    report = _import(_edit(tpl, "Assets", assets), actor=world["admin"])
    assert report.error_count == 0, _errors(report)
    assert Asset.objects.filter(asset_tag="HUB-LT-1").count() == 1
    assert Asset.objects.filter(asset_tag="HUB-NEW-7").exists()
    assert not Asset.all_objects.filter(asset_tag="hub-lt-1").exists()


# ---------------------------------------------------------------------------
# List page buttons and permissions
# ---------------------------------------------------------------------------
def test_assets_list_buttons_and_export_permissions(world):
    from django.contrib.auth.models import Permission

    def client(user):
        c = Client()
        c.force_login(user)
        return c

    url = reverse("assets:export_xlsx")
    admin = client(world["admin"])
    page = admin.get(reverse("assets:list")).content.decode()
    assert url in page and "Import assets" in page and reverse("core:datahub") + "?scope=assets" in page
    resp = admin.get(url)
    assert resp.status_code == 200 and resp.content[:2] == b"PK"
    assert "Assets" in load_workbook(io.BytesIO(resp.content), read_only=True).sheetnames
    tpl = admin.get(url + "?template=1")
    assert tpl.status_code == 200 and "Template" in tpl["Content-Disposition"]

    # Plain staff: no buttons, 403.
    plain = client(world["alice"])
    page = plain.get(reverse("assets:list")).content.decode()
    assert url not in page and "Import assets" not in page
    assert plain.get(url).status_code == 403

    # Sees and edits every asset: may export, but importing stays in the superuser-only Data Hub.
    bob = world["bob"]
    bob.user_permissions.add(Permission.objects.get(codename="view_all_assets"),
                             Permission.objects.get(codename="change_asset"))
    from accounts.models import User
    manager = client(User.objects.get(pk=bob.pk))
    page = manager.get(reverse("assets:list")).content.decode()
    assert url in page and "Import assets" not in page
    assert manager.get(url).status_code == 200
    assert manager.get(reverse("core:datahub")).status_code == 403
