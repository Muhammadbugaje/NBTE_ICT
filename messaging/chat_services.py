"""Chat services (Phase 9).

Every piece of chat behaviour lives here so the web views (HTMX / fetch) and
the future REST API for the mobile app call exactly the same code. Views only
translate HTTP <-> these functions; nothing in here touches `request`.

Conventions
-----------
* Functions take model instances (or ids) plus the acting `user`.
* Access problems raise `django.http.Http404` (the thread is invisible to a
  non-participant); rule violations raise `django.core.exceptions.ValidationError`.
* `message_payload(message, viewer)` is the canonical JSON shape of a message.
* No function ever sleeps or blocks waiting for new data: clients short-poll
  `thread_updates()` / `inbox_updates()` (see core/polling.py for the rationale).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from . import live
from .models import (
    REACTION_EMOJIS, ChatPoll, ChatPollOption, ChatPollVote, Message,
    MessageAttachment, MessageDeletion, MessageReaction, MessageRead,
    MessageReminder, MessageStar, Thread, ThreadParticipant, VoiceNote, VoiceNotePlay,
)

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
EDIT_WINDOW = timedelta(minutes=15)
READ_EDIT_WINDOW = timedelta(minutes=5)
TYPING_TTL_SECONDS = 6
MAX_POLL_OPTIONS = 6
MIN_POLL_OPTIONS = 2
UPDATES_BATCH = 100
# Far-future instant used for "mute always".
MUTE_FOREVER = datetime(2999, 12, 31, tzinfo=dt_timezone.utc)
MUTE_OPTIONS = {
    "8h": timedelta(hours=8),
    "1w": timedelta(weeks=1),
    "always": None,
}
INBOX_KINDS = ("all", "unread", "groups", "direct")

_AVATAR_COLORS = ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7"]


def avatar_color(seed):
    return _AVATAR_COLORS[(seed or 0) % len(_AVATAR_COLORS)]


def user_name(user):
    if user is None:
        return "System"
    return user.get_full_name() or user.email


def user_initials(user):
    if user is None:
        return "?"
    first = (user.first_name or "")[:1]
    last = (user.last_name or "")[:1]
    return (first + last).upper() or (user.email or "?")[:1].upper()


# ===========================================================================
# Access
# ===========================================================================
def get_participant(thread, user):
    """The user's live participant row, or Http404."""
    participant = thread.participants.filter(user=user, is_deleted=False).first()
    if participant is None:
        raise Http404("Not a participant of this conversation.")
    return participant


def get_thread_for(user, thread_id):
    """(thread, participant) for a thread the user takes part in, else Http404."""
    thread = Thread.objects.filter(pk=thread_id).first()
    if thread is None:
        raise Http404
    return thread, get_participant(thread, user)


def visible_messages(thread, user):
    """Messages of the thread this user can see (not deleted-for-me)."""
    return thread.messages.exclude(hidden_for__user=user).distinct()


def message_queryset(thread, user):
    """Visible messages with everything rendering/payloads need prefetched."""
    return (
        visible_messages(thread, user)
        .select_related(
            "sender", "reply_to__sender", "thread", "voice_note", "poll",
            "linked_task", "linked_memo",
        )
        .prefetch_related(
            "attachments", "read_receipts",
            Prefetch("reactions", queryset=MessageReaction.objects.select_related("user")),
            Prefetch("stars", queryset=MessageStar.objects.filter(user=user),
                     to_attr="viewer_stars"),
            Prefetch("reminders", queryset=MessageReminder.objects.filter(user=user, sent_at__isnull=True),
                     to_attr="viewer_reminders"),
            "poll__options", "poll__votes__user",
            "voice_note__plays",
        )
        .order_by("sent_at", "id")
    )


def get_visible_message(thread, user, message_id):
    message = message_queryset(thread, user).filter(pk=message_id).first()
    if message is None:
        raise Http404("Message not found.")
    return message


def get_message_for(user, message_id):
    """(message, thread, participant) for a message the user can see."""
    message = Message.objects.select_related("thread").filter(pk=message_id).first()
    if message is None:
        raise Http404
    thread = message.thread
    participant = get_participant(thread, user)
    return get_visible_message(thread, user, message_id), thread, participant


def thread_participants(thread):
    return list(thread.participants.select_related("user"))


# ===========================================================================
# Rules
# ===========================================================================
def can_change_for_everyone(message, now=None):
    """Edit / delete-for-everyone window: 15 min, or 5 min after first read."""
    if message.is_system or message.sender_id is None or message.is_deleted_for_everyone:
        return False
    now = now or timezone.now()
    first_read_at = min(
        (
            receipt.read_at
            for receipt in message.read_receipts.all()
            if receipt.user_id != message.sender_id
        ),
        default=None,
    )
    deadline = message.sent_at + EDIT_WINDOW
    if first_read_at is not None:
        deadline = min(deadline, first_read_at + READ_EDIT_WINDOW)
    return now <= deadline


def is_muted(participant, now=None):
    until = getattr(participant, "muted_until", None)
    return bool(until and until > (now or timezone.now()))


# ===========================================================================
# Sending, editing, deleting
# ===========================================================================
def check_attachment_sizes(uploads):
    if sum(upload.size for upload in uploads) > MAX_ATTACHMENT_BYTES:
        raise ValidationError("Attachments must total no more than 5 MB.")


def resolve_reply_to(thread, user, reply_to_id):
    if not reply_to_id:
        return None
    try:
        return (
            Message.objects.filter(thread=thread, is_deleted_for_everyone=False)
            .exclude(hidden_for__user=user)
            .get(pk=int(reply_to_id))
        )
    except (Message.DoesNotExist, TypeError, ValueError):
        raise ValidationError("The message you're replying to is no longer available.")


