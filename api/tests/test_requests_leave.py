"""requests/ and leave/ (staff door)."""
import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from .conftest import BASE, bearer, err
from .work_fixtures import PDF, api_as, boss, outsider, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db

LEAVE_SCHEMA = {"fields": [
    {"key": "start_date", "label": "First day of leave", "type": "date", "required": True},
    {"key": "end_date", "label": "Last day of leave", "type": "date", "required": True}]}
KIT_SCHEMA = {"fields": [
    {"key": "quantity", "label": "How many?", "type": "number", "required": True},
    {"key": "colour", "label": "Colour", "type": "select", "required": True, "options": ["Black", "White"]},
    {"key": "rush", "label": "Rush order", "type": "checkbox"}]}


@pytest.fixture
def types(work_seed, boss):
    from staff.models import Department
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    ict = Department.objects.get(code="ict")
    chain = ApprovalChain.objects.create(name="Boss then close", code="boss-close")
    ApprovalStep.objects.create(chain=chain, position=1, name="Director approval", kind="approval",
                                approver_kind="specific_user", approver_user=boss)
    ApprovalStep.objects.create(chain=chain, position=2, name="Confirm receipt", kind="closure",
                                approver_kind="specific_user", approver_user=boss)
    cat = RequestCategory.objects.create(name="Kit", code="kit")
    kit = RequestType.objects.create(name="New kit", code="new-kit", category=cat, handling_department=ict,
                                     approval_chain=chain, form_schema=KIT_SCHEMA)
    hr = RequestCategory.objects.create(name="HR", code="hr-cat")
    leave = RequestType.objects.create(name="Annual leave application", code="annual-leave", category=hr,
                                       handling_department=ict, approval_chain=chain, form_schema=LEAVE_SCHEMA)
    return kit, leave


@pytest.fixture
def worker_staff(worker):
    from leave.models import LeaveEntitlement
    from staff.models import Staff

    st = Staff.objects.filter(user=worker).first() or Staff.objects.create(
        user=worker, employee_no="NBTE-T-1", first_name="Worker", last_name="Test", department=worker.department)
    LeaveEntitlement.objects.create(staff=st, year=datetime.date.today().year, days=20)
    LeaveEntitlement.objects.create(staff=st, year=datetime.date.today().year + 1, days=20)
    return st


def next_monday(weeks=2):
    d = datetime.date.today() + datetime.timedelta(days=7 * weeks)
    return d - datetime.timedelta(days=d.weekday())


