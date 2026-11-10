"""v2.1 Phase C tickets: routing (I2/H23), counts (I3), rating (I4), e-mail
switches (I6), funds request bridge (I8/I9/I10), PDF (I13), asset access (I11 via X5)."""
from unittest import mock

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

pytestmark = pytest.mark.django_db
PW = "Tasks-test-pass-2026!"


def _client(client, user):
    client.force_login(user)
    return client


# ---------------------------------------------------------------- routing
def test_i2_route_accept_members_see(ticket, org_c):
    from tickets.access import visible_tickets_queryset
    from tickets.models import Ticket
    from tickets.routing import accept_division, route_to_division

    div, officer2 = org_c["division"], org_c["officer2"]
    with pytest.raises(PermissionDenied):
        route_to_division(ticket, org_c["officer"], div)
    route_to_division(ticket, org_c["director"], div, "please")
    ticket.refresh_from_db()
    assert ticket.division_state == Ticket.DIV_PENDING
    assert visible_tickets_queryset(org_c["head"]).filter(pk=ticket.pk).exists()
    with pytest.raises(PermissionDenied):
        accept_division(ticket, org_c["officer"])
    accept_division(ticket, org_c["head"])
    ticket.refresh_from_db()
    assert ticket.division_state == Ticket.DIV_ACCEPTED
    assert visible_tickets_queryset(officer2).filter(pk=ticket.pk).exists()   # members added
    assert not visible_tickets_queryset(org_c["hr_officer"]).filter(pk=ticket.pk).exists()


def test_i2_ticket_category_routes_to_division(org_c):
    from tickets import services
    from tickets.models import Ticket
    org_c["cat"].handling_division = org_c["division"]
    org_c["cat"].save()
    t = services.create_ticket(raised_by=org_c["hr_officer"], category=org_c["cat"], subject="Mouse",
                               body="Mouse is broken.")
    assert t.division_id == org_c["division"].pk and t.division_state == Ticket.DIV_PENDING


def test_b8_division_capacity_enforced_for_tickets(org_c):
    from tickets import services
    from tickets.routing import route_to_division
    div = org_c["division"]          # max_open_tasks = 3
    made = []
    for i in range(4):
        made.append(services.create_ticket(raised_by=org_c["hr_officer"], category=org_c["cat"],
                                           subject=f"T{i}", body="Something is broken."))
    for t in made[:3]:
        route_to_division(t, org_c["director"], div)
    with pytest.raises(ValidationError) as exc:
        route_to_division(made[3], org_c["director"], div)
    assert "full" in str(exc.value).lower() and "3/3" in str(exc.value)


def test_h23_decline_returns_to_director(ticket, org_c):
    from tickets.routing import decline_division, route_to_division
    route_to_division(ticket, org_c["director"], org_c["division"])
    with pytest.raises(ValidationError):
        decline_division(ticket, org_c["head"], "")
    decline_division(ticket, org_c["head"], "We have no printer parts.")
    ticket.refresh_from_db()
    assert ticket.division_id is None
    assert ticket.activities.filter(kind="division_declined").exists()


def test_h23_head_assigns_only_members(ticket, org_c):
    from tickets import services
    from tickets.routing import accept_division, route_to_division
    route_to_division(ticket, org_c["director"], org_c["division"])
    accept_division(ticket, org_c["head"])
    with pytest.raises(ValidationError):
        services.assign_ticket(ticket, org_c["head"], org_c["staff"])   # ICT but not in the division
    services.assign_ticket(ticket, org_c["head"], org_c["officer"])
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == org_c["officer"].pk