def _after_post(thread, participant, now):
    thread.last_message_at = now
    thread.save(update_fields=["last_message_at"])
    participant.last_read_at = now
    participant.last_delivered_at = now
    participant.save(update_fields=["last_read_at", "last_delivered_at"])
    clear_typing(thread, participant.user)


def send_message(thread, user, *, body="", reply_to_id=None, uploads=(),
                 forwarded=False):
    """Post a text message (with optional attachments). Returns the Message."""
    from .group_services import ensure_can_send

    participant = get_participant(thread, user)
    ensure_can_send(thread, participant)
    body = (body or "").strip()
    uploads = list(uploads or [])
    check_attachment_sizes(uploads)
    if not body and not uploads:
        raise ValidationError("Write a message or attach a file.")
    reply_to = resolve_reply_to(thread, user, reply_to_id)
    now = timezone.now()
    with transaction.atomic():
        message = Message.objects.create(
            thread=thread, sender=user, body=body, reply_to=reply_to,
            sent_at=now, forwarded=forwarded,
        )
        for upload in uploads:
            MessageAttachment.objects.create(message=message, file=upload)
        _after_post(thread, participant, now)
    return message


def edit_message(message, user, body):
    get_participant(message.thread, user)
    if message.sender_id != user.pk or not can_change_for_everyone(message):
        raise ValidationError("This message can no longer be edited for everyone.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("A message cannot be empty.")
    now = timezone.now()
    message.body = body
    message.edited_at = now
    message.changed_at = now
    message.save(update_fields=["body", "edited_at", "changed_at"])
    return message


def purge_message_files(message):
    for attachment in message.attachments.all().iterator():
        attachment.hard_delete()
    voice = VoiceNote.all_objects.filter(message=message).first()
    if voice is not None:
        voice.hard_delete()


def delete_message(message, user, scope):
    """scope = "me" (hide for me) or "everyone" (sender, within the window)."""
    get_participant(message.thread, user)
    if scope == "everyone":
        if message.sender_id != user.pk or not can_change_for_everyone(message):
            raise ValidationError("This message can no longer be deleted for everyone.")
        message.body = "This message was deleted."
        message.is_deleted_for_everyone = True
        message.edited_at = None
        message.changed_at = timezone.now()
        message.save(update_fields=["body", "is_deleted_for_everyone", "edited_at",
                                    "changed_at"])
        purge_message_files(message)
        MessageReaction.objects.filter(message=message).delete()
        return message
    if scope == "me":
        MessageDeletion.objects.get_or_create(message=message, user=user)
        return message
    raise ValidationError("Choose where to delete this message.")


# ===========================================================================
# Delivery & read receipts
# ===========================================================================
def mark_delivered(participant, now=None):
    """The user's client has fetched the thread: everything so far is delivered."""
    now = now or timezone.now()
    previous = participant.last_delivered_at
    ThreadParticipant.objects.filter(pk=participant.pk).update(last_delivered_at=now)
    participant.last_delivered_at = now
    last = Thread.objects.filter(pk=participant.thread_id).values_list("last_message_at", flat=True).first()
    if last and (previous is None or previous < last):
        live.bump_thread(participant.thread_id)  # senders' ticks change
    return now


def mark_read(thread, user, participant=None, now=None):
    """Create read receipts for every visible incoming message. Never moves an
    existing receipt's time (a test depends on that)."""
    now = now or timezone.now()
    participant = participant or get_participant(thread, user)
    incoming = (
        thread.messages.filter(is_deleted_for_everyone=False)
        .exclude(sender=user)
        .exclude(hidden_for__user=user)
        .exclude(read_receipts__user=user)
        .distinct()
        .values_list("pk", flat=True)
    )
    ids = list(incoming)
    if ids:
        MessageRead.objects.bulk_create(
            [MessageRead(message_id=pk, user=user, read_at=now) for pk in ids],
            ignore_conflicts=True,
        )
        live.bump_thread(thread.pk)  # senders' ticks turn blue
    participant.last_read_at = now
    participant.last_delivered_at = now
    participant.save(update_fields=["last_read_at", "last_delivered_at"])
    return ids


def _receipt_people(message, participants):
    """Participants (other than the sender) who were in the chat when it was sent."""
    return [
        p for p in participants
        if p.user_id != message.sender_id
        and (p.joined_at is None or p.joined_at <= message.sent_at + timedelta(seconds=1))
    ]


def receipt_for(message, participants):
    """{"state": "sent"|"delivered"|"read", "read_by", "delivered_to", "total"}."""
    people = _receipt_people(message, participants)
    reads = {r.user_id for r in message.read_receipts.all()}
    read_by = sum(1 for p in people if p.user_id in reads)
    delivered_to = sum(
        1 for p in people
        if p.user_id in reads
        or (p.last_delivered_at and p.last_delivered_at >= message.sent_at)
    )
    total = len(people)
    if total and read_by >= total:
        state = "read"
    elif total and delivered_to >= total:
        state = "delivered"
    else:
        state = "sent"
    return {"state": state, "read_by": read_by, "delivered_to": delivered_to,
            "total": total}


def receipt_label(receipt, is_group):
    if receipt["state"] == "read":
        return f"Read by {receipt['read_by']} of {receipt['total']}" if is_group else "Read"
    if receipt["state"] == "delivered":
        if is_group:
            return f"Delivered · read by {receipt['read_by']} of {receipt['total']}"
        return "Delivered"
    if is_group and receipt["total"]:
        return f"Sent · read by {receipt['read_by']} of {receipt['total']}"
    return "Sent"


def message_info(message, viewer):
    """Per-person delivery/read detail for the "Message info" sheet."""
    thread = message.thread
    get_participant(thread, viewer)
    participants = thread_participants(thread)
    reads = {r.user_id: r.read_at for r in message.read_receipts.all()}
    rows = []
    for p in _receipt_people(message, participants):
        read_at = reads.get(p.user_id)
        delivered = bool(read_at) or bool(
            p.last_delivered_at and p.last_delivered_at >= message.sent_at)
        rows.append({
            "user_id": p.user_id,
            "name": user_name(p.user),
            "initials": user_initials(p.user),
            "color": avatar_color(p.user_id),
            "read_at": read_at,
            "delivered": delivered,
            # Delivery is recorded per participant (last sync), so the time is
            # "delivered by": the read time when read, else the latest sync.
            "delivered_at": read_at or (p.last_delivered_at if delivered else None),
        })
    rows.sort(key=lambda r: (r["read_at"] is None, not r["delivered"], r["name"].lower()))
    receipt = receipt_for(message, participants)
    return {
        "message_id": message.pk,
        "sent_at": message.sent_at,
        "receipt": receipt,
        "read": [r for r in rows if r["read_at"]],
        "delivered": [r for r in rows if r["delivered"] and not r["read_at"]],
        "pending": [r for r in rows if not r["delivered"]],
    }


# ===========================================================================
# Typing indicator (cache, 6 s TTL - nothing stored in the database)
# ===========================================================================
def _typing_key(thread_id, user_id):
    return f"msg:typing:{thread_id}:{user_id}"


def set_typing(thread, user):
    get_participant(thread, user)
    cache.set(_typing_key(thread.pk, user.pk), user_name(user), TYPING_TTL_SECONDS)


def clear_typing(thread, user):
    cache.delete(_typing_key(thread.pk, user.pk))


def typing_users(thread, viewer, participants=None):
    participants = participants if participants is not None else thread_participants(thread)
    keys = {_typing_key(thread.pk, p.user_id): p for p in participants
            if p.user_id != viewer.pk}
    if not keys:
        return []
    found = cache.get_many(list(keys))
    return [
        {"id": keys[key].user_id, "name": name, "first_name":
         (keys[key].user.first_name or name.split(" ")[0])}
        for key, name in found.items()
    ]


# ===========================================================================
# Reactions & stars
# ===========================================================================
def toggle_reaction(message, user, emoji):
    """Set/replace the user's reaction; the same emoji again removes it.
    Returns the user's current emoji (or None)."""
    get_participant(message.thread, user)
    if emoji not in REACTION_EMOJIS:
        raise ValidationError("That reaction is not available.")
    if message.is_deleted_for_everyone or message.is_system:
        raise ValidationError("You can't react to this message.")
    existing = MessageReaction.objects.filter(message=message, user=user).first()
    result = emoji
    if existing and existing.emoji == emoji:
        existing.delete()
        result = None
    elif existing:
        existing.emoji = emoji
        existing.created_at = timezone.now()
        existing.save(update_fields=["emoji", "created_at"])
    else:
        MessageReaction.objects.create(message=message, user=user, emoji=emoji)
    message.touch()
    return result


def reaction_groups(message, viewer_id):
    groups = {}
    for reaction in message.reactions.all():
        g = groups.setdefault(reaction.emoji, {"emoji": reaction.emoji, "count": 0,
                                               "users": [], "mine": False})
        g["count"] += 1
        g["users"].append(user_name(reaction.user) if reaction.user_id != viewer_id else "You")
        if reaction.user_id == viewer_id:
            g["mine"] = True
    order = {e: i for i, e in enumerate(REACTION_EMOJIS)}
    return sorted(groups.values(), key=lambda g: (-g["count"], order.get(g["emoji"], 99)))


def toggle_star(message, user):
    """Star/unstar for this user. Returns True when now starred."""
    get_participant(message.thread, user)
    star = MessageStar.objects.filter(message=message, user=user).first()
    if star:
        star.delete()
        return False
    MessageStar.objects.create(message=message, user=user)
    return True


def starred_messages(user):
    """The user's stars on messages they can still see."""
    return (
        MessageStar.objects.filter(
            user=user,
            message__thread__participants__user=user,
            message__thread__participants__is_deleted=False,
            message__is_deleted_for_everyone=False,
        )
        .exclude(message__hidden_for__user=user)
        .select_related("message__sender", "message__thread", "message__voice_note",
                        "message__poll")
        .distinct()
        .order_by("-created_at", "-id")
    )


# ===========================================================================
# Forwarding
# ===========================================================================
def forwardable_threads(user, exclude_thread_id=None):
    qs = (
        Thread.objects.filter(participants__user=user, participants__is_deleted=False)
        .distinct()
        .prefetch_related("participants__user")
        .order_by("-last_message_at", "-id")
    )
    if exclude_thread_id:
        qs = qs.exclude(pk=exclude_thread_id)
    threads = list(qs[:200])
    for t in threads:
        t.display_title = t.display_title_for(user)
    return threads


def _copy_file(field_file):
    field_file.open("rb")
    try:
        return field_file.read()
    finally:
        field_file.close()


def forward_message(message, user, thread_ids):
    """Copy a message (text, attachments and voice note - the bytes are copied,
    never shared) into each of the user's threads in `thread_ids`."""
    get_participant(message.thread, user)
    if message.is_deleted_for_everyone or message.is_system:
        raise ValidationError("This message can't be forwarded.")
    if ChatPoll.objects.filter(message=message).exists():
        raise ValidationError("Polls can't be forwarded.")
    try:
        ids = {int(i) for i in thread_ids if str(i).strip()}
    except (TypeError, ValueError):
        raise ValidationError("Choose valid conversations.")
    if not ids:
        raise ValidationError("Choose at least one conversation.")
    targets = list(
        Thread.objects.filter(pk__in=ids, participants__user=user,
                              participants__is_deleted=False).distinct()
    )
    if len(targets) != len(ids):
        raise ValidationError("You can only forward to your own conversations.")
    from .group_services import ensure_can_send

    for target in targets:
        try:
            ensure_can_send(target, get_participant(target, user))
        except ValidationError:
            raise ValidationError(f"You can't send messages in \"{target.display_title_for(user)}\".")

    attachments = [(a.original_name or "attachment", _copy_file(a.file))
                   for a in message.attachments.all() if a.file]
    voice = VoiceNote.objects.filter(message=message).first()
    voice_bytes = _copy_file(voice.file) if voice and voice.file else None

    created = []
    now = timezone.now()
    with transaction.atomic():
        for thread in targets:
            participant = get_participant(thread, user)
            new = Message.objects.create(thread=thread, sender=user, body=message.body,
                                         sent_at=now, forwarded=True)
            for name, data in attachments:
                MessageAttachment.objects.create(message=new, file=ContentFile(data, name=name))
            if voice is not None and voice_bytes is not None:
                VoiceNote.objects.create(
                    message=new, file=ContentFile(voice_bytes, name="voice-note.m4a"),
                    duration_ms=voice.duration_ms, size_bytes=voice.size_bytes,
                    waveform=voice.waveform, mime=voice.mime,
                    transcript_status=voice.transcript_status,
                    transcript=voice.transcript,
                    transcript_language=voice.transcript_language,
                    transcript_confidence=voice.transcript_confidence,
                    transcript_edited=voice.transcript_edited,
                    transcribed_at=voice.transcribed_at,
                )
            _after_post(thread, participant, now)
            created.append(new)
    return created


# ===========================================================================
# Pin & mute
# ===========================================================================
def set_pinned(participant, pinned):
    pinned = bool(pinned)
    if pinned and not participant.is_pinned:
        count = ThreadParticipant.objects.filter(
            user_id=participant.user_id, is_pinned=True, is_deleted=False,
        ).count()
        if count >= ThreadParticipant.MAX_PINNED:
            raise ValidationError(
                f"You can pin up to {ThreadParticipant.MAX_PINNED} chats. Unpin one first.")
    participant.is_pinned = pinned
    participant.pinned_at = timezone.now() if pinned else None
    participant.save(update_fields=["is_pinned", "pinned_at"])
    return participant


def set_muted(participant, option):
    """option: "8h" | "1w" | "always" | "off"."""
    if option in ("off", "", None):
        participant.muted_until = None
    elif option in MUTE_OPTIONS:
        delta = MUTE_OPTIONS[option]
        participant.muted_until = MUTE_FOREVER if delta is None else timezone.now() + delta
    else:
        raise ValidationError("Choose how long to mute this chat.")
    participant.save(update_fields=["muted_until"])
    return participant


def muted_user_ids(thread, now=None):
    now = now or timezone.now()
    return set(
        ThreadParticipant.objects.filter(thread=thread, muted_until__gt=now)
        .values_list("user_id", flat=True)
    )


# ===========================================================================
# Inbox
# ===========================================================================
def preview_parts(message):
    """(kind, plain text) preview of a message, without icons.

    kind: "" (no message) | deleted | system | voice | poll | attachment | text.
    The mobile app draws its own icon for the kind."""
    if message is None:
        return "", ""
    if message.is_deleted_for_everyone:
        return "deleted", "This message was deleted."
    voice = VoiceNote.objects.filter(message=message).first()
    if voice is not None:
        return "voice", f"Voice note {voice.duration_label}"
    poll = ChatPoll.objects.filter(message=message).first()
    if poll is not None:
        return "poll", f"Poll: {poll.question}"
    body = (message.body or "").strip().replace("\n", " ")
    if body:
        return ("system" if message.is_system else "text"), body
    count = message.attachments.count()
    if count:
        return "attachment", (f"{count} attachments" if count > 1 else "Attachment")
    return "text", ""


_PREVIEW_ICONS = {"voice": "\U0001F3A4 ", "poll": "\U0001F4CA ", "attachment": "\U0001F4CE "}


def preview_text(message):
    """One-line preview of a message for inbox rows and notifications."""
    kind, text = preview_parts(message)
    if kind == "voice":
        # "🎤 Voice note (0:42)" on the website.
        return f"{_PREVIEW_ICONS['voice']}{text.replace('Voice note ', 'Voice note (')})"
    return _PREVIEW_ICONS.get(kind, "") + text


def inbox_threads(user, *, kind="all", archived=False):
    """The user's conversations, annotated and sorted (pinned first, then by
    latest message). kind: all | unread | groups | direct."""
    qs = (
        Thread.objects.filter(
            participants__user=user,
            participants__is_archived=archived,
            participants__is_deleted=False,
        )
        .distinct()
        .select_related("created_by")
        .prefetch_related("participants__user")
    )
    if kind == "groups":
        qs = qs.filter(thread_type=Thread.GROUP)
    elif kind == "direct":
        qs = qs.filter(thread_type=Thread.DIRECT)
    now = timezone.now()
    threads = []
    for t in qs:
        me = next((p for p in t.participants.all() if p.user_id == user.pk), None)
        t.my_participant = me
        t.unread = t.unread_count_for(user)
        t.last_msg = t.last_message(user)
        t.last_preview = preview_text(t.last_msg)
        t.display_title = t.display_title_for(user)
        t.is_pinned = bool(me and me.is_pinned)
        t.is_muted = bool(me and is_muted(me, now))
        t.is_group = t.thread_type == Thread.GROUP
        t.avatar_color = avatar_color(t.pk)
        t.sort_at = t.last_msg.sent_at if t.last_msg else t.updated_at
        if kind == "unread" and not t.unread:
            continue
        threads.append(t)
    threads.sort(key=lambda t: t.sort_at, reverse=True)
    threads.sort(key=lambda t: not t.is_pinned)  # stable: pinned first
    return threads


def inbox_updates(user, *, version=None):
    """Compact data for the inbox short-poll (and the app's chat list).

    v2.1 L9: answers ``{"unchanged": true, "v": ...}`` (no database work) when
    the client's ``version`` is still current; otherwise the payload comes from
    a 5-second cache keyed by the version (messaging/live.py)."""
    current = live.inbox_version(user.pk)
    if version and version == current:
        return {"unchanged": True, "v": current, "server_time": timezone.now().isoformat()}
    current, payload = live.cached_inbox_payload(user, lambda: _inbox_payload(user))
    return {**payload, "v": current}


def _inbox_payload(user):
    rows = []
    for t in inbox_threads(user):
        last = t.last_msg
        rows.append({
            "id": t.pk,
            "title": t.display_title,
            "unread": t.unread,
            "pinned": t.is_pinned,
            "muted": t.is_muted,
            "is_group": t.is_group,
            "preview": t.last_preview,
            "sender": (("You" if last.sender_id == user.pk else user_name(last.sender))
                       if last else ""),
            "last_at": last.sent_at.isoformat() if last else None,
            "sort_at": t.sort_at.isoformat(),
        })
    return {
        "server_time": timezone.now().isoformat(),
        "threads": rows,
        "total_unread": sum(r["unread"] for r in rows),
    }


# ===========================================================================
# Search inside a chat
# ===========================================================================
def search_message_ids(thread, user, q):
    q = (q or "").strip()
    if not q:
        return []
    get_participant(thread, user)
    return list(
        visible_messages(thread, user)
        .filter(is_deleted_for_everyone=False)
        .filter(Q(body__icontains=q) | Q(voice_note__transcript__icontains=q)
                | Q(poll__question__icontains=q))
        .order_by("sent_at", "id")
        .values_list("pk", flat=True)
    )


def highlight_html(text, q):
    """Escape `text` and wrap case-insensitive matches of q in <mark>."""
    from django.utils.html import escape

    escaped = escape(text or "")
    q = (q or "").strip()
    if not q:
        return escaped
    pattern = re.compile(re.escape(escape(q)), re.IGNORECASE)
    return pattern.sub(lambda m: f'<mark class="msg-hit">{m.group(0)}</mark>', escaped)


# ===========================================================================
# Turn a message into a task / memo
# ===========================================================================
def _message_context_text(message):
    when = timezone.localtime(message.sent_at).strftime("%d %b %Y %H:%M")
    body = message.body or ""
    voice = VoiceNote.objects.filter(message=message).first()
    if voice is not None and voice.transcript:
        body = f"Voice note transcript: {voice.transcript}"
    return f"{body}\n\n— From a chat message by {user_name(message.sender)} on {when}."


def _title_from(message, limit):
    text = (message.body or "").strip().splitlines()[0] if (message.body or "").strip() else ""
    if not text:
        voice = VoiceNote.objects.filter(message=message).first()
        text = (voice.transcript if voice and voice.transcript else "Voice note follow-up")
    return text[:limit]


def task_prefill_params(message):
    return {"title": _title_from(message, 200), "description": _message_context_text(message),
            "from_message": message.pk}


def memo_prefill_params(message):
    return {"subject": _title_from(message, 200), "body": _message_context_text(message),
            "from_message": message.pk}


def task_prefill_url(message):
    return reverse("tasks:create") + "?" + urlencode(task_prefill_params(message))


def memo_prefill_url(message):
    return reverse("messaging:memo_create") + "?" + urlencode(memo_prefill_params(message))


def _linkable_message(message_id, user):
    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return None
    message = Message.objects.select_related("thread").filter(pk=message_id).first()
    if message is None or message.is_deleted_for_everyone:
        return None
    if not message.thread.participants.filter(user=user, is_deleted=False).exists():
        return None
    if message.hidden_for.filter(user=user).exists():
        return None
    return message


def link_message_to_task(message_id, task, user):
    """Called by the task create view after a task was made from a message."""
    message = _linkable_message(message_id, user)
    if message is None:
        return None
    message.linked_task = task
    message.changed_at = timezone.now()
    message.save(update_fields=["linked_task", "changed_at"])
    return message


def link_message_to_memo(message_id, memo, user):
    message = _linkable_message(message_id, user)
    if message is None:
        return None
    message.linked_memo = memo
    message.changed_at = timezone.now()
    message.save(update_fields=["linked_memo", "changed_at"])
    return message


# ===========================================================================
# Polls
# ===========================================================================
def create_poll(thread, user, *, question, options, allow_multiple=False, closes_at=None):
    from .group_services import ensure_can_send

    participant = get_participant(thread, user)
    ensure_can_send(thread, participant)
    if thread.thread_type != Thread.GROUP:
        raise ValidationError("Polls are available in group chats.")
    question = (question or "").strip()
    cleaned = []
    for option in options or []:
        text = (option or "").strip()
        if text and text.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(text[:120])
    if not question:
        raise ValidationError("Write a question for the poll.")
    if not MIN_POLL_OPTIONS <= len(cleaned) <= MAX_POLL_OPTIONS:
        raise ValidationError("A poll needs between 2 and 6 different options.")
    now = timezone.now()
    with transaction.atomic():
        message = Message.objects.create(thread=thread, sender=user, body=question[:300],
                                         sent_at=now)
        poll = ChatPoll.objects.create(message=message, question=question[:300],
                                       allow_multiple=bool(allow_multiple),
                                       closes_at=closes_at)
        ChatPollOption.objects.bulk_create([
            ChatPollOption(poll=poll, text=text, position=i)
            for i, text in enumerate(cleaned)
        ])
        _after_post(thread, participant, now)
    return message


def vote_poll(poll, user, option_ids):
    """Cast the user's vote. Single-choice polls replace the previous vote;
    multiple-choice polls set exactly the given options (empty = withdraw)."""
    message = poll.message
    get_participant(message.thread, user)
    if message.is_deleted_for_everyone:
        raise ValidationError("This poll was deleted.")
    if poll.is_closed():
        raise ValidationError("This poll is closed.")
    try:
        wanted = {int(i) for i in option_ids}
    except (TypeError, ValueError):
        raise ValidationError("Choose a valid option.")
    valid = set(poll.options.values_list("pk", flat=True))
    if not wanted <= valid:
        raise ValidationError("Choose a valid option.")
    if not poll.allow_multiple and len(wanted) > 1:
        raise ValidationError("This poll allows one answer.")
    with transaction.atomic():
        if not poll.allow_multiple and wanted:
            current = set(ChatPollVote.objects.filter(poll=poll, user=user)
                          .values_list("option_id", flat=True))
            if current == wanted:  # tapping your own answer again withdraws it
                wanted = set()
        ChatPollVote.objects.filter(poll=poll, user=user).exclude(option_id__in=wanted).delete()
        existing = set(ChatPollVote.objects.filter(poll=poll, user=user)
                       .values_list("option_id", flat=True))
        ChatPollVote.objects.bulk_create([
            ChatPollVote(poll=poll, option_id=oid, user=user) for oid in wanted - existing
        ])
    message.touch()
    return poll


def poll_results(poll, viewer):
    votes = list(poll.votes.all())
    voters = {v.user_id for v in votes}
    options = []
    for option in poll.options.all():
        option_votes = [v for v in votes if v.option_id == option.pk]
        count = len(option_votes)
        options.append({
            "id": option.pk,
            "text": option.text,
            "votes": count,
            "percent": round(100 * count / len(voters)) if voters else 0,
            "mine": any(v.user_id == viewer.pk for v in option_votes),
            "voters": [user_name(v.user) for v in option_votes],
        })
    top = max((o["votes"] for o in options), default=0)
    for o in options:
        o["leading"] = bool(top) and o["votes"] == top
    return {
        "id": poll.pk,
        "question": poll.question,
        "allow_multiple": poll.allow_multiple,
        "closed": poll.is_closed(),
        "closes_at": poll.closes_at.isoformat() if poll.closes_at else None,
        "voter_count": len(voters),
        "voted": viewer.pk in voters,
        "options": options,
    }


# ===========================================================================
# Rendering helpers + the canonical payload
# ===========================================================================
def _one_to_one(message, attr):
    try:
        return getattr(message, attr)
    except Exception:
        return None


def prepare_message(message, viewer, participants, *, is_group=None, q=""):
    """Attach the derived attributes the message template and the payload use.
    Safe to call more than once."""
    if getattr(message, "_prepared_for", None) == viewer.pk:
        return message
    if is_group is None:
        is_group = message.thread.thread_type == Thread.GROUP
    message.is_mine = message.sender_id == viewer.pk
    message.is_group = is_group
    message.can_change_for_everyone = message.is_mine and can_change_for_everyone(message)
    message.reply_preview = message.reply_sender = None
    if message.reply_to_id:
        quoted = message.reply_to
        if quoted.is_deleted_for_everyone or quoted.hidden_for.filter(user=viewer).exists():
            message.reply_preview = "Message unavailable"
            message.reply_sender = "Unavailable"
        else:
            quoted_voice = VoiceNote.objects.filter(message=quoted).first()
            message.reply_preview = (
                f"\U0001F3A4 Voice note ({quoted_voice.duration_label})"
                if quoted_voice else (quoted.body or "Attachment")
            )
            message.reply_sender = user_name(quoted.sender) if quoted.sender_id else "System"
    message.seen_by_someone = any(r.user_id != message.sender_id
                                  for r in message.read_receipts.all())
    message.msg_color = avatar_color(message.sender_id or message.pk or 0)
    message.sender_name = user_name(message.sender) if message.sender_id else "System"
    message.sender_initials = user_initials(message.sender) if message.sender_id else "?"
    message.reaction_list = [] if message.is_deleted_for_everyone else reaction_groups(message, viewer.pk)
    message.my_reaction = next((g["emoji"] for g in message.reaction_list if g["mine"]), None)
    stars = getattr(message, "viewer_stars", None)
    message.is_starred = bool(stars) if stars is not None else MessageStar.objects.filter(
        message=message, user=viewer).exists()
    message.receipt = receipt_for(message, participants) if message.is_mine else None
    message.receipt_text = receipt_label(message.receipt, is_group) if message.receipt else ""
    poll = None if message.is_deleted_for_everyone else _one_to_one(message, "poll")
    message.poll_data = poll_results(poll, viewer) if poll else None
    voice = None if message.is_deleted_for_everyone else _one_to_one(message, "voice_note")
    if voice is not None and voice.is_deleted:
        voice = None
    message.voice = voice
    if voice is not None:
        message.voice_played = message.is_mine or any(
            p.user_id == viewer.pk for p in voice.plays.all())
        message.voice_bars = voice.waveform or [8] * 48
    reminders = getattr(message, "viewer_reminders", None)
    if reminders is None:
        reminders = list(MessageReminder.objects.filter(message=message, user=viewer, sent_at__isnull=True))
    message.my_reminder = reminders[0] if reminders else None
    message.is_pinned_msg = message.pinned_at is not None and not message.is_deleted_for_everyone
    message.search_q = q
    message._prepared_for = viewer.pk
    return message


def _safe_size(field_file):
    try:
        return field_file.size if field_file else 0
    except (OSError, ValueError):
        return 0


def voice_payload(voice, viewer, message=None):
    if voice is None:
        return None
    played = getattr(message, "voice_played", None)
    if played is None:
        played = (message is not None and message.sender_id == viewer.pk) or \
            VoiceNotePlay.objects.filter(voice_note=voice, user=viewer).exists()
    return {
        "id": voice.pk,
        "url": reverse("messaging:voice_audio", args=[voice.pk]),
        "mime": voice.mime,
        "duration_ms": voice.duration_ms,
        "size_bytes": voice.size_bytes,
        "waveform": voice.waveform or [],
        "played": bool(played),
        "transcript": {
            "status": voice.transcript_status,
            "text": voice.transcript,
            "language": voice.transcript_language,
            "confidence": voice.transcript_confidence,
            "edited": voice.transcript_edited,
            "transcribed_at": voice.transcribed_at.isoformat() if voice.transcribed_at else None,
        },
    }


def message_payload(message, viewer, participants=None):
    """Plain-data representation of a message for one viewer (JSON-safe)."""
    if participants is None:
        participants = thread_participants(message.thread)
    prepare_message(message, viewer, participants)
    deleted = message.is_deleted_for_everyone
    return {
        "id": message.pk,
        "thread_id": message.thread_id,
        "sender": None if message.sender_id is None else {
            "id": message.sender_id,
            "name": message.sender_name,
            "initials": message.sender_initials,
        },
        "is_mine": message.is_mine,
        "is_system": message.is_system,
        "kind": ("system" if message.is_system else "deleted" if deleted
                 else "voice" if message.voice else "poll" if message.poll_data else "text"),
        "body": message.body,
        "sent_at": message.sent_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "changed_at": message.changed_at.isoformat() if message.changed_at else None,
        "is_deleted": deleted,
        "forwarded": message.forwarded,
        "reply_to": None if not message.reply_to_id else {
            "id": message.reply_to_id,
            "sender_name": message.reply_sender,
            "preview": (message.reply_preview or "")[:180],
        },
        "attachments": [] if deleted else [
            {"id": a.pk, "name": a.original_name or "Attachment",
             "size": _safe_size(a.file), "url": a.download_url()}
            for a in message.attachments.all()
        ],
        "reactions": message.reaction_list,
        "my_reaction": message.my_reaction,
        "starred": message.is_starred,
        "receipt": message.receipt,
        "poll": message.poll_data,
        "voice_note": voice_payload(message.voice, viewer, message),
        "linked_task": None if not message.linked_task_id else {
            "id": message.linked_task_id,
            "reference": message.linked_task.reference,
            "url": reverse("tasks:detail", args=[message.linked_task_id]),
        },
        "linked_memo": None if not message.linked_memo_id else {
            "id": message.linked_memo_id,
            "reference": message.linked_memo.reference,
            "url": reverse("messaging:memo_detail", args=[message.linked_memo_id]),
        },
        "pinned_at": message.pinned_at.isoformat() if message.is_pinned_msg else None,
        "my_reminder_at": message.my_reminder.remind_at.isoformat() if message.my_reminder else None,
        "can_edit": message.can_change_for_everyone,
        "can_delete_for_everyone": message.can_change_for_everyone,
    }


# ===========================================================================
# Thread snapshot & live updates
# ===========================================================================
def open_thread(thread, user, *, q=""):
    """Everything a client needs to show a thread; marks delivered + read."""
    participant = get_participant(thread, user)
    version = live.thread_version(thread.pk)
    now = timezone.now()
    mark_read(thread, user, participant, now)
    participants = thread_participants(thread)
    is_group = thread.thread_type == Thread.GROUP
    messages = list(message_queryset(thread, user))
    for m in messages:
        prepare_message(m, user, participants, is_group=is_group, q=q)
    return {
        "participant": participant,
        "participants": participants,
        "messages": messages,
        "search_ids": search_message_ids(thread, user, q) if q else [],
        "v": version,
        "server_time": now,
        "is_group": is_group,
    }


def parse_since(value):
    """ISO timestamp from the client (normally our own `server_time`)."""
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    try:
        parsed = parse_datetime(str(value).replace(" ", "+"))
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def thread_updates(thread, user, *, after_id=0, since=None, visible=True, version=None):
    """One short-poll answer. Never waits.

    Returns Message objects (prepared for `user`) under "new"/"changed" so a
    web view can render HTML; API callers map them through message_payload().

    v2.1 L9: when the client's ``version`` token is still current nothing in
    the thread changed, so the answer is ``unchanged`` (typing only): no
    message queries and no read-receipt write.
    """
    participant = get_participant(thread, user)
    current = live.thread_version(thread.pk)
    if version and version == current and since:
        return {"unchanged": True, "v": current, "typing": typing_users(thread, user)}
    now = timezone.now()
    if visible:
        mark_read(thread, user, participant, now)
    else:
        mark_delivered(participant, now)
    participants = thread_participants(thread)
    is_group = thread.thread_type == Thread.GROUP
    try:
        after_id = int(after_id or 0)
    except (TypeError, ValueError):
        after_id = 0
    if isinstance(since, str):
        since = parse_since(since)

    qs = message_queryset(thread, user)
    new = list(qs.filter(pk__gt=after_id)[:UPDATES_BATCH])
    changed = []
    if since is not None:
        # 2 s overlap covers rows committed just after the previous answer.
        changed = list(qs.filter(pk__lte=after_id,
                                 changed_at__gte=since - timedelta(seconds=2)))
    for m in new + changed:
        prepare_message(m, user, participants, is_group=is_group)

    recent_mine = list(
        visible_messages(thread, user).filter(sender=user, is_deleted_for_everyone=False)
        .prefetch_related("read_receipts").order_by("-id")[:UPDATES_BATCH]
    )
    receipts = {}
    for m in recent_mine:
        r = receipt_for(m, participants)
        r["label"] = receipt_label(r, is_group)
        receipts[m.pk] = r

    last_id = max([after_id] + [m.pk for m in new])
    return {
        "v": current,
        "server_time": now,
        "new": new,
        "changed": changed,
        "receipts": receipts,
        "typing": typing_users(thread, user, participants),
        "last_id": last_id,
    }


# ===========================================================================
# Phase 9 API: helpers the website and the mobile API share
# ===========================================================================
MAX_PAGE = 100
DEFAULT_PAGE = 30


def messageable_users(user, q=""):
    """People ``user`` can start a conversation with - the website's compose
    picker: active users with a staff record, not themselves. ``q`` matches
    name, e-mail or department (like the picker's search box)."""
    from accounts.models import User
    from staff.models import Staff

    qs = (
        User.objects.filter(pk__in=Staff.objects.filter(user__isnull=False).values("user_id"),
                            is_active=True)
        .exclude(pk=user.pk)
        .select_related("department")
        .order_by("first_name", "last_name", "email")
    )
    q = (q or "").strip()
    if q:
        for word in q.split()[:4]:
            qs = qs.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word)
                           | Q(email__icontains=word) | Q(department__name__icontains=word))
    return qs


