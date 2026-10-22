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


# Review M17: the sidebar badge counts only read Notification and Request.
# A notification changes ONE user's counts, so it drops only that user's
# cached counts instead of bumping the "sidebar" epoch for everybody (which
# happened on every notification, message, attendance mark, portal edit and
# ticket/task save). Request changes can affect several users' approval
# queues, so they still invalidate the whole namespace.
def _bind_sidebar_for_recipient(model_label):
    Model = _model(model_label)
    if Model is None:
        return

    @receiver([post_save, post_delete], sender=Model, weak=False,
              dispatch_uid=f"cache_inv_{model_label}_sidebar_user")
    def _receiver(sender, instance, **kwargs):
        from django.core.cache import cache
        from .cache import make_key
        uid = getattr(instance, "recipient_id", None)
        if uid:
            cache.delete(make_key("sidebar", "counts", user=uid))


_bind_sidebar_for_recipient("notifications.Notification")


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
_bind("attendance.AttendanceLocation", "attendance")


# ---------------------------------------------------------------------------
# Staff — directory counts on home + dashboards
# ---------------------------------------------------------------------------
_bind("staff.Staff", "staff")
_bind("staff.Staff", "dashboards")
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


# ---------------------------------------------------------------------------
# Messaging — unread threads on home (not in the sidebar counts; review M17)
# ---------------------------------------------------------------------------
_bind("messaging.Thread", "messaging")
_bind("messaging.Message", "messaging")


# ---------------------------------------------------------------------------
# Tickets — home + dashboards + sidebar
# ---------------------------------------------------------------------------
_bind("tickets.Ticket", "tickets")
_bind("tickets.Ticket", "dashboards")


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
_bind("tasks.Task", "tasks")
_bind("tasks.Task", "dashboards")


# ---------------------------------------------------------------------------
# Config registry — already handled by config/signals.py, but also
# invalidate the public cache namespace because public content reads
# from config registry.
# ---------------------------------------------------------------------------
_bind("config.ConfigEntry", "public")

# ---------------------------------------------------------------------------
# Phase 8 card audit: the signed-in home page caches its whole context (KPI
# tiles, module cards, work board) for TTL_MEDIUM under the "home" namespace,
# but nothing ever invalidated that namespace, so a tile could lag the data
# it counts. Every model a home number is built from now drops it.
# ---------------------------------------------------------------------------
for _label in (
    "notifications.Notification", "messaging.Message", "messaging.ThreadParticipant",
    "tasks.Task", "tasks.TaskAssignee", "tickets.Ticket", "staff.Staff",
    "workflow.Request", "assets.AssetAssignment", "attendance.AttendanceRecord",
    "qa_core.PendingAction", "qa_core.TimelineEvent", "complaints.ComplaintStage",
    "payments.PaymentVerification",
):
    _bind(_label, "home")
