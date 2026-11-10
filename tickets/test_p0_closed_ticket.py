"""Phase 0 (I12, T-SEC-5): closed tickets take no comments, notes or files
(service, web and API), and the forms are hidden."""
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from api.tests.conftest import BASE
from api.tests.work_fixtures import PDF, api_as, boss, outsider, work_seed, worker  # noqa: F401
from qa_core.tests.conftest import client_for, make_user  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def ticket(work_seed, outsider, boss):
    from staff.models import Department
    from tickets import services
    from tickets.models import TicketCategory

    cat = TicketCategory.objects.create(code="hw-p0", name="Hardware", minimum_priority="normal",
                                        handling_department=Department.objects.get(code="ict"))
    return services.create_ticket(raised_by=outsider, category=cat, subject="Printer jammed",
                                  body="The printer on floor 2 keeps jamming.")


def _close(ticket, state="closed"):
    from tickets.models import Ticket

    Ticket.objects.filter(pk=ticket.pk).update(state=state)
    ticket.refresh_from_db()
    return ticket


@pytest.mark.parametrize("state", ["closed", "cancelled", "resolved"])
def test_i12_closed_ticket_rejects_comment_and_attachment(ticket, outsider, boss, state):
    from tickets import services

    _close(ticket, state)
    for user in (outsider, boss):  # the raiser and the handling director
        with pytest.raises(ValidationError):
            services.add_public_comment(ticket, user, "still broken")
        with pytest.raises(ValidationError):
            services.add_attachment(ticket, user, SimpleUploadedFile("a.pdf", PDF, content_type="application/pdf"))
    with pytest.raises(ValidationError):
        services.add_internal_comment(ticket, boss, "note")
    assert not ticket.comments.exists() and not ticket.attachments.exists()


def test_i12_open_ticket_still_takes_comments(ticket, outsider):
    from tickets import services

    services.add_public_comment(ticket, outsider, "more detail")
    assert ticket.comments.count() == 1


def test_i12_closed_ticket_forms_hidden_and_web_refused(ticket, outsider, client_for):
    _close(ticket)
    c = client_for(outsider)
    page = c.get(reverse("tickets:detail", args=[ticket.pk])).content.decode()
    assert reverse("tickets:comment", args=[ticket.pk]) not in page
    assert reverse("tickets:attach", args=[ticket.pk]) not in page
    assert "This ticket is closed" in page
    c.post(reverse("tickets:comment", args=[ticket.pk]), {"body": "web comment"})
    assert not ticket.comments.exists()


def test_i12_closed_ticket_api(ticket, outsider):
    _close(ticket)
    api = api_as(outsider)
    detail = api.get(BASE + f"tickets/{ticket.pk}/").json()
    assert detail["actions"]["can_comment"] is False and detail["actions"]["can_attach"] is False
    r = api.post(BASE + f"tickets/{ticket.pk}/comments/", {"body": "api comment"}, format="json")
    assert r.status_code in (400, 403, 409)
    r = api.post(BASE + f"tickets/{ticket.pk}/attachments/",
                 {"file": SimpleUploadedFile("a.pdf", PDF, content_type="application/pdf")}, format="multipart")
    assert r.status_code in (400, 403, 409)
    assert not ticket.comments.exists() and not ticket.attachments.exists()
