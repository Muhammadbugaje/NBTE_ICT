"""Cache invalidation signals.

One receiver per model, dispatching to `core.cache.invalidate(namespace)`.
Namespace names are the contract — anything reading a cached value
must use the same namespace when it builds its key.

To add a new cacheable module: pick a namespace string, call
`invalidate("that-namespace")` on the model's signals, and use
`cache.get_or_set_context("that-namespace", ...)` in the reader.
"""
from __future__ import annotations

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.apps import apps


# ---------------------------------------------------------------------------
# Helper — resolve a model class lazily so this file never crashes on apps
# that aren't installed yet (Phase 1 doesn't have `tickets` etc.).
# ---------------------------------------------------------------------------
def _model(label: str):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def _bind(model_label: str, namespace: str):
    """Wire post_save + post_delete on `model_label` to `invalidate(namespace)`.
    No-ops safely if the model doesn't exist in this deployment."""
    Model = _model(model_label)
    if Model is None:
        return

    from .cache import invalidate

    @receiver([post_save, post_delete], sender=Model, weak=False,
              dispatch_uid=f"cache_inv_{model_label}_{namespace}")
    def _receiver(sender, instance, **kwargs):
        invalidate(namespace)


# ---------------------------------------------------------------------------
# Notifications — sidebar unread counts + dashboards
# ---------------------------------------------------------------------------
_bind("notifications.Notification", "notifications")
_bind("notifications.Notification", "sidebar")


# ---------------------------------------------------------------------------
# Workflow — request status changes ripple into every dashboard + home
# ---------------------------------------------------------------------------
_bind("workflow.Request", "workflow")
_bind("workflow.Request", "dashboards")
_bind("workflow.Request", "sidebar")
_bind("workflow.RequestApproval", "workflow")
_bind("workflow.RequestApproval", "dashboards")
_bind("workflow.RequestActivity", "workflow")
_bind("workflow.ApprovalStep", "workflow")
_bind("workflow.ApprovalChain", "workflow")
_bind("workflow.RequestType", "workflow")
_bind("workflow.RequestCategory", "workflow")


# ---------------------------------------------------------------------------
# Attendance — every record write ripples into dashboards + home
# ---------------------------------------------------------------------------
_bind("attendance.AttendanceRecord", "attendance")
_bind("attendance.AttendanceRecord", "dashboards")
_bind("attendance.AttendanceRecord", "sidebar")
_bind("attendance.AttendanceLocation", "attendance")


# ---------------------------------------------------------------------------
# Staff — directory counts on home + dashboards
# ---------------------------------------------------------------------------
_bind("staff.Staff", "staff")
_bind("staff.Staff", "dashboards")
_bind("staff.Staff", "sidebar")
_bind("staff.Department", "staff")
_bind("staff.ExecutivePosition", "public_leadership")
_bind("staff.ExecutiveTenure", "public_leadership")


# ---------------------------------------------------------------------------
# Portal — announcements/events/documents/home cards
# ---------------------------------------------------------------------------
for _label in (
    "portal.Announcement",
    "portal.Event",
    "portal.EventAttendee",
    "portal.Document",
    "portal.DocumentVersion",
    "portal.Award",
    "portal.Holiday",
    "portal.GalleryAlbum",
    "portal.GalleryItem",
    "portal.Reminder",
):
    _bind(_label, "portal")
    _bind(_label, "sidebar")


# ---------------------------------------------------------------------------
# Messaging — unread threads on home
# ---------------------------------------------------------------------------
_bind("messaging.Thread", "messaging")
_bind("messaging.Thread", "sidebar")
_bind("messaging.Message", "messaging")
_bind("messaging.Message", "sidebar")


# ---------------------------------------------------------------------------
# Tickets — home + dashboards + sidebar
# ---------------------------------------------------------------------------
_bind("tickets.Ticket", "tickets")
_bind("tickets.Ticket", "dashboards")
_bind("tickets.Ticket", "sidebar")


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
_bind("tasks.Task", "tasks")
_bind("tasks.Task", "dashboards")
_bind("tasks.Task", "sidebar")


# ---------------------------------------------------------------------------
# Config registry — already handled by config/signals.py, but also
# invalidate the public cache namespace because public content reads
# from config registry.
# ---------------------------------------------------------------------------
_bind("config.ConfigEntry", "public")