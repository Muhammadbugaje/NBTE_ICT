"""Task emails — assigned, returned, awaiting-review, review-rejected,
completed, cancelled, reminder.

Same pipeline as tickets/workflow: `core.emails.send()` (Brevo → Gmail).
Never raises; failures logged.
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


def _recipient_email(user):
    if not user or not user.is_active:
        return ""
    return getattr(user, "email", "") or ""


def _common_context(task):
    site_name = _cfg("site.department_name", "ICT Directorate")
    org_name = _cfg("site.organisation_name",
                    "National Board for Technical Education")
    site_url = getattr(settings, "SITE_URL", "") or ""
    logo_url = getattr(settings, "EMAIL_LOGO_URL", "")

    return {
        "task": task,
        "org_name": org_name,
        "site_name": site_name,
        "site_url": site_url,
        "logo_url": logo_url,
        "signature": _cfg("tasks.email.signoff",
                          f"{org_name} — {site_name}"),
        "detail_url": (
            f"{site_url.rstrip('/')}/tasks/{task.pk}/"
            if site_url else f"/tasks/{task.pk}/"
        ),
        "now": timezone.now(),
    }


def _send(recipient, subject, template_stem, context):
    email = _recipient_email(recipient)
    if not email:
        logger.warning("Task email skipped — no recipient")
        return False
    try:
        text_body = render_to_string(f"email/tasks/{template_stem}.txt", context)
        html_body = render_to_string(f"email/tasks/{template_stem}.html", context)
    except Exception:
        logger.exception("Task template %s render failed", template_stem)
        return False
    try:
        send_email(subject=subject, to=email,
                   text_body=text_body, html_body=html_body)
        return True
    except EmailDeliveryError as exc:
        logger.error("Task email to %s failed: %s", email, exc)
        return False
    except Exception:
        logger.exception("Unexpected task email error")
        return False


# ===========================================================================
# Public senders
# ===========================================================================
def send_assigned_email(task, assignee):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Task assigned — {task.reference}"
    ctx = _common_context(task)
    ctx.update({
        "assignee": assignee,
        "assignee_name": assignee.get_full_name() or assignee.email,
        "assigned_by_name": (
            task.created_by.get_full_name() or task.created_by.email
        ),
    })
    return _send(assignee, subject, "assigned", ctx)


def send_assigned_bulk(task):
    sent = False
    for row in task.assignee_rows.select_related("user"):
        if send_assigned_email(task, row.user):
            sent = True
    return sent


def send_returned_email(task):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Task returned — {task.reference}"
    ctx = _common_context(task)
    row = task.assignee_rows.filter(state="declined").first()
    ctx["returner_name"] = (
        (row.user.get_full_name() or row.user.email) if row else "an assignee"
    )
    ctx["reason"] = (row.note if row else "")
    return _send(task.created_by, subject, "returned", ctx)


def send_awaiting_review_email(task):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Review needed — {task.reference}"
    recipient = task.reviewer or task.created_by
    ctx = _common_context(task)
    ctx["reviewer_name"] = (
        (recipient.get_full_name() or recipient.email)
        if recipient else "Reviewer"
    )
    return _send(recipient, subject, "awaiting_review", ctx)


def send_review_rejected_email(task):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Task needs more work — {task.reference}"
    ctx = _common_context(task)
    ctx["review_notes"] = task.review_notes or ""
    ctx["reviewer_name"] = (
        (task.reviewed_by.get_full_name() or task.reviewed_by.email)
        if task.reviewed_by_id else "the reviewer"
    )
    sent = False
    for row in task.assignee_rows.select_related("user"):
        ctx["assignee_name"] = row.user.get_full_name() or row.user.email
        if _send(row.user, subject, "review_rejected", ctx):
            sent = True
    return sent


def send_completed_email(task):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Task completed — {task.reference}"
    ctx = _common_context(task)
    sent = False
    if _send(task.created_by, subject, "completed", ctx):
        sent = True
    for row in task.assignee_rows.select_related("user"):
        if _send(row.user, subject, "completed", ctx):
            sent = True
    return sent


def send_cancelled_email(task):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Task cancelled — {task.reference}"
    ctx = _common_context(task)
    recipients = {task.created_by_id}
    recipients.update(r.user_id for r in task.assignee_rows.all())
    from accounts.models import User
    sent = False
    for uid in recipients:
        u = User.objects.filter(pk=uid, is_active=True).first()
        if u and _send(u, subject, "cancelled", ctx):
            sent = True
    return sent


def send_reminder_email(task, user, reason):
    prefix = _cfg("tasks.email.subject_prefix", "[NBTE ICT]")
    subject = f"{prefix} Reminder — {task.reference}"
    ctx = _common_context(task)
    ctx["reminder_reason"] = reason
    ctx["assignee_name"] = user.get_full_name() or user.email
    return _send(user, subject, "reminder", ctx)