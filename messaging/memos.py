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
    import org
    if org.has_role(user, "super-admin", "executive-secretary"):
        return True
    return False


# ---------------------------------------------------------------------------
# v2.1 Phase E: memo kinds (M1, M2, M3, M7)
# ---------------------------------------------------------------------------
def can_send_external_memo(user):
    """Only the Executive Secretary sends a memo to institutions (M2). The
    permission messaging.send_external_memo lets a named acting ES do it;
    being a superuser is not enough."""
    if not user or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    import org
    if org.is_es(user):
        return True
    if user.is_superuser:
        return False
    return user.has_perm("messaging.send_external_memo")


def can_initiate_external_memo(user):
    """Who may draft an external or general memo for the ES to approve (M3):
    the ES, an administrator, and directors (the director seat, the director
    role, or a director-rank designation)."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if can_send_external_memo(user):
        return True
    import org
    if org.is_super_admin(user):
        return True
    if org.is_director(user) or org.has_role(user, "director"):
        return True
    from . import routing
    return routing._rank_of(user) >= routing._role_rank("director", 100)


def allowed_memo_kinds(user):
    """The memo types this user may pick (M7). Everyone writes internal memos."""
    from .models import Memo
    kinds = [Memo.INTERNAL]
    if can_initiate_external_memo(user):
        kinds += [Memo.EXTERNAL, Memo.GENERAL]
    return kinds


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
            # v2.1 M2: any holder of the ES's external-memo authority acts on the ES gate.
            if routing.can_act_on_memo(memo, user, admin_override=False):
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
            # Superusers see their own steps and the memos nobody can act on,
            # not every memo in flight.
            if routing.can_act_on_memo(memo, user, admin_override=False) or (
                    user.is_superuser and routing.is_parked(memo)):
                out.append(memo)
        except Exception:
            logger.exception(
                "Could not resolve memo actor for %s", memo.reference,
            )
    rank = {Memo.URGENT: 0, Memo.HIGH: 1}
    out.sort(key=lambda m: (rank.get(m.priority, 2), -m.updated_at.timestamp()))
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
            memo_recipients__hidden_at__isnull=True,
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
        is_archived=False, hidden_at__isnull=True,
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
                is_draft=True, kind="internal", institution_scope="", institution_filter=None,
                target_institutions=None, staff_filter=None):
    from .models import Memo

    subject = (subject or "").strip()
    body = (body or "").strip()
    kind = kind or Memo.INTERNAL

    if len(subject) < 3:
        raise ValidationError("Subject must be at least 3 characters.")
    if len(body) < 3:
        raise ValidationError("Memo body must be at least 3 characters.")
    if kind == Memo.EXTERNAL:
        visibility = Memo.NO_STAFF  # M1: an external memo reaches institutions only
    if visibility not in dict(Memo.VISIBILITY_CHOICES):
        raise ValidationError("Pick a valid target.")
    institution_filter, staff_filter = validate_kind_and_targets(
        sender, kind=kind, visibility=visibility, institution_scope=institution_scope,
        institution_filter=institution_filter, target_institutions=target_institutions,
        staff_filter=staff_filter)

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

    # Broadcast gate. (A general memo is gated by the ES's approval instead.)
    if visibility in (Memo.ALL_STAFF, Memo.ALL_DIRECTORS, Memo.DEPARTMENT, Memo.STAFF_FILTER) \
            and kind != Memo.GENERAL:
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
                    kind=kind,
                    institution_scope=institution_scope if kind != Memo.INTERNAL else "",
                    institution_filter=institution_filter if kind != Memo.INTERNAL else {},
                    staff_filter=staff_filter if visibility == Memo.STAFF_FILTER else {},
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
    if kind != Memo.INTERNAL and institution_scope == Memo.INST_SELECTED:
        from .memo_audience import set_target_institutions
        set_target_institutions(memo, target_institutions)

    return memo


def validate_kind_and_targets(sender, *, kind, visibility, institution_scope="", institution_filter=None,
                              target_institutions=None, staff_filter=None):
    """The v2.1 Phase E rules shared by create and edit. Returns the cleaned
    (institution_filter, staff_filter). Raises ValidationError /
    PermissionDenied."""
    from .memo_audience import institutions_matching, staff_matching
    from .models import Memo

    if kind not in dict(Memo.KIND_CHOICES):
        raise ValidationError("Pick a valid memo type.")
    if kind not in allowed_memo_kinds(sender):
        raise PermissionDenied("Only the Executive Secretary, administrators and directors can write a "
                               "memo to institutions.")
    institution_filter = dict(institution_filter or {})
    staff_filter = dict(staff_filter or {})
    if kind == Memo.INTERNAL:
        if visibility == Memo.NO_STAFF:
            raise ValidationError("An internal memo needs staff recipients.")
        institution_filter = {}
    else:
        if institution_scope not in (Memo.INST_ALL, Memo.INST_SELECTED, Memo.INST_FILTER):
            raise ValidationError("Choose which institutions receive this memo.")
        if institution_scope == Memo.INST_SELECTED and not list(target_institutions or []):
            raise ValidationError("Pick at least one institution.")
        if institution_scope == Memo.INST_FILTER and not institutions_matching(institution_filter).exists():
            raise ValidationError("No institution matches these filters. Widen the filters or pick "
                                  "institutions by name.")
        if institution_scope != Memo.INST_FILTER:
            institution_filter = {}
    if visibility == Memo.STAFF_FILTER:
        if not staff_matching(staff_filter, exclude=sender).exists():
            raise ValidationError("No staff member matches these filters.")
    else:
        staff_filter = {}
    return institution_filter, staff_filter


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

    # Review M4: once any approver has signed an in-flight memo, its content
    # and audience are frozen - approvers must not end up approving text
    # they never saw. To change them, an approver returns the memo first
    # (RETURNED memos are editable again and walk the chain afresh).
    if memo.status == memo.AWAITING and memo.approvals.exists():
        frozen = {"subject", "body", "visibility", "department", "target_user", "kind",
                  "institution_scope", "institution_filter", "staff_filter"}
        changed = sorted(
            k for k in frozen & set(fields)
            if getattr(memo, k) != fields[k]
        )
        if changed:
            raise ValidationError(
                "This memo has already been approved by at least one person, so "
                "its subject, body and audience can no longer change. Ask an "
                "approver to return it if it needs editing."
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
        # (Phase 9 API fix: MemoAttachment has no uploaded_by field, so every
        # attachment failed with "Could not attach <name>".)
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
            Memo.STAFF_FILTER,
        ])
    if can_initiate_external_memo(user):
        base.append(Memo.NO_STAFF)  # the staff side of an external memo
    return base

# ===========================================================================
# Phase 9 API: list tabs, detail state and compose shared by the website
# views (memos_views) and the mobile API.
# ===========================================================================
MEMO_TABS = ("inbox", "awaiting", "sent", "drafts", "archived")


def memo_tab_queryset(user, tab, *, q="", priority=""):
    """The memo list for one tab (website: inbox | awaiting | sent | archived;
    the app adds "drafts" = the user's drafts, and its "sent" leaves drafts
    out - pass tab="sent_published" for that)."""
    from django.db.models import Q
    from .models import Memo

    if tab in ("sent", "sent_published"):
        qs = visible_memos_for(user).filter(sender=user)
        if tab == "sent_published":
            qs = qs.exclude(status=Memo.DRAFT)
    elif tab == "drafts":
        qs = visible_memos_for(user).filter(sender=user, status=Memo.DRAFT)
    elif tab == "archived":
        qs = visible_memos_for(user).filter(
            memo_recipients__user=user,
            memo_recipients__is_archived=True,
            memo_recipients__hidden_at__isnull=True,
        ).distinct()
    elif tab == "awaiting":
        ids = [m.pk for m in awaiting_memos_for(user)]
        qs = (
            Memo.objects.filter(pk__in=ids)
            .select_related("sender", "route", "department", "target_user")
            .order_by("-updated_at")
        )
    else:
        qs = inbox_memos_for(user)

    q = (q or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(subject__icontains=q) | Q(body__icontains=q))
    if priority in dict(Memo.PRIORITY_CHOICES):
        qs = qs.filter(priority=priority)
    return qs


def memo_tab_counts(user, tabs=("inbox", "awaiting", "sent", "archived")):
    counts = {}
    for tab in tabs:
        if tab == "awaiting":
            counts[tab] = len(awaiting_memos_for(user))
        else:
            counts[tab] = memo_tab_queryset(user, tab).count()
    return counts


def get_memo_for(user, pk):
    """A memo the user may view, else Http404 (never a 403 that leaks it)."""
    from django.http import Http404
    from .models import Memo

    memo = Memo.objects.select_related("sender", "department", "route", "target_user").filter(pk=pk).first()
    if memo is None or not can_view_memo(memo, user):
        raise Http404("Memo not found.")
    return memo


def open_memo(memo, user):
    """What opening the memo page does: re-check a parked memo, mark the
    user's copy read. Returns the (possibly refreshed) memo."""
    from . import routing
    from .models import Memo

    if memo.status == Memo.AWAITING and routing.actor_for_memo(memo) is None:
        try:
            if routing.reconcile_memo(memo):
                memo = get_memo_for(user, memo.pk)
        except Exception:
            logger.exception("Could not re-check memo %s", memo.reference)
    receipt = memo.memo_recipients.filter(user=user).first()
    if receipt is not None and receipt.read_at is None and memo.status == Memo.PUBLISHED:
        mark_memo_read(memo, user)
    return memo