def start_conversation(user, recipient_ids, *, subject="", body="", uploads=()):
    """Compose rules shared by the website (compose_send) and the API:
    at least one recipient from messageable_users(), a non-empty body,
    attachments within 5 MB. Returns the new Thread."""
    from .models import start_thread

    uploads = list(uploads or [])
    check_attachment_sizes(uploads)
    try:
        ids = {int(i) for i in recipient_ids or [] if str(i).strip()}
    except (TypeError, ValueError):
        raise ValidationError("Choose valid recipients.")
    if not ids:
        raise ValidationError("Choose at least one recipient.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("Message body cannot be empty.")
    recipients = list(messageable_users(user).filter(pk__in=ids))
    if not recipients:
        raise ValidationError("No valid recipients.")
    with transaction.atomic():
        thread = start_thread(creator=user, participants=recipients,
                              subject=(subject or "").strip(), initial_body=body)
        first = thread.messages.order_by("-sent_at", "-id").first()
        for upload in uploads:
            MessageAttachment.objects.create(message=first, file=upload)
    return thread


def set_archived(participant, archived):
    participant.is_archived = bool(archived)
    participant.save(update_fields=["is_archived"])
    return participant


def message_page(thread, user, *, before=None, after=None, around=None, limit=DEFAULT_PAGE):
    """One page of visible messages, chronological within the page.

    * default / ``before``: the newest ``limit`` messages (older than ``before``)
    * ``after``: the oldest ``limit`` messages newer than ``after``
    * ``around``: the page centred on a message (search jumps); 404 if hidden
    Marks the thread delivered + read (like open_thread)."""
    participant = get_participant(thread, user)
    now = timezone.now()
    mark_read(thread, user, participant, now)
    try:
        limit = max(1, min(MAX_PAGE, int(limit or DEFAULT_PAGE)))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE
    qs = message_queryset(thread, user)
    if around:
        anchor = qs.filter(pk=around).first()
        if anchor is None:
            raise Http404("Message not found.")
        half = limit // 2
        older = list(qs.filter(pk__lt=anchor.pk).order_by("-sent_at", "-id")[:half])
        newer = list(qs.filter(pk__gt=anchor.pk)[: limit - half - 1])
        messages = list(reversed(older)) + [anchor] + newer
    elif after:
        messages = list(qs.filter(pk__gt=after)[:limit])
    else:
        page = qs.order_by("-sent_at", "-id")
        if before:
            page = page.filter(pk__lt=before)
        messages = list(reversed(list(page[:limit])))
    participants = thread_participants(thread)
    is_group = thread.thread_type == Thread.GROUP
    for m in messages:
        prepare_message(m, user, participants, is_group=is_group)
    first_id = messages[0].pk if messages else None
    last_id = messages[-1].pk if messages else None
    visible = visible_messages(thread, user)
    return {
        "messages": messages,
        "participants": participants,
        "has_older": bool(first_id) and visible.filter(pk__lt=first_id).exists(),
        "has_newer": bool(last_id) and visible.filter(pk__gt=last_id).exists(),
        "server_time": now,
        "last_id": visible.order_by("-id").values_list("pk", flat=True).first() or 0,
    }


def search_all(user, q, limit=50):
    """Messages (text, voice transcripts, poll questions) across every
    conversation the user is still in. Returns Message objects, newest first."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    return list(
        Message.objects.filter(
            thread__participants__user=user,
            thread__participants__is_deleted=False,
            is_deleted_for_everyone=False,
        )
        .exclude(hidden_for__user=user)
        .filter(Q(body__icontains=q) | Q(voice_note__transcript__icontains=q)
                | Q(poll__question__icontains=q))
        .select_related("thread", "sender", "voice_note")
        .distinct()
        .order_by("-sent_at", "-id")[:limit]
    )


def search_snippet(message, q, width=80):
    """Plain-text excerpt around the first match of q."""
    text = (message.body or "").strip()
    voice = _one_to_one(message, "voice_note")
    if voice is not None and voice.transcript and q.lower() not in text.lower():
        text = voice.transcript
    text = text.replace("\n", " ")
    pos = text.lower().find(q.lower())
    if pos < 0 or len(text) <= width:
        return text[:width]
    start = max(0, pos - width // 3)
    snippet = text[start:start + width]
    return ("…" if start else "") + snippet + ("…" if start + width < len(text) else "")


def get_attachment_for(user, attachment_id):
    attachment = MessageAttachment.objects.select_related("message__thread").filter(
        pk=attachment_id).first()
    if attachment is None or not attachment.file or not attachment.can_download(user):
        raise Http404
    return attachment


def get_poll_for(user, poll_id):
    """A poll in a conversation the user is in (and can see), else Http404."""
    poll = ChatPoll.objects.select_related("message__thread").filter(pk=poll_id).first()
    if poll is None:
        raise Http404
    thread = poll.message.thread
    get_participant(thread, user)
    get_visible_message(thread, user, poll.message_id)
    return poll
