"""Data Hub: export -> import round trips and the superuser-only gate."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from openpyxl import load_workbook

from core.datahub import engine, registry

pytestmark = pytest.mark.django_db
PW = "Hub-test-2026!"


def _user(email, superuser=False, **extra):
    from accounts.models import User
    u = User(email=email, first_name="Test", last_name="Person", user_type="staff",
             is_superuser=superuser, is_staff=superuser, must_change_password=False, **extra)
    u.set_password(PW)
    u.save()
    return u


def _client(user):
    c = Client()
    c.force_login(user)
    return c


DEFAULT_PW = "Imported-Default-2026!"


def _import(data, apply=True, categories=None, default_password=DEFAULT_PW):
    return engine.import_workbook(io.BytesIO(data), apply=apply, categories=categories,
                                  default_password=default_password)


# ---------------------------------------------------------------------------
# Plan sanity
# ---------------------------------------------------------------------------
def test_plan_builds_and_every_key_is_unique_in_order():
    sheets = engine.plan()
    ids = [s.id for s in sheets]
    assert len(ids) == len(set(ids))
    titles = [s.title for s in sheets]
    assert len(titles) == len(set(titles)) and all(len(t) <= 31 for t in titles)
    by_id = {s.id: s for s in sheets}
    # The ordered steps of a chain are keyed on (chain, position) and come after the chain.
    assert by_id["workflow.ApprovalStep"].key == ("chain", "position")
    assert ids.index("workflow.ApprovalChain") < ids.index("workflow.ApprovalStep") < ids.index("workflow.RequestType")
    # Hierarchies inside a category are set in a second pass.
    assert "parent" in by_id["staff.Department"].deferred
    assert "next_designation" in by_id["staff.Designation"].deferred
    # Links to people live in the Users category.
    assert by_id["staff.Department@links:users"].columns == ["code", "head", "director"]
    # Passwords never leave the database unless asked for (then only as hashes, see below).
    assert "password" not in by_id["accounts.User"].columns
    assert "totp_secret" not in by_id["accounts.User"].columns


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------
def test_round_trip_offices_hierarchy_users_and_heads():
    from accounts.models import User
    from staff.models import Department, Office, Staff

    parent = Department.objects.create(code="hub-parent", name="Hub Parent Directorate")
    child = Department.objects.create(code="hub-child", name="Hub Child Department", parent=parent)
    head = _user("hub.head@nbte.gov.ng", department=child)
    child.head = head
    child.save()
    Staff.objects.filter(user=head).update(employee_no="NBTE/HUB-1", department=child)
    office = Office.objects.create(name="Hub Records Office", merged_into=child)
    office.head = Staff.objects.get(user=head)
    office.save()

    data = engine.export_workbook(["org", "users"])
    wb = load_workbook(io.BytesIO(data), read_only=True)
    assert "README" in wb.sheetnames and "Departments" in wb.sheetnames and "Departments (links)" in wb.sheetnames
    wb.close()

    # Wipe the rows, then import them back.
    Office.objects.filter(pk=office.pk).delete()
    Staff.objects.filter(user=head).delete()
    User.objects.filter(pk=head.pk).delete()
    Department.objects.filter(pk__in=[child.pk, parent.pk]).delete()

    preview = _import(data, apply=False)
    assert not preview.fatal and preview.error_count == 0, [(s.title, s.errors) for s in preview.sheets if s.errors]
    assert not Department.objects.filter(code="hub-child").exists()      # the preview saved nothing

    report = _import(data, apply=True)
    assert report.applied and report.error_count == 0
    child2 = Department.objects.get(code="hub-child")
    assert child2.parent.code == "hub-parent"
    user2 = User.objects.get(email="hub.head@nbte.gov.ng")
    assert child2.head_id == user2.pk
    # No hashes in the file: the default password from the import form, and a forced change.
    assert user2.check_password(DEFAULT_PW) and not user2.check_password(PW) and user2.must_change_password
    staff2 = Staff.objects.get(employee_no="NBTE/HUB-1")
    assert staff2.user_id == user2.pk and staff2.department_id == child2.pk
    assert Staff.objects.filter(user=user2).count() == 1                  # no auto-created duplicate
    office2 = Office.objects.get(name="Hub Records Office")
    assert office2.head_id == staff2.pk and office2.merged_into_id == child2.pk

    # Importing the same file again changes nothing (must_change_password is already on).
    again = _import(data, apply=True)
    assert again.created == 0 and again.updated == 0 and again.error_count == 0


def test_export_without_hashes_needs_a_valid_default_password():
    _user("hub.nopw@nbte.gov.ng")
    data = engine.export_workbook(["users"])
    report = _import(data, apply=False, default_password=None)
    assert report.needs_default_password and "Default password for imported users" in report.fatal
    weak = _import(data, apply=False, default_password="123")
    assert weak.fatal.startswith("Default password for imported users:")


def test_round_trip_with_password_hashes_keeps_old_passwords():
    from accounts.models import User

    from staff.models import Department
    # NBTE staff accounts are imported into a department (separation rules).
    dept = Department.objects.create(code="hub-hash", name="Hub Hash Department")
    user = _user("hub.hash@nbte.gov.ng", department=dept)
    plain = engine.export_workbook(["users"])
    wb = load_workbook(io.BytesIO(plain), read_only=True)
    assert "password" not in next(wb["Users"].iter_rows(values_only=True))      # off by default
    wb.close()

    data = engine.export_workbook(["users"], include_password_hashes=True)
    wb = load_workbook(io.BytesIO(data), read_only=True)
    rows = list(wb["Users"].iter_rows(values_only=True))
    assert any("password hashes" in str(r[0] or "") for r in wb["README"].iter_rows(values_only=True))
    wb.close()
    header = rows[0]
    row = next(r for r in rows[1:] if r[header.index("email")] == "hub.hash@nbte.gov.ng")
    assert row[header.index("password")].startswith("pbkdf2_") and PW not in str(row)

    User.objects.filter(pk=user.pk).delete()
    report = _import(data, apply=True, default_password=None)          # every row has a hash
    assert report.applied and not report.fatal and report.error_count == 0, report.fatal
    user2 = User.objects.get(email="hub.hash@nbte.gov.ng")
    assert user2.check_password(PW) and user2.must_change_password

    again = _import(data, apply=True, default_password=None)
    assert again.created == 0 and again.updated == 0 and again.error_count == 0

    # Plaintext in the password column is refused, never stored.
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Users"]
    hdr = [c.value for c in ws[1]]
    for r in ws.iter_rows(min_row=2):
        if r[hdr.index("email")].value == "hub.hash@nbte.gov.ng":
            r[hdr.index("password")].value = "PlainText-123!"
    buf = io.BytesIO()
    wb.save(buf)
    bad = _import(buf.getvalue(), apply=True, default_password=None)
    sheet = next(s for s in bad.sheets if s.label == "accounts.User")
    assert sheet.errors and "plaintext" in sheet.errors[0][1]
    assert User.objects.get(email="hub.hash@nbte.gov.ng").check_password(PW)


def test_round_trip_workflow_chain_with_ordered_steps_and_leave_type():
    from accounts.models import Role
    from leave.models import LeaveType
    from staff.models import Department
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    role = Role.objects.create(code="hub-approver", name="Hub approver", rank=50)
    dept = Department.objects.create(code="hub-hr", name="Hub HR")
    chain = ApprovalChain.objects.create(code="hub-leave-chain", name="Hub leave chain")
    for pos, name in [(1, "Supervisor"), (2, "Head of department"), (3, "HR")]:
        ApprovalStep.objects.create(chain=chain, position=pos, name=name, approver_kind="role_in_department",
                                    approver_role=role)
    cat = RequestCategory.objects.create(code="hub-cat", name="Hub category")
    RequestType.objects.create(code="hub-leave", name="Hub leave", category=cat, handling_department=dept,
                               approval_chain=chain, form_schema={"fields": [{"name": "reason"}]})
    LeaveType.objects.create(code="hub-study", name="Hub study leave", days=30)

    data = engine.export_workbook(["workflow", "leave"])
    RequestType.objects.filter(code="hub-leave").delete()
    ApprovalStep.objects.filter(chain=chain).delete()
    chain.delete()
    LeaveType.objects.filter(code="hub-study").delete()

    report = _import(data, apply=True)
    assert report.applied and report.error_count == 0, [(s.title, s.errors) for s in report.sheets if s.errors]
    chain2 = ApprovalChain.objects.get(code="hub-leave-chain")
    steps = list(chain2.steps.order_by("position").values_list("position", "name", "approver_role__code"))
    assert steps == [(1, "Supervisor", "hub-approver"), (2, "Head of department", "hub-approver"),
                     (3, "HR", "hub-approver")]
    rt = RequestType.objects.get(code="hub-leave")
    assert rt.approval_chain_id == chain2.pk and rt.form_schema == {"fields": [{"name": "reason"}]}
    assert LeaveType.objects.get(code="hub-study").days == 30


def test_import_updates_changed_cells_and_reports_bad_rows():
    from leave.models import LeaveType

    LeaveType.objects.create(code="hub-annual", name="Hub annual", days=21)
    data = engine.export_workbook(["leave"])
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Leave types"]
    header = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[header.index("code")].value == "hub-annual":
            row[header.index("days")].value = 28
    ws.append(["hub-bad", "Broken", None, "not-a-number"] + [None] * (len(header) - 4))
    buf = io.BytesIO()
    wb.save(buf)

    report = _import(buf.getvalue(), apply=True)
    sheet = next(s for s in report.sheets if s.label == "leave.LeaveType")
    assert sheet.updated == 1 and sheet.changed_fields.get("days") == 1
    assert sheet.errors and "days" in sheet.errors[0][1]
    assert LeaveType.objects.get(code="hub-annual").days == 28
    assert not LeaveType.objects.filter(code="hub-bad").exists()


def test_template_has_headers_only():
    data = engine.export_workbook(["workflow"], include_rows=False)
    wb = load_workbook(io.BytesIO(data), read_only=True)
    rows = list(wb["Approval steps"].iter_rows(values_only=True))
    assert rows[0][:2] == ("chain", "position") and len(rows) == 1


# ---------------------------------------------------------------------------
# Access: superusers only
# ---------------------------------------------------------------------------
def test_hub_is_superuser_only():
    from django.contrib.auth.models import Permission

    staff = _user("hub.staff@nbte.gov.ng")
    staff.user_permissions.add(Permission.objects.get(codename="change_configentry"))
    c = _client(staff)
    urls = [reverse("core:datahub"), reverse("core:datahub_export", args=["everything"]),
            reverse("core:datahub_template", args=["org"])]
    for url in urls:
        assert c.get(url).status_code == 403
    upload = SimpleUploadedFile("x.xlsx", engine.export_workbook(["leave"], include_rows=False))
    assert c.post(reverse("core:datahub_upload"), {"file": upload}).status_code == 403
    assert c.post(reverse("core:datahub_apply", args=["abc"])).status_code == 403
    # The sidebar link is hidden for them.
    page = c.get(reverse("core:import_export"))
    assert reverse("core:datahub") not in page.content.decode()

    anon = Client().get(reverse("core:datahub"))
    assert anon.status_code in (302, 403)


def test_superuser_upload_preview_and_apply_flow():
    from leave.models import LeaveType

    admin = _user("hub.admin@nbte.gov.ng", superuser=True)
    c = _client(admin)
    page = c.get(reverse("core:datahub"))
    assert page.status_code == 200 and b"Recommended import order" in page.content

    LeaveType.objects.create(code="hub-flow", name="Hub flow leave", days=5)
    resp = c.get(reverse("core:datahub_export", args=["leave"]))
    assert resp.status_code == 200 and resp["Content-Type"].startswith("application/vnd.openxml")
    data = b"".join(resp.streaming_content) if resp.streaming else resp.content
    LeaveType.objects.filter(code="hub-flow").delete()

    resp = c.post(reverse("core:datahub_upload"),
                  {"file": SimpleUploadedFile("leave.xlsx", data), "scope": "leave"})
    assert resp.status_code == 200 and b"Check before you import" in resp.content
    assert not LeaveType.objects.filter(code="hub-flow").exists()
    token = next(iter(c.session["datahub_uploads"]))
    resp = c.post(reverse("core:datahub_apply", args=[token]), {"skip_errors": "0"})
    assert resp.status_code == 200 and b"Import complete" in resp.content
    assert LeaveType.objects.get(code="hub-flow").days == 5


def test_every_category_exports():
    for cat in registry.CATEGORIES:
        data = engine.export_workbook([cat.key], include_rows=False)
        assert data[:2] == b"PK"