def can_download_document(memo, user):
    """PDF / Word: published memos for anyone who can view them; before that
    only the sender / managers (watermarked preview)."""
    from .models import Memo

    if memo.status == Memo.PUBLISHED:
        return True
    if can_manage_memo(memo, user):
        return True
    # v2.1 M3: the ES previews the letter they are asked to sign.
    from . import routing
    return (memo.is_outbound and memo.status == Memo.AWAITING
            and routing.can_act_on_memo(memo, user, admin_override=False))


def memo_detail_state(memo, user):
    """Everything the memo page decides for this user (buttons, banners)."""
    from . import routing
    from .models import Memo

    receipt = memo.memo_recipients.filter(user=user).first()
    show_stats = (memo.sender_id == user.pk or user.is_superuser
                  or user.has_perm("messaging.manage_memo"))
    pending_recipients = []
    if show_stats and memo.requires_acknowledgment and memo.status == Memo.PUBLISHED:
        pending_recipients = list(memo.memo_recipients.filter(acknowledged_at__isnull=True)
                                  .select_related("user")[:50])
    can_act = routing.can_act_on_memo(memo, user)
    current_step = routing.current_step(memo)
    current_actor = routing.resolve_memo_approver(current_step, memo) if current_step else None
    is_sender = memo.sender_id == user.pk
    can_manage = can_manage_memo(memo, user)
    needs_ack = (receipt is not None and memo.requires_acknowledgment
                 and receipt.acknowledged_at is None and memo.status == Memo.PUBLISHED)

    actions = []
    if can_act:
        actions += ["approve", "return", "reject"]
    if memo.status == Memo.RETURNED and is_sender:
        actions.append("resubmit")
    if can_manage and memo.status == Memo.DRAFT:
        actions.append("publish")
    if needs_ack:
        actions.append("acknowledge")
    if receipt is not None and memo.status == Memo.PUBLISHED:
        actions.append("unarchive" if receipt.is_archived else "archive")
    if can_manage and memo.status in (Memo.DRAFT, Memo.AWAITING, Memo.RETURNED):
        actions.append("cancel")

    return {
        "receipt": receipt,
        "is_sender": is_sender,
        "can_manage": can_manage,
        "show_stats": show_stats,
        "pending_recipients": pending_recipients,
        "needs_ack": needs_ack,
        "can_act": can_act,
        "current_step": current_step,
        "current_actor": current_actor,
        "acting_for": can_act and current_actor is not None and current_actor.pk != user.pk,
        "parked_reason": (routing.describe_missing(current_step, memo)
                          if memo.status == Memo.AWAITING and current_step and current_actor is None
                          else ""),
        "can_recheck": memo.status == Memo.AWAITING and (
            user.is_superuser or user.has_perm("messaging.manage_memoroute")
            or user.has_perm("messaging.manage_memo")),
        "can_download_document": can_download_document(memo, user),
        "chain_rows": routing.chain_view(memo),
        "actions": actions,
    }


