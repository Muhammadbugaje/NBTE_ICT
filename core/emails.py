"""Outbound transactional email — one entry point, real delivery only.

Reads providers from `settings.EMAIL_PROVIDERS` and tries them in order:
    1. Brevo (API — via anymail)
    2. Gmail (SMTP — App Password)

Never falls through to a console backend. If no provider succeeds, raises
`EmailDeliveryError` so callers know exactly what happened instead of
silently dropping the message.

Credentials are environment variables only:
    BREVO_API_KEY          (from app.brevo.com → SMTP & API → API keys)
    GMAIL_USER             (your Gmail address)
    GMAIL_APP_PASSWORD     (16-char app password, not your account password)
    DEFAULT_FROM_EMAIL     (e.g. "NBTE ICT <ict@nbte.gov.ng>")
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when every configured provider refused to deliver the message."""


def _ordered_providers():
    """Return the provider map from settings, or an empty dict."""
    providers = getattr(settings, "EMAIL_PROVIDERS", None) or {}
    return providers


def _default_from():
    return getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@localhost"


def _to_list(to):
    if isinstance(to, str):
        return [to]
    return [a for a in (to or []) if a]


def _try_provider(name, conf, subject, to_list, text_body, html_body,
                  from_email, reply_to):
    backend_path = conf.get("BACKEND")
    if not backend_path:
        raise EmailDeliveryError(f"{name}: no BACKEND configured")

    options = dict(conf.get("OPTIONS") or {})
    connection = get_connection(backend=backend_path, **options)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body or "",
        from_email=from_email or _default_from(),
        to=to_list,
        reply_to=[reply_to] if reply_to else None,
        connection=connection,
    )
    if html_body:
        msg.attach_alternative(html_body, "text/html")

    sent = msg.send(fail_silently=False)
    if not sent:
        raise EmailDeliveryError(f"{name}: backend returned 0 sent")


def send(*, subject, to, text_body="", html_body=None,
         from_email=None, reply_to=None):
    """Send one email. Returns the name of the provider that delivered it.

    Raises `EmailDeliveryError` if every provider fails.
    """
    to_list = _to_list(to)
    if not to_list:
        raise EmailDeliveryError("No recipient address provided")

    providers = _ordered_providers()
    if not providers:
        raise EmailDeliveryError(
            "No EMAIL_PROVIDERS configured. Set BREVO_API_KEY or "
            "GMAIL_USER / GMAIL_APP_PASSWORD in your environment."
        )

    # Try in a defined order regardless of dict insertion order.
    order = [p for p in ("brevo", "gmail") if p in providers]
    # Include any additional providers listed after gmail, preserving order.
    for extra in providers:
        if extra not in order:
            order.append(extra)

    errors = []
    for name in order:
        conf = providers.get(name) or {}
        try:
            _try_provider(
                name, conf,
                subject=subject,
                to_list=to_list,
                text_body=text_body,
                html_body=html_body,
                from_email=from_email,
                reply_to=reply_to,
            )
            logger.info("Email delivered via %s to %s", name, to_list)
            return name
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            logger.warning("Email via %s failed: %s", name, exc)

    raise EmailDeliveryError("All email providers failed: " + " | ".join(errors))