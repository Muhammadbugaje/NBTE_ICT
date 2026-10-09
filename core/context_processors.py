"""Template context processors.

Provides three things to every template:

- `site` — the SITE_NAME setting under the key `SITE_NAME`.
  Historical; kept because several templates and settings reference it.
- `site_info` — public content read from the config registry
  (department name, organisation, contact email/phone/address).
- `sidebar_counts` — badge counts for the sidebar (unread notifications,
  requests awaiting my action, incoming requests, assigned to me).

The `site_info` keys are also exposed at the top level as `site_info.<key>`
so templates can use either `{{ site_info.email }}` or any future accessor.
"""
import config


# ---------------------------------------------------------------------------
# Site name — preserved for backward compatibility
# ---------------------------------------------------------------------------
def site(request):
    """Exposes `SITE_NAME` to templates. Historically registered as
    `core.context_processors.site`."""
    from django.conf import settings

    return {"SITE_NAME": settings.SITE_NAME}


# ---------------------------------------------------------------------------
# Public site info (contact details, names)
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
    """Public content read from the config registry. Falls back to defaults
    when a key hasn't been seeded yet, so templates never crash."""
    info = {key: config.get(f"site.{key}", default) for key, default in _DEFAULTS.items()}
    return {"site_info": info}


# ---------------------------------------------------------------------------
# Sidebar badge counts
# ---------------------------------------------------------------------------

def sidebar_counts(request):
    """Badge counts for the sidebar. Only runs for authenticated users."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"sidebar_counts": {}}

    counts = {
        "notifications": 0,
        "assigned": 0,
        "incoming": 0,
        "awaiting": 0,
        "my_requests": 0,
        "my_active": 0,
        "office_requests": 0,
        "all_requests": 0,
    }

    # --- Notifications ---
    try:
        from notifications.models import Notification
        counts["notifications"] = Notification.objects.filter(
            recipient=user, is_read=False
        ).count()
    except Exception:
        pass

    # --- My requests ---
    try:
        from workflow.models import Request
        mine = Request.objects.filter(requester=user)
        counts["my_requests"] = mine.count()
        counts["my_active"] = mine.exclude(status__in=Request.CLOSED_STATUSES).count()
    except Exception:
        pass

    # --- Assigned requests ---
    try:
        from workflow.models import Request
        counts["assigned"] = Request.objects.filter(
            assigned_to=user
        ).exclude(status__in=Request.CLOSED_STATUSES).count()
    except Exception:
        pass

    # --- Incoming (for directors of a receiving department) ---
    try:
        from workflow.models import Request
        if user.department_id and user.role_id and user.role.code == "director":
            counts["incoming"] = Request.objects.filter(
                receiving_department=user.department
            ).exclude(status__in=Request.CLOSED_STATUSES).count()
    except Exception:
        pass

    # --- Office requests (director's own department, any side) ---
    try:
        from django.db.models import Q
        from workflow.models import Request
        if user.department_id and user.role_id and user.role.code == "director":
            counts["office_requests"] = (
                Request.objects
                .filter(Q(requester__department=user.department)
                        | Q(receiving_department=user.department))
                .distinct()
                .count()
            )
    except Exception:
        pass

    # --- All requests (ES / superuser) ---
    try:
        from workflow.models import Request
        if user.is_superuser or user.has_perm("accounts.view_all_requests"):
            counts["all_requests"] = Request.objects.count()
    except Exception:
        pass

    # --- Awaiting my action ---
    try:
        from workflow import services
        from workflow.models import Request

        if not user.is_superuser:
            candidates = (
                Request.objects
                .exclude(status__in=Request.CLOSED_STATUSES)
                .select_related("requester", "receiving_department")
                .order_by("-created_at")[:200]
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

    return {"sidebar_counts": counts}