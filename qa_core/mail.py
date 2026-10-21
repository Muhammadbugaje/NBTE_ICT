"""QA e-mail (Phase 2: logged to the console, never sent).

`send()` is the single path every QA e-mail takes: credentials links,
notification e-mails, payment advices. While
``settings.QA_EMAIL_DELIVERY`` is ``"console"`` (the default) a message is
written to the ``qa.email`` logger / stderr and stored in
:class:`~qa_core.models.OutboundEmail`; nothing leaves the server. Set it to
``"queue"`` to hand messages to the existing notifications e-mail engine.
"""
from __future__ import annotations

import logging
import sys

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger("qa.email")


def delivery_mode():
    return str(getattr(settings, "QA_EMAIL_DELIVERY", "console") or "console").lower()


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


def send(subject, recipients, template_name, context=None, *, category="qa"):
    recipients = sorted({r for r in (recipients or []) if r})
    if not recipients:
        return None
    if delivery_mode() == "queue":
        from core.email_utils import send_templated_email

        return send_templated_email(subject=subject, recipients=recipients, template_name=template_name,
                                    context=context, category=category)
    from .models import OutboundEmail

    ctx = {"site_name": getattr(settings, "SITE_NAME", "NBTE"), "site_url": getattr(settings, "SITE_URL", "")}
    ctx.update(context or {})
    try:
        html = render_to_string(template_name, ctx)
    except Exception:  # a template problem must never break the action
        logger.exception("QA e-mail template %s failed", template_name)
        html = ""
    text = html_to_text(html)
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
    return record
