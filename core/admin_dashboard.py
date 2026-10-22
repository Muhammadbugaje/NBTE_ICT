"""Callbacks for the Django admin theme (django-unfold, Phase 8).

* ``dashboard_callback`` — the numbers and shortcuts on /admin/.
* ``*_badge`` — small counters shown next to sidebar links.
* ``environment_callback`` — the "Development" / "Production" pill.
* ``perm(...)`` — sidebar permission checks.

Everything is defensive: a missing app or table shows a dash, never a 500.
Counts read the explicit ``unscoped`` managers for QA models (admin is system
code, Master Plan §4).
"""
# scoped-by-caller
from __future__ import annotations

import logging

from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _url(name, *args, query=""):
    try:
        return reverse(name, args=args) + (f"?{query}" if query else "")
    except NoReverseMatch:
        return ""


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # never break the admin index over one tile
        logger.debug("admin dashboard: tile failed", exc_info=True)
        return default


# ---------------------------------------------------------------------------
# Counters (shared by the dashboard tiles and the sidebar badges)
# ---------------------------------------------------------------------------
def count_users():
    from dashboards import counts
    return counts.active_users().count()


def count_staff():
    from dashboards import counts
    return counts.active_staff().count()


def count_open_tasks():
    from dashboards import counts
    return counts.open_tasks().count()


def count_open_tickets():
    from dashboards import counts
    return counts.open_tickets().count()


def count_institutions():
    from institutions.models import Institution
    return Institution.unscoped.count()


def count_open_cases():
    from dashboards import qa_metrics
    return qa_metrics.cases()["open"]


def count_payments_awaiting():
    from payments.models import PaymentVerification
    return PaymentVerification.unscoped.filter(is_open=True).count()


def count_open_complaints():
    from dashboards import qa_metrics
    return qa_metrics.complaints()["open"]


def count_unread_contact():
    from core.models import ContactMessage
    return ContactMessage.objects.filter(is_read=False).count()


def count_failed_emails():
    from notifications.models import EmailLog
    return EmailLog.objects.filter(status="failed").count()


def _badge(fn):
    def _cb(request):
        value = _safe(fn, 0) or 0
        return value if value else None
    return _cb


users_badge = _badge(count_users)
open_tasks_badge = _badge(count_open_tasks)
open_tickets_badge = _badge(count_open_tickets)
open_cases_badge = _badge(count_open_cases)
payments_badge = _badge(count_payments_awaiting)
complaints_badge = _badge(count_open_complaints)
contact_badge = _badge(count_unread_contact)


# ---------------------------------------------------------------------------
# Environment pill + permission helper
# ---------------------------------------------------------------------------
def environment_callback(request):
    if settings.DEBUG:
        return ["Development", "warning"]
    return ["Production", "success"]


def perm(*codenames):
    """Sidebar permission check: superuser, or any of the given perms."""
    def _check(request):
        user = request.user
        return user.is_superuser or any(user.has_perm(c) for c in codenames)
    return _check


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
TILES = [
    # key, label, counter, admin changelist, icon, tone, hint
    ("users", "Active users", count_users, "admin:accounts_user_changelist", "group", "primary",
     "Sign-in accounts that are switched on"),
    ("staff", "Staff records", count_staff, "admin:staff_staff_changelist", "badge", "primary",
     "Nominal roll entries (not deleted)"),
    ("tasks", "Open tasks", count_open_tasks, "admin:tasks_task_changelist", "task_alt", "info",
     "Not completed, cancelled or draft"),
    ("tickets", "Open tickets", count_open_tickets, "admin:tickets_ticket_changelist", "support_agent", "info",
     "New, assigned, in progress or escalated"),
    ("institutions", "Institutions", count_institutions, "admin:institutions_institution_changelist",
     "account_balance", "primary", "Every institution on record"),
    ("cases", "Open QA cases", count_open_cases, "admin:accreditation_qacase_changelist", "folder_open",
     "warning", "SOP-03 cases not yet closed"),
    ("payments", "Payments to confirm", count_payments_awaiting,
     "admin:payments_paymentverification_changelist", "payments", "warning",
     "Verifications still open in the Finance queue"),
    ("complaints", "Open complaints", count_open_complaints, "admin:complaints_complaint_changelist",
     "report", "danger", "Not resolved or closed"),
]

SHORTCUTS = [
    ("Add a user", "admin:accounts_user_add", "person_add"),
    ("Roles", "admin:accounts_role_changelist", "admin_panel_settings"),
    ("Settings", "admin:config_configentry_changelist", "tune"),
    ("Audit log", "admin:core_auditlogentry_changelist", "history"),
    ("Email log", "admin:notifications_emaillog_changelist", "mail"),
    ("Contact messages", "admin:core_contactmessage_changelist", "inbox"),
]


def _filter_query(key):
    return {
        "users": "is_active__exact=1",
        "tickets": "",
        "payments": "is_open__exact=1",
    }.get(key, "")


def dashboard_callback(request, context):
    user = request.user
    tiles = []
    for key, label, fn, url_name, icon, tone, hint in TILES:
        value = _safe(fn)
        url = _url(url_name, query=_filter_query(key))
        tiles.append({"key": key, "label": label, "value": value, "url": url, "icon": icon,
                      "tone": tone, "hint": hint})

    shortcuts = [{"label": label, "url": _url(name), "icon": icon}
                 for label, name, icon in SHORTCUTS if _url(name)]

    recent = []
    try:
        from django.contrib.admin.models import LogEntry

        for entry in (LogEntry.objects.select_related("user", "content_type")
                      .order_by("-action_time")[:10]):
            recent.append({
                "who": entry.user.get_full_name() or entry.user.email if entry.user_id else "System",
                "what": entry.object_repr,
                "kind": {1: "added", 2: "changed", 3: "deleted"}.get(entry.action_flag, "updated"),
                "model": entry.content_type.name if entry.content_type_id else "",
                "when": entry.action_time,
                "url": entry.get_admin_url() if not entry.is_deletion() else "",
            })
    except Exception:
        logger.debug("admin dashboard: recent actions unavailable", exc_info=True)

    hour = timezone.localtime().hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")
    context.update({
        "nbte_tiles": tiles,
        "nbte_shortcuts": shortcuts,
        "nbte_recent": recent,
        "nbte_greeting": f"{greeting}, {user.first_name or user.get_username()}",
        "nbte_site_links": [
            {"label": "Open the staff system", "url": "/home/", "icon": "home"},
            {"label": "Super Admin dashboard", "url": _url("dashboards:superadmin"), "icon": "monitoring"},
        ],
    })
    return context
