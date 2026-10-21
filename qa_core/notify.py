"""QA notifications (Master Plan §5.1 NotificationTemplate / NotificationRule).

Templates are rows that settings admins edit, so they are rendered in a
**sandbox**:

* a private template Engine with no loaders (``{% include %}`` and
  ``{% extends %}`` cannot read files) and no extra libraries (``{% load %}``
  of anything but the builtins fails);
* an **allow-listed context** of plain values only — strings, numbers,
  booleans, and lists/dicts of those. Model instances are never passed, so a
  template cannot walk the ORM (``{{ user.password }}`` has nothing to reach).

Delivery reuses the existing services: in-app through
``notifications.services.notify`` (category ``qa``) and email through the
existing email engine (``notifications.api.queue_email``).
"""
from __future__ import annotations

import datetime
import decimal
import logging

from django.template import Context, Engine, TemplateSyntaxError
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

_ENGINE = Engine(dirs=[], app_dirs=False, loaders=[], libraries={}, debug=False,
                 string_if_invalid="", autoescape=True)

_PRIMITIVES = (str, int, float, bool, decimal.Decimal, datetime.date, datetime.datetime)


class TemplateRenderError(ValueError):
    pass


def safe_context(data, _depth=0):
    """Copy `data` keeping only primitives (and lists/dicts of them)."""
    if _depth > 4:
        return None
    if data is None or isinstance(data, _PRIMITIVES):
        return data
    if isinstance(data, dict):
        return {str(k): safe_context(v, _depth + 1) for k, v in data.items()
                if isinstance(k, str) and not k.startswith("_")}
    if isinstance(data, (list, tuple)):
        return [safe_context(v, _depth + 1) for v in data][:50]
    return str(data)


def render_string(source, context):
    try:
        template = _ENGINE.from_string(source or "")
        return template.render(Context(safe_context(context or {}), autoescape=True))
    except TemplateSyntaxError as exc:
        raise TemplateRenderError(f"Template error: {exc}") from None
    except Exception as exc:  # TemplateDoesNotExist from a stray include, etc.
        raise TemplateRenderError(f"Template could not be rendered: {type(exc).__name__}.") from None


def validate_template(subject, body):
    """Raise TemplateRenderError if either part does not compile."""
    render_string(subject, {})
    render_string(body, {})


def render(template, context):
    """Return (subject, body) for a NotificationTemplate row."""
    return render_string(template.subject, context).strip(), render_string(template.body, context)


def dispatch(event_code, context, recipients, *, url="", actor=None, level="info"):
    """Send every active rule for `event_code`.

    `recipients` maps a rule's `recipient_kind` ("initiator",
    "eligible_confirmers", …) to an iterable of users. Never raises into the
    caller: a failed notification must not break the action that triggered it.
    """
    from notifications.models import Notification
    from notifications.services import notify

    from .models import NotificationRule

    sent = 0
    try:
        rules = (NotificationRule.objects.filter(event_code=event_code, is_active=True,
                                                 template__is_active=True)
                 .select_related("template"))
        for rule in rules:
            users = [u for u in (recipients.get(rule.recipient_kind) or []) if u is not None]
            if not users:
                continue
            try:
                subject, body = render(rule.template, context)
            except TemplateRenderError:
                logger.exception("QA template %s failed to render", rule.template.code)
                continue
            if rule.template.channel == "in_app":
                for user in {u.pk: u for u in users}.values():
                    notify(user, title=subject or event_code, body=strip_tags(body)[:2000], url=url,
                           category=Notification.QA, level=level, actor=actor,
                           metadata={"event": event_code})
                    sent += 1
            elif rule.template.channel == "email":
                _email(subject, body, users)
                sent += len(users)
            # SMS has no provider yet (Master Plan §19); rows are kept for later.
    except Exception:
        logger.exception("QA notification dispatch failed for %s", event_code)
    return sent


def _email(subject, body, users):
    from . import mail

    recipients = sorted({u.email for u in users if getattr(u, "email", "")})
    if not recipients:
        return
    mail.send(subject or "NBTE Quality Assurance", recipients, "emails/qa_notification.html",
              {"subject": subject, "body_html": body}, category="qa")
