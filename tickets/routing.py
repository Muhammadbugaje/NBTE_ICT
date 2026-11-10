"""Division routing of tickets (v2.1 Phase C: H23 paradigm for tickets, I2, I3).

    route_to_division(ticket, actor, division, note="")  director → division (head must accept)
    accept_division(ticket, actor)                        head accepts; members see it
    decline_division(ticket, actor, reason)               head declines; back to the director
    auto_route(ticket)                                    TicketCategory.handling_division on create
    division_load(division) / has_capacity(division)      open tickets routed to the division
    routable_divisions(ticket)                            active divisions of the handling department

Capacity: ``Division.max_open_tasks`` (B8) also caps the open tickets routed
to the division (pending or accepted); 0 = no limit.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify

from .models import Ticket


def _org():
    import org
    return org


def division_load(division) -> int:
    return Ticket.objects.filter(division=division, state__in=Ticket.OPEN_STATES,
                                 division_state__in=(Ticket.DIV_PENDING, Ticket.DIV_ACCEPTED)).count()


def has_capacity(division) -> bool:
    cap = division.max_open_tasks or 0
    return cap == 0 or division_load(division) < cap


def load_label(division) -> str:
    cap = division.max_open_tasks or 0
    used = division_load(division)
    if cap and used >= cap:
        return f"Full {used}/{cap}"
    return f"{used} open" + (f" of {cap}" if cap else "")


def routable_divisions(ticket):
    from org.models import Division
    return Division.objects.filter(department_id=ticket.handling_department_id, is_active=True) \
        .select_related("head").order_by("name")


def can_route(ticket, user) -> bool:
    """Director (or a lead) of the handling department, ES, super admin."""
    org = _org()
    if not ticket.is_open or not getattr(user, "is_authenticated", False):
        return False
    if ticket.state == Ticket.AWAITING_FUNDS:
        return False
    return bool(org.is_super_admin(user) or org.is_es(user)
                or org.is_department_lead(user, ticket.handling_department_id))


def can_decide(ticket, user) -> bool:
    """The head of the division the ticket is waiting on (director / ES override)."""
    if ticket.division_state != Ticket.DIV_PENDING or not ticket.division_id or not ticket.is_open:
        return False
    div = ticket.division
    org = _org()
    return bool(div.head_id == getattr(user, "pk", None) or org.is_super_admin(user)
                or org.is_director(user, ticket.handling_department_id))


def _log(ticket, kind, actor, message, **meta):
    from .services import log_activity
    return log_activity(ticket, kind, actor=actor, message=message, metadata=meta)


@transaction.atomic
def route_to_division(ticket, actor, division, note=""):
    if not can_route(ticket, actor):
        raise PermissionDenied("Only the director of the handling department (or the ES) can route this ticket.")
    if division is None or not division.is_active:
        raise ValidationError("Choose an active division.")
    if division.department_id != ticket.handling_department_id:
        raise ValidationError("The division must belong to the department handling this ticket.")
    if not division.head_id:
        raise ValidationError(f"{division.name} has no head yet. Appoint a head before routing work to it.")
    if ticket.division_id == division.pk and ticket.division_state in (Ticket.DIV_PENDING, Ticket.DIV_ACCEPTED):
        raise ValidationError(f"This ticket is already with {division.name}.")
    if not has_capacity(division):
        raise ValidationError(f"{division.name} is full ({load_label(division)}). Choose another division "
                              "or ask the head to close some tickets first.")
    previous = ticket.division.name if ticket.division_id else ""
    ticket.division = division
    ticket.division_state = Ticket.DIV_PENDING
    ticket.division_routed_by = actor
    ticket.division_routed_at = timezone.now()
    ticket.division_accepted_by = None
    ticket.division_accepted_at = None
    ticket.save(update_fields=["division", "division_state", "division_routed_by", "division_routed_at",
                               "division_accepted_by", "division_accepted_at", "updated_at"])
    _log(ticket, "routed_division", actor, f"Routed to {division.name}" + (f" — {note}" if note else ""),
         division=division.pk, previous=previous, path=True)
    notify(division.head, title=f"Ticket for {division.name} — {ticket.reference}",
           body=f"{ticket.subject}. Accept it to share it with the division.",
           url=f"/tickets/{ticket.pk}/", category=Notification.REQUEST, level=Notification.INFO, actor=actor)
    return ticket


def auto_route(ticket):
    """On create: route to the category's division when it has a head and room."""
    division = ticket.category.handling_division
    if division is None or not division.is_active or division.department_id != ticket.handling_department_id:
        return None
    if not division.head_id or not has_capacity(division):
        _log(ticket, "routing_skipped", None,
             f"Not routed to {division.name}: " + ("no head" if not division.head_id else "division full"),
             division=division.pk)
        return None
    ticket.division = division
    ticket.division_state = Ticket.DIV_PENDING
    ticket.division_routed_at = timezone.now()
    ticket.save(update_fields=["division", "division_state", "division_routed_at", "updated_at"])
    _log(ticket, "routed_division", None, f"Routed to {division.name} by the category rule",
         division=division.pk, auto=True, path=True)
    notify(division.head, title=f"Ticket for {division.name} — {ticket.reference}",
           body=f"{ticket.subject}. Accept it to share it with the division.",
           url=f"/tickets/{ticket.pk}/", category=Notification.REQUEST, level=Notification.INFO)
    return division


