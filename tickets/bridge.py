"""Tickets <-> workflow requests (v2.1 Phase C: I8, I9, I10).

    request_funds(ticket, actor, amount, purpose)   ticket -> Funds request (Finance + ES approve, Finance releases)
    on_request_closed(request)                      called by workflow when a request completes / is rejected / cancelled
    convert_request_to_ticket(request, actor, category, note="")   request -> ticket (I9)
    trail_snapshot(ticket)                          the "how it came about" record Finance and the ES read
    path_events(ticket)                             every hop of the ticket, for the trail panel (I10)

The funds request type (setting ``tickets.funds.request_type``, default
``ticket-funds``) is seeded by the ``tickets.funds`` seed set and created on
first use when missing: Finance director approval -> ES approval -> Finance
releases the funds (receiving director of Finance) -> completed.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify

from .models import Ticket

logger = logging.getLogger(__name__)

FUNDS_STATES_FROM = (Ticket.ASSIGNED, Ticket.IN_PROGRESS, Ticket.AWAITING_RESPONSE, Ticket.REOPENED,
                     Ticket.ESCALATED)
PATH_KINDS = ("created", "routed_division", "division_accepted", "division_declined", "routing_skipped",
              "assigned", "reassigned", "in_progress", "awaiting_response", "funds_requested", "funds_approved",
              "funds_rejected", "converted_from_request", "escalated", "awaiting_director", "director_signed_off",
              "resolved", "closed", "reopened", "cancelled", "rated")


def _cfg(key, default):
    try:
        import config
        return config.get(key, default)
    except Exception:
        return default


def _name(u):
    if u is None:
        return "System"
    return u.get_full_name() or u.email


# ---------------------------------------------------------------------------
# Request type
# ---------------------------------------------------------------------------
FUNDS_TYPE_DEFAULTS = {
    "name": "Funds for a ticket",
    "description": "Money needed to finish a helpdesk ticket. Finance and the Executive Secretary approve "
                   "with the ticket's full trail; Finance then releases the funds and the ticket continues.",
}


def ensure_funds_request_type():
    """The workflow RequestType used by 'Request funds' (idempotent)."""
    from accounts.models import Role
    from staff.models import Department
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    code = _cfg("tickets.funds.request_type", "ticket-funds") or "ticket-funds"
    rt = RequestType.objects.filter(code=code).first()
    if rt is not None:
        return rt
    finance = Department.objects.filter(code=_cfg("tickets.funds.finance_department", "finance") or "finance").first()
    if finance is None:
        raise ValidationError("There is no Finance department to send the funds request to.")
    cat, _ = RequestCategory.objects.get_or_create(code="funds", defaults={"name": "Funds", "sort_order": 90})
    chain, created = ApprovalChain.objects.get_or_create(
        code="ticket-funds-release", defaults={"name": "Ticket funds: Finance releases",
                                               "description": "After Finance and ES approval, Finance releases."})
    if created or not chain.steps.exists():
        ApprovalStep.objects.create(
            chain=chain, position=1, name="Finance releases the funds", kind=ApprovalStep.KIND_APPROVAL,
            approver_kind=ApprovalStep.APPROVER_RECEIVING_DIRECTOR, scope="receiving", is_hierarchical=False,
            approver_role=Role.objects.filter(code="director").first(),
            instructions="Release the approved amount to the ticket's handler, then approve this step.")
    rt, _ = RequestType.objects.get_or_create(code=code, defaults={
        **FUNDS_TYPE_DEFAULTS, "category": cat, "handling_department": finance, "approval_chain": chain,
        "requires_finance_approval": True, "requires_es_approval": True, "is_active": True, "sort_order": 900,
        "form_schema": [], "icon": "banknote",
    })
    return rt


# ---------------------------------------------------------------------------
# Trail
# ---------------------------------------------------------------------------
def trail_snapshot(ticket):
    """Everything Finance and the ES need to see how the need came about.
    Internal notes are left out (the timeline never carries their text)."""
    asset = None
    if ticket.asset_id:
        a = ticket.asset
        asset = {"tag": a.asset_tag, "name": a.display_name, "category": a.category.name,
                 "state": a.get_state_display(), "location": a.current_location,
                 "past_tickets": ticket.asset.tickets.exclude(pk=ticket.pk).count(),
                 "past_issues": a.issues.count()}
    return {
        "ticket_id": ticket.pk, "reference": ticket.reference, "subject": ticket.subject, "body": ticket.body,
        "category": ticket.category.name, "priority": ticket.get_priority_display(),
        "raised_by": _name(ticket.raised_by), "raised_at": ticket.created_at.isoformat(),
        "handling_department": ticket.handling_department.name,
        "division": ticket.division.name if ticket.division_id else "",
        "assignee": _name(ticket.assigned_to) if ticket.assigned_to_id else "",
        "asset": asset,
        "activities": [{"at": a.created_at.isoformat(), "kind": a.kind, "actor": _name(a.actor),
                        "message": a.message} for a in ticket.activities.select_related("actor").order_by("created_at")],
        "comments": [{"at": c.created_at.isoformat(), "author": _name(c.author), "body": c.body}
                     for c in ticket.comments.filter(is_internal=False).select_related("author")],
        "attachments": [{"name": a.original_name, "by": _name(a.uploaded_by), "at": a.created_at.isoformat()}
                        for a in ticket.attachments.select_related("uploaded_by")],
    }


PATH_LABELS = {"in_progress": "Work started", "routed_division": "Routed to division",
               "division_accepted": "Division accepted", "division_declined": "Division declined",
               "routing_skipped": "Routing skipped", "awaiting_response": "Awaiting response",
               "funds_requested": "Funds requested", "funds_approved": "Funds approved",
               "funds_rejected": "Funds rejected", "converted_from_request": "Converted from request",
               "awaiting_director": "Sent for sign-off", "director_signed_off": "Director signed off"}


def path_events(ticket):
    """The hops of a ticket (I10), oldest first, with the linked request references."""
    out = list(ticket.activities.filter(kind__in=PATH_KINDS).select_related("actor").order_by("created_at", "pk"))
    for e in out:
        e.label = PATH_LABELS.get(e.kind, e.kind.replace("_", " ").capitalize())
    return out


# ---------------------------------------------------------------------------
# Ticket -> funds request (I8)
# ---------------------------------------------------------------------------
def can_request_funds(ticket, user) -> bool:
    if ticket.state not in FUNDS_STATES_FROM or not getattr(user, "is_authenticated", False):
        return False
    if ticket.funds_state == Ticket.FUNDS_REQUESTED:
        return False
    import org
    from .services import is_division_head_of
    return bool(ticket.assigned_to_id == user.pk or org.is_super_admin(user)
                or org.is_department_lead(user, ticket.handling_department_id) or is_division_head_of(user, ticket))


def _amount(value):
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValidationError("Enter the amount in naira, e.g. 45000.")
    if amount <= 0 or amount > Decimal("999999999999"):
        raise ValidationError("Enter an amount greater than zero.")
    return amount.quantize(Decimal("0.01"))


@transaction.atomic
def request_funds(ticket, actor, amount, purpose):
    """Raise a Funds request carrying the ticket's trail; the ticket waits
    (AWAITING_FUNDS) until Finance and the ES decide."""
    if not can_request_funds(ticket, actor):
        raise PermissionDenied("Only the ticket's handler or the department's leads can request funds, "
                               "while the ticket is being worked on.")
    amount = _amount(amount)
    purpose = (purpose or "").strip()
    if len(purpose) < 10:
        raise ValidationError("Explain what the money is for (at least 10 characters).")
    from workflow import services as ws

    rt = ensure_funds_request_type()
    trail = trail_snapshot(ticket)
    body = (f"{purpose}\n\nAmount requested: NGN {amount:,.2f}\n"
            f"Raised from ticket {ticket.reference} — {ticket.subject} (opened by {trail['raised_by']}). "
            "The ticket's full trail is attached to this request.")
    req = ws.raise_request(requester=actor, request_type=rt, subject=f"Funds for {ticket.reference}: "
                           f"{ticket.subject}"[:255], body=body, priority=_priority(ticket),
                           form_data={"amount": str(amount), "ticket": ticket.reference})
    req.metadata = {**(req.metadata or {}), "ticket_id": ticket.pk, "ticket_reference": ticket.reference,
                    "amount": str(amount), "purpose": purpose, "ticket_trail": trail}
    req.save(update_fields=["metadata", "updated_at"])
    ws.log_activity(req, "linked", actor=actor, message=f"Raised from ticket {ticket.reference}",
                    metadata={"ticket_id": ticket.pk})
    previous_state = ticket.state
    ticket.funds_request = req
    ticket.funds_state = Ticket.FUNDS_REQUESTED
    ticket.funds_amount = amount
    ticket.funds_decision_note = ""
    ticket.state = Ticket.AWAITING_FUNDS
    ticket.save(update_fields=["funds_request", "funds_state", "funds_amount", "funds_decision_note", "state",
                               "updated_at"])
    from .services import log_activity, _notify_raiser
    log_activity(ticket, "funds_requested", actor=actor,
                 message=f"Funds requested: NGN {amount:,.2f} ({req.reference}) — {purpose[:200]}",
                 metadata={"request_id": req.pk, "request_reference": req.reference, "amount": str(amount),
                           "previous_state": previous_state, "path": True})
    from .services import _after_change
    _after_change(ticket, actor)
    _notify_raiser(ticket, actor, f"{ticket.reference} needs funds; it waits for Finance and the ES "
                                  f"({req.reference}).")
    return req


def _priority(ticket):
    return {"urgent": "urgent", "high": "high", "low": "low"}.get(ticket.priority, "normal")


@transaction.atomic
def on_request_closed(request):
    """workflow hook: a request reached completed / rejected / cancelled.
    Brings each ticket waiting on it back to its handler."""
    from workflow.models import Request, RequestApproval

    tickets = list(Ticket.objects.select_for_update().filter(funds_request=request,
                                                             funds_state=Ticket.FUNDS_REQUESTED))
    for ticket in tickets:
        approved = request.status == Request.COMPLETED
        note = ""
        if not approved:
            last = request.approvals.filter(decision=RequestApproval.REJECT).order_by("-decided_at").first()
            note = (last.comment if last else "") or (
                "The funds request was cancelled." if request.status == Request.CANCELLED else "")
        ticket.funds_state = Ticket.FUNDS_APPROVED if approved else Ticket.FUNDS_REJECTED
        ticket.funds_decision_note = note
        if ticket.state == Ticket.AWAITING_FUNDS:
            ticket.state = Ticket.IN_PROGRESS if ticket.assigned_to_id else Ticket.NEW
        ticket.save(update_fields=["funds_state", "funds_decision_note", "state", "updated_at"])
        from .services import _after_change, _email_event, _notify_raiser, log_activity
        _after_change(ticket, None)
        kind = "funds_approved" if approved else "funds_rejected"
        msg = (f"Funds approved and released: NGN {ticket.funds_amount:,.2f} ({request.reference}). "
               "Work continues." if approved else f"Funds not approved ({request.reference}): {note or '—'}")
        log_activity(ticket, kind, actor=None, message=msg[:500],
                     metadata={"request_id": request.pk, "request_reference": request.reference, "path": True})
        if ticket.assigned_to_id:
            notify(ticket.assigned_to, title=f"{'Funds approved' if approved else 'Funds not approved'} — "
                   f"{ticket.reference}", body=msg, url=f"/tickets/{ticket.pk}/", category=Notification.REQUEST,
                   level=Notification.SUCCESS if approved else Notification.WARNING)
            _email_event(ticket, "funds", [ticket.assigned_to])
        _notify_raiser(ticket, None, msg)
    return len(tickets)


# ---------------------------------------------------------------------------
# Request -> ticket (I9)
# ---------------------------------------------------------------------------
def can_convert_request(request, user) -> bool:
    if request.is_closed or not getattr(user, "is_authenticated", False):
        return False
    if request.metadata and request.metadata.get("ticket_id"):
        return False  # a funds request of a ticket is never turned back into a ticket
    import org
    return bool(org.is_super_admin(user) or org.is_es(user)
                or org.is_department_lead(user, request.receiving_department_id))


@transaction.atomic
def convert_request_to_ticket(request, actor, category, note=""):
    """Turn a fix-type request into a ticket of ``category`` (opened in the
    requester's name, so they are told when it is resolved). The request
    closes as 'converted' and both records link to each other."""
    if not can_convert_request(request, actor):
        raise PermissionDenied("Only the receiving department's director (or the ES) can convert this request.")
    if category is None or not category.is_active:
        raise ValidationError("Choose an open ticket category.")
    from workflow import services as ws

    from .services import create_ticket, log_activity

    asset = None
    issue = request.asset_issues.filter(resolved_at__isnull=True).select_related("asset").first()
    if issue is not None:
        asset = issue.asset
    body = (f"{request.body}\n\n— Converted from request {request.reference} by {_name(actor)}"
            + (f": {note.strip()}" if (note or "").strip() else "."))
    ticket = create_ticket(raised_by=request.requester, category=category, subject=request.subject[:255],
                           body=body, priority={"urgent": "urgent", "high": "high"}.get(request.priority, None),
                           asset=asset)
    ticket.source_request = request
    ticket.save(update_fields=["source_request", "updated_at"])
    log_activity(ticket, "converted_from_request", actor=actor,
                 message=f"Converted from request {request.reference}",
                 metadata={"request_id": request.pk, "request_reference": request.reference, "path": True})
    ws.close_as_converted(request, actor, ticket)
    return ticket
