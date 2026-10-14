from django import template

from portal import services
from portal.models import Announcement, Event, Reminder

register = template.Library()


@register.inclusion_tag("portal/widgets/_announcements.html", takes_context=True)
def announcements_widget(context, limit=3):
    user = context["request"].user
    return {
        "announcements": services.dashboard_announcements(user, limit=limit),
        "total": services.live_announcements(user=user).count(),
    }


@register.inclusion_tag("portal/widgets/_events.html", takes_context=True)
def events_widget(context, limit=3):
    user = context["request"].user
    return {
        "events": services.dashboard_events(user, limit=limit),
        "total": services.live_events(user=user, when="upcoming").count(),
    }


@register.inclusion_tag("portal/widgets/_reminders.html", takes_context=True)
def reminders_widget(context, limit=4):
    user = context["request"].user
    return {
        "reminders": services.dashboard_reminders(user, limit=limit),
    }


@register.filter
def initials_of(user):
    if not user:
        return "?"
    fn = (getattr(user, "first_name", "") or "")[:1]
    ln = (getattr(user, "last_name", "") or "")[:1]
    return (fn + ln).upper() or (getattr(user, "email", "") or "?")[:1].upper()


@register.simple_tag
def portal_unread_badge(user):
    """Total visible-but-unseen reminders for the sidebar badge."""
    try:
        return services.visible_reminders(user=user).count()
    except Exception:
        return 0
        
        
@register.inclusion_tag("portal/widgets/_documents.html", takes_context=True)
def documents_widget(context, limit=3):
    user = context["request"].user
    return {
        "documents": services.dashboard_documents(user, limit=limit),
        "total": services.published_documents(user=user).count(),
    }


@register.inclusion_tag("portal/widgets/_public_documents.html", takes_context=True)
def public_documents_widget(context, limit=3):
    return {
        "featured": services.featured_public_documents(limit=limit),
        "recent":   services.public_documents(limit=6),
    }        
        
        