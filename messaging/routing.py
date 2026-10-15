"""Memo routing engine.

This is to memos what `workflow.services` is to requests: the runtime
that resolves who approves what, tracks position through the chain,
and executes the transitions.

Public API:
    route_for_memo(memo) -> MemoRoute | None
    build_memo_chain(memo) -> list[MemoRouteStep]
    resolve_memo_approver(step, memo) -> User | None
    current_step(memo) -> MemoRouteStep | None
    start_chain(memo, *, actor=None) -> None
    advance_chain(memo, from_position) -> None
    approve_step(memo, actor, comment="") -> Memo
    return_to_sender(memo, actor, comment="") -> Memo
    reject_memo(memo, actor, comment="") -> Memo
    publish_memo(memo, *, actor=None) -> Memo
    can_act_on_memo(memo, user) -> bool
    actor_for_memo(memo) -> User | None
    resolve_memo_audience(memo) -> QuerySet[User]

Rank rules (mirrors workflow.services._advance_to_next_actor):
    - Skip step if actor is the sender (no self-approval).
    - Skip step if actor is strictly junior to sender (is_hierarchical only).
    - Same-rank behaviour driven by route.same_rank_behaviour.
    - Skip step if is_optional and no actor exists.
    - Park the memo if a required step has no actor (alert admins).
    - When the chain is exhausted, publish.
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from notifications.models import Notification
from notifications.services import notify

logger = logging.getLogger(__name__)


# ===========================================================================
# Rank helpers
# ===========================================================================
def _rank_of(user):
    """Effective seniority of a user. Superuser outranks everyone."""
    if not user or not user.is_authenticated:
        return 0
    if user.is_superuser:
        return 1000
    if user.role_id:
        return user.role.rank or 0
    return 0


def _role_code(user):
    return user.role.code if (user and user.role_id) else ""


# ===========================================================================
# Step keys — survive step reordering
# ===========================================================================
def step_key(step):
    if step is None or not step.pk:
        return ""
    return f"db:{step.pk}"


# ===========================================================================
# Route resolution
# ===========================================================================
def route_for_memo(memo):
    """Pick the route that applies to this memo.

    Matching, in order of precedence:
      1. Rule's `sender_role_codes` includes the sender's role code
         (empty = any).
      2. Rule's `target_kind` equals the memo's visibility.
      3. If target_kind = 'specific_user', rule's `target_role_codes` is
         empty OR includes the target user's role code.

    Returns the route of the highest-priority match, or None if no rule
    matches (memo publishes directly).
    """
    from .models import MemoRouteRule

    if not memo.sender_id:
        return None

    sender_code = _role_code(memo.sender)
    target_code = ""
    if memo.visibility == memo.SPECIFIC_USER and memo.target_user_id:
        target_code = _role_code(memo.target_user)

    rules = (
        MemoRouteRule.objects
        .filter(is_active=True, target_kind=memo.visibility)
        .select_related("route")
        .order_by("-priority", "-updated_at")
    )

    for rule in rules:
        allowed_senders = rule.sender_role_codes or []
        if allowed_senders and sender_code not in allowed_senders:
            continue

        if memo.visibility == memo.SPECIFIC_USER:
            allowed_targets = rule.target_role_codes or []
            if allowed_targets and target_code not in allowed_targets:
                continue

        if rule.route and rule.route.is_active:
            return rule.route

    return None


# ===========================================================================
# Chain building
# ===========================================================================
def build_memo_chain(memo):
    """Return the ordered list of steps for this memo's route.

    If the memo has no route, returns [].
    """
    if memo.route_id is None:
        return []
    return list(
        memo.route.steps
        .select_related("approver_role", "approver_user")
        .order_by("position", "pk")
    )


# ===========================================================================
# Approver resolution
# ===========================================================================
def _find_user_with_role_in_department(role_code, department):
    """First active user with role=role_code in the given department."""
    if not role_code or department is None:
        return None
    from accounts.models import User
    return (
        User.objects
        .filter(is_active=True, role__code=role_code, department=department)
        .order_by("pk")
        .first()
    )


def resolve_memo_approver(step, memo):
    """Who should act at this step for this memo? None if no one matches."""
    from accounts.models import User

    sender = memo.sender
    sender_dept = sender.department if sender else None

    kind = step.approver_kind

    if kind == step.APPROVER_SPECIFIC:
        return (
            step.approver_user
            if (step.approver_user_id and step.approver_user.is_active)
            else None
        )

    if kind == step.APPROVER_ES:
        return (
            User.objects
            .filter(is_active=True, role__code="executive-secretary")
            .order_by("pk")
            .first()
        )

    if kind == step.APPROVER_DIVISION_HEAD:
        return _find_user_with_role_in_department("division-head", sender_dept)

    if kind == step.APPROVER_DIRECTOR:
        return _find_user_with_role_in_department("director", sender_dept)

    if kind == step.APPROVER_DEPUTY:
        return _find_user_with_role_in_department("deputy-director", sender_dept)

    if kind == step.APPROVER_ROLE_IN_DEPT:
        # Scope determines department.
        scope = (step.scope or "requester").strip().lower()
        if scope == "requester":
            dept = sender_dept
        elif scope == "receiving":
            # Not really applicable to memos, but supported for symmetry.
            dept = memo.department
        elif scope == "es":
            dept = None
        else:
            from staff.models import Department
            dept = Department.objects.filter(code=scope).first()

        if step.approver_role_id:
            return _find_user_with_role_in_department(
                step.approver_role.code, dept,
            )
        return None

    return None


# ===========================================================================
# Current step tracking
# ===========================================================================
def current_step(memo):
    """The step the memo is currently parked at, or None."""
    if not memo.current_step_key:
        return None
    for s in build_memo_chain(memo):
        if step_key(s) == memo.current_step_key:
            return s
    return None


def _set_current_step(memo, step):
    memo.current_step = step if (step and step.pk) else None
    memo.current_step_key = step_key(step) if step else ""
    memo.current_step_started_at = timezone.now()
    memo.save(update_fields=[
        "current_step", "current_step_key", "current_step_started_at",
        "updated_at",
    ])


def _clear_current_step(memo):
    memo.current_step = None
    memo.current_step_key = ""
    memo.current_step_started_at = None
    memo.save(update_fields=[
        "current_step", "current_step_key", "current_step_started_at",
        "updated_at",
    ])


def actor_for_memo(memo):
    """Resolve the user who should act on the memo right now, or None."""
    step = current_step(memo)
    if step is None:
        return None
    return resolve_memo_approver(step, memo)


# ===========================================================================
# Activity helper
# ===========================================================================
def _log(memo, kind, actor=None, message="", metadata=None):
    """Small structured log on the memo. Reuses RequestActivity semantics
    but writes to a private history list on the memo. Kept inline for
    simplicity — no new model.
    """
    # No dedicated log model for memos yet; we surface key events via
    # notifications and MemoApproval rows. This hook exists so a future
    # audit table can drop in cleanly.
    logger.info(
        "[memo %s] %s — %s",
        memo.reference, kind, message or "",
    )


# ===========================================================================
# Notifications
# ===========================================================================
def _notify_actor(memo, step, actor):
    if actor is None:
        return
    try:
        notify(
            recipient=actor,
            title=f"Memo awaiting your approval — {memo.reference}",
            body=f"{memo.subject} · step: {step.name}",
            url=f"/messaging/memos/{memo.pk}/",
            category=Notification.MESSAGE,
            level=Notification.WARNING,
            actor=memo.sender,
        )
    except Exception:
        logger.exception("Could not notify memo actor %s", actor.pk)


def _notify_sender_returned(memo, actor, comment):
    try:
        notify(
            recipient=memo.sender,
            title=f"Memo returned for revision — {memo.reference}",
            body=comment or "Please review and resubmit.",
            url=f"/messaging/memos/{memo.pk}/",
            category=Notification.MESSAGE,
            level=Notification.WARNING,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify memo sender of return")


def _notify_sender_rejected(memo, actor, comment):
    try:
        notify(
            recipient=memo.sender,
            title=f"Memo rejected — {memo.reference}",
            body=comment or "Your memo was rejected.",
            url=f"/messaging/memos/{memo.pk}/",
            category=Notification.MESSAGE,
            level=Notification.ERROR,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify memo sender of rejection")


def _notify_admins_no_actor(memo, step):
    from accounts.models import User
    for admin in User.objects.filter(is_active=True, is_superuser=True):
        try:
            notify(
                recipient=admin,
                title=f"No one matches a memo step — {memo.reference}",
                body=f"Step '{step.name}' has no acting user. Configure a role or user.",
                url=f"/messaging/memos/{memo.pk}/",
                category=Notification.SYSTEM,
                level=Notification.ERROR,
            )
        except Exception:
            logger.exception("Could not notify admin %s", admin.pk)


# ===========================================================================
# Audience resolution (for publishing)
# ===========================================================================
def resolve_memo_audience(memo):
    """Return a User queryset — the final recipients of a published memo.

    The sender is always excluded from the audience.
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()

    base = User.objects.filter(is_active=True)

    if memo.sender_id:
        base = base.exclude(pk=memo.sender_id)

    if memo.visibility == memo.SPECIFIC_USER:
        if not memo.target_user_id:
            return User.objects.none()
        return base.filter(pk=memo.target_user_id).distinct()

    if memo.visibility == memo.ALL_STAFF:
        return base.distinct()

    if memo.visibility == memo.ALL_DIRECTORS:
        return base.filter(role__code="director").distinct()

    if memo.visibility == memo.DEPARTMENT:
        if not memo.department_id:
            return User.objects.none()
        return base.filter(department_id=memo.department_id).distinct()

    if memo.visibility == memo.ROLE:
        group_ids = list(memo.roles.values_list("pk", flat=True))
        if not group_ids:
            return User.objects.none()
        return base.filter(groups__in=group_ids).distinct()

    if memo.visibility == memo.INDIVIDUALS:
        return base.filter(
            pk__in=memo.recipients.values_list("pk", flat=True),
        ).distinct()

    return User.objects.none()


