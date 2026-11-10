"""Who may see which tickets (v2.1 Phase C, I1/I2/I3).

The rule is the shared one in ``work.visibility`` (tasks, tickets, requests):

    ES / super admin / ``accounts.view_all_requests`` .. every ticket
    Involved ............................................ raiser, assignee, resolver
    Director of the handling department ................ every ticket of the department
    Other staff of the handling department ............. tickets handled by lower-ranked staff
    Division head ....................................... tickets routed to the division (pending too)
    Division members .................................... tickets the head accepted (H23 "members added")
    ``accounts.view_department_requests`` / admin-staff . the handling department's tickets (kept from v2)
    Department leads ..................................... also tickets raised by their own staff (kept from v2)

The ticket spec is registered by ``tickets.apps.TicketsConfig.ready``.
"""
from __future__ import annotations

from django.db.models import Q

from accounts.access import is_super_admin

PERM_ALL = "accounts.view_all_requests"
PERM_DEPARTMENT = "accounts.view_department_requests"
HANDLER_ROLE_CODES = ("admin-staff",)


def can_view_all_tickets(user):
    if not getattr(user, "is_authenticated", False):
        return False
    from work.visibility import can_see_all
    from .models import Ticket
    return is_super_admin(user) or can_see_all(user, Ticket)


def _is_handling_staff(user):
    import org
    return bool(user.department_id and org.has_role(user, *HANDLER_ROLE_CODES))


def division_member_q(user):
    """Accepted tickets of the user's own division (members are 'added' when the head accepts)."""
    import org
    from .models import Ticket
    div = org.division_of(user)
    if div is None:
        return Q(pk__in=[])
    return Q(division_id=div.pk, division_state=Ticket.DIV_ACCEPTED)


def department_handler_q(user):
    """Kept from v2: leads also see the tickets their own staff raised (in
    any department); department-request viewers and admin staff see the
    tickets their department handles."""
    if not user.department_id:
        return Q(pk__in=[])
    import org
    q = Q(pk__in=[])
    if org.is_department_lead(user, user.department_id) or user.has_perm(PERM_DEPARTMENT):
        q |= Q(raised_by__department_id=user.department_id)
    if user.has_perm(PERM_DEPARTMENT) or _is_handling_staff(user) or org.is_department_lead(user, user.department_id):
        q |= Q(handling_department_id=user.department_id)
    return q


def register_spec():
    from work.visibility import VisibilitySpec, register
    register("tickets.ticket", VisibilitySpec(
        department="handling_department",
        department_fallback="raised_by__department",
        involved=("raised_by", "assigned_to", "resolved_by"),
        handlers=("raised_by", "assigned_to"),
        division="division",
        view_all_perm=PERM_ALL,
        extra=(division_member_q, department_handler_q),
    ))


def visible_tickets_queryset(user):
    from work.visibility import visible
    from .models import Ticket

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Ticket.objects.none()
    base = Ticket.objects.select_related(
        "category", "raised_by", "assigned_to", "handling_department", "asset", "division",
    )
    if is_super_admin(user):
        return base
    return visible(user, base)


def can_view_ticket(user, ticket):
    if ticket is None or not getattr(user, "is_authenticated", False):
        return False
    return visible_tickets_queryset(user).filter(pk=ticket.pk).exists()