@transaction.atomic
def accept_division(ticket, actor):
    if not can_decide(ticket, actor):
        raise PermissionDenied("Only the head of this division can accept the ticket.")
    division = ticket.division
    ticket.division_state = Ticket.DIV_ACCEPTED
    ticket.division_accepted_by = actor
    ticket.division_accepted_at = timezone.now()
    ticket.save(update_fields=["division_state", "division_accepted_by", "division_accepted_at", "updated_at"])
    members = [m.user for m in division.members.filter(user__isnull=False, user__is_active=True)
               .select_related("user") if m.user_id != actor.pk]
    from .services import _after_change
    _after_change(ticket, actor)
    _log(ticket, "division_accepted", actor, f"{division.name} accepted the ticket; "
         f"{len(members)} member(s) can now see it.", division=division.pk, members=len(members), path=True)
    from notifications.services import notify_many
    notify_many(members, title=f"New division ticket — {ticket.reference}", body=ticket.subject,
                url=f"/tickets/{ticket.pk}/", category=Notification.REQUEST, level=Notification.INFO, actor=actor)
    if ticket.division_routed_by_id and ticket.division_routed_by_id != actor.pk:
        notify(ticket.division_routed_by, title=f"{division.name} accepted {ticket.reference}",
               body=ticket.subject, url=f"/tickets/{ticket.pk}/", category=Notification.REQUEST, actor=actor)
    return ticket


@transaction.atomic
def decline_division(ticket, actor, reason=""):
    if not can_decide(ticket, actor):
        raise PermissionDenied("Only the head of this division can decline the ticket.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Say why the division cannot take this ticket.")
    division = ticket.division
    routed_by = ticket.division_routed_by
    ticket.division = None
    ticket.division_state = ""
    ticket.save(update_fields=["division", "division_state", "updated_at"])
    _log(ticket, "division_declined", actor, f"{division.name} declined: {reason}", division=division.pk,
         reason=reason, path=True)
    org = _org()
    target = routed_by or org.director_of(ticket.handling_department_id)
    if target is not None and target.pk != actor.pk:
        notify(target, title=f"{division.name} declined {ticket.reference}", body=reason,
               url=f"/tickets/{ticket.pk}/", category=Notification.REQUEST, level=Notification.WARNING, actor=actor)
    return ticket


