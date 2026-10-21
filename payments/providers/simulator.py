"""In-process stand-in for the Remita and Paystack APIs — DEVELOPMENT ONLY.

Active only while ``settings.QA_PAYMENTS_SIMULATE`` is true *and* a
provider's real keys are absent (production forces it off). It answers the
same calls the real adapters make, so the whole path — initialise, checkout,
signed webhook, server-side verify — runs exactly as it will with real keys.
State lives in the cache for a day.
"""
from __future__ import annotations

import json
import secrets

from django.core.cache import cache

TTL = 24 * 3600


def _key(provider, ref):
    return f"qa:paysim:{provider}:{ref}"


def _get(provider, ref):
    return cache.get(_key(provider, ref))


def _put(provider, ref, data):
    cache.set(_key(provider, ref), data, TTL)


# --------------------------------------------------------------------------- Paystack
def paystack(method, path, payload=None):
    from qa_core.hosts import portal_base_url, portal_reverse

    if method == "POST" and path == "/transaction/initialize":
        ref = payload["reference"]
        _put("paystack", ref, {"reference": ref, "amount": int(payload["amount"]), "status": "pending",
                               "email": payload.get("email", ""), "callback": payload.get("callback_url", "")})
        url = portal_base_url() + portal_reverse("qa_portal:pay_simulator", provider="paystack", ref=ref)
        return 200, {"status": True, "message": "Authorization URL created",
                     "data": {"authorization_url": url, "access_code": secrets.token_hex(8), "reference": ref}}
    if method == "GET" and path.startswith("/transaction/verify/"):
        ref = path.rsplit("/", 1)[-1]
        data = _get("paystack", ref)
        if data is None:
            return 404, {"status": False, "message": "Transaction reference not found"}
        from decimal import Decimal

        from .paystack import fee_bearer, paystack_fee

        status = data["status"]
        fees = 0
        if status == "success" and fee_bearer() == "institution":
            fees = int(paystack_fee(Decimal(data["amount"]) / 100) * 100)
        return 200, {"status": True, "message": "Verification successful",
                     "data": {"status": status, "reference": ref, "amount": data["amount"], "fees": fees,
                              "gateway_response": "Successful" if status == "success" else status.title(),
                              "currency": "NGN"}}
    return 404, {"status": False, "message": "Not simulated"}


def paystack_complete(ref, outcome="success"):
    """Mark a simulated checkout finished and return (raw_body, signature) of
    the webhook Paystack would send. outcome: success · failed · webhook_only
    (the webhook says success but verify will not — "verify wins")."""
    from .paystack import PaystackProvider

    data = _get("paystack", ref) or {"reference": ref, "amount": 0}
    data["status"] = "success" if outcome == "success" else "failed"
    _put("paystack", ref, data)
    event = "charge.success" if outcome in ("success", "webhook_only") else "charge.failed"
    body = json.dumps({"event": event, "data": {"reference": ref, "amount": data["amount"], "status":
                                                 "success" if event == "charge.success" else "failed"}}).encode()
    return body, PaystackProvider().signature_for(body)


# --------------------------------------------------------------------------- Remita
def remita_checkout_url(rrr):
    from qa_core.hosts import portal_base_url, portal_reverse

    return portal_base_url() + portal_reverse("qa_portal:pay_simulator", provider="remita", ref=rrr)


def remita(method, path, payload=None):
    if method == "POST" and path.endswith("/merchant/api/paymentinit"):
        rrr = "".join(secrets.choice("0123456789") for _ in range(12))
        _put("remita", rrr, {"rrr": rrr, "amount": payload["amount"], "orderId": payload["orderId"],
                             "status": "021"})
        return 200, {"statusCode": "025", "RRR": rrr, "status": "Payment Reference generated"}
    if method == "GET" and path.endswith("/status.reg"):
        parts = path.strip("/").split("/")
        rrr = parts[-3] if len(parts) >= 3 else ""
        data = _get("remita", rrr)
        if data is None:
            return 200, {"status": "998", "message": "Invalid RRR"}
        return 200, {"status": data["status"], "amount": data["amount"], "RRR": rrr, "orderId": data["orderId"],
                     "message": "Approved" if data["status"] in ("00", "01") else "Pending"}
    return 404, {"status": "999", "message": "Not simulated"}


def remita_complete(rrr, outcome="success"):
    """Mark a simulated RRR paid; return the notification body Remita sends."""
    data = _get("remita", rrr) or {"rrr": rrr, "amount": "0", "orderId": ""}
    data["status"] = "00" if outcome == "success" else "02"
    _put("remita", rrr, data)
    return json.dumps([{"rrr": rrr, "orderRef": data.get("orderId", ""), "amount": data.get("amount"),
                        "transactiondate": "", "debitdate": ""}]).encode()


def lookup(provider, ref):
    return _get(provider, ref)
