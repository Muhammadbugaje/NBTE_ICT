"""Paystack (Master Plan §10.1).

1. Initialise a transaction (amount in kobo, our reference).
2. Redirect to Paystack checkout.
3. Accept a webhook only when the ``x-paystack-signature`` HMAC-SHA512 of the
   raw body, keyed with the secret key, matches.
4. ALWAYS follow it with a server-side transaction verify. "Verify wins": a
   webhook that says success while verify does not is recorded as failed.

Whether Paystack settlement is permissible for federal revenue must be
confirmed by Finance before the toggle is switched on in production (Q-05).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

import config

from . import simulator
from .base import PaymentProvider, ProviderError, VerificationResult, http_json, redact

SIM_SECRET = "sk_test_nbte_simulator"


def paystack_fee(amount):
    """Local cards: 1.5% + ₦100 (the ₦100 waived under ₦2,500), capped at ₦2,000."""
    amount = Decimal(amount)
    fee = amount * Decimal("0.015") + (Decimal("100") if amount >= Decimal("2500") else Decimal("0"))
    return min(fee, Decimal("2000")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fee_bearer():
    return str(config.get("qa.payment.paystack.fee_bearer", "institution") or "institution").strip().lower()


class PaystackProvider(PaymentProvider):
    code = "paystack"
    label = "Paystack (card, transfer, USSD)"
    env_vars = ("PAYSTACK_SECRET_KEY", "PAYSTACK_PUBLIC_KEY")
    online = True

    def secret(self):
        return settings.PAYSTACK_SECRET_KEY or (SIM_SECRET if self.is_simulated() else "")

    def supports_partial(self):
        return True

    def charge_for(self, amount):
        """What the institution is charged so NBTE receives `amount` after
        Paystack's fee (grossed up) when the institution bears the fee."""
        amount = Decimal(amount)
        if fee_bearer() != "institution":
            return amount
        flat = Decimal("100") if amount >= Decimal("2400") else Decimal("0")
        gross = ((amount + flat) / Decimal("0.985")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if gross - amount > Decimal("2000"):
            gross = amount + Decimal("2000")
        return gross

    def _call(self, method, path, payload=None):
        if self.is_simulated():
            return simulator.paystack(method, path, payload)
        if not self.secret():
            from .base import ProviderConfigError
            raise ProviderConfigError("PAYSTACK_SECRET_KEY is not set.")
        return http_json(method, f"{settings.PAYSTACK_BASE_URL}{path}", payload=payload,
                         headers={"Authorization": f"Bearer {self.secret()}"})

    def initiate(self, obligation, payer, amount, *, request=None, attempt=None, callback_url=""):
        charge = self.charge_for(amount)
        payload = {
            "email": payer.email,
            "amount": int((charge * 100).to_integral_value(rounding=ROUND_HALF_UP)),
            "currency": "NGN",
            "reference": attempt.provider_reference,
            "callback_url": callback_url,
            "metadata": {"payment_reference": obligation.reference, "institution": obligation.institution.code,
                         "amount_due": str(amount), "fee_bearer": fee_bearer()},
        }
        status, body = self._call("POST", "/transaction/initialize", payload)
        attempt.raw_request = redact(payload)
        attempt.raw_response = redact(body)
        if status >= 400 or not body.get("status"):
            raise ProviderError(body.get("message") or f"Paystack refused the request (HTTP {status}).")
        data = body.get("data") or {}
        attempt.checkout_url = data.get("authorization_url", "")
        return attempt

    def verify(self, attempt):
        status, body = self._call("GET", f"/transaction/verify/{attempt.provider_reference}")
        data = body.get("data") or {}
        ok = bool(body.get("status")) and data.get("status") == "success" and status < 400
        amount = None
        if data.get("amount") is not None:
            charged = Decimal(str(data.get("amount"))) / 100
            fees = Decimal(str(data.get("fees") or 0)) / 100
            # When the institution bears the fee, NBTE is credited the charge minus fees.
            amount = (charged - fees) if (fee_bearer() == "institution" and fees) else charged
            amount = amount.quantize(Decimal("0.01"))
        return VerificationResult(paid=ok, status=str(data.get("status") or body.get("message") or status),
                                  amount=amount, reference=data.get("reference", attempt.provider_reference),
                                  message=str(data.get("gateway_response") or body.get("message") or ""),
                                  raw=redact(body), currency=str(data.get("currency") or ""))

    # -- webhooks ------------------------------------------------------------
    def signature_for(self, raw_body):
        return hmac.new(self.secret().encode(), raw_body, hashlib.sha512).hexdigest()

    def parse_webhook(self, request):
        """(signature_valid, reference, payload) — the reference is taken only
        from a correctly signed body."""
        raw = request.body or b""
        given = request.headers.get("x-paystack-signature", "")
        secret = self.secret()
        valid = bool(secret and given and hmac.compare_digest(self.signature_for(raw), given))
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        reference = ""
        if valid and payload.get("event") == "charge.success":
            reference = str((payload.get("data") or {}).get("reference") or "")
        return valid, reference, payload
