"""Memo service layer — the only place memo state changes.

Public API:
    generate_reference()                 -> MEMO-YYYY-NNNNN
    create_memo(...)                     -> Memo (draft)
    update_memo(memo, actor, **fields)   -> Memo
    publish_memo(memo, actor)            -> Memo (delegates to routing)
    acknowledge_memo(memo, actor)        -> MemoRecipient
    mark_memo_read(memo, actor)          -> MemoRecipient
    archive_memo(memo, actor)            -> MemoRecipient
    unarchive_memo(memo, actor)          -> MemoRecipient
    cancel_memo(memo, actor, reason)     -> Memo
    delete_memo(memo, actor)             -> None (hard delete)
    add_attachment(memo, actor, file)    -> MemoAttachment
    remove_attachment(memo, actor, att)  -> None
    visible_memos_for(user)              -> queryset
    inbox_memos_for(user)                -> queryset
    awaiting_memos_for(user)             -> queryset
    unread_memo_count_for(user)          -> int
    pending_ack_count_for(user)          -> int
    can_compose_memo(user)               -> bool
    can_broadcast_memo(user)             -> bool
    can_manage_memo(memo, user)          -> bool
    can_view_memo(memo, user)            -> bool
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ===========================================================================
# Reference allocation
# ===========================================================================
@transaction.atomic
def generate_reference():
    """Allocate MEMO-YYYY-NNNNN. Retries safe under concurrency."""
    from datetime import date
    from .models import Memo

    year = date.today().year
    prefix = f"MEMO-{year}-"
    last = (
        Memo.objects
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
# Permissions
# ===========================================================================
_COMPOSE_ROLE_CODES = {
    "super-admin",
    "executive-secretary",
    "director",
    "deputy-director",
    "office-head-admin",
    "office-head-ts",
    "office-head-dev",
    "division-head",
}


def can_compose_memo(user):
    """Who may draft memos.

    With the routing system in place, everyone can compose. The chain
    a memo walks — resolved from the routing rules — decides whether
    it needs approval before delivery. Broadcast targets (All Staff,
    All Directors, whole Department) remain gated separately, by
    `can_broadcast_memo`.

    Kept as a function (rather than dropped entirely) so callers don't
    need to change, and so a future policy can re-tighten this in one
    place if the directorate ever wants that.
    """
    return bool(user and user.is_authenticated)


def can_broadcast_memo(user):
    """Who may address a memo to All Staff / All Directors / Department."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.has_perm("messaging.send_memo_broadcast"):
        return True
    # The ES and Super Admin always have it.
    if user.role_id and user.role.code in {"super-admin", "executive-secretary"}:
        return True
    return False


