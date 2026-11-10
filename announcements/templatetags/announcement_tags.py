"""Phase 9: announcements template tags (home-page banner)."""
from django import template

register = template.Library()


@register.inclusion_tag("announcements/_home_banner.html", takes_context=True)
def announcement_banner(context):
    """The newest action-needed / urgent announcement the viewer has not
    acknowledged yet (one query; nothing for non-staff)."""
    request = context.get("request")
    user = getattr(request, "user", None)
    item = None
    try:
        from announcements.models import Announcement

        item = (
            Announcement.objects.needing_ack_from(user)
            .filter(kind__in=[Announcement.ACTION_NEEDED, Announcement.URGENT])
            .order_by("-published_at")
            .only("pk", "title", "kind", "published_at")
            .first()
        )
    except Exception:
        item = None
    return {"item": item}


def panel_items(user, limit=3):
    """v2.1 Phase E (L10): the newest announcements for ``user``, pinned first,
    with read / acknowledged state; plus the unread count."""
    from announcements.models import Announcement

    qs = Announcement.objects.visible_to(user)
    items = list(qs.with_user_state(user).order_by("-pinned", "-published_at")[:limit])
    unread = qs.unread_for(user).count() if items else 0
    return items, unread


@register.inclusion_tag("announcements/_home_panel.html", takes_context=True)
def announcements_panel(context, limit=3):
    """Announcements on every staff dashboard (v2.1 L10, T-CONS-3): the latest
    three, pinned first, unread dots and an Acknowledge button. Nothing for
    non-staff."""
    request = context.get("request")
    user = getattr(request, "user", None)
    items, unread = [], 0
    try:
        if getattr(user, "user_type", None) == "staff":
            items, unread = panel_items(user, limit)
    except Exception:
        items, unread = [], 0
    return {"items": items, "unread": unread, "request": request,
            "is_staff_user": getattr(user, "user_type", None) == "staff"}
