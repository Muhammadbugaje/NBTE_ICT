"""Ticket emails.

Thin wrapper over `core.emails.send()` — sends the resolution notification
to the raiser via the project's real email pipeline (Brevo primary, Gmail
fallback). Provider outcomes are logged without recipient addresses; failures
are skipped so a bad email never blocks a ticket from being resolved.
"""
import logging

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from core.emails import EmailDeliveryError, send as send_email

logger = logging.getLogger(__name__)


def _cfg(key, default):
    try:
        import config as _cfg
        return _cfg.get(key, default)
    except Exception:
        return default


def _enabled():
    return str(_cfg("tickets.email.resolution_enabled", "1")) in ("1", "true", "True")


def _build_context(ticket):
    site_name = _cfg("site.department_name", "ICT Directorate")
    org_name = _cfg("site.organisation_name", "National Board for Technical Education")
    site_url = getattr(settings, "SITE_URL", "")

    # Logo — read from config registry first, then env var, then a
    # hardcoded fallback that will always work.
    # Logo — env var / settings only. The config registry deliberately
    # blocks site-identity keys, so this is env-var or settings only.
    logo_url = getattr(
        settings, "EMAIL_LOGO_URL",
        "https://res.cloudinary.com/viepz5yp/image/upload/v1791034670/NBTE_logo.png",
    )

    resolver = ticket.resolved_by or ticket.director_signed_off_by
    resolver_name = ""
    if resolver:
        resolver_name = resolver.get_full_name() or resolver.email

    return {
        "ticket": ticket,
        "subject": f"{ticket.reference} — {ticket.subject}",   # ← add this
        "raiser_name": ticket.raised_by.get_full_name() or ticket.raised_by.email,
        "resolver_name": resolver_name,
        "intro": _cfg(
            "tickets.email.resolution_intro",
            "Your ticket has been resolved. Details below.",
        ),
        "signature": _cfg(
            "tickets.email.resolution_signoff",
            f"{org_name} — {site_name}",
        ),
        "site_name": site_name,
        "org_name": org_name,
        "site_url": site_url,
        "logo_url": logo_url,
        "detail_url": (
            f"{site_url.rstrip('/')}/tickets/{ticket.pk}/"
            if site_url else f"/tickets/{ticket.pk}/"
        ),
        "now": timezone.now(),
    }


def send_ticket_resolution_email(ticket):
    """Send the resolution notification. Returns True if delivered.

    Never raises — a bad email must not break the resolution workflow.
    """
    if not _enabled():
        logger.info("Resolution emails disabled via config for %s", ticket.reference)
        return False

    recipient = getattr(ticket.raised_by, "email", "") or ""
    if not recipient:
        logger.warning("Raiser has no email for ticket %s", ticket.reference)
        return False

    prefix = _cfg("tickets.email.resolution_subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} {ticket.reference} — {ticket.subject}"

    context = _build_context(ticket)
    # The raiser may have typed the tag of an asset they are not allowed to
    # see; the email then shows the tag only, never its brand/model.
    asset_name_visible = False
    if ticket.asset_id:
        try:
            from assets.access import can_view_asset
            asset_name_visible = can_view_asset(ticket.raised_by, ticket.asset)
        except Exception:
            asset_name_visible = False
    context["asset_name_visible"] = asset_name_visible
    text_body = render_to_string("tickets/email/resolution.txt", context)
    html_body = render_to_string("tickets/email/resolution.html", context)

    reply_to = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    # Strip "Name <addr>" down to just the address for reply-to
    if reply_to and "<" in reply_to and ">" in reply_to:
        reply_to = reply_to.split("<", 1)[1].rstrip(">").strip()

    try:
        provider = send_email(
            subject=subject,
            to=recipient,
            text_body=text_body,
            html_body=html_body,
            reply_to=reply_to,
        )
    except EmailDeliveryError as exc:
        logger.error(
            "Resolution email for %s could not be delivered: %s",
            ticket.reference, exc,
        )
        return False
    except Exception:
        logger.exception("Unexpected error sending resolution email for %s",
                         ticket.reference)
        return False

    logger.info("Resolution email for %s sent via %s", ticket.reference, provider)
    return True