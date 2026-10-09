"""Tickets & Helpdesk — the service layer.

Every business operation on a ticket goes through here. Views call these
functions; they never mutate ticket state directly.

Public API:
    generate_reference()                       — allocate TKT-YYYY-NNNNN
    create_ticket(...)                         — create + start SLA clock
    assign_ticket(ticket, user, assignee)      — assign or reassign
    start_work(ticket, user, note)             — NEW/ASSIGNED → IN_PROGRESS
    mark_awaiting_response(ticket, user, why)  — IN_PROGRESS → AWAITING
    resolve_ticket(ticket, user, notes)        — IN_PROGRESS → RESOLVED
    close_ticket(ticket, user, rating, comment) — RESOLVED → CLOSED
    reopen_ticket(ticket, user, reason)        — RESOLVED/CLOSED → REOPENED
    cancel_ticket(ticket, user, reason)        — NEW/ASSIGNED → CANCELLED
    escalate_ticket(ticket, reason, level)     — bump escalation level
    add_public_comment(ticket, user, body)     — visible to raiser + staff
    add_internal_comment(ticket, user, body)   — staff-only note
    add_attachment(ticket, user, file, desc)   — file upload
    delete_comment(ticket, user, comment)      — author or department head
    delete_attachment(ticket, user, attachment) — uploader or department head
    sla_check_task()                           — Celery Beat; scans + escalates
    log_activity(ticket, kind, actor, message, metadata)
"""
import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify

