"""Chats for the mobile app: shapes messaging.chat_services data as JSON.

Every rule (who may see/post/edit, receipts, typing, polls, voice notes)
lives in messaging.chat_services / messaging.voice; this module only maps
their results to the contract shapes in api/docs/chats.md and replaces the
website URLs in ``message_payload`` with API paths.
"""
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from messaging import chat_services as cs
from messaging.models import REACTION_EMOJIS, Thread


def _iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()
    return value


def _api_path(name, *args):
    return reverse(f"api:{name}", args=args)


def person(user, viewer=None):
    if user is None:
        return None
    return {"id": user.pk, "name": cs.user_name(user), "initials": cs.user_initials(user),
            "color": cs.avatar_color(user.pk)}


def thread_initials(thread, viewer, participants):
    others = [p.user for p in participants if p.user_id != viewer.pk]
    if thread.thread_type == Thread.DIRECT and others:
        return cs.user_initials(others[0])
    if thread.subject:
        words = [w for w in thread.subject.split() if w[:1].isalnum()]
        return "".join(w[0] for w in words[:3]).upper() or thread.subject[:1].upper()
    return "".join(cs.user_initials(u)[:1] for u in others[:2]) or "?"


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def message_json(message, viewer, participants=None):
    """messaging.chat_services.message_payload + API paths and flat voice fields."""
    data = cs.message_payload(message, viewer, participants)
    deleted = data["is_deleted"]
    attachments = []
    if not deleted:
        for a in message.attachments.all():
            attachments.append({
                "id": a.pk,
                "name": a.original_name or "Attachment",
                "size_bytes": cs._safe_size(a.file),
                "content_type": _content_type(a.original_name or a.file.name),
                "download_path": _api_path("chats_attachment", a.pk),
                "expires_at": _iso(a.expires_at),
            })
    voice = data["voice_note"]
    voice_json = None
    if voice:
        t = voice["transcript"]
        voice_json = {
            "id": voice["id"],
            "duration_ms": voice["duration_ms"],
            "waveform": voice["waveform"],
            "size_bytes": voice["size_bytes"],
            "mime": voice["mime"],
            "audio_path": _api_path("chats_voice_audio", voice["id"]),
            "played": voice["played"],
            "transcript_status": t["status"],
            "transcript": t["text"],
            "transcript_language": t["language"],
            "transcript_edited": t["edited"],
        }
    receipt = data["receipt"]
    if receipt is not None:
        receipt = dict(receipt, label=cs.receipt_label(receipt, message.is_group))
    return {
        "id": data["id"],
        "thread_id": data["thread_id"],
        "kind": data["kind"],
        "sender": None if message.sender_id is None else {
            **data["sender"], "color": cs.avatar_color(message.sender_id)},
        "is_mine": data["is_mine"],
        "is_system": data["is_system"],
        "body": data["body"],
        "sent_at": _iso(message.sent_at),
        "edited": bool(message.edited_at) and not deleted,
        "edited_at": _iso(message.edited_at),
        "changed_at": _iso(message.changed_at),
        "deleted": deleted,
        "forwarded": data["forwarded"],
        "reply_to": data["reply_to"],
        "attachments": attachments,
        "voice_note": voice_json,
        "poll": data["poll"],
        "reactions": data["reactions"],
        "my_reaction": data["my_reaction"],
        "starred": data["starred"],
        "receipt": receipt,
        "linked_task": None if not message.linked_task_id else {
            "id": message.linked_task_id, "reference": message.linked_task.reference,
            "route": f"/tasks/{message.linked_task_id}"},
        "linked_memo": None if not message.linked_memo_id else {
            "id": message.linked_memo_id, "reference": message.linked_memo.reference,
            "route": f"/memos/{message.linked_memo_id}"},
        "can_edit": data["can_edit"],
        "can_delete_for_everyone": data["can_delete_for_everyone"],
    }


def _content_type(name):
    import mimetypes

    return mimetypes.guess_type(name or "")[0] or "application/octet-stream"


def fresh_message_json(thread, viewer, message_id):
    """Re-read a message with everything prefetched (after an action)."""
    participants = cs.thread_participants(thread)
    message = cs.get_visible_message(thread, viewer, message_id)
    return message_json(message, viewer, participants)


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------
FILTERS = ("all", "unread", "groups", "direct", "archived")


def thread_rows(user, *, kind="all", q=""):
    """inbox_threads() for the list filters; "archived" = the archive."""
    if kind == "archived":
        threads = cs.inbox_threads(user, archived=True)
    else:
        threads = cs.inbox_threads(user, kind=kind)
    q = (q or "").strip().lower()
    if q:
        threads = [t for t in threads if q in t.display_title.lower() or any(
            q in cs.user_name(p.user).lower() for p in t.participants.all())]
    return threads


