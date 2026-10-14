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
    """Send synchronously through the shared Brevo-first provider engine."""
    recipients = _to_list(to)
    if not recipients:
        raise EmailDeliveryError("No recipient address provided.")

    return _send(
        subject,
        recipients,
        html_body or "",
        text_body or "",
        category=category,
        from_email=from_email,
        reply_to=reply_to,
    )