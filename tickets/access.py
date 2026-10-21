"""Who may see which tickets / complaints.

Same shape as workflow/access.py and assets/access.py.

    Super admin ........... every ticket, automatically.
    All tickets ........... `accounts.view_all_requests` (the same "all"
                            permission as workflow requests; Executive
                            Secretary by default).
    Own department ........ a director / deputy / office head / admin-staff of
                            the handling department, or anyone holding
                            `accounts.view_department_requests`: tickets
                            handled by their department AND tickets raised by
                            people in their department.
    Everyone else ......... tickets they raised or that are assigned to them.

Not based on `accounts.access_all_departments`.
"""
from __future__ import annotations

from django.db.models import Q

from accounts.access import is_department_lead, is_super_admin

PERM_ALL = "accounts.view_all_requests"
PERM_DEPARTMENT = "accounts.view_department_requests"
HANDLER_ROLE_CODES = ("admin-staff",)


def can_view_all_tickets(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_super_admin(user) or user.has_perm(PERM_ALL)


def _is_handling_staff(user):
    return bool(user.department_id and user.role_id
                and user.role.code in HANDLER_ROLE_CODES)


def visible_tickets_queryset(user):
    from .models import Ticket

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Ticket.objects.none()
    base = Ticket.objects.select_related(
        "category", "raised_by", "assigned_to", "handling_department", "asset",
    )
    if can_view_all_tickets(user):
        return base
    q = Q(raised_by=user) | Q(assigned_to=user)
    if user.department_id:
        dept = user.department_id
        if is_department_lead(user) or user.has_perm(PERM_DEPARTMENT):
            q |= Q(handling_department_id=dept) | Q(raised_by__department_id=dept)
        elif _is_handling_staff(user):
            q |= Q(handling_department_id=dept)
    return base.filter(q)


def can_view_ticket(user, ticket):
    if ticket is None or not getattr(user, "is_authenticated", False):
        return False
    return visible_tickets_queryset(user).filter(pk=ticket.pk).exists()