def thread_row_json(t, user, archived=False):
    participants = list(t.participants.all())
    others = [p for p in participants if p.user_id != user.pk and not p.is_deleted]
    last = t.last_msg
    kind, text = cs.preview_parts(last)
    me = t.my_participant
    return {
        "id": t.pk,
        "title": t.display_title,
        "kind": t.thread_type,
        "avatar": {"initials": thread_initials(t, user, participants), "color": t.avatar_color,
                   "seed": t.pk},
        "participants": [person(p.user) for p in others[:5]],
        "participant_count": len([p for p in participants if not p.is_deleted]),
        "last_message": None if last is None else {
            "id": last.pk,
            "kind": kind,
            "preview": text,
            "sender_name": "You" if last.sender_id == user.pk else cs.user_name(last.sender),
            "sent_at": _iso(last.sent_at),
        },
        "last_message_at": _iso(last.sent_at if last else t.sort_at),
        "unread": t.unread,
        "pinned": t.is_pinned,
        "muted": t.is_muted,
        "muted_until": _iso(me.muted_until) if me and t.is_muted else None,
        "archived": bool(me and me.is_archived),
        "typing": [row["first_name"] for row in cs.typing_users(t, user, participants)],
    }


def thread_detail_json(thread, participant, user):
    participants = cs.thread_participants(thread)
    rows = []
    for p in participants:
        if p.is_deleted:
            continue
        u = p.user
        staff = getattr(u, "staff_profile", None)
        rows.append({
            **person(u),
            "role": "creator" if thread.created_by_id == u.pk else "member",
            "is_me": u.pk == user.pk,
            "designation": (staff.designation.name if staff is not None and staff.designation_id else None),
            "department": u.department.name if u.department_id else None,
            "joined_at": _iso(p.joined_at),
        })
    is_group = thread.thread_type == Thread.GROUP
    return {
        "id": thread.pk,
        "title": thread.display_title_for(user),
        "subject": thread.subject,
        "kind": thread.thread_type,
        "avatar": {"initials": thread_initials(thread, user, participants),
                   "color": cs.avatar_color(thread.pk), "seed": thread.pk},
        "created_by": person(thread.created_by),
        "created_at": _iso(thread.created_at),
        "participants": rows,
        "pinned": participant.is_pinned,
        "muted": cs.is_muted(participant),
        "muted_until": _iso(participant.muted_until) if cs.is_muted(participant) else None,
        "archived": participant.is_archived,
        "unread": thread.unread_count_for(user),
        "can_post": True,
        "can_create_poll": is_group,
        "reaction_emojis": list(REACTION_EMOJIS),
        "limits": {
            "attachments_total_bytes": cs.MAX_ATTACHMENT_BYTES,
            "voice_max_seconds": int(getattr(settings, "VOICE_MAX_SECONDS", 900)),
            "poll_options_min": cs.MIN_POLL_OPTIONS,
            "poll_options_max": cs.MAX_POLL_OPTIONS,
            "edit_window_minutes": int(cs.EDIT_WINDOW.total_seconds() // 60),
        },
    }


def updates_json(thread, user, data):
    participants = cs.thread_participants(thread)
    return {
        "server_time": _iso(data["server_time"]),
        "last_id": data["last_id"],
        "new": [message_json(m, user, participants) for m in data["new"]],
        "changed": [message_json(m, user, participants) for m in data["changed"]],
        "receipts": {str(k): v for k, v in data["receipts"].items()},
        "typing": data["typing"],
    }


def info_json(info):
    def row(r):
        return {k: _iso(v) for k, v in r.items()}

    receipt = info["receipt"]
    return {
        "message_id": info["message_id"],
        "sent_at": _iso(info["sent_at"]),
        "receipt": receipt,
        "read": [row(r) for r in info["read"]],
        "delivered": [row(r) for r in info["delivered"]],
        "pending": [row(r) for r in info["pending"]],
    }


def contact_json(user):
    staff = getattr(user, "staff_profile", None)
    return {
        **person(user),
        "email": user.email,
        "department": user.department.name if user.department_id else None,
        "designation": (staff.designation.name if staff is not None and staff.designation_id else None),
    }


def star_json(star, user):
    m = star.message
    thread = m.thread
    message = cs.get_visible_message(thread, user, m.pk)
    return {
        "starred_at": _iso(star.created_at),
        "thread_id": thread.pk,
        "thread_title": thread.display_title_for(user),
        "message": message_json(message, user),
    }


def search_hit_json(message, user, q):
    kind, _ = cs.preview_parts(message)
    return {
        "thread_id": message.thread_id,
        "thread_title": message.thread.display_title_for(user),
        "message_id": message.pk,
        "kind": kind,
        "snippet": cs.search_snippet(message, q),
        "sender_name": "You" if message.sender_id == user.pk else cs.user_name(message.sender),
        "sent_at": _iso(message.sent_at),
    }