def submit_memo_form(user, cleaned, *, publish=False, from_message=None):
    """Create a memo from a valid MemoForm's cleaned_data (website compose and
    the API): draft, attachments, optional chat-message link, then publish.

    Returns (memo, notes) where notes are (level, text) pairs for the user:
    level is "success" | "warning". Raises ValidationError / PermissionDenied
    from create_memo (nothing is saved then)."""
    from .models import Memo

    memo = create_memo(
        sender=user,
        subject=cleaned["subject"],
        body=cleaned["body"],
        visibility=cleaned["visibility"],
        target_user=cleaned.get("target_user"),
        department=cleaned.get("department"),
        roles=list(cleaned.get("roles") or []),
        recipients=list(cleaned.get("recipients") or []),
        priority=cleaned.get("priority") or Memo.NORMAL,
        requires_acknowledgment=cleaned.get("requires_acknowledgment", False),
        expires_at=cleaned.get("expires_at"),
        kind=cleaned.get("kind") or Memo.INTERNAL,
        institution_scope=cleaned.get("institution_scope") or "",
        institution_filter=cleaned.get("institution_filter") or {},
        target_institutions=list(cleaned.get("target_institutions") or []),
        staff_filter=cleaned.get("staff_filter") or {},
        # Always a draft first: publish_memo() only starts the chain from
        # DRAFT/RETURNED (creating it as AWAITING made "Send" fail with
        # "already entered the pipeline" and left the memo stuck).
        is_draft=True,
    )
    notes = []
    for f in cleaned.get("attachments") or []:
        try:
            add_attachment(memo, user, f)
        except Exception:
            notes.append(("warning", f"Could not attach {f.name}."))

    # Phase 9: a memo made from a chat message links back to it.
    if from_message:
        from .chat_services import link_message_to_memo
        link_message_to_memo(from_message, memo, user)

    if not publish:
        notes.append(("success", "Memo saved as draft."))
        return memo, notes
    try:
        publish_memo(memo, user)
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        notes.append(("warning", "Draft saved, but the chain could not start: " + "; ".join(msgs)))
        return memo, notes
    notes.append(("success", publish_notice(memo)))
    return memo, notes