# ===========================================================================
# Chain lifecycle
# ===========================================================================
@transaction.atomic
def start_chain(memo, *, actor=None):
    """Attach the route (if not already attached) and advance to the
    first actionable step. Publishes immediately if no route or chain
    is empty after skips.
    """
    if memo.route_id is None:
        memo.route = route_for_memo(memo)
        if memo.route_id is not None:
            memo.save(update_fields=["route", "updated_at"])

    if memo.route_id is None:
        # No route — direct publish.
        return publish_memo(memo, actor=actor or memo.sender)

    memo.status = memo.AWAITING
    memo.save(update_fields=["status", "is_draft", "updated_at"])

    advance_chain(memo, from_position=-1)
    return memo


@transaction.atomic
def advance_chain(memo, from_position):
    """Move the chain forward from `from_position`. Skips steps
    according to the rank and optional rules. Publishes if exhausted.
    """
    steps = build_memo_chain(memo)
    idx = from_position + 1

    sender_rank = _rank_of(memo.sender)
    same_rank_mode = (
        memo.route.same_rank_behaviour if memo.route_id else "skip"
    )

    while idx < len(steps):
        step = steps[idx]
        actor = resolve_memo_approver(step, memo)

        # 1. Optional step, no actor → skip.
        if actor is None and step.is_optional:
            _log(memo, "skipped_step",
                 message=f"Skipped optional step '{step.name}' (no actor).")
            idx += 1
            continue

        # 2. Required step, no actor → park and alert admins.
        if actor is None:
            _set_current_step(memo, step)
            memo.status = memo.AWAITING
            memo.save(update_fields=["status", "is_draft", "updated_at"])
            _log(memo, "parked_no_actor",
                 message=f"Waiting for someone to fill '{step.name}'.")
            _notify_admins_no_actor(memo, step)
            return memo

        # 3. Self-approval — never allowed.
        if actor.pk == memo.sender_id:
            _log(memo, "skipped_step",
                 message=f"Skipped '{step.name}' (sender is the actor).")
            idx += 1
            continue

        # 4. Rank-aware skips (only for hierarchical steps).
        if step.is_hierarchical:
            actor_rank = _rank_of(actor)

            if actor_rank < sender_rank:
                _log(memo, "skipped_step",
                     message=(
                         f"Skipped '{step.name}' "
                         f"({actor.get_full_name() or actor.email} "
                         f"is junior to the sender)."
                     ))
                idx += 1
                continue

            if actor_rank == sender_rank and same_rank_mode == "skip":
                _log(memo, "skipped_step",
                     message=(
                         f"Skipped '{step.name}' (same rank as sender; "
                         f"route behaviour = skip)."
                     ))
                idx += 1
                continue

        # 5. We have an actionable actor.
        _set_current_step(memo, step)
        memo.status = memo.AWAITING
        memo.save(update_fields=["status", "is_draft", "updated_at"])
        _notify_actor(memo, step, actor)
        return memo

    # Chain exhausted → publish.
    return publish_memo(memo, actor=memo.sender)


