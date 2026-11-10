"""Remita (Master Plan §10.1) — the expected default rail for federal revenue
(Treasury Single Account; Finance to confirm, Risk 2).

1. Generate an RRR against ``FeeSchedule.remita_service_type_id`` with our
   payment reference as the order ID.
2. The institution pays online or at any bank with the RRR (printed on the slip).
3. Remita's notification is never trusted on its own: a server-side status
   query is made before anything is recorded. An optional IP allow-list
   (REMITA_WEBHOOK_ALLOWED_IPS) filters callers.
4. A scheduled poll catches notifications that never arrive.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.conf import settings

from . import simulator
from .base import PaymentProvider, ProviderConfigError, ProviderError, VerificationResult, http_json, redact

PAID_STATUSES = ("00", "01")


def sha512(text):
    return hashlib.sha512(text.encode()).hexdigest()


class RemitaProvider(PaymentProvider):
    code = "remita"
    label = "Remita (RRR — online or at any bank)"
    env_vars = ("REMITA_MERCHANT_ID", "REMITA_API_KEY")
    online = True

    def merchant(self):
        return settings.REMITA_MERCHANT_ID or ("2547916" if self.is_simulated() else "")

    def api_key(self):
        return settings.REMITA_API_KEY or ("1946" if self.is_simulated() else "")

    def _call(self, method, path, payload=None, headers=None):
        if self.is_simulated():
            return simulator.remita(method, path, payload)
        api_path = getattr(settings, "REMITA_API_PATH", "/exapp/api/v1/send/api")
        return http_json(method, f"{settings.REMITA_BASE_URL}{api_path}{path}", payload=payload, headers=headers)

    # -- RRR ----------------------------------------------------------------
    def generate_rrr(self, obligation, payer, amount, order_id):
        service_type = (obligation.fee_schedule.remita_service_type_id or getattr(settings, "REMITA_SERVICE_TYPE_ID", "")
                        or ("4430731" if self.is_simulated() else ""))
        if not service_type:
            raise ProviderConfigError("The fee schedule has no Remita service type ID (Settings → Fees).")
        amount_text = f"{Decimal(amount):.2f}"
        token = sha512(f"{self.merchant()}{service_type}{order_id}{amount_text}{self.api_key()}")
        payload = {"serviceTypeId": service_type, "amount": amount_text, "orderId": order_id,
                   "payerName": obligation.institution.name[:100], "payerEmail": getattr(payer, "email", ""),
                   "payerPhone": "", "description": obligation.description[:100]}
        headers = {"Authorization": f"remitaConsumerKey={self.merchant()},remitaConsumerToken={token}"}
        status, body = self._call("POST", "/echannelsvc/merchant/api/paymentinit", payload, headers)
        rrr = str(body.get("RRR") or "").strip()
        if status >= 400 or not rrr:
            raise ProviderError(body.get("status") or body.get("statusMessage")
                                or f"Remita did not return an RRR (HTTP {status}).")
        return rrr, redact(payload), redact(body)

    def checkout_url(self, rrr, response_url=""):
        if self.is_simulated():
            return simulator.remita_checkout_url(rrr)
        digest = sha512(f"{self.merchant()}{rrr}{self.api_key()}")
        return (f"{settings.REMITA_BASE_URL}/ecomm/finalize.reg?merchantId={self.merchant()}&hash={digest}"
                f"&rrr={rrr}&responseurl={response_url}")

    def initiate(self, obligation, payer, amount, *, request=None, attempt=None, callback_url=""):
        rrr, req, resp = self.generate_rrr(obligation, payer, amount, attempt.raw_request.get("orderId")
                                           or attempt.provider_reference)
        attempt.raw_request, attempt.raw_response = req, resp
        attempt.raw_request["orderId"] = req.get("orderId")
        attempt.provider_reference = rrr
        attempt.checkout_url = self.checkout_url(rrr, callback_url)
        return attempt

    def verify(self, attempt):
        rrr = attempt.provider_reference
        digest = sha512(f"{rrr}{self.api_key()}{self.merchant()}")
        headers = {"Authorization": f"remitaConsumerKey={self.merchant()},remitaConsumerToken={digest}"}
        status, body = self._call("GET", f"/echannelsvc/{self.merchant()}/{rrr}/{digest}/status.reg",
                                  headers=headers)
        code = str(body.get("status") or "")
        amount = None
        if body.get("amount") not in (None, ""):
            amount = Decimal(str(body.get("amount"))).quantize(Decimal("0.01"))
        return VerificationResult(paid=code in PAID_STATUSES and status < 400, status=code, amount=amount,
                                  reference=rrr, message=str(body.get("message") or ""), raw=redact(body))

    # -- notifications ---------------------------------------------------
    def parse_webhook(self, request):
        """(caller_allowed, [rrr, …], payload). Remita does not sign its
        notifications, so the status query that follows is what counts."""
        allowed = settings.REMITA_WEBHOOK_ALLOWED_IPS
        from core.ratelimit import client_ip

        ip = client_ip(request)   # X-Real-IP from nginx or the socket address — never a forgeable XFF
        caller_ok = (not allowed) or ip in allowed
        token = getattr(settings, "REMITA_WEBHOOK_TOKEN", "")
        if token:  # v2.1 F.18: shared secret in the registered notification URL
            import hmac

            given = ""
            if hasattr(request, "GET"):
                given = request.GET.get("token", "")
            caller_ok = caller_ok and bool(given) and hmac.compare_digest(given, token)
        try:
            payload = json.loads((request.body or b"").decode("utf-8") or "[]")
        except ValueError:
            payload = []
        items = payload if isinstance(payload, list) else [payload]
        rrrs = [str(i.get("rrr") or i.get("RRR") or "").strip() for i in items if isinstance(i, dict)]
        return caller_ok, [r for r in rrrs if r], payload
