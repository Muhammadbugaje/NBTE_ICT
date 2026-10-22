"""Announcement business logic (Phase 9).

Views call these; a future API should too. Every function takes plain model
instances / ids and raises `PermissionDenied` or `ValidationError` on misuse.

    publish(announcement, actor=None) -> int            # notifications created
    mark_read(user, announcement) -> bool                # True if newly read
    acknowledge(user, announcement) -> (AnnouncementAck, created)
    vote(user, poll, option_ids) -> list[int]            # the user's option ids now
    report(announcement) -> dict
    remind_pending(announcement, actor=None) -> int      # reminders sent
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from .models import (
    Announcement,
    AnnouncementAck,
    AnnouncementPoll,
    AnnouncementRead,
    PollOption,
    PollVote,
    audience_users,
)

logger = logging.getLogger(__name__)

_LEVEL_FOR_KIND = {
    Announcement.NOTICE: "info",
    Announcement.ACTION_NEEDED: "info",
    Announcement.URGENT: "warning",
}


# ---------------------------------------------------------------------------
# Cache helpers (sidebar badge)
# ---------------------------------------------------------------------------
def _refresh_user_sidebar(user):
    try:
        from django.core.cache import cache

        from core.cache import make_key

        cache.delete(make_key("sidebar", "counts", user=user))
    except Exception:  # caching is best-effort
        pass


def _refresh_everyones_sidebar():
    try:
        from core.cache import invalidate

        invalidate("sidebar")
    except Exception:
        pass


def _can_see(user, announcement):
    return Announcement.objects.visible_to(user).filter(pk=announcement.pk).exists()


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
def publish(announcement, actor=None):
    """Publish a draft and notify its audience in-app (one bulk insert).

    Publishing an already-published announcement is a no-op (returns 0), so
    a double-click never notifies anyone twice.
    """
    from notifications.services import notify_many

    with transaction.atomic():
        locked = Announcement.objects.select_for_update().get(pk=announcement.pk)
        if locked.published_at is not None:
            announcement.published_at = locked.published_at
            return 0
        locked.published_at = timezone.now()
        locked.save(update_fields=["published_at", "updated_at"])
        announcement.published_at = locked.published_at

    # The poster has obviously read what they wrote: record it so they are
    # not listed as "pending" in their own report.
    if actor is not None and audience_users(announcement).filter(pk=actor.pk).exists():
        AnnouncementRead.objects.get_or_create(announcement=announcement, user=actor)
        if announcement.requires_ack:
            AnnouncementAck.objects.get_or_create(announcement=announcement, user=actor)

    recipients = audience_users(announcement).exclude(pk=getattr(actor, "pk", None)).only("pk")
    prefix = {"urgent": "Urgent: ", "action_needed": "Action needed: "}.get(announcement.kind, "")
    created = notify_many(
        recipients.iterator(chunk_size=500),
        title=f"{prefix}{announcement.title}"[:255],
        body=(announcement.body[:200] + ("…" if len(announcement.body) > 200 else "")),
        url=announcement.get_absolute_url(),
        level=_LEVEL_FOR_KIND.get(announcement.kind, "info"),
        category="system",
        actor=actor,
        metadata={"announcement": announcement.pk},
    )
    _refresh_everyones_sidebar()
    return len(created)


# ---------------------------------------------------------------------------
# Read / acknowledge
# ---------------------------------------------------------------------------
def mark_read(user, announcement):
    """Record that `user` opened the announcement. True if this is the first time."""
    if not getattr(user, "pk", None) or announcement.published_at is None:
        return False
    _, created = AnnouncementRead.objects.get_or_create(announcement=announcement, user=user)
    if created:
        _refresh_user_sidebar(user)
    return created


def acknowledge(user, announcement):
    """Idempotent acknowledgement. Returns (ack, created)."""
    if not announcement.requires_ack:
        raise ValidationError("This announcement does not ask for acknowledgement.")
    if not _can_see(user, announcement):
        raise PermissionDenied("This announcement is not addressed to you.")
    try:
        with transaction.atomic():
            ack, created = AnnouncementAck.objects.get_or_create(announcement=announcement, user=user)
    except IntegrityError:  # a concurrent double-tap won the race
        ack, created = AnnouncementAck.objects.get(announcement=announcement, user=user), False
    mark_read(user, announcement)
    return ack, created


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------
def vote(user, poll, option_ids):
    """Set `user`'s choice in `poll` to `option_ids`.

    The new choice replaces any earlier one (for single-choice polls this is
    "change my vote"; for multi-choice polls the list is the full selection).
    Returns the list of option ids the user has now chosen.
    """
    if not getattr(user, "pk", None):
        raise PermissionDenied("Sign in to vote.")
    if not poll.is_open:
        raise ValidationError("This poll has closed.")
    if poll.announcement_id is not None and not _can_see(user, poll.announcement):
        raise PermissionDenied("This poll is not addressed to you.")

    try:
        ids = sorted({int(i) for i in option_ids or []})
    except (TypeError, ValueError):
        raise ValidationError("Choose a valid option.")
    if not ids:
        raise ValidationError("Choose an option.")
    if not poll.allow_multiple and len(ids) > 1:
        raise ValidationError("Choose only one option.")
    valid = set(PollOption.objects.filter(poll=poll, pk__in=ids).values_list("pk", flat=True))
    if valid != set(ids):
        raise ValidationError("Choose a valid option.")

    with transaction.atomic():
        PollVote.objects.filter(poll=poll, user=user).exclude(option_id__in=ids).delete()
        have = set(PollVote.objects.filter(poll=poll, user=user).values_list("option_id", flat=True))
        PollVote.objects.bulk_create(
            [PollVote(poll=poll, option_id=i, user=user) for i in ids if i not in have],
            ignore_conflicts=True,
        )
    if poll.announcement_id:
        mark_read(user, poll.announcement)
    return ids


def my_votes(user, poll):
    return list(PollVote.objects.filter(poll=poll, user=user).values_list("option_id", flat=True))


def save_poll(announcement, *, question, options, allow_multiple=False, closes_at=None):
    """Create / update / remove an announcement's poll from form data.

    Options can only change while nobody has voted; afterwards only the
    question, multi-choice flag and closing time are updated.
    Returns the poll, or None when it was removed / not wanted.
    """
    question = (question or "").strip()
    options = [o.strip() for o in options if o and o.strip()]
    poll = announcement.poll_or_none()
    if not question:
        if poll and not poll.votes.exists():
            poll.delete()
            return None
        return poll
    with transaction.atomic():
        if poll is None:
            poll = AnnouncementPoll.objects.create(
                announcement=announcement, question=question,
                allow_multiple=allow_multiple, closes_at=closes_at,
            )
        else:
            poll.question = question
            poll.allow_multiple = allow_multiple
            poll.closes_at = closes_at
            poll.save()
        if not poll.votes.exists():
            poll.options.all().delete()
            PollOption.objects.bulk_create(
                [PollOption(poll=poll, text=text[:200], position=i) for i, text in enumerate(options)]
            )
    return poll


# ---------------------------------------------------------------------------
# Report + reminders
# ---------------------------------------------------------------------------
def _audience_with_state(announcement):
    return (
        audience_users(announcement)
        .select_related("department", "role")
        .annotate(
            has_read=Exists(AnnouncementRead.objects.filter(announcement=announcement, user=OuterRef("pk"))),
            has_acked=Exists(AnnouncementAck.objects.filter(announcement=announcement, user=OuterRef("pk"))),
        )
        .order_by("department__name", "first_name", "last_name", "email")
    )


def pending_users(announcement):
    """Audience members still to act: not acknowledged (if acknowledgement is
    required), otherwise not yet read."""
    qs = _audience_with_state(announcement)
    return qs.filter(has_acked=False) if announcement.requires_ack else qs.filter(has_read=False)


def report(announcement):
    """Counts and lists for the poster's report page."""
    audience = _audience_with_state(announcement)
    audience_ids = audience_users(announcement).values("pk")
    size = audience.count()
    read = AnnouncementRead.objects.filter(announcement=announcement, user_id__in=audience_ids).count()
    acked = AnnouncementAck.objects.filter(announcement=announcement, user_id__in=audience_ids).count()
    poll = announcement.poll_or_none()
    pct = (lambda n: round(100 * n / size) if size else 0)
    return {
        "audience_size": size,
        "read": read,
        "read_percent": pct(read),
        "acknowledged": acked,
        "acknowledged_percent": pct(acked),
        "pending": list(pending_users(announcement)),
        "poll": poll,
        "poll_results": poll.results() if poll else None,
        "audience": audience,
    }