@transaction.atomic
def approve_step(memo, actor, comment=""):
    """Record an approval, then advance."""
    from .models import MemoApproval

    step = current_step(memo)
    if step is None:
        raise ValidationError("This memo is not awaiting an approval.")
    resolved = resolve_memo_approver(step, memo)
    if resolved is None or (resolved.pk != actor.pk and not actor.is_superuser):
        raise PermissionDenied("You are not the acting user for this step.")

    now = timezone.now()
    duration = 0
    if memo.current_step_started_at:
        duration = int((now - memo.current_step_started_at).total_seconds())

    MemoApproval.objects.create(
        memo=memo, step=step,
        actor=actor,
        actor_role=actor.role.name if actor.role_id else "",
        decision=MemoApproval.APPROVE,
        comment=comment or "",
        duration_seconds=duration,
    )

    _log(memo, "approved",
         actor=actor, message=f"Approved step '{step.name}'.")

    # Find where we are in the chain and advance past it.
    steps = build_memo_chain(memo)
    current_idx = -1
    key = step_key(step)
    for i, s in enumerate(steps):
        if step_key(s) == key:
            current_idx = i
            break

    return advance_chain(memo, from_position=current_idx)


@transaction.atomic
def return_to_sender(memo, actor, comment=""):
    """Send the memo back to the sender for revision. The chain is
    reset so that on resubmit it starts from the beginning (per your
    design — a resubmitted memo walks the chain fresh)."""
    from .models import MemoApproval

    step = current_step(memo)
    if step is None:
        raise ValidationError("This memo is not awaiting an approval.")
    resolved = resolve_memo_approver(step, memo)
    if resolved is None or (resolved.pk != actor.pk and not actor.is_superuser):
        raise PermissionDenied("You are not the acting user for this step.")

    now = timezone.now()
    duration = 0
    if memo.current_step_started_at:
        duration = int((now - memo.current_step_started_at).total_seconds())

    MemoApproval.objects.create(
        memo=memo, step=step,
        actor=actor,
        actor_role=actor.role.name if actor.role_id else "",
        decision=MemoApproval.RETURN,
        comment=comment or "",
        duration_seconds=duration,
    )

    memo.status = memo.RETURNED
    memo.returned_by = actor
    memo.returned_from_step_key = step_key(step)
    memo.returned_at = now
    memo.returned_comment = comment or ""
    _clear_current_step(memo)
    memo.save(update_fields=[
        "status", "is_draft",
        "returned_by", "returned_from_step_key", "returned_at",
        "returned_comment", "updated_at",
    ])

    _log(memo, "returned",
         actor=actor, message=f"Returned from step '{step.name}'.")
    _notify_sender_returned(memo, actor, comment)
    return memo


