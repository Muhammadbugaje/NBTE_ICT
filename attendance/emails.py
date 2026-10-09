"""Attendance emails — late notice, weekly summary.

Uses `core.emails.send()` (Brevo → Gmail). Never raises.
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


def _common_context():
    org_name = _cfg("site.organisation_name",
                    "National Board for Technical Education")
    site_name = _cfg("site.department_name", "ICT Directorate")
    site_url = getattr(settings, "SITE_URL", "") or ""
    logo_url = getattr(settings, "EMAIL_LOGO_URL", "")
    return {
        "org_name": org_name,
        "site_name": site_name,
        "site_url": site_url,
        "logo_url": logo_url,
        "signature": _cfg("attendance.email.signoff",
                          f"{org_name} — {site_name}"),
        "now": timezone.now(),
    }


def _send(to_user, subject, template_stem, context):
    if not to_user or not getattr(to_user, "email", ""):
        return False
    try:
        text = render_to_string(f"email/attendance/{template_stem}.txt", context)
        html = render_to_string(f"email/attendance/{template_stem}.html", context)
    except Exception:
        logger.exception("Template %s failed to render", template_stem)
        return False
    try:
        send_email(subject=subject, to=to_user.email,
                   text_body=text, html_body=html)
        return True
    except EmailDeliveryError as exc:
        logger.error("Attendance email to %s failed: %s", to_user.email, exc)
        return False
    except Exception:
        logger.exception("Unexpected attendance email error")
        return False


def send_late_notice(record):
    """Sent to a user the first time they are recorded LATE on a day."""
    ctx = _common_context()
    ctx.update({
        "record": record,
        "user_name": record.user.get_full_name() or record.user.email,
        "location_name": record.location.name if record.location_id else "",
        "check_in_time": (
            timezone.localtime(record.check_in_at).strftime("%H:%M")
            if record.check_in_at else ""
        ),
    })
    return _send(
        record.user,
        f"Late arrival recorded — {record.date:%b %d, %Y}",
        "late_notice",
        ctx,
    )


def send_weekly_summary(user, summary):
    """Monday-morning summary for a department head."""
    ctx = _common_context()
    ctx.update({
        "user": user,
        "user_name": user.get_full_name() or user.email,
        "summary": summary,
        "department": summary["department"],
        "start": summary["start"],
        "end": summary["end"],
        "working_days": summary["working_days"],
        "users": summary["users"],
    })
    return _send(
        user,
        f"Weekly attendance — {summary['department'].name}",
        "weekly_summary",
        ctx,
    )