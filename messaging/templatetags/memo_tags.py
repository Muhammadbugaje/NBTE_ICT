"""Template tags for the memo system."""
from django import template

from messaging import memos as memo_service

register = template.Library()


@register.inclusion_tag("messaging/_dashboard_memos.html", takes_context=True)
def memo_dashboard_widget(context, limit=3):
    """Render the memos widget on the self-service dashboard.

    Shows memos awaiting the user's approval plus recent delivered memos.
    """
    request = context.get("request")
    if request is None or not request.user.is_authenticated:
        return {
            "awaiting": [],
            "memos": [],
            "unread_count": 0,
            "pending_ack": 0,
            "request": request,
        }

    user = request.user
    awaiting = list(memo_service.awaiting_memos_for(user)[:5])
    awaiting_ids = {m.pk for m in awaiting}
    delivered_qs = memo_service.inbox_memos_for(user).exclude(pk__in=awaiting_ids)
    memos = list(delivered_qs[:limit])

    return {
        "awaiting": awaiting,
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


@register.simple_tag
def awaiting_memo_count(user):
    try:
        return len(memo_service.awaiting_memos_for(user))
    except Exception:
        return 0


@register.simple_tag
def memo_badge_count(user):
    """Combined unread + awaiting count for the sidebar Memos badge.

    Distinct memos — a memo that is both unread and awaiting you still
    counts once.
    """
    if not user or not user.is_authenticated:
        return 0
    # v2.3 Phase 1: this tag is in the sidebar of every page, and working out
    # "awaiting you" resolves the approver of up to 200 memos one by one
    # (dozens of queries per page for the ES). Cache it per user; any memo,
    # route or seat change bumps the namespace (core.fastcache.MEMO_BADGE),
    # and the 60 s TTL is only a safety net.
    from core import fastcache

    return fastcache.cached(fastcache.MEMO_BADGE, "count", lambda: _memo_badge_count(user), ttl=60, user=user)


def _memo_badge_count(user):
    try:
        from messaging.models import MemoRecipient

        unread_ids = set(
            MemoRecipient.objects
            .filter(
                user=user,
                read_at__isnull=True,
                is_archived=False,
                memo__status="published",
            )
            .values_list("memo_id", flat=True)
        )
        awaiting_ids = {
            m.pk for m in memo_service.awaiting_memos_for(user)
        }
        return len(unread_ids | awaiting_ids)
    except Exception:
        return 0