"""Temporary asset access for a ticket's assignee (v2.1 X5; replaces I11/K3).

While a ticket is open, its assignee holds an ``AccessGrant`` (scope
``asset``) on the linked asset when they could not see the asset otherwise:
details, custodian, past issues and past tickets. The grant is revoked when
the ticket is resolved, closed or cancelled, or moves to someone else;
reopening grants it again. Switch: ``tickets.asset_access.enabled``.
"""
from __future__ import annotations


def _enabled():
    try:
        import config
        return str(config.get("tickets.asset_access.enabled", "1")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def _native(user, asset):
    from assets.access import can_view_asset_natively
    return can_view_asset_natively(user, asset)


def sync_asset_grant(ticket, actor=None):
    """Idempotent: make the ticket's grants match its current state."""
    from assignments.grants import sync_for_source
    from assignments.models import AccessGrant

    assignee = ticket.assigned_to if ticket.assigned_to_id else None
    asset = ticket.asset if ticket.asset_id else None
    wanted = bool(_enabled() and ticket.is_open and assignee is not None and asset is not None
                  and assignee.is_active and not _native(assignee, asset))
    revoke_reason = AccessGrant.R_COMPLETED if not ticket.is_open else AccessGrant.R_REASSIGNED
    return sync_for_source(
        ticket, assignee, asset, AccessGrant.SCOPE_ASSET, wanted=wanted,
        reason=f"Assigned ticket {ticket.reference}: {ticket.subject}"[:255],
        granted_by=actor if getattr(actor, "pk", None) else None, revoke_reason=revoke_reason,
        department=ticket.handling_department)


def ticket_for_grant(grant):
    from .models import Ticket
    if grant is None or not grant.source_ct_id or grant.source_ct.model_class() is not Ticket:
        return None
    return Ticket.objects.filter(pk=grant.source_id).first()
