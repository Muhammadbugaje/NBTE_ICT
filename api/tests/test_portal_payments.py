"""Portal door — bills, slip, online payment (simulator), verify on return,
proof and provider receipt uploads, Payment Advice download."""
import pytest
from rest_framework.test import APIClient

from qa_core.tests.phase2_fixtures import pdf_upload

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


def test_list_summary_detail_slip(portal_api, portal_host, bill, inst_user, p3):
    api = bearer(portal_api, inst_user)
    listing = api.get(BASE + "payments/?group=due").json()
    assert listing["count"] == 1
    row = listing["results"][0]
    assert row["id"] == bill.pk and row["amount"] == "4000000.00" and row["amount_display"].startswith("₦")
    assert api.get(BASE + "payments/?group=settled").json()["count"] == 0
    summary = api.get(BASE + "payments/summary/").json()
    assert summary["counts"]["due"] == 1 and summary["due_total"] == "4000000.00"
    detail = api.get(BASE + f"payments/{bill.pk}/").json()
    assert detail["payable"] is True and detail["reference"] == bill.reference
    assert detail["bank_account"]["account_number"] == "1234567890"
    assert detail["slip"]["version"] == 1 and detail["steps"][0]["state"] == "current"
    assert detail["slip"]["file"]["download_url"] == f"/api/v1/payments/{bill.pk}/slip/"
    r = api.get(BASE + f"payments/{bill.pk}/slip/")
    assert r.status_code == 200 and b"".join(r.streaming_content).startswith(b"%PDF")
    # no Payment Advice before Finance confirms
    assert api.get(BASE + f"payments/{bill.pk}/receipt/").status_code == 404
    # another institution: 404 everywhere
    other = bearer(APIClient(HTTP_HOST=portal_host), p3.admin)
    for path in ("", "slip/", "receipt/"):
        assert other.get(BASE + f"payments/{bill.pk}/{path}").status_code == 404
    assert other.post(BASE + f"payments/{bill.pk}/online/", {"provider": "paystack"},
                      format="json").status_code == 404


def test_online_payment_and_verify(portal_api, bill, inst_user, settings):
    from payments import services

    settings.QA_PAYMENTS_SIMULATE = True
    api = bearer(portal_api, inst_user)
    r = api.post(BASE + f"payments/{bill.pk}/online/", {"provider": "visa"}, format="json")
    assert r.status_code == 400 and "provider" in err(r)["fields"]
    r = api.post(BASE + f"payments/{bill.pk}/online/", {"provider": "paystack", "amount": "1.00"}, format="json")
    assert r.status_code == 400  # part payments are not accepted online
    r = api.post(BASE + f"payments/{bill.pk}/online/", {"provider": "paystack"}, format="json")
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["checkout_url"] and body["provider"] == "paystack"
    assert body["return_url"].endswith(f"/payments/{bill.pk}/")
    r = api.post(BASE + f"payments/{bill.pk}/verify/", {"params": {"reference": "nothing-like-it"}}, format="json")
    assert r.status_code == 200 and r.json()["matched"] is False
    services.simulate_provider_callback("paystack", body["reference"], "success")
    r = api.post(BASE + f"payments/{bill.pk}/verify/", {"params": {"reference": body["reference"]}}, format="json")
    assert r.status_code == 200
    res = r.json()
    assert res["matched"] is True and res["attempt"]["status"] == "provider_reported_paid"
    # v2.1 X21: a provider-verified payment needs no receipt upload; it goes straight to the DoF.
    assert res["bill"]["awaiting_confirmation"] is True and res["bill"]["receipt_needed"] is False


def test_proof_upload(portal_api, bill, inst_user):
    api = bearer(portal_api, inst_user)
    r = api.post(BASE + f"payments/{bill.pk}/proof/", {"bank_name": "CBN"}, format="multipart")
    assert r.status_code == 400 and {"amount_claimed", "paid_on", "file"} <= set(err(r)["fields"])
    r = api.post(BASE + f"payments/{bill.pk}/proof/", {
        "amount_claimed": "4000000", "paid_on": "2020-01-01", "bank_name": "CBN", "teller_number": "T-1",
        "payer_name": "Bursar", "file": pdf_upload("teller.pdf")}, format="multipart")
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["payable"] is False and body["proofs"][0]["kind"] == "teller"
    proof_url = body["proofs"][0]["file"]["download_url"]
    assert api.get(proof_url).status_code == 200


def test_slip_regenerate_only_while_unpaid(portal_api, bill, inst_user):
    api = bearer(portal_api, inst_user)
    r = api.post(BASE + f"payments/{bill.pk}/slip/")
    assert r.status_code == 200 and r.json()["slip"]["version"] == 2
