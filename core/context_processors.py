"""Template context processors.

Provides three things to every template:

- `site` — the SITE_NAME setting under the key `SITE_NAME`.
- `site_info` — public content read from the config registry.
- `sidebar_counts` — badge counts for the sidebar (cached).
"""
import config

from core.cache import get_or_set_context, TTL_SHORT


# ---------------------------------------------------------------------------
# Site name
# ---------------------------------------------------------------------------
def site(request):
    """SITE_NAME: Settings → Site → site.name when set, else the SITE_NAME setting."""
    from django.conf import settings
    try:
        name = str(config.get("site.name", "") or "").strip()
    except Exception:  # registry not ready (fresh database)
        name = ""
    return {"SITE_NAME": name or settings.SITE_NAME}


# ---------------------------------------------------------------------------
# Public site info
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "department_name": "ICT Directorate",
    "organisation_name": "National Board for Technical Education",
    "tagline": "",
    "email": "",
    "phone": "",
    "address": "",
    "footer_note": "Authorized access only. All actions logged and audited.",
}


def site_info(request):
    info = {key: config.get(f"site.{key}", default) for key, default in _DEFAULTS.items()}
    return {"site_info": info}


# ---------------------------------------------------------------------------
# Sidebar badge counts (cached, 30s)
# ---------------------------------------------------------------------------
def sidebar_counts(request):
    """Badge counts for the sidebar. Only runs for authenticated users.

    Cached per user under the `sidebar` namespace with a 30-second TTL.
    Writes to any tracked model bump the namespace via signals — so the
    next request after a write gets fresh numbers immediately, and even
    a missed signal heals in ≤30 seconds.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"sidebar_counts": {}}

    def _build():
        counts = {
            "notifications": 0,
            "assigned": 0,
            "incoming": 0,
            "awaiting": 0,
            "my_requests": 0,
            "my_active": 0,
            "office_requests": 0,
            "all_requests": 0,
            "can_incoming": False,
            "can_office_requests": False,
            "can_all_requests": False,
        }

        try:
            from workflow.views import can_see_incoming
            counts["can_incoming"] = can_see_incoming(user)
        except Exception:
            pass

        try:
            from notifications.models import Notification
            counts["notifications"] = Notification.objects.filter(
                recipient=user, is_read=False,
            ).count()
        except Exception:
            pass

        # Phase 9: announcements — unread badge (one query).
        try:
            from announcements.models import Announcement
            counts["announcements_unread"] = Announcement.objects.unread_for(user).count()
        except Exception:
            counts["announcements_unread"] = 0

        try:
            from workflow.models import Request
            mine = Request.objects.filter(requester=user)
            counts["my_requests"] = mine.count()
            counts["my_active"] = mine.exclude(
                status__in=Request.CLOSED_STATUSES,
            ).count()
        except Exception:
            pass

        try:
            from workflow.models import Request
            counts["assigned"] = Request.objects.filter(
                assigned_to=user,
            ).exclude(status__in=Request.CLOSED_STATUSES).count()
        except Exception:
            pass

        try:
            from workflow.models import Request
            from workflow.access import is_department_request_viewer
            if is_department_request_viewer(user):
                counts["incoming"] = Request.objects.filter(
                    receiving_department=user.department,
                ).exclude(status__in=Request.CLOSED_STATUSES).count()
        except Exception:
            pass

        try:
            from django.db.models import Q
            from workflow.models import Request
            from workflow.access import is_department_request_viewer
            if is_department_request_viewer(user):
                counts["can_office_requests"] = True
                counts["office_requests"] = (
                    Request.objects
                    .filter(
                        Q(requester__department=user.department)
                        | Q(receiving_department=user.department),
                    )
                    .distinct()
                    .count()
                )
        except Exception:
            pass

        try:
            from workflow.models import Request
            from workflow.access import can_view_all_requests
            if can_view_all_requests(user):
                counts["can_all_requests"] = True
                counts["all_requests"] = Request.objects.count()
        except Exception:
            pass

        try:
            from workflow import services
            from workflow.models import Request

            if not user.is_superuser:
                # Review M17: one query narrows the open requests to those
                # this user could act on (was: the 200 newest, resolved one
                # by one, which also miscounted beyond 200).
                candidates = (
                    services.candidate_requests_for(user)
                    .select_related("requester", "requester__department",
                                    "receiving_department", "request_type")
                    .order_by("-created_at")
                )
                for req in candidates:
                    step = services._resolve_current_step(req)
                    if step is None:
                        continue
                    actor = services.resolve_approver(step, req)
                    if actor and actor.pk == user.pk:
                        counts["awaiting"] += 1
        except Exception:
            pass

        return counts

    value = get_or_set_context(
        "sidebar", ["counts"], _build, ttl=TTL_SHORT, user=user,
    )
    return {"sidebar_counts": value}