"""payments/<id>/online/ with "return_to": "app" and the portal's app-return
page (payments/app_return.py), driven through the development simulator."""
from urllib.parse import urlparse

import pytest
from django.test import Client

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


@pytest.fixture
def sim(settings):
    settings.QA_PAYMENTS_SIMULATE = True


def _start(portal_api, bill, inst_user, provider="paystack", **extra):
    r = bearer(portal_api, inst_user).post(BASE + f"payments/{bill.pk}/online/", {"provider": provider, **extra},
                                           format="json")
    assert r.status_code == 201, r.content
    return r.json()


def _path(url):
    u = urlparse(url)
    return u.path + (f"?{u.query}" if u.query else "")


def test_return_to_validation_and_scoping(portal_api, portal_host, bill, inst_user, p3, sim):
    r = bearer(portal_api, inst_user).post(BASE + f"payments/{bill.pk}/online/",
                                           {"provider": "paystack", "return_to": "phone"}, format="json")
    assert r.status_code == 400 and "return_to" in err(r)["fields"]
    other = bearer(portal_api.__class__(HTTP_HOST=portal_host), p3.admin)
    r = other.post(BASE + f"payments/{bill.pk}/online/", {"provider": "paystack", "return_to": "app"},
                   format="json")
    assert r.status_code == 404


@pytest.mark.parametrize("provider", ["paystack", "remita"])
def test_simulated_payment_returns_to_the_app(portal_api, portal_host, bill, inst_user, sim, provider):
    body = _start(portal_api, bill, inst_user, provider=provider, return_to="app")
    assert body["return_to"] == "app" and f"/payments/{bill.pk}/app-return/" in body["return_url"]
    browser = Client(HTTP_HOST=portal_host)  # the phone's browser: no website session
    r = browser.post(_path(body["checkout_url"]), {"outcome": "success"})
    assert r.status_code == 302 and f"/payments/{bill.pk}/app-return/" in r["Location"]
    page = browser.get(_path(r["Location"]))
    assert page.status_code == 200
    html = page.content.decode()
    link = (f"digitalnbte://payment-return?bill={bill.pk}&amp;status=paid&amp;reference={body['reference']}")
    assert "Return to the Digital NBTE app" in html and link in html
    assert "window.location.href" in html and page["Cache-Control"] == "no-store"
    assert bill.institution.name not in html and bill.description not in html  # no bill details


def test_failed_and_pending(portal_api, portal_host, bill, inst_user, sim):
    body = _start(portal_api, bill, inst_user, return_to="app")
    browser = Client(HTTP_HOST=portal_host)
    # the provider sends the payer back before the payment finished: pending
    page = browser.get(_path(body["return_url"]) + f"?reference={body['reference']}")
    assert page.status_code == 200 and "status=pending" in page.content.decode()
    r = browser.post(_path(body["checkout_url"]), {"outcome": "failed"})
    page = browser.get(_path(r["Location"]))
    assert "status=failed" in page.content.decode() and "did not go through" in page.content.decode()


def test_bad_token_is_404(portal_api, portal_host, bill, inst_user, sim):
    from payments import app_return

    body = _start(portal_api, bill, inst_user, return_to="app")
    browser = Client(HTTP_HOST=portal_host)
    good = _path(body["return_url"])
    assert browser.get(good.replace(f"/payments/{bill.pk}/", f"/payments/{bill.pk + 999}/")).status_code == 404
    assert browser.get(f"/payments/{bill.pk}/app-return/forged:token/").status_code == 404
    other_token = app_return.make_token(bill.pk + 1)
    assert browser.get(f"/payments/{bill.pk}/app-return/{other_token}/").status_code == 404


def test_website_flow_unchanged(portal_api, portal_host, bill, inst_user, sim):
    body = _start(portal_api, bill, inst_user)
    assert body["return_to"] == "web" and body["return_url"].endswith(f"/payments/{bill.pk}/")
    r = Client(HTTP_HOST=portal_host).post(_path(body["checkout_url"]), {"outcome": "success"})
    assert r.status_code == 302 and "app-return" not in r["Location"]
    assert r["Location"].endswith(f"/payments/{bill.pk}/")