@transaction.atomic
def reject_memo(memo, actor, comment=""):
    """Terminate the memo. Rejection is final."""
    from .models import MemoApproval

    if not (comment or "").strip():
        raise ValidationError("A comment is required when rejecting a memo.")

    step = current_step(memo)
    if step is None:
        raise ValidationError("This memo is not awaiting an approval.")
    resolved = resolve_memo_approver(step, memo)
    if resolved is None or (resolved.pk != actor.pk and not actor.is_superuser):
        raise PermissionDenied("You are not the acting user for this step.")

    now = timezone.now()
    duration = 0
    if memo.current_step_started_at:
        duration = int((now - memo.current_step_started_at).total_seconds())

    MemoApproval.objects.create(
        memo=memo, step=step,
        actor=actor,
        actor_role=actor.role.name if actor.role_id else "",
        decision=MemoApproval.REJECT,
        comment=comment,
        duration_seconds=duration,
    )

    memo.status = memo.REJECTED
    memo.rejected_by = actor
    memo.rejected_at = now
    memo.rejection_comment = comment
    _clear_current_step(memo)
    memo.save(update_fields=[
        "status", "is_draft",
        "rejected_by", "rejected_at", "rejection_comment", "updated_at",
    ])

    _log(memo, "rejected",
         actor=actor, message=f"Rejected at step '{step.name}'.")
    _notify_sender_rejected(memo, actor, comment)
    return memo


