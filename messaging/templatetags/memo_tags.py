"""Template tags for the memo system."""
from django import template

from messaging import memos as memo_service

register = template.Library()


@register.inclusion_tag("messaging/_dashboard_memos.html", takes_context=True)
def memo_dashboard_widget(context, limit=3):
    """Render the memos widget on the self-service dashboard."""
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return {
            "memos": [],
            "unread_count": 0,
            "pending_ack": 0,
            "request": request,
        }

    user = request.user
    qs = memo_service.inbox_memos_for(user)
    memos = list(qs[:limit])

    return {
        "memos": memos,
        "unread_count": memo_service.unread_memo_count_for(user),
        "pending_ack": memo_service.pending_ack_count_for(user),
        "request": request,
    }


@register.simple_tag
def unread_memo_count(user):
    return memo_service.unread_memo_count_for(user)


@register.simple_tag
def pending_memo_ack_count(user):
    return memo_service.pending_ack_count_for(user)