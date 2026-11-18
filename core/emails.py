"""Compatibility adapter for synchronous mail, using the shared email engine."""

from notifications.engine import DeliveryError as EmailDeliveryError
from notifications.engine import send as _send


def _to_list(to):
    if isinstance(to, str):
        return [to] if to else []
    return [address for address in (to or []) if address]


def send(
    *,
    subject,
    to,
    text_body="",
    html_body=None,
    from_email=None,
    reply_to=None,
    category="general",
):
    """Hand the message to the shared provider engine. The send happens in the
    background after the action commits (core.background), so a slow or
    failing provider never holds up the page; failures are logged. Returns
    "queued" (or the provider name when EMAIL_DELIVERY_INLINE is on, e.g.
    under tests)."""
    recipients = _to_list(to)
    if not recipients:
        raise EmailDeliveryError("No recipient address provided.")

    from core import background

    args = (subject, recipients, html_body or "", text_body or "")
    kwargs = {"category": category, "from_email": from_email, "reply_to": reply_to}
    if background.inline():
        return _send(*args, **kwargs)
    background.submit(_send_logged, *args, label=f"e-mail '{subject[:40]}'", **kwargs)
    return "queued"


def _send_logged(subject, recipients, html, text, **kwargs):
    import logging

    try:
        _send(subject, recipients, html, text, **kwargs)
    except EmailDeliveryError as exc:
        logging.getLogger(__name__).warning("E-mail %r to %s not delivered: %s", subject, recipients, exc)