def test_i3_counts_match_lists(ticket, org_c):
    from tickets.access import visible_tickets_queryset
    from tickets.models import Ticket
    from tickets.routing import accept_division, counts_for, route_to_division
    route_to_division(ticket, org_c["director"], org_c["division"])
    accept_division(ticket, org_c["head"])
    d = counts_for(org_c["director"])
    assert d["scope"] == "department"
    assert d["totals"]["open"] == visible_tickets_queryset(org_c["director"]).filter(
        state__in=Ticket.OPEN_STATES).count() == 1
    row = [r for r in d["divisions"] if r.pk == org_c["division"].pk][0]
    assert row.open == 1 and row.pending == 0
    assert counts_for(org_c["head"])["scope"] == "division"
    assert counts_for(org_c["es"])["scope"] == "all"
    assert counts_for(org_c["officer"]) is None
    paths = {p["kind"]: p["count"] for p in d["paths"]}
    assert paths["routed_division"] == 1 and paths["division_accepted"] == 1


# ---------------------------------------------------------------- X5 asset access
def test_i11_temporary_asset_access_ends_on_close(ticket, org_c):
    from assets.access import can_manage_asset, can_view_asset
    from assignments.models import AccessGrant
    from core.models import AuditLogEntry
    from tickets import services

    officer, printer = org_c["officer"], org_c["printer"]
    assert not can_view_asset(officer, printer)
    services.assign_ticket(ticket, org_c["director"], officer)
    assert can_view_asset(officer, printer)
    assert not can_manage_asset(officer, printer)            # read only
    g = AccessGrant.objects.get(grantee=officer)
    assert g.scope == "asset" and g.source == ticket and g.expires_at is not None
    services.start_work(ticket, officer)
    services.resolve_ticket(ticket, officer, resolution_notes="Fuser replaced.")
    assert not can_view_asset(officer, printer)
    g.refresh_from_db()
    assert g.revoke_reason == AccessGrant.R_COMPLETED
    actions = list(AuditLogEntry.objects.filter(model_label="assignments.AccessGrant", object_pk=str(g.pk))
                   .values_list("action", flat=True))
    assert "grant_created" in actions and "grant_revoked" in actions
    # reopening grants again
    services.reopen_ticket(ticket, org_c["fin_officer"], "Still printing blank pages.")
    assert can_view_asset(fresh_user(officer), printer)


def fresh_user(u):
    from accounts.models import User
    return User.objects.get(pk=u.pk)


def test_x5_reassign_moves_grant(ticket, org_c):
    from assets.access import can_view_asset
    from tickets import services
    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    services.assign_ticket(ticket, org_c["director"], org_c["officer2"])
    assert not can_view_asset(org_c["officer"], org_c["printer"])
    assert can_view_asset(org_c["officer2"], org_c["printer"])


def test_x5_asset_detail_banner_and_event(ticket, org_c, client):
    from assignments.models import AccessEvent
    from tickets import services
    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    page = _client(client, org_c["officer"]).get(reverse("assets:detail", args=["T-PRN-1"]))
    assert page.status_code == 200
    html = page.content.decode()
    assert "Temporary access" in html and ticket.reference in html
    assert AccessEvent.objects.filter(user=org_c["officer"], action=AccessEvent.VIEW_VIA_GRANT).count() == 1
    assert client.get(reverse("assets:detail", args=["T-PRN-1"])).status_code == 200
    services.cancel_ticket(ticket, org_c["fin_officer"], "Fixed itself")
    assert client.get(reverse("assets:detail", args=["T-PRN-1"])).status_code == 404


# ---------------------------------------------------------------- rating
def test_i4_rating_reminder_and_rate(ticket, org_c):
    import datetime
    from django.utils import timezone
    from notifications.models import Notification
    from tickets import services
    from tickets.rating import pending_ratings, rate_ticket, send_rating_reminders

    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    services.start_work(ticket, org_c["officer"])
    services.resolve_ticket(ticket, org_c["officer"], resolution_notes="Fuser replaced.")
    raiser = org_c["fin_officer"]
    assert list(pending_ratings(raiser)) == [ticket]
    assert send_rating_reminders() == 0                                   # too early
    later = timezone.now() + datetime.timedelta(days=3)
    assert send_rating_reminders(now=later) == 1
    assert send_rating_reminders(now=later) == 0                          # not twice in a row
    n = Notification.objects.filter(recipient=raiser, title__startswith="Please rate").first()
    assert n is not None and n.url.endswith("#rate")
    with pytest.raises(PermissionDenied):
        rate_ticket(ticket, org_c["officer"], 5)
    ticket.refresh_from_db()
    rate_ticket(ticket, raiser, 4, "Good")
    ticket.refresh_from_db()
    assert ticket.state == "closed" and ticket.satisfaction_rating == 4 and ticket.rated_at
    assert not list(pending_ratings(raiser))
    with pytest.raises(PermissionDenied):
        rate_ticket(ticket, raiser, 5)


