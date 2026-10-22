"""Remita's JSON API lives under /exapp/api/v1/send/api on the base URL;
the browser checkout does not (Phase 8 live-test fix)."""
from decimal import Decimal
from types import SimpleNamespace

from django.test import override_settings

from payments.providers import base, remita


@override_settings(REMITA_BASE_URL="https://remitademo.net/remita", REMITA_API_PATH="/exapp/api/v1/send/api",
                   REMITA_MERCHANT_ID="2547916", REMITA_API_KEY="1946", QA_PAYMENTS_SIMULATE=False)
def test_rrr_and_status_use_the_api_path(monkeypatch):
    calls = []

    def fake(method, url, payload=None, headers=None, timeout=20):
        calls.append((method, url, headers))
        if url.endswith("paymentinit"):
            return 200, {"RRR": "280007021192", "statuscode": "025"}
        return 200, {"status": "00", "amount": "1000.00", "message": "Successful"}

    monkeypatch.setattr(remita, "http_json", fake)
    provider = remita.RemitaProvider()
    obligation = SimpleNamespace(fee_schedule=SimpleNamespace(remita_service_type_id="4430731"),
                                 institution=SimpleNamespace(name="Demo Polytechnic"), description="Fee")
    rrr, _req, _resp = provider.generate_rrr(obligation, SimpleNamespace(email="a@b.c"), Decimal("1000"), "ORD1")
    assert rrr == "280007021192"
    assert calls[0][1] == ("https://remitademo.net/remita/exapp/api/v1/send/api"
                           "/echannelsvc/merchant/api/paymentinit")
    assert calls[0][2]["Authorization"].startswith("remitaConsumerKey=2547916,remitaConsumerToken=")
    result = provider.verify(SimpleNamespace(provider_reference=rrr))
    assert result.paid
    assert calls[1][1].startswith("https://remitademo.net/remita/exapp/api/v1/send/api/echannelsvc/2547916/280007021192/")
    assert calls[1][1].endswith("/status.reg")
    # The checkout page is not under the API path.
    assert provider.checkout_url(rrr).startswith("https://remitademo.net/remita/ecomm/finalize.reg?")


def test_jsonp_body_is_unwrapped():
    assert base.parse_body('jsonp ({"RRR":"1","statuscode":"025"})') == {"RRR": "1", "statuscode": "025"}
