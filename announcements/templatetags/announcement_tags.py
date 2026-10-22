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
