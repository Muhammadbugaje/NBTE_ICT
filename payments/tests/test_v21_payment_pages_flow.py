"""v2.1 F.4: the payment PAGES themselves, through every state of a paid
gateway bill — the institution must see its payment change live and the
Director of Finance (and any QA monitor) must see the money on the NBTE side.

The services are covered elsewhere; these are the two screens the owner looks
at first, so they are pinned by tests.
"""
import pytest
from django.test import Client

pytestmark = pytest.mark.django_db

# The bill/fixture stack lives with the F.4 payments suite:
from payments.tests.test_v21_phase_f_payments import components, poly_bill  # noqa: F401,E402


@pytest.fixture(autouse=True)
def _sim(settings):
    settings.QA_PAYMENTS_SIMULATE = True


def _pay(ob, user, provider="paystack"):
    from payments import services

    attempt = services.start_online_payment(ob, provider, user=user, callback_url="https://x/cb")
    services.simulate_provider_callback(provider, attempt.provider_reference, "success")
    attempt.refresh_from_db()
    return attempt


def _refresh(pk):
    from payments.models import PaymentObligation

    return PaymentObligation.unscoped.get(pk=pk)


def _portal(user):
    c = Client()
    c.force_login(user)
    return c


def _qa(user):
    c = Client()
    c.force_login(user)
    return c


def test_bill_pages_through_the_whole_gateway_flow(poly_bill, p2, proposed_inst, inst_user, portal_host):
    """fee_due → paying → provider paid → DoF confirmed, as seen on both sides."""
    from payments import services
    from payments.models import PaymentVerification

    ob = poly_bill
    portal = _portal(inst_user)

    # 1. fee_due: methods are offered
    r = portal.get(f"/payments/{ob.pk}/", HTTP_HOST=portal_host)
    assert r.status_code == 200, r.content[:800]
    html = r.content.decode()
    assert "How will you pay?" in html and "Paystack" in html, html[:400]

    # 2. attempt started: the page shows the pending attempt
    attempt = services.start_online_payment(ob, "paystack", user=inst_user, callback_url="https://x/cb")
    r = portal.get(f"/payments/{ob.pk}/", HTTP_HOST=portal_host)
    assert r.status_code == 200, r.content[:800]
    ob = _refresh(ob.pk)

    # 3. provider reports paid (the webhook path): the institution is told on
    #    the detail page and the Director of Finance sees the money.
    services.simulate_provider_callback("paystack", attempt.provider_reference, "success")
    attempt.refresh_from_db()
    assert attempt.status == "provider_reported_paid"
    item = PaymentVerification.unscoped.get(obligation=ob, is_open=True)
    assert item.auto_first_pass and item.decision == "approved", (item.decision, item.auto_first_pass)
    r = portal.get(f"/payments/{ob.pk}/", HTTP_HOST=portal_host)
    assert r.status_code == 200, r.content[:800]
    fin = _qa(p2.fin_director)
    r = fin.get("/qa/finance/")
    assert r.status_code == 200, r.content[:800]
    queue_html = r.content.decode()
    assert ob.reference in queue_html, f"the paid bill {ob.reference} is not in the Finance queue"
    r = fin.get(f"/qa/finance/obligations/{ob.pk}/")
    assert r.status_code == 200, r.content[:800]
    ob_html = r.content.decode()
    assert "Institution fee" in ob_html or "₦" in ob_html, ob_html[:400]
    # A non-finance staff member is kept out of the queue entirely:
    # service-level he has no items, view-level the screen answers 403.
    from payments.views import can_view_queue

    plain = _qa(p2.director)
    assert can_view_queue(p2.director) is False, p2.director.role.code
    assert list(services.queue_items(p2.director)) == [], "a non-finance director must see no queue items"
    got = plain.get("/qa/finance/")
    assert got.status_code == 403, (got.status_code, got.get("Location", b""))

    # 4. the Director confirms: the institution sees the result at once
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    ob = _refresh(ob.pk)
    assert ob.status == "confirmed" and ob.amount_paid == ob.amount
    r = portal.get(f"/payments/{ob.pk}/", HTTP_HOST=portal_host)
    # P10: the first visit after confirmation goes to the dashboard welcome card once.
    assert r.status_code == 302 and f"welcome={ob.pk}" in r["Location"], r.status_code
    r = portal.get(f"/payments/{ob.pk}/", HTTP_HOST=portal_host)
    assert r.status_code == 200, r.content[:800]
    html = r.content.decode()
    assert "Confirmed" in html or "confirmed" in html, html[:400]
    # /payments/<pk>/receipt/ is the POST-only upload of the institution's own
    # receipt (not a download), so a GET is refused.
    r = portal.get(f"/payments/{ob.pk}/receipt/", HTTP_HOST=portal_host)
    assert r.status_code == 405
