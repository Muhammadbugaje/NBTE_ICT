"""Who may see which workflow requests.

One rule for the list pages, detail/action URLs, attachment downloads, global
search and the dashboards.

    Super admin ........... every request, automatically.
    All requests .......... `accounts.view_all_requests` (Executive Secretary by
                            default; grantable to anyone from the permission
                            screens). Directors NO LONGER carry it.
    Own department ........ a director / deputy / office head, or anyone holding
                            `accounts.view_department_requests`, sees requests
                            raised by people in their department and requests
                            addressed to (received by) their department.
    Everyone else ......... requests they raised, requests assigned to them,
                            requests they have acted on, and the request that
                            is waiting for them at their approval step.

Not based on `accounts.access_all_departments` (every director carries it).
"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

import org
from accounts.access import is_department_lead, is_super_admin

# Roles that already had the "Incoming" queue (requests addressed to their
# department). They must be able to open what that queue lists.
INCOMING_LEAD_ROLE_CODES = (
    "executive-secretary", "director", "deputy-director",
    "office-head-admin", "office-head-ts", "office-head-dev", "division-head",
)

PERM_ALL = "accounts.view_all_requests"
PERM_DEPARTMENT = "accounts.view_department_requests"


def can_view_all_requests(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_super_admin(user) or user.has_perm(PERM_ALL)


def is_department_request_viewer(user):
    if not getattr(user, "is_authenticated", False) or not user.department_id:
        return False
    return is_department_lead(user) or user.has_perm(PERM_DEPARTMENT)


def visible_requests_queryset(user):
    """Requests this user may see in lists/search. (The one request waiting at
    the user's approval step is added by `can_view_request` for detail pages and
    by the action queue for lists.)"""
    from .models import Request, RequestApproval

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Request.objects.none()
    base = Request.objects.select_related(
        "requester", "request_type", "receiving_department", "assigned_to",
    )
    if can_view_all_requests(user):
        return base
    acted = Exists(RequestApproval.objects.filter(request=OuterRef("pk"), actor=user))
    q = Q(requester=user) | Q(assigned_to=user) | Q(acted)
    if is_department_request_viewer(user):
        dept = user.department_id
        q |= Q(requester__department_id=dept) | Q(receiving_department_id=dept)
    elif user.department_id and (org.has_role(user, *INCOMING_LEAD_ROLE_CODES)
                                 or org.is_director(user, user.department_id)):
        q |= Q(receiving_department_id=user.department_id)
    return base.filter(q)


def can_view_request(user, req):
    """Object-level check for detail, comment, attach, approve, return, reject,
    reassign and attachment download."""
    if req is None or not getattr(user, "is_authenticated", False):
        return False
    if visible_requests_queryset(user).filter(pk=req.pk).exists():
        return True
    # Waiting for me at my step.
    from . import services
    step = services._resolve_current_step(req)
    if step is not None:
        actor = services.resolve_approver(step, req)
        if actor is not None and actor.pk == user.pk:
            return True
    return False
