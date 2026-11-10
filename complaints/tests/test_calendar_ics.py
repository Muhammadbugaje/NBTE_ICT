"""Add to calendar: a complaint's SLA deadline as .ics (core/calendar_views.py)."""
import pytest
from django.urls import reverse

from complaints import services
from complaints.models import ComplaintLayer


def _raise(user):
    return services.raise_complaint(user, layer=ComplaintLayer.objects.get(code="payment-issues"),
                                    subject="Payment not confirmed after a week",
                                    body="We paid and uploaded the teller a week ago.")


@pytest.mark.django_db
def test_complaint_ics_for_handlers_404_for_outsiders(world, client_for):
    comp = _raise(world["a_user"])
    assert comp.open_stage().due_at is not None
    url = reverse("nbte_cal:complaint", args=[comp.pk])

    r = client_for(world["fin_director"]).get(url)
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/calendar")
    assert "attachment" in r["Content-Disposition"]
    body = r.content.decode()
    assert "BEGIN:VEVENT" in body and "BEGIN:VALARM" in body and comp.reference in body

    # Another department's officer cannot open the complaint, so no .ics either.
    assert client_for(world["vtsd_officer"]).get(url).status_code == 404
    # The raiser (institution) has no internal door: ExternalUserGuardMiddleware
    # refuses external users on the internal host (403) before any view runs.
    assert client_for(world["a_user"]).get(url).status_code in (302, 403, 404)


@pytest.mark.django_db
def test_complaint_detail_offers_add_to_calendar(world, client_for):
    comp = _raise(world["a_user"])
    page = client_for(world["fin_director"]).get(reverse("qa_complaints:detail", args=[comp.pk])).content.decode()
    assert reverse("nbte_cal:complaint", args=[comp.pk]) in page and "calendar.google.com" in page
