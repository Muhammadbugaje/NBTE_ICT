"""v2.1 Phase E (M4): the portal dashboard's "memos from NBTE" call-out."""
from django import template

register = template.Library()


@register.inclusion_tag("qa_portal/memos/_dashboard_callout.html")
def portal_memo_callout(institution):
    unread = 0
    if institution is not None:
        try:
            from messaging.memos import unread_institution_memo_count
            unread = unread_institution_memo_count(institution)
        except Exception:
            unread = 0
    return {"unread": unread}
