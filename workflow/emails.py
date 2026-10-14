"""Workflow emails — fired from the service layer alongside in-app notifications.

Uses `core.emails.send()` — same Brevo → Gmail pipeline as ticket emails.
Every function swallows errors and logs them; a failed email never blocks
a request from advancing.
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


def _user_email(user):
    if not user or not user.is_active:
        return ""
    return getattr(user, "email", "") or ""


def _display_name(user):
    if not user:
        return ""
    return user.get_full_name() or user.email


def _common_context(request):
    """Shared context for every workflow email template."""
    site_name = _cfg("site.department_name", "ICT Directorate")
    org_name = _cfg("site.organisation_name", "National Board for Technical Education")
    site_url = getattr(settings, "SITE_URL", "") or ""
    logo_url = getattr(settings, "EMAIL_LOGO_URL", "")

    return {
        "request": request,
        "org_name": org_name,
        "site_name": site_name,
        "site_url": site_url,
        "logo_url": logo_url,
        "signature": _cfg(
            "workflow.email.signoff",
            f"{org_name} — {site_name}",
        ),
        "detail_url": (
            f"{site_url.rstrip('/')}/workflow/{request.pk}/"
            if site_url else f"/workflow/{request.pk}/"
        ),
        "now": timezone.now(),
    }


def _send(recipient, subject, template_stem, context):
    """Render both text and html and send. Never raises."""
    email = _user_email(recipient)
    if not email:
        logger.warning("Workflow email skipped — no recipient")
        return False

    try:
        text_body = render_to_string(f"email/workflow/{template_stem}.txt", context)
        html_body = render_to_string(f"email/workflow/{template_stem}.html", context)
    except Exception:
        logger.exception("Could not render workflow email template %s", template_stem)
        return False

    try:
        provider = send_email(
            subject=subject,
            to=email,
            text_body=text_body,
            html_body=html_body,
        )
    except EmailDeliveryError as exc:
        logger.error("Workflow email to %s failed: %s", email, exc)
        return False
    except Exception:
        logger.exception("Unexpected workflow email error")
        return False

    logger.info("Workflow email delivered via %s to %s", provider, email)
    return True


# ===========================================================================
# Individual events
# ===========================================================================
def send_request_awaiting_approval_email(request, approver, step):
    """Fires when the request lands on a new approver's desk."""
    if not approver or not _user_email(approver):
        return False

    prefix = _cfg("workflow.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Approval needed — {request.reference}"

    context = _common_context(request)
    context.update({
        "approver": approver,
        "approver_name": _display_name(approver),
        "step": step,
        "step_name": step.name if step is not None else "(step)",
    })
    return _send(approver, subject, "awaiting_approval", context)


def send_request_completed_email(request):
    """Fires once the chain reaches COMPLETED."""
    if not request.requester_id:
        return False

    prefix = _cfg("workflow.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Request completed — {request.reference}"

    context = _common_context(request)
    context.update({
        "requester_name": _display_name(request.requester),
    })
    return _send(request.requester, subject, "completed", context)


def send_request_rejected_email(request, rejector, comment=""):
    """Fires on REJECTED."""
    if not request.requester_id:
        return False

    prefix = _cfg("workflow.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Request rejected — {request.reference}"

    context = _common_context(request)
    context.update({
        "requester_name": _display_name(request.requester),
        "rejector_name": _display_name(rejector),
        "rejection_comment": (comment or "").strip(),
    })
    return _send(request.requester, subject, "rejected", context)


def send_request_returned_email(request, returner, comment=""):
    """Fires on RETURNED (needs revision)."""
    if not request.requester_id:
        return False

    prefix = _cfg("workflow.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Revision needed — {request.reference}"

    context = _common_context(request)
    context.update({
        "requester_name": _display_name(request.requester),
        "returner_name": _display_name(returner),
        "return_comment": (comment or "").strip(),
    })
    return _send(request.requester, subject, "returned", context)