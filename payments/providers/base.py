"""The provider interface and shared HTTP plumbing (stdlib urllib, with
certifi's CA bundle for TLS verification)."""
from __future__ import annotations

import functools
import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings

import config

logger = logging.getLogger(__name__)
USER_AGENT = "NBTE-QA-Portal/1.0 (+https://nbte.gov.ng)"
SECRET_KEYS = ("secret", "authorization", "token", "api_key", "apikey", "hash", "password")


class ProviderError(Exception):
    """The provider could not be reached or refused the request."""

    #: plain words for the institution (no technical detail)
    public_message = "The payment provider did not accept the request. Nothing was charged. Please try again."


class ProviderUnreachable(ProviderError):
    """No answer: offline, DNS failure, timeout or a dropped connection. The
    request may or may not have reached the provider, so the caller must ask
    the provider (verify) before treating the attempt as failed."""

    public_message = ("We could not reach the payment provider. Check your internet connection and try again "
                      "in a moment. If money left your account, do not pay again — the payment is checked "
                      "automatically and will appear here.")

    def __init__(self, message="", *, timeout=False):
        super().__init__(message)
        self.timeout = timeout


class ProviderConfigError(ProviderError):
    """NBTE's own set-up is wrong (missing or invalid keys, bad base URL)."""

    public_message = ("Online payment is temporarily unavailable. NBTE has been alerted. You can pay by bank "
                      "transfer instead, or try again later.")


def public_message(exc):
    """What the institution should read for any provider error."""
    return getattr(exc, "public_message", ProviderError.public_message)


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
    currency: str = ""  # v2.1 X21: "" when the provider does not say (Remita: NGN only)


def redact(data):
    """Copy of `data` with anything secret-looking blanked (raw_request/response)."""
    if isinstance(data, dict):
        return {k: ("***" if any(s in str(k).lower() for s in SECRET_KEYS) else redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


@functools.lru_cache(maxsize=1)
def tls_context():
    """A verifying TLS context that trusts certifi's CA bundle.

    python.org builds of Python on macOS do not use the system keychain, so the
    default context fails with ``CERTIFICATE_VERIFY_FAILED: unable to get local
    issuer certificate`` unless "Install Certificates.command" was run. certifi
    makes outbound HTTPS work the same on every OS. Verification stays on."""
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi is in requirements.txt
        logger.warning("certifi is not installed; using the OS certificate store for HTTPS")
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def urlopen(request, *, timeout=20):
    """``urllib.request.urlopen`` with :func:`tls_context` — use it for every outbound HTTPS call."""
    return urllib.request.urlopen(request, timeout=timeout, context=tls_context())  # noqa: S310


def http_json(method, url, *, payload=None, headers=None, timeout=20):
    """Minimal JSON over HTTPS with urllib. Returns (status, parsed_body)."""
    import socket

    body = json.dumps(payload).encode() if payload is not None else None
    try:
        request = urllib.request.Request(url, data=body, method=method)
    except ValueError as exc:  # an empty or malformed base URL in .env
        logger.error("payment provider URL is invalid: %r", url)
        raise ProviderConfigError(f"The payment provider address is not valid ({exc}).") from None
    request.add_header("Accept", "application/json")
    # Paystack's edge answers 403 to the default "Python-urllib/x.y" agent,
    # which then looks like refused keys: always send our own.
    request.add_header("User-Agent", USER_AGENT)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urlopen(request, timeout=timeout) as response:  # https URLs from settings
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
        status = exc.code
    except (TimeoutError, socket.timeout) as exc:
        logger.warning("payment provider timeout: %s %s", method, url.split("?")[0])
        raise ProviderUnreachable(f"The payment provider did not answer in time ({type(exc).__name__}).",
                                  timeout=True) from None
    except urllib.error.URLError as exc:
        timed_out = isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
        logger.warning("payment provider unreachable: %s %s (%s)", method, url.split("?")[0], exc.reason)
        raise ProviderUnreachable(f"Could not reach the payment provider ({type(exc.reason).__name__}).",
                                  timeout=timed_out) from None
    except OSError as exc:
        logger.warning("payment provider connection error: %s", exc)
        raise ProviderUnreachable(f"Could not reach the payment provider ({type(exc).__name__}).") from None
    except ValueError as exc:  # an empty or malformed base URL in .env
        logger.error("payment provider URL is invalid: %r", url)
        raise ProviderConfigError(f"The payment provider address is not valid ({exc}).") from None
    if status in (401, 403):
        logger.error("payment provider refused NBTE's credentials (HTTP %s) for %s", status, url.split("?")[0])
        raise ProviderConfigError(f"The payment provider refused NBTE's keys (HTTP {status}). Check the keys in .env.")
    if status >= 500:
        logger.warning("payment provider error HTTP %s for %s", status, url.split("?")[0])
        raise ProviderUnreachable(f"The payment provider had an internal error (HTTP {status}).")
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