def test_i4_dashboard_prompt(ticket, org_c):
    from dashboards.action_queue import _ticket_items
    from tickets import services
    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    services.resolve_ticket(ticket, org_c["officer"], resolution_notes="Fuser replaced.")
    items = _ticket_items(org_c["fin_officer"])
    assert any(i["reference"] == ticket.reference and i["title"].startswith("Rate") for i in items)


# ---------------------------------------------------------------- e-mail switches
def test_i6_email_switches(org_c, settings):
    from config.models import ConfigEntry
    from config.registry import invalidate
    from tickets import services

    def setv(key, value):
        ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": value, "value_type": "int"})
        invalidate(key)

    with mock.patch("tickets.emails.send_email") as send:
        t = services.create_ticket(raised_by=org_c["hr_officer"], category=org_c["cat"], subject="A", body="Broken a")
        services.assign_ticket(t, org_c["director"], org_c["officer"])
        assert send.call_count >= 2          # created (director) + assigned
        send.reset_mock()
        setv("tickets.email.enabled", "0")
        t2 = services.create_ticket(raised_by=org_c["hr_officer"], category=org_c["cat"], subject="B", body="Broken b")
        services.assign_ticket(t2, org_c["director"], org_c["officer"])
        services.resolve_ticket(t2, org_c["officer"], resolution_notes="Done and dusted.")
        assert send.call_count == 0
        from notifications.models import Notification
        assert Notification.objects.filter(recipient=org_c["officer"], title__contains=t2.reference).exists()
        setv("tickets.email.enabled", "1")
        setv("tickets.email.assigned", "0")
        services.assign_ticket(t, org_c["director"], org_c["officer2"])
        assert send.call_count == 0


# ---------------------------------------------------------------- funds bridge
def _approve_all(req):
    from workflow import services as ws
    from workflow.models import Request
    for _ in range(6):
        req.refresh_from_db()
        if req.status in Request.CLOSED_STATUSES:
            return req
        actor = ws.resolve_approver(ws._resolve_current_step(req), req)
        ws.approve_step(req, actor, "ok")
    req.refresh_from_db()
    return req


def test_i8_ticket_to_request_and_back(ticket, org_c):
    from tickets import bridge, services
    from tickets.models import Ticket
    from workflow.models import Request

    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    services.start_work(ticket, org_c["officer"])
    services.add_public_comment(ticket, org_c["officer"], "Fuser is worn out.")
    with pytest.raises(PermissionDenied):
        bridge.request_funds(ticket, org_c["fin_officer"], "1000", "Not my ticket to fund")
    with pytest.raises(ValidationError):
        bridge.request_funds(ticket, org_c["officer"], "-5", "Replacement fuser unit")
    req = bridge.request_funds(ticket, org_c["officer"], "45,000", "Replacement fuser unit for the printer")
    ticket.refresh_from_db()
    assert ticket.state == Ticket.AWAITING_FUNDS and ticket.funds_request == req
    trail = req.metadata["ticket_trail"]
    assert trail["reference"] == ticket.reference and trail["asset"]["tag"] == "T-PRN-1"
    assert any(c["body"] == "Fuser is worn out." for c in trail["comments"])
    first = Request.objects.get(pk=req.pk)
    from workflow import services as ws
    approver = ws.resolve_approver(ws._resolve_current_step(first), first)
    assert approver == org_c["fin_dir"]                        # Finance first ...
    req = _approve_all(req)
    assert req.status == Request.COMPLETED
    approvers = set(req.approvals.values_list("actor_id", flat=True))
    assert org_c["es"].pk in approvers                        # ... and the ES
    ticket.refresh_from_db()
    assert ticket.funds_state == Ticket.FUNDS_APPROVED and ticket.state == Ticket.IN_PROGRESS
    kinds = list(ticket.activities.values_list("kind", flat=True))
    assert "funds_requested" in kinds and "funds_approved" in kinds
    # Finance (an approver) can open the request with the trail
    from workflow.views import _require_view  # noqa: F401 (exists)


