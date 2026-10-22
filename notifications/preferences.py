"""Notification preferences and quiet hours (Phase 9).

ONE central decision helper:

    should_deliver(user, kind, urgent=False, channel="push", now=None) -> bool

* channel "inapp": always True - bell records are always stored.
* channel "push":  the kind's switch is on AND (outside quiet hours, or the
  notification is urgent and urgent ones may break through).
* channel "email": the kind's switch is on AND `email_digest` is on.
  (Quiet hours do not hold e-mail back - it waits in the inbox anyway.)

`kind` may be one of KINDS or an existing Notification.category; categories
are mapped through CATEGORY_TO_KIND. Other helpers:

    get_preferences(user) -> NotificationPreference (unsaved defaults if none)
    save_preferences(user, data) -> NotificationPreference
    in_quiet_hours(pref, now=None) -> bool
    kind_for(category) -> str
    is_urgent(notification) -> bool
    push_allowed(pref, notification, kind, now=None) -> bool   (push rule, row already loaded)
"""
from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from .models import Notification, NotificationPreference

CHANNELS = ("push", "email", "inapp")

# (field, label, help, doors) - doors: which front door shows the switch.
KINDS = (
    ("chats", "Chats and messages", "New chat messages and replies.", ("internal", "portal")),
    ("tasks_tickets", "Tasks and tickets", "Tasks assigned to you, ticket updates and reminders.", ("internal",)),
    ("approvals_memos", "Approvals and memos", "Requests waiting for your approval and memos sent to you.",
     ("internal",)),
    ("payments", "Payments", "Invoices, receipts and payment confirmations.", ("internal", "portal")),
    ("qa_cases", "Quality assurance", "Applications, QA requests, queries, inspections and decisions.",
     ("internal", "portal")),
    ("announcements", "Announcements", "NBTE-wide announcements and circulars.", ("internal", "portal")),
    ("general", "General", "Account, system and everything else.", ("internal", "portal")),
)
KIND_FIELDS = tuple(k[0] for k in KINDS)

# Existing Notification.category values -> preference kind.
CATEGORY_TO_KIND = {
    Notification.MESSAGE: "chats",
    Notification.TASK: "tasks_tickets",
    Notification.TICKET: "tasks_tickets",
    Notification.REQUEST: "approvals_memos",
    Notification.QA: "qa_cases",
    Notification.SYSTEM: "general",
    Notification.STAFF: "general",
    Notification.ASSET: "general",
    Notification.IMPORT: "general",
    # Categories other apps may add later.
    "memo": "approvals_memos",
    "approval": "approvals_memos",
    "payment": "payments",
    "announcement": "announcements",
    "chat": "chats",
}

TIMEZONE_CHOICES = (
    "Africa/Lagos", "Africa/Accra", "Africa/Cairo", "Africa/Johannesburg",
    "Africa/Nairobi", "Europe/London", "Europe/Paris", "Asia/Dubai", "Asia/Riyadh", "America/New_York", "UTC",
)


def kind_for(kind_or_category):
    value = (kind_or_category or "").strip()
    if value in KIND_FIELDS:
        return value
    return CATEGORY_TO_KIND.get(value, "general")


def get_preferences(user):
    """The user's row, or an unsaved instance with the defaults (no write on read)."""
    if user is None or not getattr(user, "pk", None):
        return NotificationPreference()
    pref = NotificationPreference.objects.filter(user=user).first()
    return pref if pref is not None else NotificationPreference(user=user)


def save_preferences(user, data):
    """Update from a dict of field -> value (unknown keys ignored)."""
    pref, _ = NotificationPreference.objects.get_or_create(user=user)
    allowed = {f.name for f in NotificationPreference._meta.concrete_fields} - {"id", "user", "updated_at"}
    for key, value in (data or {}).items():
        if key in allowed:
            setattr(pref, key, value)
    pref.full_clean(exclude=["user"])
    pref.save()
    return pref


def _zone(pref):
    try:
        return ZoneInfo(pref.timezone or "Africa/Lagos")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Africa/Lagos")


def in_quiet_hours(pref, now=None):
    if not pref.quiet_hours_enabled:
        return False
    now = now or timezone.now()
    local = now.astimezone(_zone(pref)).time().replace(tzinfo=None)
    start, end = pref.quiet_from, pref.quiet_to
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end  # crosses midnight (22:00 -> 06:30)


def should_deliver(user, kind, urgent=False, channel="push", now=None):
    if channel not in CHANNELS:
        raise ValueError(f"Unknown channel {channel!r}")
    if channel == "inapp":
        return True
    pref = get_preferences(user)
    if not getattr(pref, kind_for(kind), True):
        return False
    if channel == "email":
        return bool(pref.email_digest)
    if in_quiet_hours(pref, now=now):
        return bool(urgent and pref.allow_urgent_during_quiet)
    return True


def is_urgent(notification):
    """A notification that may break through quiet hours (when the user
    allows it): level "error", or ``metadata.urgent`` / ``metadata.priority
    == "urgent"`` set by the sender."""
    meta = notification.metadata if isinstance(getattr(notification, "metadata", None), dict) else {}
    return bool(getattr(notification, "level", "") == Notification.ERROR or meta.get("urgent")
                or meta.get("priority") == "urgent")


def push_allowed(pref, notification, kind, now=None):
    """should_deliver(channel="push") for an already-loaded preference row,
    so callers that check many notifications read the row once."""
    if not getattr(pref, kind_for(kind), True):
        return False
    if in_quiet_hours(pref, now=now):
        return bool(is_urgent(notification) and pref.allow_urgent_during_quiet)
    return True


def fields_for_door(door):
    """Kind switches shown on a door ("internal" or "portal")."""
    return [k for k in KINDS if door in k[3]]
