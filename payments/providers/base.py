"""The provider interface and shared HTTP plumbing (stdlib only — no new
dependencies)."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

import config

logger = logging.getLogger(__name__)
SECRET_KEYS = ("secret", "authorization", "token", "api_key", "apikey", "hash", "password")


class ProviderError(Exception):
    """The provider could not be reached or refused the request."""


@dataclass
class VerificationResult:
    """What a server-side verify found. `paid` means the provider says the
    money arrived — evidence for Finance, never a confirmation."""

    paid: bool
    status: str = ""
    amount: Decimal | None = None
    reference: str = ""
    message: str = ""
    raw: dict = field(default_factory=dict)


def redact(data):
    """Copy of `data` with anything secret-looking blanked (raw_request/response)."""
    if isinstance(data, dict):
        return {k: ("***" if any(s in str(k).lower() for s in SECRET_KEYS) else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


def http_json(method, url, *, payload=None, headers=None, timeout=20):
    """Minimal JSON over HTTPS with urllib. Returns (status, parsed_body)."""
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (https URLs from settings)
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"Could not reach the payment provider ({type(exc).__name__}).") from None
    return status, parse_body(raw)


def parse_body(raw):
    text = (raw or "").strip()
    if text.startswith("jsonp"):  # Remita wraps some answers: jsonp ({...})
        text = text[text.find("(") + 1: text.rfind(")")]
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return {"_raw": text[:2000]}


class PaymentProvider:
    code = ""
    label = ""
    env_vars = ()
    online = False

    # -- availability ------------------------------------------------------
    def setting_enabled(self):
        value = config.get(f"qa.payment.{self.code}.enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    def missing_env(self):
        return [name for name in self.env_vars if not getattr(settings, name, "")]

    def is_simulated(self):
        return bool(self.online and self.missing_env() and getattr(settings, "QA_PAYMENTS_SIMULATE", False))

    def is_configured(self):
        return not self.missing_env() or self.is_simulated()

    def is_enabled(self):
        """Setting on AND required environment variables present (§10.1)."""
        return self.setting_enabled() and self.is_configured()

    def supports_partial(self):
        return False

    # -- operations (overridden) -----------------------------------------
    def initiate(self, obligation, payer, amount, *, request=None, attempt=None):  # pragma: no cover
        raise ProviderError(f"{self.label} does not take online payments.")

    def verify(self, attempt):  # pragma: no cover
        raise ProviderError(f"{self.label} has nothing to verify.")

    def parse_webhook(self, request):  # pragma: no cover
        raise ProviderError(f"{self.label} does not send webhooks.")
