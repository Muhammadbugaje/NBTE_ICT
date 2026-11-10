"""tickets/ - the website's Tickets app.

Reuses: tickets.access.visible_tickets_queryset (via tickets.views),
tickets.views.ticket_list_base (my / assigned / all tabs),
tickets.forms.* (validation), tickets.services.* (create, assign, start,
awaiting, resolve, director_sign_off, close, reopen, cancel, comments,
attachments) and tickets.services.available_actions (detail buttons).
"""
from django.db.models import Q

from .work import activity_ref, attachment_ref, iso, user_ref

PRIORITY_ORDER = ["low", "normal", "high", "urgent"]


def _ref(obj):
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


def visible(user):
    from tickets.views import _visible_tickets_qs

    return _visible_tickets_qs(user)


def list_queryset(user, params):
    """tickets.views.ticket_list / my_tickets / assigned_to_me filters."""
    from tickets.models import Ticket
    from tickets.views import ticket_list_base

    scope = params.get("scope") or "my"
    qs = ticket_list_base(user, scope)
    q = (params.get("q") or "").strip()
    if q:
        cond = Q(reference__icontains=q) | Q(subject__icontains=q)
        if scope == "all":
            cond |= Q(body__icontains=q) | Q(current_location__icontains=q)
        qs = qs.filter(cond)
    state = params.get("state")
    if state in dict(Ticket.STATE_CHOICES):
        qs = qs.filter(state=state)
    elif state == "open":
        qs = qs.filter(state__in=Ticket.OPEN_STATES)
    elif state == "closed":
        qs = qs.filter(state__in=Ticket.CLOSED_STATES)
    if params.get("priority"):
        qs = qs.filter(priority=params["priority"])
    if params.get("category"):
        qs = qs.filter(category__code=params["category"])
    return qs.select_related("category", "raised_by", "assigned_to", "handling_department").order_by("-created_at")


def list_item(t):
    return {
        "id": t.pk, "reference": t.reference, "subject": t.subject, "state": t.state,
        "state_display": t.get_state_display(), "priority": t.priority,
        "priority_display": t.get_priority_display(),
        "category": {"id": t.category.pk, "code": t.category.code, "name": t.category.name} if t.category_id else None,
        "raised_by": user_ref(t.raised_by), "assigned_to": user_ref(t.assigned_to),
        "handling_department": _ref(t.handling_department),
        "resolution_due_at": iso(t.resolution_due_at),
        "sla_overdue": bool(t.sla_resolution_overdue),
        "created_at": iso(t.created_at), "updated_at": iso(t.updated_at),
    }


def detail(ticket, user, request=None):
    from tickets import services

    flags = services.available_actions(ticket, user)
    can_internal = flags["can_internal"]
    data = list_item(ticket)
    asset = None
    if ticket.asset_id:
        from assets.access import can_view_asset

        asset = {"tag": ticket.asset.asset_tag, "name": ticket.asset.display_name,
                 "can_open": can_view_asset(user, ticket.asset)}
    comments = ticket.comments.select_related("author").order_by("created_at")
    if not can_internal:
        comments = comments.filter(is_internal=False)
    activity = ticket.activities.select_related("actor").order_by("created_at")
    if not can_internal:
        activity = activity.exclude(kind="internal_note")
    data.update({
        "body": ticket.body,
        "current_location": ticket.current_location or "",
        "asset": asset,
        "resolution_notes": ticket.resolution_notes or "",
        "collection_location": ticket.collection_location or "",
        "collection_timeframe": ticket.collection_timeframe or "",
        "response_due_at": iso(ticket.response_due_at),
        "first_response_at": iso(ticket.first_response_at),
        "resolved_at": iso(ticket.resolved_at),
        "closed_at": iso(ticket.closed_at),
        "escalation_level": ticket.escalation_level or 0,
        "reopen_count": ticket.reopen_count or 0,
        "satisfaction_rating": ticket.satisfaction_rating,
        "satisfaction_comment": ticket.satisfaction_comment or "",
        "director_signoff_required": bool(ticket.director_signoff_required),
        "comments": [{"id": c.pk, "author": user_ref(c.author), "body": c.body, "is_internal": c.is_internal,
                      "created_at": iso(c.created_at)} for c in comments],
        "attachments": [attachment_ref(a, "tickets", ticket.pk, request)
                        for a in ticket.attachments.select_related("uploaded_by").order_by("-created_at")],
        "activity": [activity_ref(a) for a in activity],
        "actions": {k: flags[k] for k in (
            "is_raiser", "is_assignee", "can_assign", "can_start", "can_await", "can_resolve", "can_signoff",
            "can_close", "can_reopen", "can_cancel", "can_comment", "can_internal", "can_attach",
            "can_route", "can_accept_division", "can_request_funds", "can_rate")},
        # v2.1 Phase C
        "division": ({"id": ticket.division_id, "name": ticket.division.name, "state": ticket.division_state}
                     if ticket.division_id else None),
        "funds": ({"state": ticket.funds_state, "amount": str(ticket.funds_amount) if ticket.funds_amount else None,
                   "request": ticket.funds_request.reference if ticket.funds_request_id else None,
                   "note": ticket.funds_decision_note or ""} if ticket.funds_state else None),
        "report_pdf_url": f"/api/v1/tickets/{ticket.pk}/report.pdf",
    })
    return data


def categories():
    from tickets.models import TicketCategory

    return (TicketCategory.objects.filter(is_active=True).select_related("handling_department")
            .order_by("sort_order", "name"))


def category_item(c):
    from tickets.forms import TicketCreateForm

    allowed = [p for p, _label in TicketCreateForm(category=c).fields["priority"].choices]
    allowed.sort(key=lambda p: PRIORITY_ORDER.index(p) if p in PRIORITY_ORDER else 99)
    return {"id": c.pk, "code": c.code, "name": c.name, "description": c.description or "",
            "icon": c.icon or "", "handling_department": _ref(c.handling_department),
            "minimum_priority": c.minimum_priority, "priorities": allowed}


def assignable(ticket):
    """tickets.forms.AssignTicketForm's choices for this ticket."""
    from tickets.forms import AssignTicketForm

    return AssignTicketForm(department=ticket.handling_department).fields["assignee"].queryset
