"""v2.1 Phase C: the ticket API mirrors routing, rating, funds and the PDF."""
import pytest
from rest_framework.test import APIClient

from tickets.conftest import org_c, ticket  # noqa: F401
from tasks.conftest import depts, people, roles  # noqa: F401

pytestmark = pytest.mark.django_db


def _api(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_api_route_accept_and_visibility(ticket, org_c):
    div = org_c["division"]
    r = _api(org_c["officer"]).post(f"/api/v1/tickets/{ticket.pk}/route/", {"division": div.pk}, format="json")
    assert r.status_code in (403, 404)
    r = _api(org_c["director"]).post(f"/api/v1/tickets/{ticket.pk}/route/", {"division": div.pk}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    data = body.get("data", body)
    assert data["division"]["state"] == "pending"
    r = _api(org_c["head"]).post(f"/api/v1/tickets/{ticket.pk}/division/accept/", {}, format="json")
    assert r.status_code == 200
    assert _api(org_c["officer2"]).get(f"/api/v1/tickets/{ticket.pk}/").status_code == 200
    assert _api(org_c["hr_officer"]).get(f"/api/v1/tickets/{ticket.pk}/").status_code == 404


def test_api_rate_and_pdf(ticket, org_c):
    from tickets import services
    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    services.resolve_ticket(ticket, org_c["officer"], resolution_notes="All good now.")
    assert _api(org_c["officer"]).post(f"/api/v1/tickets/{ticket.pk}/rate/", {"rating": 5},
                                       format="json").status_code == 403
    r = _api(org_c["fin_officer"]).post(f"/api/v1/tickets/{ticket.pk}/rate/", {"rating": 5}, format="json")
    assert r.status_code == 200
    ticket.refresh_from_db()
    assert ticket.satisfaction_rating == 5
    r = _api(org_c["fin_officer"]).get(f"/api/v1/tickets/{ticket.pk}/report.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_api_asset_visible_only_while_ticket_open(ticket, org_c):
    from tickets import services
    c = _api(org_c["officer"])
    url = "/api/v1/assets/T-PRN-1/"
    before = c.get(url).status_code
    services.assign_ticket(ticket, org_c["director"], org_c["officer"])
    during = c.get(url).status_code
    services.cancel_ticket(ticket, org_c["fin_officer"], "gone")
    after = c.get(url).status_code
    assert before == 404 and during == 200 and after == 404