def remind_pending(announcement, actor=None):
    """In-app reminder to everyone who has not acknowledged (or read) yet.
    Returns how many reminders were created."""
    from notifications.services import notify_many

    if announcement.published_at is None:
        raise ValidationError("Publish the announcement before sending reminders.")
    verb = "acknowledge" if announcement.requires_ack else "read"
    created = notify_many(
        pending_users(announcement).iterator(chunk_size=500),
        title=f"Reminder: please {verb} “{announcement.title}”"[:255],
        body="This announcement is still waiting for you.",
        url=announcement.get_absolute_url(),
        level="warning",
        category="system",
        actor=actor,
        metadata={"announcement": announcement.pk, "reminder": True},
    )
    _refresh_everyones_sidebar()
    return len(created)


# ---------------------------------------------------------------------------
# Access + saving (shared by the website views and the Phase 9 mobile API)
# ---------------------------------------------------------------------------
def access_for(user, announcement, *, include_expired=True):
    """(may_open, in_audience): `user` may open the announcement if it is
    addressed to them (published, not expired unless `include_expired`) or
    they may edit it / see its report."""
    from .permissions import can_edit, can_view_report

    in_audience = Announcement.objects.visible_to(user, include_expired=include_expired).filter(
        pk=announcement.pk).exists()
    may_open = in_audience or can_edit(user, announcement) or can_view_report(user, announcement)
    return may_open, in_audience


