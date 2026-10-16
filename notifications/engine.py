"""Email delivery engine (doc 30 §5.4 / NR-05).

Brevo is the primary provider for every category. Gmail is the fallback when
Brevo is unavailable, over quota, or fails. A manual provider override can pin
the first choice. Quota is reserved with an atomic conditional UPDATE (safe on
SQLite, Postgres and MySQL, and across concurrent Celery workers) and released
if the send fails.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import F
from django.utils import timezone

import config

from .models import EmailQuotaTracker

logger = logging.getLogger(__name__)

PROVIDERS = ("brevo_smtp", "brevo", "gmail")
DEFAULT_LIMITS = {"brevo": 300,"brevo_smtp": 300,"gmail": 500}  # doc 30 §5.4


class DeliveryError(Exception):
    pass


class NoEmailProviderConfigured(DeliveryError):
    """No provider has all required credentials configured."""


class QuotaExhausted(DeliveryError):
    """Every eligible provider has hit today's limit."""


class AllProvidersFailed(DeliveryError):
    pass


def today():
    return timezone.localdate()


# Review M15: Brevo SMTP and the Brevo HTTP API are two doors into ONE Brevo
# account with ONE daily allowance, so they share a single quota counter.
_QUOTA_KEYS = {"brevo_smtp": "brevo", "brevo": "brevo", "gmail": "gmail"}


def quota_key(provider):
    return _QUOTA_KEYS.get(provider, provider)


def daily_limit(provider):
    key = quota_key(provider)
    return int(config.get(f"email.{key}_daily_limit", DEFAULT_LIMITS[key]))


def used_today(provider):
    row = EmailQuotaTracker.objects.filter(provider=quota_key(provider), date=today()).first()
    return row.sent_count if row else 0



def _provider_is_configured(provider):
    cfg = settings.EMAIL_PROVIDERS.get(provider, {})
    backend = cfg.get("BACKEND")
    options = cfg.get("OPTIONS") or {}

    if backend == "anymail.backends.brevo.EmailBackend":
        return bool(getattr(settings, "ANYMAIL", {}).get("BREVO_API_KEY"))
    if backend == "django.core.mail.backends.smtp.EmailBackend":
        return bool(options.get("host") and options.get("username") and options.get("password"))
    if not backend or backend == "django.core.mail.backends.console.EmailBackend":
        return False
    return True


def safe_error_message(exc):
    """Return useful delivery diagnostics without leaking configured secrets."""
    message = f"{type(exc).__name__}: {exc}"
    providers = getattr(settings, "EMAIL_PROVIDERS", {})
    secrets = [
        getattr(settings, "ANYMAIL", {}).get("BREVO_API_KEY", ""),
        providers.get("gmail", {}).get("OPTIONS", {}).get("password", ""),
        # Review M15: the Brevo SMTP key was not redacted.
        providers.get("brevo_smtp", {}).get("OPTIONS", {}).get("password", ""),
        getattr(settings, "BREVO_SMTP_KEY", ""),
        getattr(settings, "EMAIL_HOST_PASSWORD", ""),
    ]
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def provider_order(category="general"):
    """Try Brevo SMTP first, then Brevo API, then Gmail.
    An explicit override via the config registry reshuffles the order."""
    order = ["brevo_smtp", "brevo", "gmail"]
    override = config.get("email.provider_override", "auto")
    if override in PROVIDERS:
        order.remove(override)
        order.insert(0, override)

    configured = [
        provider for provider in order
        if _provider_is_configured(provider)
    ]
    logger.debug("Email provider order category=%s providers=%s", category, configured)
    return configured

def _reserve(provider, n):
    """Atomically take n units of today's quota. False if it would exceed the limit."""
    limit = daily_limit(provider)
    key = quota_key(provider)
    EmailQuotaTracker.objects.get_or_create(provider=key, date=today())
    with transaction.atomic():
        return bool(
            EmailQuotaTracker.objects.filter(
                provider=key, date=today(), sent_count__lte=limit - n
            ).update(sent_count=F("sent_count") + n)
        )


def _release(provider, n):
    EmailQuotaTracker.objects.filter(provider=quota_key(provider), date=today(), sent_count__gte=n).update(
        sent_count=F("sent_count") - n
    )


def _connection(provider):
    cfg = settings.EMAIL_PROVIDERS.get(provider, {})
    backend = cfg.get("BACKEND")
    if not backend:
        raise NoEmailProviderConfigured(f"No backend is configured for {provider}.")
    return get_connection(backend=backend, fail_silently=False, **cfg.get("OPTIONS", {}))


def send(
    subject,
    recipients,
    html,
    text="",
    category="general",
    trail=None,
    from_email=None,
    reply_to=None,
):
    """Send now, synchronously. Returns the provider name that delivered it.
    If `trail` (a list) is given, every provider tried is appended to it as
    {"provider", "ok", "error", "at"} - this is what the email log displays."""
    recipients = list(recipients)
    n = max(len(recipients), 1)
    trail = trail if trail is not None else []
    order = provider_order(category)
    if not order:
        logger.error("Email delivery unavailable category=%s; no configured provider", category)
        raise NoEmailProviderConfigured(
            "No email provider is configured. Set BREVO_API_KEY or both "
            "GMAIL_USER and GMAIL_APP_PASSWORD."
        )
    errors, quota_skipped = [], 0

    def note(provider, ok, error=""):
        trail.append({"provider": provider, "ok": ok, "error": error, "at": timezone.now().isoformat()})

    for provider in order:
        if not _reserve(provider, n):
            quota_skipped += 1
            note(provider, False, "daily limit reached - skipped")
            logger.warning(
                "Email provider skipped category=%s provider=%s reason=daily_quota",
                category, provider,
            )
            continue
        try:
            logger.info(
                "Email delivery attempt category=%s provider=%s recipient_count=%s",
                category, provider, len(recipients),
            )
            msg = EmailMultiAlternatives(
                subject,
                text or "",
                from_email or settings.DEFAULT_FROM_EMAIL,
                recipients,
                reply_to=[reply_to] if reply_to else None,
                connection=_connection(provider),
            )
            if html:
                msg.attach_alternative(html, "text/html")
            sent = msg.send()
            if not sent:
                raise AllProvidersFailed(f"{provider}: backend returned 0 sent messages")
            note(provider, True)
            logger.info(
                "Email accepted by provider category=%s provider=%s recipient_count=%s",
                category, provider, len(recipients),
            )
            return provider
        except Exception as exc:  # any provider failure -> try the next one
            _release(provider, n)
            error = safe_error_message(exc)
            note(provider, False, error)
            errors.append(f"{provider}: {error}")
            logger.error(
                "Email delivery failed category=%s provider=%s error=%s",
                category, provider, error,
            )
    if quota_skipped == len(order):
        raise QuotaExhausted("Daily email quota exhausted on all providers.")
    raise AllProvidersFailed("; ".join(errors) or "No provider available.")