def test_i8_rejected_funds_back_to_handler(ticket, org_c):
    from tickets import bridge, services
    from tickets.models import Ticket
    from workflow import services as ws

    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    services.start_work(ticket, org_c["officer"])
    req = bridge.request_funds(ticket, org_c["officer"], "1000", "Replacement fuser unit")
    ws.reject_step(req, org_c["fin_dir"], "Use the spare in stores.")
    ticket.refresh_from_db()
    assert ticket.funds_state == Ticket.FUNDS_REJECTED and ticket.state == Ticket.IN_PROGRESS
    assert "spare in stores" in ticket.funds_decision_note


def test_i9_convert_request_to_ticket(org_c):
    from tickets.bridge import convert_request_to_ticket
    from workflow import services as ws
    from workflow.models import ApprovalChain, Request, RequestCategory, RequestType

    chain = ApprovalChain.objects.create(name="c", code="c-t")
    rt = RequestType.objects.create(name="Fix", code="fix-t", category=RequestCategory.objects.create(
        name="Fix", code="fix"), handling_department=org_c["ict"], approval_chain=chain)
    req = Request.objects.create(reference="REQ-T-1", request_type=rt, requester=org_c["hr_officer"],
                                 subject="AC leaking", body="Water drips.", receiving_department=org_c["ict"],
                                 status=Request.AWAITING)
    with pytest.raises(PermissionDenied):
        convert_request_to_ticket(req, org_c["officer"], org_c["cat"])
    t = convert_request_to_ticket(req, org_c["director"], org_c["cat"], "a fix")
    req.refresh_from_db()
    assert t.source_request == req and t.raised_by == org_c["hr_officer"]
    assert req.is_closed and req.metadata["converted_to_ticket"] == t.pk
    assert t.activities.filter(kind="converted_from_request").exists()
    with pytest.raises(ValidationError):
        ws.add_comment(req, org_c["hr_officer"], "late comment")


# ---------------------------------------------------------------- PDF + web
def test_i13_ticket_pdf(ticket, org_c, client):
    from tickets import services
    services.add_internal_comment(ticket, org_c["director"], "SECRET-NOTE")
    resp = _client(client, org_c["fin_officer"]).get(reverse("tickets:report_pdf", args=[ticket.pk]))
    assert resp.status_code == 200 and resp["Content-Type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"
    client.force_login(org_c["hr_officer"])
    assert client.get(reverse("tickets:report_pdf", args=[ticket.pk])).status_code == 404


def test_i13_closed_ticket_page_read_only_with_pdf(ticket, org_c, client):
    from tickets import services
    services.cancel_ticket(ticket, org_c["fin_officer"], "no longer needed")
    html = _client(client, org_c["fin_officer"]).get(reverse("tickets:detail", args=[ticket.pk])).content.decode()
    assert "data-closed-readonly" in html and reverse("tickets:report_pdf", args=[ticket.pk]) in html
    assert 'action="%s"' % reverse("tickets:comment", args=[ticket.pk]) not in html


def test_detail_pages_render_for_roles(ticket, org_c, client):
    from tickets.routing import route_to_division
    route_to_division(ticket, org_c["director"], org_c["division"])
    for who in ("director", "head", "fin_officer", "es"):
        client.force_login(org_c[who])
        r = client.get(reverse("tickets:detail", args=[ticket.pk]))
        assert r.status_code == 200, who
    client.force_login(org_c["head"])
    assert "data-division-decision" in client.get(reverse("tickets:detail", args=[ticket.pk])).content.decode()
    assert client.get(reverse("tickets:counts")).status_code == 200
    client.force_login(org_c["officer"])
    assert client.get(reverse("tickets:counts")).status_code == 403
