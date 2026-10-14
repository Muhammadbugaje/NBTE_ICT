"""Memo service layer — the only place memo state changes.

Public API:
    generate_reference()                 -> MEMO-YYYY-NNNNN
    create_memo(...)                     -> Memo (draft)
    update_memo(memo, actor, **fields)   -> Memo
    publish_memo(memo, actor)            -> Memo (with recipients materialized)
    acknowledge_memo(memo, actor)        -> MemoRecipient
    mark_memo_read(memo, actor)          -> MemoRecipient
    archive_memo(memo, actor)            -> MemoRecipient
    unarchive_memo(memo, actor)          -> MemoRecipient
    delete_memo(memo, actor)             -> None (hard delete)
    add_attachment(memo, actor, file)    -> MemoAttachment
    remove_attachment(memo, actor, att)  -> None
    visible_memos_for(user)              -> queryset
    inbox_memos_for(user)                -> queryset
    unread_memo_count_for(user)          -> int
    pending_ack_count_for(user)          -> int
    resolve_recipients_for(memo)         -> queryset of User
    can_compose_memo(user)               -> bool
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
def can_compose_memo(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.has_perm("messaging.add_memo"):
        return True

    # Role-based fallback — mirrors the roles that hold authority elsewhere
    if user.role_id:
        code = user.role.code
        if code in {
            "super-admin",
            "executive-secretary",
            "director",
            "deputy-director",
            "office-head-admin",
            "office-head-ts",
            "office-head-dev",
            "division-head",
        }:
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
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if memo.sender_id == user.pk:
        return True
    if memo.is_draft:
        return False
    return memo.memo_recipients.filter(user=user).exists()


# ===========================================================================
# Visibility scope
# ===========================================================================
def visible_memos_for(user):
    """Memos this user can list or search. Mirrors `can_view_memo`."""
    from django.db.models import Q
    from .models import Memo

    if not user or not user.is_authenticated:
        return Memo.objects.none()

    base = Memo.objects.select_related("sender", "department")

    if user.is_superuser:
        return base

    return base.filter(
        Q(sender=user) | Q(memo_recipients__user=user)
    ).distinct()


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
            is_draft=False,
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
        memo__is_draft=False,
    ).count()


def pending_ack_count_for(user):
    from .models import MemoRecipient
    if not user or not user.is_authenticated:
        return 0
    return MemoRecipient.objects.filter(
        user=user,
        acknowledged_at__isnull=True,
        is_archived=False,
        memo__is_draft=False,
        memo__requires_acknowledgment=True,
    ).count()


# ===========================================================================
# Recipient resolution
# ===========================================================================
def resolve_recipients_for(memo):
    """Return a User queryset — the audience for this memo."""
    from django.contrib.auth import get_user_model
    User = get_user_model()

    base = User.objects.filter(is_active=True).exclude(pk=memo.sender_id)

    if memo.visibility == memo.ALL_STAFF:
        return base.distinct()

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
            pk__in=memo.recipients.values_list("pk", flat=True)
        ).distinct()

    return User.objects.none()


# ===========================================================================
# CRUD
# ===========================================================================
@transaction.atomic
def create_memo(*, sender, subject, body, visibility, priority="normal",
                department=None, roles=None, recipients=None,
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
        raise ValidationError("Pick a valid audience.")

    if visibility == Memo.DEPARTMENT and department is None:
        raise ValidationError("Pick a department for this audience.")
    if visibility == Memo.ROLE and not (roles or []):
        raise ValidationError("Pick at least one role for this audience.")
    if visibility == Memo.INDIVIDUALS and not (recipients or []):
        raise ValidationError("Pick at least one recipient for this audience.")

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
                    priority=priority,
                    department=department if visibility == Memo.DEPARTMENT else None,
                    requires_acknowledgment=requires_acknowledgment,
                    expires_at=expires_at,
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
    if memo.is_published:
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
    """Flip draft → published and materialize recipient rows."""
    from .models import Memo, MemoRecipient
    from notifications.models import Notification
    from notifications.services import notify

    if not can_manage_memo(memo, actor):
        raise PermissionDenied("You cannot publish this memo.")
    if memo.is_published:
        raise ValidationError("This memo has already been published.")

    audience = list(resolve_recipients_for(memo))
    if not audience:
        raise ValidationError("No eligible recipients match the selected audience.")

    now = timezone.now()
    memo.is_draft = False
    memo.published_at = now
    memo.save(update_fields=["is_draft", "published_at", "updated_at"])

    for user in audience:
        MemoRecipient.objects.get_or_create(
            memo=memo, user=user,
            defaults={"delivered_at": now},
        )

    if notify_recipients:
        url = f"/messaging/memos/{memo.pk}/"
        level = (
            Notification.WARNING
            if memo.priority in (Memo.HIGH, Memo.URGENT)
            else Notification.INFO
        )
        title = f"New memo — {memo.reference}"
        if memo.priority == Memo.URGENT:
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
                    actor=actor,
                    metadata={"memo_id": memo.pk, "reference": memo.reference},
                )
            except Exception:
                logger.exception("Memo notify failed for user %s", user.pk)

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
    """Shared helper for archive / unarchive. Caller holds the transaction."""
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