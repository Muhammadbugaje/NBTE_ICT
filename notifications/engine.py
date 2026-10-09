"""Email delivery engine (doc 30 §5.4 / NR-05).

Order: manual override (config `email.provider_override` = brevo|gmail|auto)
pins the first choice, otherwise Brevo first. A provider whose daily quota is
used up is skipped; a provider that errors is failed over to the other one.
Quota is reserved with an atomic conditional UPDATE (safe on SQLite, Postgres
and MySQL, and across concurrent Celery workers) and released if the send fails.
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

PROVIDERS = ("brevo", "gmail")
DEFAULT_LIMITS = {"brevo": 300, "gmail": 500}  # doc 30 §5.4
# Same idea as NAMETS: important mail tries Brevo first (better deliverability);
# everything else tries Gmail first to save Brevo's daily allowance. Quotas apply
# to ALL mail - being critical does not bypass them (NAMETS doesn't either).
DEFAULT_CRITICAL = ["credentials", "reset", "task", "approval", "test"]


class DeliveryError(Exception):
    pass


class QuotaExhausted(DeliveryError):
    """Every eligible provider has hit today's limit."""


class AllProvidersFailed(DeliveryError):
    pass


def today():
    return timezone.localdate()


def daily_limit(provider):
    return int(config.get(f"email.{provider}_daily_limit", DEFAULT_LIMITS[provider]))


def used_today(provider):
    row = EmailQuotaTracker.objects.filter(provider=provider, date=today()).first()
    return row.sent_count if row else 0


def critical_categories():
    return set(config.get("email.critical_categories", DEFAULT_CRITICAL))


def provider_order(category="general"):
    """Category decides the default order; a manual override pins the first
    choice (the other provider is still tried if the pinned one fails)."""
    order = ["brevo", "gmail"] if category in critical_categories() else ["gmail", "brevo"]
    override = config.get("email.provider_override", "auto")
    if override in PROVIDERS:
        order.remove(override)
        order.insert(0, override)
    return order


def _reserve(provider, n):
    """Atomically take n units of today's quota. False if it would exceed the limit."""
    limit = daily_limit(provider)
    EmailQuotaTracker.objects.get_or_create(provider=provider, date=today())
    with transaction.atomic():
        return bool(
            EmailQuotaTracker.objects.filter(
                provider=provider, date=today(), sent_count__lte=limit - n
            ).update(sent_count=F("sent_count") + n)
        )


def _release(provider, n):
    EmailQuotaTracker.objects.filter(provider=provider, date=today(), sent_count__gte=n).update(
        sent_count=F("sent_count") - n
    )


def _connection(provider):
    cfg = settings.EMAIL_PROVIDERS.get(provider, {})
    backend = cfg.get("BACKEND") or settings.EMAIL_BACKEND
    return get_connection(backend=backend, fail_silently=False, **cfg.get("OPTIONS", {}))


def send(subject, recipients, html, text="", category="general", trail=None):
    """Send now, synchronously. Returns the provider name that delivered it.
    If `trail` (a list) is given, every provider tried is appended to it as
    {"provider", "ok", "error", "at"} - this is what the email log displays."""
    recipients = list(recipients)
    n = max(len(recipients), 1)
    trail = trail if trail is not None else []
    order = provider_order(category)
    errors, quota_skipped = [], 0

    def note(provider, ok, error=""):
        trail.append({"provider": provider, "ok": ok, "error": error, "at": timezone.now().isoformat()})

    for provider in order:
        if not _reserve(provider, n):
            quota_skipped += 1
            note(provider, False, "daily limit reached - skipped")
            logger.warning("Email quota reached for %s; skipping", provider)
            continue
        try:
            msg = EmailMultiAlternatives(
                subject, text or "", settings.DEFAULT_FROM_EMAIL, recipients, connection=_connection(provider)
            )
            if html:
                msg.attach_alternative(html, "text/html")
            msg.send()
            note(provider, True)
            return provider
        except Exception as exc:  # any provider failure -> try the next one
            _release(provider, n)
            note(provider, False, str(exc)[:500])
            errors.append(f"{provider}: {exc}")
            logger.exception("Email send via %s failed", provider)
    if quota_skipped == len(order):
        raise QuotaExhausted("Daily email quota exhausted on all providers.")
    raise AllProvidersFailed("; ".join(errors) or "No provider available.")