def publish_notice(memo):
    """What the sender is told after "Send"."""
    from .models import Memo
    if memo.status == Memo.PUBLISHED and not memo.is_outbound:
        return f"Memo {memo.reference} published to {memo.recipient_count} recipient(s)."
    if memo.status == Memo.PUBLISHED:
        parts = []
        if memo.recipient_count or not memo.is_outbound:
            parts.append(f"{memo.recipient_count} staff recipient(s)")
        if memo.is_outbound:
            parts.append(f"{memo.institution_recipients.count()} institution(s)")
        return f"Memo {memo.reference} published to " + " and ".join(parts) + "."
    if memo.is_outbound:
        return f"Memo {memo.reference} sent to the Executive Secretary for approval."
    return f"Memo {memo.reference} sent for approval."


# ===========================================================================
# v2.1 Phase E (M4): the institution side - portal Memos inbox
# ===========================================================================
def institution_memos_for(institution):
    """Published memos delivered to ``institution`` (newest first), with the
    institution's receipt as ``receipt_*`` annotations via the queryset of
    MemoInstitutionRecipient."""
    from .models import Memo, MemoInstitutionRecipient

    if institution is None:
        return MemoInstitutionRecipient.objects.none()
    return (MemoInstitutionRecipient.objects
            .filter(institution=institution, memo__status=Memo.PUBLISHED)
            .select_related("memo", "memo__sender", "memo__es_signatory")
            .order_by("-memo__published_at", "-pk"))


def unread_institution_memo_count(institution):
    return institution_memos_for(institution).filter(read_at__isnull=True).count()


def get_institution_receipt(institution, pk):
    """The institution's copy of memo ``pk``, else Http404 (never leaks)."""
    from django.http import Http404

    receipt = institution_memos_for(institution).filter(memo_id=pk).first()
    if receipt is None:
        raise Http404("Memo not found.")
    return receipt


def open_institution_memo(receipt, user, *, impersonating=False):
    """First open by any of the institution's users marks it read (an NBTE
    officer viewing the portal as the institution does not)."""
    if receipt.read_at is None and getattr(user, "user_type", "") == "institution" and not impersonating:
        receipt.read_at = timezone.now()
        receipt.read_by = user
        receipt.save(update_fields=["read_at", "read_by"])
    return receipt


@transaction.atomic
def acknowledge_institution_memo(receipt, user):
    if getattr(user, "user_type", "") != "institution":
        raise PermissionDenied("Only the institution acknowledges its memo.")
    if receipt.acknowledged_at is None:
        now = timezone.now()
        receipt.acknowledged_at = now
        receipt.acknowledged_by = user
        if receipt.read_at is None:
            receipt.read_at, receipt.read_by = now, user
        receipt.save(update_fields=["acknowledged_at", "acknowledged_by", "read_at", "read_by"])
    return receipt


def selected_institutions(ids):
    """Institutions picked by pk for an external memo (not delisted). v2.1
    P.3: the view never calls ``.unscoped`` itself; only ES-level senders
    reach this (``can_initiate_external_memo``)."""
    from institutions.models import Institution
    return Institution.unscoped.exclude(status=Institution.DELISTED).filter(pk__in=ids).order_by("name")