def raise_kit(client, **fields):
    data = {"request_type": "new-kit", "subject": "Two monitors", "body": "Need two monitors for the lab.",
            "fields": {"quantity": 2, "colour": "Black", "rush": True, **fields}}
    return client.post(BASE + "requests/", data, format="json")


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
def test_auth_and_door(staff_api, rp_user, types):
    assert staff_api.get(BASE + "requests/").status_code == 401
    r = bearer(staff_api, rp_user).get(BASE + "requests/types/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_types_and_fields(worker, types):
    c = api_as(worker)
    listed = c.get(BASE + "requests/types/").json()["results"]
    assert {t["code"] for t in listed} == {"new-kit", "annual-leave"}
    kit = c.get(BASE + "requests/types/new-kit/").json()
    assert [f["key"] for f in kit["fields"]] == ["quantity", "colour", "rush"]
    assert kit["fields"][1]["options"] == [{"value": "Black", "label": "Black"},
                                           {"value": "White", "label": "White"}]
    assert c.get(BASE + "requests/types/nope/").status_code == 404


def test_create_validation_reports_schema_fields(worker, types):
    r = raise_kit(api_as(worker), quantity=None, colour="Pink")
    assert r.status_code == 400
    assert {"fields.quantity", "fields.colour"} <= set(err(r)["fields"])
    r = api_as(worker).post(BASE + "requests/", {"request_type": "zzz", "subject": "x", "body": "y"},
                            format="json")
    assert r.status_code == 400 and "request_type" in err(r)["fields"]


def test_raise_approve_comment_attach_and_close(worker, boss, outsider, types, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    w = api_as(worker)
    r = raise_kit(w)
    assert r.status_code == 201, r.content
    req = r.json()
    assert req["status"] == "awaiting_approval" and req["step"]["actor"]["id"] == boss.pk
    assert {f["key"]: f["value"] for f in req["form_data"]} == {"quantity": 2, "colour": "Black", "rush": True}
    assert [s["state"] for s in req["chain"]] == ["current", "upcoming"]
    assert req["actions"]["can_cancel"] and not req["actions"]["can_approve"]
    # strangers get 404, the requester may not approve
    assert api_as(outsider).get(BASE + f"requests/{req['id']}/").status_code == 404
    r = w.post(BASE + f"requests/{req['id']}/approve/", {}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    b = api_as(boss)
    awaiting = b.get(BASE + "requests/?scope=awaiting").json()
    assert [x["id"] for x in awaiting["results"]] == [req["id"]] and awaiting["results"][0]["current_step"]
    d = b.get(BASE + f"requests/{req['id']}/").json()
    assert d["actions"]["can_approve"] and d["actions"]["can_reject"] and d["step"]["is_me"]
    r = b.post(BASE + f"requests/{req['id']}/reject/", {"comment": ""}, format="json")
    assert r.status_code == 400 and "comment" in err(r)["fields"]
    r = b.post(BASE + f"requests/{req['id']}/comments/", {"body": "Checking budget", "is_internal": True},
               format="json")
    assert r.status_code == 201
    assert all(not c["is_internal"] for c in w.get(BASE + f"requests/{req['id']}/").json()["comments"])
    up = SimpleUploadedFile("quote.pdf", PDF, content_type="application/pdf")
    r = w.post(BASE + f"requests/{req['id']}/attachments/", {"file": up}, format="multipart")
    assert r.status_code == 201
    att = r.json()["attachments"][0]
    assert b.get(BASE + f"requests/{req['id']}/attachments/{att['id']}/").status_code == 200
    assert api_as(outsider).get(BASE + f"requests/{req['id']}/attachments/{att['id']}/").status_code == 404
    r = b.post(BASE + f"requests/{req['id']}/approve/", {"comment": "OK"}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["approvals"][0]["decision"] == "approve"
    assert [s["state"] for s in r.json()["chain"]] == ["done", "current"]
    r = w.post(BASE + f"requests/{req['id']}/close/", {}, format="json")  # closure: the requester
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert w.get(BASE + "requests/?scope=mine&status=completed").json()["count"] == 1


def test_return_to_requester_and_resubmit(worker, boss, types):
    w, b = api_as(worker), api_as(boss)
    req = raise_kit(w).json()
    r = b.post(BASE + f"requests/{req['id']}/return-to-requester/", {"comment": "Say which lab"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "returned"
    d = w.get(BASE + f"requests/{req['id']}/").json()
    assert d["actions"]["can_resubmit"]
    r = b.post(BASE + f"requests/{req['id']}/resubmit/", {"subject": "x", "body": "y"}, format="json")
    assert r.status_code == 403
    r = w.post(BASE + f"requests/{req['id']}/resubmit/",
               {"subject": "Two monitors (Lab 3)", "body": "Need two monitors for lab 3.",
                "fields": {"quantity": 2, "colour": "White"}}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "awaiting_approval" and r.json()["revision_count"] == 1


def test_cancel_only_by_requester(worker, boss, types):
    req = raise_kit(api_as(worker)).json()
    assert api_as(boss).post(BASE + f"requests/{req['id']}/cancel/", {}, format="json").status_code == 403
    r = api_as(worker).post(BASE + f"requests/{req['id']}/cancel/", {"comment": "No longer needed"},
                            format="json")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Leave
# ---------------------------------------------------------------------------
def test_leave_overview_without_staff_record(worker, types):
    from staff.models import Staff

    Staff.objects.filter(user=worker).delete()
    d = api_as(worker).get(BASE + "leave/").json()
    assert d["staff_linked"] is False and d["can_apply"] is False and d["balances"] == []
    r = api_as(worker).post(BASE + "leave/applications/",
                            {"start_date": str(next_monday()), "end_date": str(next_monday())}, format="json")
    assert r.status_code == 400 and "staff record" in err(r)["message"]


def test_leave_apply_preview_approve_cancel(worker, boss, outsider, types, worker_staff):
    w = api_as(worker)
    start = next_monday()
    end = start + datetime.timedelta(days=4)
    overview = w.get(BASE + "leave/").json()
    assert overview["staff_linked"] and overview["can_apply"]
    assert overview["balances"][0]["entitlement"] == "20.00" and overview["balances"][0]["is_set"]
    p = w.get(BASE + f"leave/preview/?start_date={start}&end_date={end}").json()
    assert p["working_days"] == 5 and p["resumption_date"] == str(start + datetime.timedelta(days=7))
    sat = start + datetime.timedelta(days=5)
    r = w.get(BASE + f"leave/preview/?start_date={sat}&end_date={sat + datetime.timedelta(days=1)}")
    assert r.status_code == 400 and "no working days" in err(r)["message"]
    r = w.post(BASE + "leave/applications/", {"start_date": str(start), "end_date": str(end)}, format="json")
    assert r.status_code == 201, r.content
    app = r.json()
    assert app["working_days"] == 5 and app["status"] == "awaiting_approval" and app["can_cancel"]
    # overlapping dates are refused, as on the website
    r = w.post(BASE + "leave/applications/", {"start_date": str(end), "end_date": str(end)}, format="json")
    assert r.status_code == 400 and "overlap" in err(r)["message"]
    assert w.get(BASE + "leave/").json()["balances"][0]["pending"] == "5"
    # the approver sees it in leave/approvals/ and decides through requests/
    queue = api_as(boss).get(BASE + "leave/approvals/").json()["results"]
    assert queue[0]["request_id"] == app["request_id"] and queue[0]["requested_days"] == "5"
    assert api_as(outsider).get(BASE + "leave/approvals/").json()["count"] == 0
    detail = api_as(boss).get(BASE + f"requests/{app['request_id']}/").json()
    assert detail["leave"]["requested_days"] == "5"
    # cancel: only my own applications
    assert api_as(outsider).post(BASE + f"leave/applications/{app['id']}/cancel/", {},
                                 format="json").status_code == 404
    r = w.post(BASE + f"leave/applications/{app['id']}/cancel/", {"reason": "Plans changed"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and not r.json()["can_cancel"]
    listed = w.get(BASE + "leave/applications/?status=closed").json()
    assert listed["count"] == 1


def test_leave_holidays(worker, work_seed):
    from portal.models import Holiday, Scope

    year = datetime.date.today().year
    Holiday.objects.create(name="Staff day", date_start=datetime.date(year, 6, 1),
                           date_end=datetime.date(year, 6, 1), visibility=Scope.STAFF)
    Holiday.objects.create(name="Finance day", date_start=datetime.date(year, 6, 2),
                           date_end=datetime.date(year, 6, 2), visibility=Scope.DEPARTMENT,
                           department=__import__("staff.models", fromlist=["Department"]).Department
                           .objects.get(code="finance"))
    names = [h["name"] for h in api_as(worker).get(BASE + f"leave/holidays/?year={year}").json()["results"]]
    assert names == ["Staff day"]