def save_from_form(form, user, *, creating, publish_now=False, remove_attachment_ids=()):
    """Save a valid AnnouncementForm: the announcement, its poll, a new
    attachment and attachment removals; publish when asked (drafts only).

    Returns (announcement, notified) - `notified` is the number of staff
    notified when it was published now, else None.
    """
    from .models import AnnouncementAttachment

    with transaction.atomic():
        ann = form.save(commit=False)
        if creating:
            ann.created_by = user
        ann.save()
        form.save_m2m()
        if not form.poll_locked:
            save_poll(
                ann,
                question=form.cleaned_data.get("poll_question"),
                options=form.poll_options(),
                allow_multiple=form.cleaned_data.get("poll_allow_multiple") or False,
                closes_at=form.cleaned_data.get("poll_closes_at"),
            )
        else:
            poll = ann.poll_or_none()
            if poll:
                poll.question = form.cleaned_data.get("poll_question") or poll.question
                poll.closes_at = form.cleaned_data.get("poll_closes_at")
                poll.save(update_fields=["question", "closes_at", "updated_at"])
        upload = form.cleaned_data.get("attachment")
        if upload:
            AnnouncementAttachment.objects.create(announcement=ann, file=upload)
        if remove_attachment_ids:
            for att in ann.attachments.filter(pk__in=list(remove_attachment_ids)):
                att.delete()
    notified = None
    if publish_now and ann.is_draft:
        notified = publish(ann, actor=user)
    return ann, notified