# ---------------------------------------------------------------------------
# Counts (I3, I10): per division and per path, scoped to the viewer
# ---------------------------------------------------------------------------
PATH_KINDS = {
    "routed_division": "Routed to a division",
    "division_accepted": "Accepted by a division",
    "division_declined": "Declined by a division",
    "funds_requested": "Funds requested (became a request)",
    "funds_approved": "Funds approved",
    "funds_rejected": "Funds rejected",
    "converted_from_request": "Converted from a request",
    "reopened": "Reopened",
    "escalated": "Escalated",
}


def counts_scope(user):
    """("all" | "department" | "division" | None, department_id, division)."""
    org = _org()
    if org.is_super_admin(user) or org.is_es(user) or user.has_perm("accounts.view_all_requests"):
        return "all", None, None
    if user.department_id and org.is_department_lead(user, user.department_id):
        return "department", user.department_id, None
    from org.models import Division
    div = Division.objects.filter(head=user, is_active=True).first()
    if div is not None:
        return "division", div.department_id, div
    return None, None, None


def counts_for(user, since=None):
    """Counts shown on the ticket counts page (match the ticket lists)."""
    from django.db.models import Count, Q
    from org.models import Division
    from staff.models import Department

    from .models import TicketActivity

    scope, dept_id, own_div = counts_scope(user)
    if scope is None:
        return None
    now = timezone.now()
    since = since or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    tickets = Ticket.objects.all()
    divisions = Division.objects.filter(is_active=True)
    if scope == "department":
        tickets = tickets.filter(handling_department_id=dept_id)
        divisions = divisions.filter(department_id=dept_id)
    elif scope == "division":
        tickets = tickets.filter(division=own_div)
        divisions = divisions.filter(pk=own_div.pk)
    open_q = Q(tickets__state__in=list(Ticket.OPEN_STATES))
    div_rows = (divisions.annotate(
        pending=Count("tickets", filter=open_q & Q(tickets__division_state=Ticket.DIV_PENDING)),
        open=Count("tickets", filter=open_q & Q(tickets__division_state=Ticket.DIV_ACCEPTED)),
        resolved=Count("tickets", filter=Q(tickets__resolved_at__gte=since)),
    ).select_related("department", "head").order_by("department__name", "name"))
    dept_rows = []
    if scope in ("all", "department"):
        depts = Department.objects.filter(is_active=True)
        if scope == "department":
            depts = depts.filter(pk=dept_id)
        dq = Q(tickets_handled__state__in=list(Ticket.OPEN_STATES))
        dept_rows = [d for d in depts.annotate(
            open=Count("tickets_handled", filter=dq),
            unrouted=Count("tickets_handled", filter=dq & Q(tickets_handled__division__isnull=True)),
            resolved=Count("tickets_handled", filter=Q(tickets_handled__resolved_at__gte=since)),
        ).order_by("name") if d.open or d.resolved]
    paths = dict(TicketActivity.objects.filter(ticket__in=tickets, kind__in=list(PATH_KINDS))
                 .values_list("kind").annotate(n=Count("pk")).values_list("kind", "n"))
    path_rows = [{"kind": k, "label": label, "count": paths.get(k, 0)} for k, label in PATH_KINDS.items()]
    return {
        "scope": scope, "division": own_div, "since": since,
        "totals": {
            "open": tickets.filter(state__in=Ticket.OPEN_STATES).count(),
            "resolved": tickets.filter(resolved_at__gte=since).count(),
            "with_division": tickets.filter(state__in=Ticket.OPEN_STATES, division__isnull=False).count(),
            "awaiting_funds": tickets.filter(state=Ticket.AWAITING_FUNDS).count(),
            "unrated": tickets.filter(state__in=(Ticket.RESOLVED, Ticket.CLOSED),
                                      satisfaction_rating__isnull=True).count(),
        },
        "divisions": list(div_rows), "departments": dept_rows, "paths": path_rows,
    }
