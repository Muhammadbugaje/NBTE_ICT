"""Regression tests for review findings M1, M2 (workflow) and M3 (tickets)."""
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department, Staff

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
from workflow.models import ApprovalChain, Request, RequestCategory, RequestComment, RequestType


@pytest.fixture
def req(ict, roles):
    requester = mk("req-d@x.org", ict, roles["staff"])
    cat = RequestCategory.objects.create(name="Cd", code="cd")
    chain = ApprovalChain.objects.create(name="chd", code="chd")
    rt = RequestType.objects.create(name="Td", code="td", category=cat, handling_department=ict,
                                    approval_chain=chain)
    return Request.objects.create(reference="REQ-D1", request_type=rt, requester=requester,
                                  subject="Laptop", body="b", receiving_department=ict)


# M1
def test_view_all_holder_opens_detail_and_finds_in_search(client, roles, req):
    from core.search import search
    fin = Department.objects.create(name="Fin", code="fin-d")
    es = mk("es-d@x.org", fin, roles["executive-secretary"])
    client.force_login(es)
    assert client.get(reverse("workflow:detail", args=[req.pk])).status_code == 200
    groups = search(User.objects.get(pk=es.pk), "Laptop", types=["request"])["groups"]
    assert groups and groups[0]["results"][0]["id"] == req.pk


def test_plain_staff_still_blocked(client, ict, roles, req):
    client.force_login(mk("other-d@x.org", ict, roles["staff"]))
    assert client.get(reverse("workflow:detail", args=[req.pk])).status_code == 403


# M2
def test_internal_comment_hidden_from_requester_shown_to_director(client, ict, roles, req):
    from workflow import services
    director = mk("dir-d@x.org", ict, roles["director"])
    services.add_comment(req, director, "SECRET-NOTE-D", internal=True)
    client.force_login(req.requester)
    html = client.get(reverse("workflow:detail", args=[req.pk])).content
    assert b"SECRET-NOTE-D" not in html
    assert b'name="is_internal"' not in html
    client.force_login(director)
    assert b"SECRET-NOTE-D" in client.get(reverse("workflow:detail", args=[req.pk])).content


def test_internal_note_text_not_in_timeline(req, ict, roles):
    from workflow import services
    from workflow.models import RequestActivity
    director = mk("dir-d2@x.org", ict, roles["director"])
    services.add_comment(req, director, "SECRET-NOTE-D2", internal=True)
    assert not RequestActivity.objects.filter(request=req, message__contains="SECRET-NOTE-D2").exists()


def test_requester_cannot_post_internal(req):
    from workflow import services
    c = services.add_comment(req, req.requester, "hello", internal=True)
    assert c.is_internal is False


# M3
def test_ticket_internal_note_not_in_raiser_timeline(client, ict, roles):
    from tickets.models import TicketCategory
    from tickets import services as ts
    raiser = mk("raiser-d@x.org", ict, roles["staff"])
    head = mk("head-d@x.org", ict, roles["director"])
    cat = TicketCategory.objects.create(code="hw-d", name="HW", handling_department=ict)
    t = ts.create_ticket(raised_by=raiser, category=cat, subject="printer", body="broken")
    ts.add_internal_comment(t, head, "STAFF-ONLY-D")
    client.force_login(raiser)
    assert b"STAFF-ONLY-D" not in client.get(reverse("tickets:detail", args=[t.pk])).content
    assert b"STAFF-ONLY-D" not in client.get(reverse("tickets:timeline_partial", args=[t.pk])).content
    client.force_login(head)
    assert b"STAFF-ONLY-D" in client.get(reverse("tickets:detail", args=[t.pk])).content