def can_manage_memo(memo, user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if memo.sender_id == user.pk:
        return True
    return user.has_perm("messaging.manage_memo")


def can_view_memo(memo, user):
    """Recipients, sender, current approver, anyone who acted, superusers."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if memo.sender_id == user.pk:
        return True

    # Recipients of a published memo.
    if memo.status == memo.PUBLISHED:
        if memo.memo_recipients.filter(user=user).exists():
            return True

    # Current approver.
    if memo.status == memo.AWAITING:
        from . import routing
        step = routing.current_step(memo)
        if step is not None:
            actor = routing.resolve_memo_approver(step, memo)
            if actor and actor.pk == user.pk:
                return True

    # Past approvers on this memo.
    if memo.approvals.filter(actor=user).exists():
        return True

    return False


# ===========================================================================
# Visibility scope
# ===========================================================================
def visible_memos_for(user):
    """Memos this user can list or search.

    Includes:
      - Memos they sent
      - Published memos delivered to them
      - Memos they've previously acted on
      - Memos currently awaiting their approval (computed, not SQL-expressible)
    """
    from django.db.models import Q
    from .models import Memo

    if not user or not user.is_authenticated:
        return Memo.objects.none()

    base = Memo.objects.select_related(
        "sender", "department", "route", "target_user", "current_step",
    )

    if user.is_superuser:
        return base

    sql_q = (
        Q(sender=user)
        | Q(memo_recipients__user=user)
        | Q(approvals__actor=user)
    )

    # Memos where the user is the *current* approver but hasn't acted yet.
    # `current_step` is a JSON-keyed reference, so we resolve those in
    # Python and fold the resulting PKs back into the same Q — avoids
    # OR-ing two querysets, which Django forbids when only one has
    # .distinct() applied.
    awaiting_ids = [m.pk for m in awaiting_memos_for(user)]
    if awaiting_ids:
        sql_q |= Q(pk__in=awaiting_ids)

    return base.filter(sql_q).distinct()


def awaiting_memos_for(user):
    """Memos where the user is the current actor."""
    from .models import Memo

    if not user or not user.is_authenticated:
        return []

    from . import routing

    candidates = (
        Memo.objects
        .select_related(
            "sender", "department", "route", "current_step", "target_user",
        )
        .filter(status=Memo.AWAITING)
        .order_by("-updated_at")[:200]
    )
    out = []
    for memo in candidates:
        try:
            if routing.can_act_on_memo(memo, user):
                out.append(memo)
        except Exception:
            logger.exception(
                "Could not resolve memo actor for %s", memo.reference,
            )
    return out


def inbox_memos_for(user):
    """Published, not-expired, not-archived memos the user received."""
    from django.db.models import Q
    from .models import Memo

    if not user or not user.is_authenticated:
        return Memo.objects.none()

    now = timezone.now()
    return (
        Memo.objects
        .select_related("sender", "department")
        .filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
            status=Memo.PUBLISHED,
        )
        .filter(
            memo_recipients__user=user,
            memo_recipients__is_archived=False,
        )
        .distinct()
        .order_by("-published_at", "-pk")
    )


def unread_memo_count_for(user):
    from .models import MemoRecipient
    if not user or not user.is_authenticated:
        return 0
    return MemoRecipient.objects.filter(
        user=user, read_at__isnull=True,
        is_archived=False,
        memo__status="published",
    ).count()


def pending_ack_count_for(user):
    from .models import MemoRecipient
    if not user or not user.is_authenticated:
        return 0
    return MemoRecipient.objects.filter(
        user=user,
        acknowledged_at__isnull=True,
        is_archived=False,
        memo__status="published",
        memo__requires_acknowledgment=True,
    ).count()


# ===========================================================================
# CRUD
# ===========================================================================
@transaction.atomic
def create_memo(*, sender, subject, body, visibility,
                target_user=None, department=None, roles=None, recipients=None,
                priority="normal",
                requires_acknowledgment=False, expires_at=None,
                is_draft=True):
    from .models import Memo

    subject = (subject or "").strip()
    body = (body or "").strip()

    if len(subject) < 3:
        raise ValidationError("Subject must be at least 3 characters.")
    if len(body) < 3:
        raise ValidationError("Memo body must be at least 3 characters.")
    if visibility not in dict(Memo.VISIBILITY_CHOICES):
        raise ValidationError("Pick a valid target.")

    # Target-specific validation.
    if visibility == Memo.SPECIFIC_USER:
        if target_user is None or not target_user.is_active:
            raise ValidationError("Pick a valid recipient.")
        if target_user.pk == sender.pk:
            raise ValidationError("You can't address a memo to yourself.")

    if visibility == Memo.DEPARTMENT and department is None:
        raise ValidationError("Pick a department.")

    if visibility == Memo.ROLE and not (roles or []):
        raise ValidationError("Pick at least one role.")

    if visibility == Memo.INDIVIDUALS and not (recipients or []):
        raise ValidationError("Pick at least one person.")

    # Broadcast gate.
    if visibility in (Memo.ALL_STAFF, Memo.ALL_DIRECTORS, Memo.DEPARTMENT):
        if not can_broadcast_memo(sender):
            raise PermissionDenied(
                "You don't have permission to address a memo this broadly."
            )

    # Allocate reference with retry.
    memo = None
    last_err = None
    for _ in range(5):
        ref = generate_reference()
        try:
            with transaction.atomic():
                memo = Memo.objects.create(
                    reference=ref,
                    subject=subject,
                    body=body,
                    sender=sender,
                    visibility=visibility,
                    target_user=target_user if visibility == Memo.SPECIFIC_USER else None,
                    department=department if visibility == Memo.DEPARTMENT else None,
                    priority=priority,
                    requires_acknowledgment=requires_acknowledgment,
                    expires_at=expires_at,
                    status=Memo.DRAFT if is_draft else Memo.AWAITING,
                    is_draft=is_draft,
                )
            break
        except IntegrityError as exc:
            last_err = exc
            continue

    if memo is None:
        logger.exception("Could not allocate memo reference: %s", last_err)
        raise ValidationError("Could not allocate a memo reference right now. Try again.")

    if visibility == Memo.ROLE and roles:
        memo.roles.set(roles)
    if visibility == Memo.INDIVIDUALS and recipients:
        memo.recipients.set(recipients)

    return memo


@transaction.atomic
def update_memo(memo, actor, **fields):
    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot edit this memo.")
    if memo.status == memo.PUBLISHED:
        # Only a few safe edits after publication.
        allowed_after_publish = {"expires_at", "requires_acknowledgment"}
        given = set(fields)
        if not given.issubset(allowed_after_publish):
            raise ValidationError(
                "Published memos can only have their expiry or "
                "acknowledgment requirement changed."
            )

    for k, v in fields.items():
        setattr(memo, k, v)
    memo.save()
    return memo


@transaction.atomic
def publish_memo(memo, actor, *, notify_recipients=True):
    """Start the routing chain. Delegates to the routing engine."""
    from . import routing

    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot publish this memo.")
    if memo.status not in (memo.DRAFT, memo.RETURNED):
        raise ValidationError("This memo has already entered the pipeline.")

    routing.start_chain(memo, actor=actor)
    return memo


@transaction.atomic
def acknowledge_memo(memo, actor):
    from .models import MemoRecipient

    receipt = MemoRecipient.objects.filter(memo=memo, user=actor).first()
    if receipt is None:
        raise PermissionDenied("You are not a recipient of this memo.")
    if not memo.requires_acknowledgment:
        raise ValidationError("This memo does not require acknowledgment.")
    if receipt.acknowledged_at is None:
        receipt.acknowledged_at = timezone.now()
        if receipt.read_at is None:
            receipt.read_at = receipt.acknowledged_at
        receipt.save(update_fields=["acknowledged_at", "read_at"])
    return receipt


@transaction.atomic
def mark_memo_read(memo, actor):
    from .models import MemoRecipient

    receipt = MemoRecipient.objects.filter(memo=memo, user=actor).first()
    if receipt is None:
        return None
    if receipt.read_at is None:
        receipt.read_at = timezone.now()
        receipt.save(update_fields=["read_at"])
    return receipt


def _set_archive_state(memo, actor, archived):
    from .models import MemoRecipient

    receipt = MemoRecipient.objects.filter(memo=memo, user=actor).first()
    if receipt is None:
        raise PermissionDenied("You are not a recipient of this memo.")
    if receipt.is_archived != archived:
        receipt.is_archived = archived
        receipt.save(update_fields=["is_archived"])
    return receipt


@transaction.atomic
def archive_memo(memo, actor):
    return _set_archive_state(memo, actor, True)


@transaction.atomic
def unarchive_memo(memo, actor):
    return _set_archive_state(memo, actor, False)


@transaction.atomic
def cancel_memo(memo, actor, reason=""):
    """Sender cancels an in-flight or draft memo."""
    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot cancel this memo.")
    if memo.status in (memo.PUBLISHED, memo.REJECTED, memo.CANCELLED):
        raise ValidationError("This memo can no longer be cancelled.")
    memo.status = memo.CANCELLED
    memo.current_step = None
    memo.current_step_key = ""
    memo.current_step_started_at = None
    memo.save(update_fields=[
        "status", "is_draft", "current_step", "current_step_key",
        "current_step_started_at", "updated_at",
    ])
    return memo


@transaction.atomic
def delete_memo(memo, actor):
    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot delete this memo.")
    for att in memo.attachments.all():
        try:
            if att.file:
                att.file.storage.delete(att.file.name)
        except Exception:
            logger.exception("Could not delete memo attachment %s", att.pk)
        att.delete()
    memo.delete()


@transaction.atomic
def add_attachment(memo, actor, file, description=""):
    from .models import MemoAttachment

    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot attach files to this memo.")

    return MemoAttachment.objects.create(
        memo=memo,
        file=file,
        description=(description or "")[:255],
        uploaded_by=actor,
    )


@transaction.atomic
def remove_attachment(memo, actor, attachment):
    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot remove attachments from this memo.")
    try:
        if attachment.file:
            attachment.file.storage.delete(attachment.file.name)
    except Exception:
        logger.exception("Could not delete memo attachment file %s", attachment.pk)
    attachment.delete()


def allowed_target_kinds_for(user):
    """Return the list of Memo.visibility values this user may choose.

    Everyone can send to a specific person or a hand-picked list.
    Broadcast (all-staff, all-directors, whole department, roles) needs
    `messaging.send_memo_broadcast`, or the ES / superuser role.
    """
    from .models import Memo

    base = [Memo.SPECIFIC_USER, Memo.INDIVIDUALS]
    if can_broadcast_memo(user):
        base.extend([
            Memo.ALL_STAFF,
            Memo.ALL_DIRECTORS,
            Memo.DEPARTMENT,
            Memo.ROLE,
        ])
    return base