@transaction.atomic
def publish_memo(memo, *, actor=None):
    """Deliver the memo to its audience.

    Idempotent — safe to call twice. Materializes `MemoRecipient` rows
    and sends an in-app notification to each recipient.
    """
    from .models import MemoRecipient

    if memo.status == memo.PUBLISHED:
        return memo

    now = timezone.now()
    memo.status = memo.PUBLISHED
    memo.published_at = now
    memo.is_draft = False
    _clear_current_step(memo)
    memo.save(update_fields=[
        "status", "is_draft", "published_at", "updated_at",
    ])

    audience = list(resolve_memo_audience(memo))
    for user in audience:
        MemoRecipient.objects.get_or_create(
            memo=memo, user=user,
            defaults={"delivered_at": now},
        )

    # Notify recipients.
    url = f"/messaging/memos/{memo.pk}/"
    level = (
        Notification.WARNING
        if memo.priority in (memo.HIGH, memo.URGENT)
        else Notification.INFO
    )
    title = f"New memo — {memo.reference}"
    if memo.priority == memo.URGENT:
        title = f"URGENT memo — {memo.reference}"
    body = memo.subject
    if memo.requires_acknowledgment:
        body += " · Acknowledgment required"

    for user in audience:
        try:
            notify(
                recipient=user,
                title=title,
                body=body,
                url=url,
                category=Notification.MESSAGE,
                level=level,
                actor=actor or memo.sender,
                metadata={"memo_id": memo.pk, "reference": memo.reference},
            )
        except Exception:
            logger.exception("Memo notify failed for user %s", user.pk)

    _log(memo, "published",
         actor=actor,
         message=f"Delivered to {len(audience)} recipient(s).")
    return memo


@transaction.atomic
def resubmit_memo(memo, actor, *, subject=None, body=None, comment=""):
    """Sender revises a returned memo and sends it back through the chain.

    The chain is reset — resubmission walks it fresh from position -1,
    applying current rank and optional rules.
    """
    if memo.sender_id != actor.pk and not actor.is_superuser:
        raise PermissionDenied("Only the sender can resubmit this memo.")
    if memo.status != memo.RETURNED:
        raise ValidationError("Only returned memos can be resubmitted.")

    if subject is not None and subject.strip():
        memo.subject = subject.strip()
    if body is not None and body.strip():
        memo.body = body.strip()

    memo.status = memo.AWAITING
    memo.returned_by = None
    memo.returned_from_step_key = ""
    memo.returned_at = None
    memo.returned_comment = ""
    memo.resubmitted_count += 1
    memo.save(update_fields=[
        "subject", "body", "status", "is_draft",
        "returned_by", "returned_from_step_key", "returned_at",
        "returned_comment", "resubmitted_count", "updated_at",
    ])

    _log(memo, "resubmitted", actor=actor,
         message=f"Resubmitted (revision #{memo.resubmitted_count}).")

    advance_chain(memo, from_position=-1)
    return memo


# ===========================================================================
# Permission helpers
# ===========================================================================
def can_act_on_memo(memo, user):
    """True if `user` is the current step's actor."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return memo.status == memo.AWAITING
    step = current_step(memo)
    if step is None:
        return False
    actor = resolve_memo_approver(step, memo)
    return actor is not None and actor.pk == user.pk