from .models import (
    Ticket,
    TicketActivity,
    TicketAttachment,
    TicketCategory,
    TicketComment,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# Config
# ===========================================================================
def _cfg_int(key, default):
    try:
        import config as _cfg
        return int(_cfg.get(key, default))
    except Exception:
        return default


PRIORITY_MULTIPLIER_KEYS = {
    Ticket.URGENT: "tickets.sla.multiplier.urgent",
    Ticket.HIGH:   "tickets.sla.multiplier.high",
    Ticket.NORMAL: "tickets.sla.multiplier.normal",
    Ticket.LOW:    "tickets.sla.multiplier.low",
}

DEFAULT_MULTIPLIERS = {
    Ticket.URGENT: 1,
    Ticket.HIGH:   2,
    Ticket.NORMAL: 4,
    Ticket.LOW:    8,
}


# ===========================================================================
# Reference allocation
# ===========================================================================
@transaction.atomic
def generate_reference():
    """Allocate the next ticket reference of the form TKT-YYYY-NNNNN.

    Uses select_for_update on the last matching row so concurrent
    creations don't collide. Same technique as workflow.generate_reference.
    """
    year = date.today().year
    prefix = f"TKT-{year}-"

    last = (
        Ticket.objects
        .select_for_update()
        .filter(reference__startswith=prefix)
        .order_by("-reference")
        .first()
    )
    if last:
        try:
            seq = int(last.reference.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:05d}"


# ===========================================================================
# Activity log
# ===========================================================================
def log_activity(ticket, kind, actor=None, message="", metadata=None):
    """Append-only timeline entry. Never raises on bad metadata."""
    return TicketActivity.objects.create(
        ticket=ticket,
        kind=kind,
        actor=actor,
        message=(message or "")[:500],
        metadata=metadata or {},
    )


# ===========================================================================
# SLA computation
# ===========================================================================
def _priority_multiplier(priority):
    key = PRIORITY_MULTIPLIER_KEYS.get(priority)
    if key is None:
        return 1
    return _cfg_int(key, DEFAULT_MULTIPLIERS.get(priority, 1))


def _compute_sla_deadlines(category, priority, created_at):
    """Return (response_due_at, resolution_due_at).

    Effective window = category base (or global default) × priority multiplier.
    """
    base_response = category.effective_response_seconds()
    base_resolution = category.effective_resolution_seconds()
    mult = _priority_multiplier(priority)

    response_seconds = base_response * mult
    resolution_seconds = base_resolution * mult

    return (
        created_at + timedelta(seconds=response_seconds),
        created_at + timedelta(seconds=resolution_seconds),
    )


# ===========================================================================
# Permission helpers
# ===========================================================================
def _is_staff_on_ticket(ticket, user):
    """True for the assignee, the raiser, or any director/deputy in the
    handling department."""
    if user.is_superuser:
        return True
    if ticket.assigned_to_id == user.pk:
        return True
    if ticket.raised_by_id == user.pk:
        return True
    if (
        user.department_id
        and user.department_id == ticket.handling_department_id
        and user.role_id
        and user.role.code in ("director", "deputy-director",
                                "office-head-admin", "office-head-ts",
                                "office-head-dev")
    ):
        return True
    return False


def _is_department_head(user, department_id):
    """True if `user` is a director/deputy/office-head in this department."""
    if user.is_superuser:
        return True
    if not user.department_id or user.department_id != department_id:
        return False
    if not user.role_id:
        return False
    return user.role.code in (
        "director", "deputy-director",
        "office-head-admin", "office-head-ts", "office-head-dev",
    )


def _can_assign(ticket, user):
    """Who can assign/reassign a ticket:
       - the raiser, only for the initial assignment OR if the category
         allows raiser reassignment
       - a department head in the handling department
       - a superuser
    """
    if user.is_superuser:
        return True
    if ticket.raised_by_id == user.pk:
        # Initial assignment is always allowed
        if ticket.assigned_to_id is None:
            return True
        # Reassignment follows the category toggle
        return bool(getattr(ticket.category, "allow_raiser_reassign", True))
    return _is_department_head(user, ticket.handling_department_id)


def _can_resolve(ticket, user):
    """Who can resolve/close-work: the assignee, or a department head."""
    if user.is_superuser:
        return True
    if ticket.assigned_to_id == user.pk:
        return True
    return _is_department_head(user, ticket.handling_department_id)


def _can_comment(ticket, user, internal=False):
    """Public comments: raiser or anyone in the handling department.
    Internal comments: staff only (never the raiser, unless they're
    themselves a department head — edge case, allow it).
    """
    if user.is_superuser:
        return True
    if internal:
        # Internal notes: staff on the ticket only.
        return _is_staff_on_ticket(ticket, user) and (
            ticket.raised_by_id != user.pk
            or _is_department_head(user, ticket.handling_department_id)
        )
    # Public comment
    if ticket.raised_by_id == user.pk:
        return True
    return _is_staff_on_ticket(ticket, user)


# ===========================================================================
# Create
# ===========================================================================
@transaction.atomic
def create_ticket(*, raised_by, category, subject, body,
                  priority=None, current_location="",
                  asset=None, attachment=None):
    """Create a ticket, allocate a reference, compute SLA deadlines,
    optionally auto-assign, and log the opening activity.

    `priority` defaults to the category's `minimum_priority` if not given.
    If the category has a `default_assignee`, the ticket starts in ASSIGNED.
    """
    if not category.is_active:
        raise ValidationError("This ticket category is currently closed.")

    # Priority floor
    priority = priority or category.minimum_priority
    if priority not in dict(Ticket.PRIORITY_CHOICES):
        priority = Ticket.NORMAL
    floor_order = ["low", "normal", "high", "urgent"]
    try:
        if floor_order.index(priority) < floor_order.index(category.minimum_priority):
            priority = category.minimum_priority
    except ValueError:
        pass

    # Allocate reference (retry on race)
    ticket = None
    last_error = None
    for _ in range(5):
        reference = generate_reference()
        try:
            with transaction.atomic():
                ticket = Ticket.objects.create(
                    reference=reference,
                    raised_by=raised_by,
                    category=category,
                    priority=priority,
                    subject=subject.strip(),
                    body=body.strip(),
                    current_location=(current_location or "").strip(),
                    handling_department=category.handling_department,
                    asset=asset,
                    state=Ticket.NEW,
                )
            break
        except IntegrityError as exc:
            last_error = exc
            continue

    if ticket is None:
        logger.exception(
            "Could not allocate a ticket reference after 5 attempts: %s",
            last_error,
        )
        raise ValidationError(
            "Could not allocate a ticket reference right now. Please try again."
        )

    # SLA deadlines
    response_due, resolution_due = _compute_sla_deadlines(
        category, priority, ticket.created_at,
    )
    ticket.response_due_at = response_due
    ticket.resolution_due_at = resolution_due
    ticket.save(update_fields=["response_due_at", "resolution_due_at", "updated_at"])

    log_activity(
        ticket, "created", actor=raised_by,
        message=f"Ticket raised: {ticket.subject}",
        metadata={
            "category": category.code,
            "priority": priority,
            "has_asset": asset is not None,
        },
    )

    # Optional attachment at creation time
    if attachment:
        add_attachment(
            ticket, raised_by, attachment,
            description="Attached at creation.",
        )

    # Optional auto-assign
    if category.default_assignee_id:
        try:
            _apply_assignment(
                ticket,
                actor=None,  # system-driven
                assignee=category.default_assignee,
                reason="Auto-assigned by category default.",
                log_as_system=True,
            )
        except Exception:
            logger.exception(
                "Auto-assignment failed for ticket %s", ticket.reference,
            )

    # Notify the handling department's team about the new ticket.
    _notify_new_ticket(ticket)

    return ticket


# ===========================================================================
# Assignment
# ===========================================================================
def _apply_assignment(ticket, *, actor, assignee, reason="", log_as_system=False):
    """Internal — mutate assignment + state + log + notify. Caller has
    already done the permission check. Does not commit on its own."""
    if assignee is None or not assignee.is_active:
        raise ValidationError("Invalid assignee.")

    was_unassigned = ticket.assigned_to_id is None
    ticket.assigned_to = assignee
    if ticket.state in (Ticket.NEW, Ticket.REOPENED):
        ticket.state = Ticket.ASSIGNED
    ticket.save(update_fields=["assigned_to", "state", "updated_at"])

    log_activity(
        ticket, "assigned" if was_unassigned else "reassigned",
        actor=actor,
        message=(
            f"{'Auto-assigned' if log_as_system else 'Assigned'} to "
            f"{assignee.get_full_name() or assignee.email}"
            + (f" — {reason}" if reason else "")
        ),
        metadata={
            "assigned_to": assignee.pk,
            "previous_assignee": None,  # not tracked on model; log-only
            "reason": reason,
            "system": log_as_system,
        },
    )
    if actor is not None and actor.pk != assignee.pk:
        _notify_assignee(ticket, assignee, actor)
    return ticket


@transaction.atomic
def assign_ticket(ticket, user, assignee, reason=""):
    """Assign or reassign. Permission: raiser, department head, or superuser."""
    if not _can_assign(ticket, user):
        raise PermissionDenied("You cannot assign this ticket.")
    if ticket.is_closed:
        raise ValidationError("Closed tickets cannot be reassigned.")

    return _apply_assignment(
        ticket, actor=user, assignee=assignee, reason=reason,
    )


# ===========================================================================
# Workflow transitions
# ===========================================================================
@transaction.atomic
def start_work(ticket, user, note=""):
    """IN_PROGRESS. Records first response if this is the first staff action."""
    if ticket.assigned_to_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the assignee can start work.")
    if ticket.state not in (Ticket.ASSIGNED, Ticket.REOPENED, Ticket.AWAITING_RESPONSE):
        raise ValidationError(
            f"Cannot start work from state '{ticket.get_state_display()}'."
        )

    now = timezone.now()
    ticket.state = Ticket.IN_PROGRESS
    if ticket.first_response_at is None:
        ticket.first_response_at = now
    ticket.save(update_fields=["state", "first_response_at", "updated_at"])

    log_activity(
        ticket, "in_progress", actor=user,
        message=note or "Work started.",
    )
    _notify_raiser(ticket, user, f"{user.get_full_name() or user.email} has started work on your ticket.")
    return ticket


@transaction.atomic
def mark_awaiting_response(ticket, user, reason=""):
    """IN_PROGRESS → AWAITING_RESPONSE. Usually the assignee waiting on
    the raiser (or a vendor)."""
    if ticket.assigned_to_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the assignee can pause a ticket.")

    if ticket.state != Ticket.IN_PROGRESS:
        raise ValidationError(
            f"Cannot pause from state '{ticket.get_state_display()}'."
        )

    ticket.state = Ticket.AWAITING_RESPONSE
    ticket.save(update_fields=["state", "updated_at"])

    log_activity(
        ticket, "awaiting_response", actor=user,
        message=reason or "Awaiting response.",
    )
    _notify_raiser(
        ticket, user,
        f"Your reply is needed on {ticket.reference}: {reason or 'see the ticket for details.'}",
    )
    return ticket


@transaction.atomic
def resolve_ticket(ticket, user, resolution_notes="",
                   collection_location="", collection_timeframe=""):
    """IN_PROGRESS/AWAITING → RESOLVED (or AWAITING_DIRECTOR).

    If the category requires director sign-off, the ticket parks in
    AWAITING_DIRECTOR and the director is notified. Otherwise it goes
    straight to RESOLVED and the raiser is emailed + notified.
    """
    if not _can_resolve(ticket, user):
        raise PermissionDenied("You cannot resolve this ticket.")
    if ticket.state not in (
        Ticket.IN_PROGRESS, Ticket.AWAITING_RESPONSE,
        Ticket.ASSIGNED, Ticket.REOPENED,
    ):
        raise ValidationError(
            f"Cannot resolve from state '{ticket.get_state_display()}'."
        )
    notes = (resolution_notes or "").strip()
    if len(notes) < 5:
        raise ValidationError("Resolution notes are required (at least 5 characters).")

    now = timezone.now()
    ticket.resolution_notes = notes
    ticket.collection_location = (collection_location or "").strip()[:255]
    ticket.collection_timeframe = (collection_timeframe or "").strip()[:255]
    ticket.resolved_at = now
    ticket.resolved_by = user
    if ticket.first_response_at is None:
        ticket.first_response_at = now

    requires_signoff = bool(getattr(ticket.category, "requires_director_signoff", False))
    ticket.director_signoff_required = requires_signoff

    if requires_signoff:
        ticket.state = Ticket.AWAITING_DIRECTOR
        ticket.save(update_fields=[
            "state", "resolution_notes", "collection_location",
            "collection_timeframe", "resolved_at", "resolved_by",
            "first_response_at", "director_signoff_required", "updated_at",
        ])
        log_activity(
            ticket, "awaiting_director", actor=user,
            message="Resolution submitted; awaiting director sign-off.",
            metadata={"resolution_notes": notes[:500]},
        )
        _notify_director_signoff_needed(ticket, user)
    else:
        ticket.state = Ticket.RESOLVED
        ticket.save(update_fields=[
            "state", "resolution_notes", "collection_location",
            "collection_timeframe", "resolved_at", "resolved_by",
            "first_response_at", "director_signoff_required", "updated_at",
        ])
        log_activity(
            ticket, "resolved", actor=user,
            message="Ticket marked as resolved.",
            metadata={"resolution_notes": notes[:500]},
        )
        _finalise_resolution(ticket, user)

    return ticket


@transaction.atomic
def director_sign_off(ticket, user, notes=""):
    """Director approves the resolution — moves AWAITING_DIRECTOR → RESOLVED."""
    if not (user.is_superuser or _is_department_head(user, ticket.handling_department_id)):
        raise PermissionDenied(
            "Only the handling department's director can sign off this resolution."
        )
    if ticket.state != Ticket.AWAITING_DIRECTOR:
        raise ValidationError(
            "This ticket is not awaiting director sign-off."
        )

    now = timezone.now()
    ticket.state = Ticket.RESOLVED
    ticket.director_signed_off_by = user
    ticket.director_signed_off_at = now
    ticket.director_signoff_notes = (notes or "").strip()
    ticket.save(update_fields=[
        "state", "director_signed_off_by", "director_signed_off_at",
        "director_signoff_notes", "updated_at",
    ])

    log_activity(
        ticket, "director_signed_off", actor=user,
        message="Director approved the resolution.",
        metadata={"notes": (notes or "")[:500]},
    )
    _finalise_resolution(ticket, user)
    return ticket


def _finalise_resolution(ticket, actor):
    """Called when a ticket lands in RESOLVED. Sends the raiser email +
    in-app notification."""
    sent = False
    try:
        from .emails import send_ticket_resolution_email
        sent = send_ticket_resolution_email(ticket)
    except Exception:
        logger.exception("Resolution email failed for %s", ticket.reference)

    if sent:
        ticket.resolution_email_sent_at = timezone.now()
        ticket.save(update_fields=["resolution_email_sent_at", "updated_at"])
        log_activity(
            ticket, "email_sent", actor=None,
            message="Resolution email sent to the raiser.",
        )

    _notify_raiser(
        ticket, actor,
        f"{ticket.reference} has been resolved. "
        f"{'Check your email for details.' if sent else 'Open the ticket for details.'}",
    )

@transaction.atomic
def close_ticket(ticket, user, *, rating=None, comment=""):
    """RESOLVED → CLOSED. Only the raiser (or superuser). Optional star rating."""
    if ticket.raised_by_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the raiser can close this ticket.")
    if ticket.state not in (Ticket.RESOLVED, Ticket.IN_PROGRESS):
        raise ValidationError(
            f"Cannot close from state '{ticket.get_state_display()}'."
        )

    now = timezone.now()
    ticket.state = Ticket.CLOSED
    ticket.closed_at = now
    if rating is not None:
        try:
            r = int(rating)
            if 1 <= r <= 5:
                ticket.satisfaction_rating = r
        except (TypeError, ValueError):
            pass
    if comment:
        ticket.satisfaction_comment = comment.strip()[:2000]

    ticket.save(update_fields=[
        "state", "closed_at", "satisfaction_rating",
        "satisfaction_comment", "updated_at",
    ])

    log_activity(
        ticket, "closed", actor=user,
        message="Ticket closed by raiser.",
        metadata={
            "rating": ticket.satisfaction_rating,
            "comment": (comment or "")[:200],
        },
    )
    _notify_staff_on_change(ticket, user, "Ticket closed", "closed")
    return ticket


@transaction.atomic
def reopen_ticket(ticket, user, reason=""):
    """RESOLVED/CLOSED → REOPENED. Only the raiser, within the reopen window."""
    if ticket.raised_by_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the raiser can reopen this ticket.")
    if ticket.state not in (Ticket.RESOLVED, Ticket.CLOSED):
        raise ValidationError(
            f"Cannot reopen from state '{ticket.get_state_display()}'."
        )

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please explain why this needs to be reopened.")

    window_days = _cfg_int("tickets.escalation.auto_reopen_window_days", 14)
    if ticket.closed_at and not user.is_superuser:
        elapsed = timezone.now() - ticket.closed_at
        if elapsed.days > window_days:
            raise ValidationError(
                f"This ticket can only be reopened within {window_days} days of closing."
            )

    ticket.state = Ticket.REOPENED
    ticket.reopen_count += 1
    ticket.last_reopened_at = timezone.now()
    ticket.resolved_at = None
    ticket.closed_at = None
    # Recompute resolution SLA from now.
    _, new_resolution_due = _compute_sla_deadlines(
        ticket.category, ticket.priority, timezone.now(),
    )
    ticket.resolution_due_at = new_resolution_due
    ticket.sla_resolution_breached = False
    ticket.save(update_fields=[
        "state", "reopen_count", "last_reopened_at",
        "resolved_at", "closed_at", "resolution_due_at",
        "sla_resolution_breached", "updated_at",
    ])

    log_activity(
        ticket, "reopened", actor=user,
        message=f"Ticket reopened: {reason}",
        metadata={"reopen_count": ticket.reopen_count},
    )
    if ticket.assigned_to_id:
        _notify_assignee(ticket, ticket.assigned_to, user)
    else:
        _notify_new_ticket(ticket)
    return ticket


@transaction.atomic
def cancel_ticket(ticket, user, reason=""):
    """NEW/ASSIGNED → CANCELLED. Raiser or superuser."""
    if ticket.raised_by_id != user.pk and not user.is_superuser:
        raise PermissionDenied("Only the raiser can cancel this ticket.")
    if ticket.state not in (Ticket.NEW, Ticket.ASSIGNED, Ticket.AWAITING_RESPONSE):
        raise ValidationError(
            f"Cannot cancel from state '{ticket.get_state_display()}'."
        )

    ticket.state = Ticket.CANCELLED
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=["state", "closed_at", "updated_at"])

    log_activity(
        ticket, "cancelled", actor=user,
        message=reason or "Cancelled by raiser.",
    )
    if ticket.assigned_to_id:
        _notify_staff_on_change(ticket, user, "Ticket cancelled", "cancelled")
    return ticket


