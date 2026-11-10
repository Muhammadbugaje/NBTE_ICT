"""QA e-mail delivery through the configured notification engine.

`send()` is the single path every QA e-mail takes: credentials links,
notification e-mails, payment advices. While
``settings.QA_EMAIL_DELIVERY`` is ``"queue"`` (the default), a message is sent
through the existing notifications e-mail engine. Set it to ``"console"`` to
log-only for local development; nothing leaves the server in that mode.
"""
from __future__ import annotations

import logging
import sys

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger("qa.email")


def delivery_mode():
    return str(getattr(settings, "QA_EMAIL_DELIVERY", "queue") or "queue").lower()


def html_to_text(html):
    """A readable plain-text version of an e-mail: no <head>, no hidden
    preheader, entities decoded, paragraphs and table rows on their own lines."""
    import html as htmllib
    import re

    body = re.sub(r"(?is)<(head|style|script|title)\b.*?</\1>", " ", html or "")
    body = re.sub(r'(?is)<(div|span|p)\b[^>]*display\s*:\s*none[^>]*>.*?</\1>', " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|h[1-6]|li|table)>", "\n", body)
    body = re.sub(r"(?i)</t[dh]>", "  ", body)
    body = htmllib.unescape(strip_tags(body)).replace("\u200c", "").replace("\u200b", "").replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in body.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def send(subject, recipients, template_name, context=None, *, category="qa", attachments=None,
         portal_notification=True):
    """``attachments``: optional ``[(filename, bytes, mime type)]``. In console
    mode only their names are recorded; in queue mode the message is rendered
    and delivered immediately through the e-mail engine (with its attachments)
    and logged, then copied to the recipient's portal notification feed.

    Every e-mail (except sensitive credential/reset ones) also lands as an
    in-app notification for each recipient with a portal account, so the
    portal always carries the message even if the mail provider is down."""
    recipients = sorted({r for r in (recipients or []) if r})
    if not recipients:
        return None
    if delivery_mode() == "queue":
        # v2.7: deliver now rather than waiting for a Celery worker. A mail
        # outage must not lose the portal copy, so the notification is written
        # first and the delivery is best-effort.
        ctx = {"site_name": getattr(settings, "SITE_NAME", "NBTE"), "site_url": getattr(settings, "SITE_URL", "")}
        ctx.update(context or {})
        try:
            from django.template.loader import render_to_string

            html = render_to_string(template_name, ctx)
        except Exception:  # a template problem must never break the action
            logger.exception("QA e-mail template %s failed", template_name)
            html = ""
        text = html_to_text(html)
        if portal_notification:
            _notify_recipients(subject, recipients, category, context, body_text=text)
        try:
            record = _send_now_with_attachments(subject, recipients, template_name, context, category, attachments)
        except Exception:
            logger.exception("QA e-mail delivery failed for %r; the portal copy was kept", subject)
            return None
        return record
    from .models import OutboundEmail

    ctx = {"site_name": getattr(settings, "SITE_NAME", "NBTE"), "site_url": getattr(settings, "SITE_URL", "")}
    ctx.update(context or {})
    try:
        html = render_to_string(template_name, ctx)
    except Exception:  # a template problem must never break the action
        logger.exception("QA e-mail template %s failed", template_name)
        html = ""
    text = html_to_text(html)
    if attachments:
        text += "\n\nAttachments: " + ", ".join(f"{name} ({len(content or b'') // 1024 + 1} KB)"
                                                 for name, content, _mime in attachments)
    record = OutboundEmail.objects.create(subject=subject[:255], recipients=recipients, body_html=html,
                                          body_text=text[:20000], category=category[:40])
    block = (f"\n----- QA e-mail (console; not sent) -----\nTo: {', '.join(recipients)}\n"
             f"Subject: {subject}\n\n{text[:4000]}\n----- end -----\n")
    logger.info(block)
    if getattr(settings, "QA_EMAIL_ECHO_STDERR", True):
        try:
            sys.stderr.write(block)
        except Exception:
            pass
    if portal_notification:
        _notify_recipients(subject, recipients, category, context, body_text=text)
    return record


def _notify_recipients(subject, recipients, category, context, *, body_text=""):
    """v2.6: one copy of the e-mail as a portal notification per user account
    (never for credential/reset mails — a password link does not belong in an
    inbox feed). Never raises; best-effort."""
    try:
        from notifications.models import EmailLog, Notification
        from notifications.services import notify_many

        from accounts.models import User

        if category in EmailLog.sensitive_categories():
            return []
        url = str((context or {}).get("url") or "")
        text = (body_text or "").strip()
        title = str(subject or "Message from NBTE")[:255]
        body = text[:600] or f"E-mail sent to you: {title}."
        from django.db.models import Q

        email_filter = Q()
        for recipient in recipients:
            email_filter |= Q(email__iexact=recipient)
        users = list(User.objects.filter(is_active=True).filter(email_filter).distinct())
        return notify_many(users, title=title, body=body, url=url,
                           category="qa" if category else Notification.SYSTEM,
                           level=Notification.INFO)
    except Exception:
        logger.exception("portal copy of the e-mail %r failed", subject)
        return []


def _send_now_with_attachments(subject, recipients, template_name, context, category, attachments):
    """Queue mode with attachments: render, send through the engine now, log it."""
    from django.utils import timezone

    from notifications import engine
    from notifications.models import EmailLog

    ctx = {"site_name": getattr(settings, "SITE_NAME", "NBTE"), "site_url": getattr(settings, "SITE_URL", "")}
    ctx.update(context or {})
    html = render_to_string(template_name, ctx)
    text = html_to_text(html)
    log = EmailLog.objects.create(subject=subject[:255], recipients=recipients, recipient_text=", ".join(recipients),
                                  category=category, scheduled_for=timezone.now(),
                                  body_html="" if category in EmailLog.sensitive_categories() else html)
    trail = []
    log.attempts = 1
    try:
        provider = engine.send(subject, recipients, html, text, category=category, trail=trail,
                               attachments=attachments)
    except Exception as exc:
        log.status, log.last_error, log.attempt_trail = EmailLog.FAILED, engine.safe_error_message(exc), trail
        log.save(update_fields=["status", "last_error", "attempt_trail", "attempts"])
        raise
    log.status, log.provider, log.sent_at, log.attempt_trail = EmailLog.SENT, provider, timezone.now(), trail
    log.save(update_fields=["status", "provider", "sent_at", "attempt_trail", "attempts"])
    return log
