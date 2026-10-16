"""Regression tests for review finding M16 (workflow/leave/asset integrity)."""
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.forms.models import model_to_dict
from django.urls import reverse

from accounts.models import Role, User
from staff.models import Department

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


def _type(ict, code="t16"):
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType
    cat = RequestCategory.objects.get_or_create(code="c16", defaults={"name": "C16"})[0]
    chain = ApprovalChain.objects.create(name=f"ch-{code}", code=f"ch-{code}")
    step = ApprovalStep.objects.create(chain=chain, position=1, name="Director",
                                       kind=ApprovalStep.KIND_APPROVAL,
                                       approver_kind=ApprovalStep.APPROVER_RECEIVING_DIRECTOR)
    rt = RequestType.objects.create(name=code, code=code, category=cat,
                                    handling_department=ict, approval_chain=chain)
    return rt, chain, step


def _req(rt, requester, ict, ref, **kw):
    from workflow.models import Request
    return Request.objects.create(reference=ref, request_type=rt, requester=requester,
                                  subject="s", body="b", receiving_department=ict, **kw)


# ------------------------------------------------- asset state on cancel
def test_cancelling_request_does_not_mark_asset_repaired(ict, roles):
    from assets.models import Asset, AssetCategory
    from assets.services import report_issue
    from workflow import services
    rt, _, _ = _type(ict)
    requester = mk("m16a@x.org", ict, roles["staff"])
    req = _req(rt, requester, ict, "REQ-M16-1")
    cat = AssetCategory.objects.create(name="Lap16", code="lap16")
    asset = Asset.objects.create(asset_tag="ICT-16", category=cat, owner_department=ict,
                                 state="under_repair")
    issue = report_issue(asset=asset, reported_by=requester, summary="broken", request=req)
    asset.state = "under_repair"
    asset.save()

    services.cancel_request(req, requester, "never mind")
    asset.refresh_from_db()
    issue.refresh_from_db()
    assert issue.resolved_at is not None
    assert asset.state == "under_repair"


# ------------------------------------------------- deleting closed records
def test_requester_cannot_delete_completed_request(ict, roles):
    from workflow import services
    from workflow.models import Request
    rt, _, _ = _type(ict)
    requester = mk("m16b@x.org", ict, roles["staff"])
    req = _req(rt, requester, ict, "REQ-M16-2", status=Request.COMPLETED)
    with pytest.raises(ValidationError):
        services.delete_request(req, requester, reason="tidy up")


def test_superuser_can_still_delete_completed_request(ict, roles):
    from workflow import services
    from workflow.models import Request
    rt, _, _ = _type(ict)
    requester = mk("m16c@x.org", ict, roles["staff"])
    su = mk("su16@x.org", is_superuser=True, is_staff=True)
    req = _req(rt, requester, ict, "REQ-M16-3", status=Request.COMPLETED)
    services.delete_request(req, su, reason="duplicate")
    assert Request.all_objects.get(pk=req.pk).is_deleted


# ------------------------------------------------- overlapping leave
def test_overlapping_leave_is_rejected(ict, roles, monkeypatch):
    from leave import services as leave_services
    from leave.models import LeaveApplication
    from staff.models import Staff
    from workflow.models import Request
    monkeypatch.setattr(leave_services, "_visible_holidays", lambda *a, **k: [], raising=False)
    rt, _, _ = _type(ict, code="annual-leave")
    u = mk("m16d@x.org", ict, roles["staff"])
    staff = Staff.objects.get(user=u)
    first = _req(rt, u, ict, "REQ-M16-4", status=Request.AWAITING)
    LeaveApplication.objects.create(request=first, staff=staff,
                                    start_date=date(2026, 11, 2), end_date=date(2026, 11, 6))
    with pytest.raises(ValidationError, match="overlap"):
        leave_services.validate_application(
            u, {"start_date": "2026-11-05", "end_date": "2026-11-10"})
    # editing the same application is not a clash with itself
    leave_services.validate_application(
        u, {"start_date": "2026-11-03", "end_date": "2026-11-06"},
        exclude_request_id=first.pk)


def test_deleted_leave_does_not_block_or_count(ict, roles, monkeypatch):
    from leave import services as leave_services
    from leave.models import LeaveApplication
    from staff.models import Staff
    from workflow.models import Request
    monkeypatch.setattr(leave_services, "_visible_holidays", lambda *a, **k: [], raising=False)
    rt, _, _ = _type(ict, code="annual-leave")
    u = mk("m16e@x.org", ict, roles["staff"])
    staff = Staff.objects.get(user=u)
    old = _req(rt, u, ict, "REQ-M16-5", status=Request.AWAITING)
    LeaveApplication.objects.create(request=old, staff=staff,
                                    start_date=date(2026, 11, 2), end_date=date(2026, 11, 6))
    old.delete()
    leave_services.validate_application(u, {"start_date": "2026-11-03", "end_date": "2026-11-04"})


# ------------------------------------------------- editing used steps
def _approved_step(ict, roles):
    from workflow.models import RequestApproval
    rt, chain, step = _type(ict, code="t16s")
    requester = mk("m16f@x.org", ict, roles["staff"])
    req = _req(rt, requester, ict, "REQ-M16-6")
    RequestApproval.objects.create(request=req, step=step, actor=requester,
                                   decision=RequestApproval.DECISION_CHOICES[0][0])
    return chain, step


def test_step_with_approvals_cannot_be_deleted(client, ict, roles):
    from workflow.models import ApprovalStep
    chain, step = _approved_step(ict, roles)
    client.force_login(mk("su16b@x.org", is_superuser=True, is_staff=True))
    url = reverse("workflow_manage:step_delete", args=[chain.pk, step.pk])
    resp = client.post(url, HTTP_ACCEPT="application/json",
                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert resp.status_code in (302, 409)
    assert ApprovalStep.objects.filter(pk=step.pk).exists()


def test_step_in_use_cannot_change_who_acts(client, ict, roles):
    from workflow.models import ApprovalStep
    chain, step = _approved_step(ict, roles)
    client.force_login(mk("su16c@x.org", is_superuser=True, is_staff=True))
    data = {k: ("" if v is None else v) for k, v in model_to_dict(step).items()
            if k not in ("id", "chain")}
    data["approver_kind"] = ApprovalStep.APPROVER_REQUESTER_HEAD
    data["name"] = "Renamed"
    client.post(reverse("workflow_manage:step_update", args=[chain.pk, step.pk]), data)
    step.refresh_from_db()
    assert step.approver_kind == ApprovalStep.APPROVER_RECEIVING_DIRECTOR
