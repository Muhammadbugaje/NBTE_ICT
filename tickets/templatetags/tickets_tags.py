"""Ticket template tags."""
from django import template

register = template.Library()


@register.simple_tag
def open_tickets_badge(user):
    """Count of open tickets assigned to `user`."""
    if not user or not user.is_authenticated:
        return 0
    from tickets.models import Ticket
    return (
        Ticket.objects
        .filter(assigned_to=user)
        .exclude(state__in=Ticket.CLOSED_STATES)
        .count()
    )


@register.inclusion_tag("tickets/_dashboard_widget.html", takes_context=True)
def ticket_dashboard_widget(context):
    """Small widget for the home dashboard."""
    from tickets.models import Ticket
    request = context.get("request")
    user = request.user if request else None
    if not user or not user.is_authenticated:
        return {"visible": False}

    my_open = (
        Ticket.objects
        .filter(raised_by=user)
        .exclude(state__in=Ticket.CLOSED_STATES)
        .select_related("category", "assigned_to")
        .order_by("-created_at")[:5]
    )
    assigned = (
        Ticket.objects
        .filter(assigned_to=user)
        .exclude(state__in=Ticket.CLOSED_STATES)
        .select_related("category", "raised_by")
        .order_by("-created_at")[:5]
    )

    return {
        "visible": bool(my_open or assigned),
        "my_open": list(my_open),
        "assigned": list(assigned),
        "my_open_count": Ticket.objects.filter(raised_by=user).exclude(state__in=Ticket.CLOSED_STATES).count(),
        "assigned_count": Ticket.objects.filter(assigned_to=user).exclude(state__in=Ticket.CLOSED_STATES).count(),
    }