@transaction.atomic
def escalate_ticket(ticket, *, reason="", level=None, actor=None):
    """Bump escalation level. Called by SLA checker or manually by a
    department head."""
    if level is None:
        level = min(ticket.escalation_level + 1, 3)

    ticket.state = Ticket.ESCALATED
    ticket.escalation_level = level
    ticket.escalated_at = timezone.now()
    ticket.save(update_fields=[
        "state", "escalation_level", "escalated_at", "updated_at",
    ])

    log_activity(
        ticket, "escalated", actor=actor,
        message=reason or f"Escalated to level {level}.",
        metadata={"level": level, "reason": reason},
    )
    _notify_escalation(ticket, level, reason)
    return ticket


# ===========================================================================
# Comments
# ===========================================================================
@transaction.atomic
def add_public_comment(ticket, user, body):
    """Visible to raiser + handling staff."""
    if not _can_comment(ticket, user, internal=False):
        raise PermissionDenied("You cannot comment on this ticket.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("Comment cannot be empty.")

    comment = TicketComment.objects.create(
        ticket=ticket, author=user, body=body, is_internal=False,
    )

    # First response clock stops on the first *staff* reply.
    if (
        ticket.first_response_at is None
        and user.pk != ticket.raised_by_id
        and ticket.assigned_to_id == user.pk
    ):
        ticket.first_response_at = timezone.now()
        ticket.save(update_fields=["first_response_at", "updated_at"])

    log_activity(
        ticket, "commented", actor=user,
        message=body[:200],
        metadata={"internal": False, "comment_id": comment.pk},
    )

    # Notify the other side.
    if user.pk == ticket.raised_by_id:
        # Raiser commented → notify assignee (or handling department).
        if ticket.assigned_to_id and ticket.assigned_to_id != user.pk:
            _notify_assignee(ticket, ticket.assigned_to, user)
    else:
        # Staff commented → notify raiser.
        _notify_raiser(ticket, user, f"New reply on {ticket.reference}.")
    return comment


@transaction.atomic
def add_internal_comment(ticket, user, body):
    """Staff-only note. Raiser never sees it."""
    if not _can_comment(ticket, user, internal=True):
        raise PermissionDenied("Only staff can add internal notes.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("Comment cannot be empty.")

    comment = TicketComment.objects.create(
        ticket=ticket, author=user, body=body, is_internal=True,
    )
    log_activity(
        ticket, "internal_note", actor=user,
        message=body[:200],
        metadata={"internal": True, "comment_id": comment.pk},
    )
    return comment


# ===========================================================================
# Attachments
# ===========================================================================
@transaction.atomic
def add_attachment(ticket, user, file, description=""):
    """Attach a file. Raiser can attach at any time while the ticket is
    open; staff can attach throughout."""
    if ticket.is_closed and user.pk != ticket.raised_by_id and not user.is_superuser:
        raise PermissionDenied("This ticket is closed. No new attachments.")
    if user.pk != ticket.raised_by_id and not _is_staff_on_ticket(ticket, user):
        raise PermissionDenied("You cannot attach files to this ticket.")

    attachment = TicketAttachment.objects.create(
        ticket=ticket,
        uploaded_by=user,
        file=file,
        original_name=getattr(file, "name", "") or "",
        description=(description or "").strip()[:255],
    )
    log_activity(
        ticket, "attached", actor=user,
        message=f"Attached {attachment.original_name}",
        metadata={"attachment_id": attachment.pk},
    )
    return attachment


# ===========================================================================
# Deletion (comments + attachments)
# ===========================================================================
def _can_delete_content(actor, owner, ticket):
    """Author, handling-department head, or superuser."""
    if actor is None or owner is None:
        return False
    if actor.is_superuser:
        return True
    if actor.pk == owner.pk:
        return True
    return _is_department_head(actor, ticket.handling_department_id)


@transaction.atomic
def delete_comment(ticket, user, comment):
    if not _can_delete_content(user, comment.author, ticket):
        raise PermissionDenied("You cannot delete this comment.")
    log_activity(
        ticket, "comment_deleted", actor=user,
        message=f"Comment deleted by {user.get_full_name() or user.email}.",
        metadata={
            "comment_id": comment.pk,
            "author_id": comment.author_id,
            "body_excerpt": (comment.body or "")[:120],
        },
    )
    comment.delete()


@transaction.atomic
def delete_attachment(ticket, user, attachment):
    if not _can_delete_content(user, attachment.uploaded_by, ticket):
        raise PermissionDenied("You cannot delete this attachment.")
    log_activity(
        ticket, "attachment_deleted", actor=user,
        message=f"Attachment '{attachment.original_name}' deleted by "
                f"{user.get_full_name() or user.email}.",
        metadata={
            "attachment_id": attachment.pk,
            "uploader_id": attachment.uploaded_by_id,
            "original_name": attachment.original_name,
        },
    )
    try:
        attachment.file.delete(save=False)
    except Exception:
        logger.exception("Could not delete file for attachment %s", attachment.pk)
    attachment.delete()


# ===========================================================================
# SLA checker (Celery Beat)
# ===========================================================================
def sla_check_task():
    """Scan open tickets and act on SLA state.

    For each open ticket:
      - If response SLA has passed with no first response and not yet
        flagged: mark breached, escalate to level 1.
      - If resolution SLA is past 75% and no escalation yet: escalate 1.
      - If resolution SLA has passed and not yet flagged: mark breached,
        escalate to level 2 (or 3 if already 2).

    Idempotent — safe to run frequently. Never raises past the outer
    loop so one bad ticket doesn't block the rest.
    """
    now = timezone.now()
    open_qs = Ticket.objects.filter(state__in=Ticket.OPEN_STATES)

    response_breaches = 0
    resolution_approaching = 0
    resolution_breaches = 0

    for ticket in open_qs.iterator():
        try:
            # Response breach
            if (
                ticket.response_due_at
                and not ticket.first_response_at
                and not ticket.sla_response_breached
                and now > ticket.response_due_at
            ):
                ticket.sla_response_breached = True
                ticket.save(update_fields=["sla_response_breached", "updated_at"])
                escalate_ticket(
                    ticket, reason="Response SLA breached.",
                    level=1, actor=None,
                )
                response_breaches += 1
                continue  # escalation already saved

            # Resolution breach
            if (
                ticket.resolution_due_at
                and not ticket.sla_resolution_breached
                and not ticket.is_closed
                and now > ticket.resolution_due_at
            ):
                ticket.sla_resolution_breached = True
                ticket.save(update_fields=["sla_resolution_breached", "updated_at"])
                next_level = min(ticket.escalation_level + 1, 3) or 2
                escalate_ticket(
                    ticket, reason="Resolution SLA breached.",
                    level=next_level, actor=None,
                )
                resolution_breaches += 1
                continue

            # Approaching resolution breach (>= 75% of window)
            if (
                ticket.resolution_due_at
                and not ticket.sla_resolution_breached
                and not ticket.is_closed
                and ticket.escalation_level == 0
                and ticket.state not in (Ticket.ESCALATED,)
            ):
                total = (ticket.resolution_due_at - ticket.created_at).total_seconds()
                elapsed = (now - ticket.created_at).total_seconds()
                if total > 0 and (elapsed / total) >= 0.75:
                    escalate_ticket(
                        ticket,
                        reason="Approaching resolution SLA.",
                        level=1, actor=None,
                    )
                    resolution_approaching += 1
        except Exception:
            logger.exception(
                "SLA check failed for ticket %s (pk=%s)",
                ticket.reference, ticket.pk,
            )

    logger.info(
        "SLA check complete: response_breaches=%s, resolution_approaching=%s, "
        "resolution_breaches=%s",
        response_breaches, resolution_approaching, resolution_breaches,
    )
    return {
        "response_breaches": response_breaches,
        "resolution_approaching": resolution_approaching,
        "resolution_breaches": resolution_breaches,
    }


# ===========================================================================
# Notifications
# ===========================================================================
def _notify_new_ticket(ticket):
    """Ping the handling department's team about a new ticket.

    Recipients: every active user in `handling_department` holding a
    director / deputy / office-head role. If none exist, notify admins.
    """
    from accounts.models import User
    recipients = list(
        User.objects.filter(
            is_active=True,
            department_id=ticket.handling_department_id,
            role__code__in=(
                "director", "deputy-director",
                "office-head-admin", "office-head-ts", "office-head-dev",
            ),
        )
    )
    for user in recipients:
        try:
            notify(
                recipient=user,
                title=f"New ticket — {ticket.reference}",
                body=f"{ticket.subject} ({ticket.get_priority_display()})",
                url=f"/tickets/{ticket.pk}/",
                category=Notification.REQUEST,
                level=(
                    Notification.WARNING
                    if ticket.priority in (Ticket.URGENT, Ticket.HIGH)
                    else Notification.INFO
                ),
                actor=ticket.raised_by,
            )
        except Exception:
            logger.exception("Could not notify %s of new ticket", user.pk)


def _notify_assignee(ticket, assignee, assigner):
    if not assignee or not assignee.is_active:
        return
    try:
        notify(
            recipient=assignee,
            title=f"You've been assigned — {ticket.reference}",
            body=ticket.subject,
            url=f"/tickets/{ticket.pk}/",
            category=Notification.REQUEST,
            level=Notification.INFO,
            actor=assigner,
        )
    except Exception:
        logger.exception("Could not notify assignee %s", assignee.pk)


def _notify_raiser(ticket, actor, message):
    if not ticket.raised_by_id:
        return
    try:
        notify(
            recipient=ticket.raised_by,
            title=f"Update on {ticket.reference}",
            body=message,
            url=f"/tickets/{ticket.pk}/",
            category=Notification.REQUEST,
            level=Notification.INFO,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify raiser %s", ticket.raised_by_id)


def _notify_staff_on_change(ticket, actor, title, kind):
    """Notify the assignee (if any) and the handling department's heads."""
    from accounts.models import User
    recipients = set()
    if ticket.assigned_to_id and ticket.assigned_to_id != (actor.pk if actor else None):
        recipients.add(ticket.assigned_to_id)
    heads = User.objects.filter(
        is_active=True,
        department_id=ticket.handling_department_id,
        role__code__in=("director", "deputy-director"),
    ).values_list("pk", flat=True)
    recipients.update(heads)
    if actor is not None:
        recipients.discard(actor.pk)

    for uid in recipients:
        recipient = User.objects.filter(pk=uid, is_active=True).first()
        if recipient is None:
            continue
        try:
            notify(
                recipient=recipient,
                title=f"{title} — {ticket.reference}",
                body=ticket.subject,
                url=f"/tickets/{ticket.pk}/",
                category=Notification.REQUEST,
                level=Notification.INFO,
                actor=actor,
            )
        except Exception:
            logger.exception("Could not notify %s of %s", uid, kind)


def _notify_escalation(ticket, level, reason):
    """Escalation notifications:
       1 — assignee + department heads
       2 — deputy director of handling department
       3 — director of handling department
    """
    from accounts.models import User

    targets = set()
    dept_id = ticket.handling_department_id

    if level >= 1:
        if ticket.assigned_to_id:
            targets.add(ticket.assigned_to_id)
        targets.update(
            User.objects.filter(
                is_active=True,
                department_id=dept_id,
                role__code__in=("office-head-admin", "office-head-ts", "office-head-dev"),
            ).values_list("pk", flat=True)
        )
    if level >= 2:
        targets.update(
            User.objects.filter(
                is_active=True,
                department_id=dept_id,
                role__code="deputy-director",
            ).values_list("pk", flat=True)
        )
    if level >= 3:
        targets.update(
            User.objects.filter(
                is_active=True,
                department_id=dept_id,
                role__code="director",
            ).values_list("pk", flat=True)
        )

    for uid in targets:
        recipient = User.objects.filter(pk=uid, is_active=True).first()
        if recipient is None:
            continue
        try:
            notify(
                recipient=recipient,
                title=f"Ticket escalated (L{level}) — {ticket.reference}",
                body=reason or ticket.subject,
                url=f"/tickets/{ticket.pk}/",
                category=Notification.REQUEST,
                level=Notification.ERROR if level >= 2 else Notification.WARNING,
                actor=None,
            )
        except Exception:
            logger.exception("Could not notify %s of escalation", uid)
            
            
# ===========================================================================
# Dashboard helpers
# ===========================================================================
def open_counts_for(user):
    """Return a dict of open ticket counts for a user.

    Used by the home dashboard and the sidebar badge — one query per
    bucket, all scoped to the given user.
    """
    if not user or not user.is_authenticated:
        return {"raised": 0, "assigned": 0}

    return {
        "raised": (
            Ticket.objects
            .filter(raised_by=user)
            .exclude(state__in=Ticket.CLOSED_STATES)
            .count()
        ),
        "assigned": (
            Ticket.objects
            .filter(assigned_to=user)
            .exclude(state__in=Ticket.CLOSED_STATES)
            .count()
        ),
    }            
            
            
def _notify_director_signoff_needed(ticket, actor):
    """Ping the handling department's director (or deputy) that a
    resolution needs sign-off."""
    from accounts.models import User
    recipient = (
        User.objects
        .filter(
            is_active=True,
            department_id=ticket.handling_department_id,
            role__code__in=("director", "deputy-director"),
        )
        .order_by("pk")
        .first()
    )
    if recipient is None:
        # No director — fall through to superusers
        for admin in User.objects.filter(is_active=True, is_superuser=True):
            try:
                notify(
                    recipient=admin,
                    title=f"Sign-off needed — {ticket.reference}",
                    body=f"{ticket.subject} (no director configured)",
                    url=f"/tickets/{ticket.pk}/",
                    category=Notification.REQUEST,
                    level=Notification.WARNING,
                    actor=actor,
                )
            except Exception:
                logger.exception("Could not notify superuser %s", admin.pk)
        return

    try:
        notify(
            recipient=recipient,
            title=f"Sign-off needed — {ticket.reference}",
            body=f"{ticket.subject} (resolved by {actor.get_full_name() or actor.email})",
            url=f"/tickets/{ticket.pk}/",
            category=Notification.REQUEST,
            level=Notification.WARNING,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify director %s", recipient.pk)            
            
            
            