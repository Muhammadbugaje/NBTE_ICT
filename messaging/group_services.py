"""Group administration, invite links, in-chat pins and reminders
(v2.1 Phase E: L3, L4, L6, L7; X41 read-only/auto-managed rules).

The web views (messaging/group_views.py) and the REST API (api/views/chats.py)
both call these functions; every rule lives here.

Conventions (as messaging.chat_services): access problems raise Http404 (a
non-participant cannot tell the chat exists); rule violations raise
ValidationError; "not allowed" raises PermissionDenied.

Who may do what in a group
--------------------------
* owner / admin: change settings, remove, promote and demote members (never
  the owner), pin messages, create and revoke invite links.
* edit the name, photo and description: Thread.edit_info_policy (all/admins).
* add members and share links: Thread.add_members_policy (all/admins).
* send messages, polls and voice notes: Thread.send_policy (all/admins).
* auto-managed groups (X41): membership follows the subject's team, so nobody
  adds, removes, leaves or invites by hand; archived groups are read-only.
* direct chats have no roles: both people may pin; there is nothing else.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from .models import Message, MessageReminder, Thread, ThreadInvite, ThreadParticipant

OWNER, ADMIN, MEMBER = ThreadParticipant.OWNER, ThreadParticipant.ADMIN, ThreadParticipant.MEMBER
POLICIES = (Thread.ALL, Thread.ADMINS)
MAX_PHOTO_BYTES = 2 * 1024 * 1024
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_TITLE = 120
MAX_DESCRIPTION = 500
MAX_INVITE_DAYS = 30


# ---------------------------------------------------------------------------
# Settings (ConfigEntry, seeded from messaging/settings_catalogue.py)
# ---------------------------------------------------------------------------
def _setting(key, default):
    try:
        from config.registry import get

        return get(key, default)
    except Exception:  # pragma: no cover
        return default


def pins_per_chat():
    return max(1, int(_setting("messaging.pins_per_chat", 3)))


def invite_days_default():
    return max(1, min(int(_setting("messaging.invite_link_days", 7)), MAX_INVITE_DAYS))


# ---------------------------------------------------------------------------
# Access helpers
# ---------------------------------------------------------------------------
def _participant(thread, user):
    from .chat_services import get_participant

    return get_participant(thread, user)


def is_group(thread):
    return thread.thread_type == Thread.GROUP


def _is_admin(participant):
    return participant.role in (OWNER, ADMIN)


def _policy_allows(policy, participant):
    return policy == Thread.ALL or _is_admin(participant)


def permissions_for(thread, participant):
    """Flags the UI and the API show (and the services below enforce)."""
    group = is_group(thread)
    archived = thread.archived_at is not None
    admin = group and _is_admin(participant)
    manual = group and not thread.auto_managed and not archived
    return {
        "is_admin": admin,
        "is_owner": group and participant.role == OWNER,
        "can_send": not archived and (not group or _policy_allows(thread.send_policy, participant)),
        "can_edit_info": group and not archived and _policy_allows(thread.edit_info_policy, participant),
        "can_change_settings": admin and not archived,
        "can_add_members": manual and _policy_allows(thread.add_members_policy, participant),
        "can_invite": manual and _policy_allows(thread.add_members_policy, participant),
        "can_manage_members": manual and admin,
        "can_leave": manual,
        "can_pin": not archived and (not group or admin),
        "read_only": archived,
        "auto_managed": bool(thread.auto_managed),
    }


def _require(flag, thread, participant, message):
    if not permissions_for(thread, participant)[flag]:
        raise PermissionDenied(message)


def ensure_can_send(thread, participant):
    """Called by every way of posting (text, poll, voice note, forward)."""
    if thread.archived_at is not None:
        raise ValidationError("This group is archived and read-only.")
    if is_group(thread) and not _policy_allows(thread.send_policy, participant):
        raise ValidationError("Only admins can send messages in this group.")


def post_system_message(thread, body, *, now=None):
    now = now or timezone.now()
    message = Message.objects.create(thread=thread, sender=None, body=body[:1000], sent_at=now,
                                     is_system=True)
    Thread.objects.filter(pk=thread.pk).update(last_message_at=now)
    thread.last_message_at = now
    return message


def _name(user):
    from .chat_services import user_name

    return user_name(user)


def _names(users):
    names = [_name(u) for u in users]
    if len(names) > 3:
        return f"{', '.join(names[:3])} and {len(names) - 3} more"
    if len(names) > 1:
        return f"{', '.join(names[:-1])} and {names[-1]}"
    return names[0] if names else ""


# ---------------------------------------------------------------------------
# Group information (L3)
# ---------------------------------------------------------------------------
def members(thread):
    order = {OWNER: 0, ADMIN: 1, MEMBER: 2}
    rows = list(thread.participants.select_related("user"))
    rows.sort(key=lambda p: (order.get(p.role, 3), (p.user.get_full_name() or p.user.email).lower()))
    return rows


def member_counts(thread):
    rows = list(thread.participants.values_list("role", flat=True))
    return {"members": len(rows), "admins": sum(1 for r in rows if r in (OWNER, ADMIN))}


def photo_url(thread):
    if not thread.photo:
        return ""
    return reverse("messaging:group_photo", args=[thread.pk]) + f"?v={int(thread.updated_at.timestamp())}"


def group_info(thread, user):
    """Everything the group-info sheet / API shows to one participant."""
    me = _participant(thread, user)
    perms = permissions_for(thread, me)
    counts = member_counts(thread)
    invites = []
    if perms["can_invite"]:
        invites = [i for i in thread.invites.filter(revoked_at__isnull=True).order_by("-created_at")
                   if i.is_usable()]
    return {
        "thread": thread,
        "me": me,
        "is_group": is_group(thread),
        "permissions": perms,
        "members": members(thread),
        "counts": counts,
        "photo_url": photo_url(thread),
        "invites": invites,
        "pinned": pinned_messages(thread, user),
    }


def update_info(thread, user, *, title=None, description=None, photo=None, remove_photo=False):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("Only group chats have a name, photo and description.")
    _require("can_edit_info", thread, me, "Only admins can change this group's information.")
    changes, fields = [], []
    if title is not None:
        title = " ".join(title.split())[:MAX_TITLE]
        if not title:
            raise ValidationError("A group needs a name.")
        if title != thread.subject:
            thread.subject = title
            fields.append("subject")
            changes.append(f'{_name(user)} renamed the group to "{title}"')
    if description is not None:
        description = description.strip()
        if len(description) > MAX_DESCRIPTION:
            raise ValidationError(f"Keep the description under {MAX_DESCRIPTION} characters.")
        if description != thread.description:
            thread.description = description
            fields.append("description")
            changes.append(f"{_name(user)} changed the group description")
    if photo is not None:
        _check_photo(photo)
        old = thread.photo.name if thread.photo else ""
        thread.photo = photo
        fields.append("photo")
        changes.append(f"{_name(user)} changed the group photo")
        _delete_file_later(old)
    elif remove_photo and thread.photo:
        _delete_file_later(thread.photo.name)
        thread.photo = ""
        fields.append("photo")
        changes.append(f"{_name(user)} removed the group photo")
    if fields:
        with transaction.atomic():
            thread.save(update_fields=fields + ["updated_at"])
            for line in changes:
                post_system_message(thread, line)
    return thread


def _check_photo(upload):
    size = getattr(upload, "size", 0) or 0
    if size > MAX_PHOTO_BYTES:
        raise ValidationError("The photo must be 2 MB or smaller.")
    head = upload.read(16)
    upload.seek(0)
    if not (head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
        raise ValidationError("Choose a JPEG, PNG or WebP photo.")


def _delete_file_later(name):
    if not name:
        return
    from core.storage import restricted_storage

    def _rm():
        try:
            restricted_storage.delete(name)
        except Exception:  # pragma: no cover
            pass
    transaction.on_commit(_rm)


def update_settings(thread, user, *, send_policy=None, edit_info_policy=None, add_members_policy=None):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("Only group chats have settings.")
    _require("can_change_settings", thread, me, "Only admins can change group settings.")
    labels = {"send_policy": "send messages", "edit_info_policy": "edit group info",
              "add_members_policy": "add members"}
    changed = []
    for field, value in (("send_policy", send_policy), ("edit_info_policy", edit_info_policy),
                         ("add_members_policy", add_members_policy)):
        if value is None:
            continue
        if value not in POLICIES:
            raise ValidationError("Choose all members or only admins.")
        if getattr(thread, field) != value:
            setattr(thread, field, value)
            changed.append(field)
    if changed:
        with transaction.atomic():
            thread.save(update_fields=changed + ["updated_at"])
            for field in changed:
                who = "all members" if getattr(thread, field) == Thread.ALL else "only admins"
                post_system_message(thread, f"{_name(user)} changed who can {labels[field]}: {who}")
    return thread


# ---------------------------------------------------------------------------
# Members (L3)
# ---------------------------------------------------------------------------
def add_member_candidates(thread, user, q=""):
    """Staff the user may add (same people as the compose picker), minus members."""
    from .chat_services import messageable_users

    current = thread.participants.values_list("user_id", flat=True)
    return messageable_users(user, q).filter(user_type="staff").exclude(pk__in=current)


def _add(thread, users, *, role=MEMBER, now=None):
    now = now or timezone.now()
    added = []
    for u in users:
        row, created = ThreadParticipant.objects.get_or_create(
            thread=thread, user=u, defaults={"role": role, "joined_at": now})
        if created:
            added.append(u)
        elif row.is_deleted:
            row.is_deleted = False
            row.save(update_fields=["is_deleted"])
    return added


def add_members(thread, user, user_ids):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("Members can only be added to group chats.")
    if thread.auto_managed:
        raise ValidationError("This group follows its team; members change with the team.")
    _require("can_add_members", thread, me, "Only admins can add members to this group.")
    try:
        ids = {int(i) for i in user_ids or [] if str(i).strip()}
    except (TypeError, ValueError):
        raise ValidationError("Choose valid people.")
    if not ids:
        raise ValidationError("Choose at least one person to add.")
    allowed = list(add_member_candidates(thread, user).filter(pk__in=ids))
    if not allowed:
        raise ValidationError("Those people are already members or can't be added.")
    with transaction.atomic():
        added = _add(thread, allowed)
        if added:
            post_system_message(thread, f"{_name(user)} added {_names(added)}")
    return added


def _target(thread, user_id):
    row = thread.participants.select_related("user").filter(user_id=user_id).first()
    if row is None:
        raise ValidationError("That person is not in this group.")
    return row


def remove_member(thread, user, user_id):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("Nobody can be removed from a direct chat.")
    if thread.auto_managed:
        raise ValidationError("This group follows its team; members change with the team.")
    _require("can_manage_members", thread, me, "Only admins can remove members.")
    target = _target(thread, user_id)
    if target.user_id == user.pk:
        raise ValidationError("Use Leave group to leave.")
    if target.role == OWNER:
        raise PermissionDenied("The group owner can't be removed.")
    with transaction.atomic():
        removed_user = target.user
        target.delete()
        post_system_message(thread, f"{_name(user)} removed {_name(removed_user)}")
    return removed_user


def set_role(thread, user, user_id, role):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("Direct chats have no admins.")
    if role not in (ADMIN, MEMBER):
        raise ValidationError("Choose admin or member.")
    _require("can_manage_members", thread, me, "Only admins can change roles.")
    target = _target(thread, user_id)
    if target.role == OWNER:
        raise PermissionDenied("The group owner's role can't be changed.")
    if target.role == role:
        return target
    with transaction.atomic():
        target.role = role
        target.save(update_fields=["role"])
        if role == ADMIN:
            post_system_message(thread, f"{_name(user)} made {_name(target.user)} an admin")
        else:
            post_system_message(thread, f"{_name(user)} dismissed {_name(target.user)} as admin")
    return target


def leave_group(thread, user):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("You can't leave a direct chat; delete it instead.")
    if thread.auto_managed:
        raise ValidationError("This group follows its team; you leave it when you leave the team.")
    with transaction.atomic():
        was_owner = me.role == OWNER
        me.delete()
        post_system_message(thread, f"{_name(user)} left")
        if was_owner:
            _hand_over_ownership(thread)
    return thread


def _hand_over_ownership(thread):
    """The longest-serving admin (else member) becomes the owner."""
    rows = thread.participants.select_related("user").order_by("joined_at", "id")
    heir = rows.filter(role=ADMIN).first() or rows.first()
    if heir is None:
        return None
    heir.role = OWNER
    heir.save(update_fields=["role"])
    post_system_message(thread, f"{_name(heir.user)} is now the group owner")
    return heir


# ---------------------------------------------------------------------------
# Invite links (L4) - staff only, expiring, revocable, optional use limit
# ---------------------------------------------------------------------------
def create_invite(thread, user, *, days=None, max_uses=None):
    me = _participant(thread, user)
    if not is_group(thread):
        raise ValidationError("Invite links are for group chats.")
    _require("can_invite", thread, me, "Only admins can create invite links for this group.")
    try:
        days = invite_days_default() if days in (None, "") else int(days)
        max_uses = None if max_uses in (None, "", 0, "0") else int(max_uses)
    except (TypeError, ValueError):
        raise ValidationError("Choose how long the link works and how many people can use it.")
    if not 1 <= days <= MAX_INVITE_DAYS:
        raise ValidationError(f"A link can work for 1 to {MAX_INVITE_DAYS} days.")
    if max_uses is not None and not 1 <= max_uses <= 500:
        raise ValidationError("Allow between 1 and 500 people, or leave it open.")
    invite = ThreadInvite.objects.create(
        thread=thread, token=secrets.token_urlsafe(24), created_by=user,
        expires_at=timezone.now() + timedelta(days=days), max_uses=max_uses)
    return invite


def invite_url(invite, request=None):
    path = reverse("messaging:join", args=[invite.token])
    return request.build_absolute_uri(path) if request is not None else path


def revoke_invite(thread, user, invite_id):
    me = _participant(thread, user)
    _require("can_invite", thread, me, "Only admins can revoke invite links.")
    invite = thread.invites.filter(pk=invite_id).first()
    if invite is None:
        raise Http404
    if invite.revoked_at is None:
        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at"])
    return invite


def _invite_for(token):
    invite = ThreadInvite.objects.select_related("thread").filter(token=str(token or "")[:43]).first()
    if invite is None:
        raise Http404("This invite link is not valid.")
    return invite


def invite_status(token, user):
    """(invite, state) for the join page: ok | member | expired | not_staff | closed."""
    invite = _invite_for(token)
    thread = invite.thread
    if getattr(user, "user_type", "staff") != "staff" or not user.is_active:
        return invite, "not_staff"
    if thread.participants.filter(user=user).exists():
        return invite, "member"
    if thread.archived_at is not None or thread.auto_managed or thread.thread_type != Thread.GROUP:
        return invite, "closed"
    if not invite.is_usable():
        return invite, "expired"
    return invite, "ok"


def join_by_invite(token, user):
    invite, state = invite_status(token, user)
    if state == "member":
        return invite.thread
    if state == "not_staff":
        raise PermissionDenied("Invite links are for NBTE staff accounts only.")
    if state == "closed":
        raise ValidationError("This group no longer accepts new members by link.")
    if state == "expired":
        raise ValidationError("This invite link has expired or was revoked.")
    with transaction.atomic():
        locked = ThreadInvite.objects.select_for_update().get(pk=invite.pk)
        if not locked.is_usable():
            raise ValidationError("This invite link has expired or was revoked.")
        locked.use_count += 1
        locked.save(update_fields=["use_count"])
        _add(invite.thread, [user])
        post_system_message(invite.thread, f"{_name(user)} joined using an invite link")
    return invite.thread


# ---------------------------------------------------------------------------
# In-chat pins (L6)
# ---------------------------------------------------------------------------
def pinned_messages(thread, user):
    from .chat_services import visible_messages

    return list(visible_messages(thread, user).filter(pinned_at__isnull=False,
                                                      is_deleted_for_everyone=False)
                .select_related("sender").order_by("-pinned_at", "-id"))


def pin_message(message, user, pinned=True):
    thread = message.thread
    me = _participant(thread, user)
    _require("can_pin", thread, me, "Only admins can pin messages in this group.")
    if pinned:
        if message.is_deleted_for_everyone or message.is_system:
            raise ValidationError("This message can't be pinned.")
        if message.pinned_at is not None:
            return message
        limit = pins_per_chat()
        if thread.messages.filter(pinned_at__isnull=False).count() >= limit:
            raise ValidationError(f"You can pin up to {limit} messages. Unpin one first.")
        now = timezone.now()
        message.pinned_at, message.pinned_by, message.changed_at = now, user, now
        with transaction.atomic():
            message.save(update_fields=["pinned_at", "pinned_by", "changed_at"])
            post_system_message(thread, f"{_name(user)} pinned a message")
    elif message.pinned_at is not None:
        message.pinned_at, message.pinned_by, message.changed_at = None, None, timezone.now()
        message.save(update_fields=["pinned_at", "pinned_by", "changed_at"])
    return message


# ---------------------------------------------------------------------------
# Reminders (L7)
# ---------------------------------------------------------------------------
REMINDER_PRESETS = {"1h": timedelta(hours=1), "3h": timedelta(hours=3), "tomorrow": None, "1w": timedelta(weeks=1)}


def preset_time(preset, now=None):
    """Clock time for a quick choice (tomorrow = 09:00 local tomorrow)."""
    now = now or timezone.now()
    if preset == "tomorrow":
        local = timezone.localtime(now) + timedelta(days=1)
        return local.replace(hour=9, minute=0, second=0, microsecond=0)
    delta = REMINDER_PRESETS.get(preset)
    if delta is None:
        raise ValidationError("Choose when to be reminded.")
    return now + delta


def set_reminder(message, user, *, remind_at=None, preset=None, note=""):
    from .chat_services import get_visible_message

    thread = message.thread
    _participant(thread, user)
    get_visible_message(thread, user, message.pk)
    if message.is_deleted_for_everyone:
        raise ValidationError("This message was deleted.")
    now = timezone.now()
    if preset:
        remind_at = preset_time(preset, now)
    if remind_at is None:
        raise ValidationError("Choose when to be reminded.")
    if timezone.is_naive(remind_at):
        remind_at = timezone.make_aware(remind_at)
    if remind_at <= now + timedelta(seconds=30):
        raise ValidationError("Choose a time in the future.")
    if remind_at > now + timedelta(days=366):
        raise ValidationError("Choose a time within the next year.")
    pending = MessageReminder.objects.filter(user=user, message=message, sent_at__isnull=True).first()
    if pending is not None:
        pending.remind_at, pending.note = remind_at, (note or "")[:200]
        pending.save(update_fields=["remind_at", "note"])
        return pending
    return MessageReminder.objects.create(user=user, message=message, remind_at=remind_at,
                                          note=(note or "")[:200])


def cancel_reminder(message, user):
    _participant(message.thread, user)
    return MessageReminder.objects.filter(user=user, message=message, sent_at__isnull=True).delete()[0]


def my_reminders(user, thread=None):
    qs = MessageReminder.objects.filter(user=user, sent_at__isnull=True).select_related("message__thread")
    if thread is not None:
        qs = qs.filter(message__thread=thread)
    return list(qs.order_by("remind_at"))


def send_due_reminders(now=None, limit=500):
    """Notify everyone whose reminder time has come. Returns how many were sent.
    A reminder for a chat the person has since left is closed silently."""
    from notifications.models import Notification
    from notifications.services import notify

    from .chat_services import preview_text

    now = now or timezone.now()
    due = list(MessageReminder.objects.filter(sent_at__isnull=True, remind_at__lte=now)
               .select_related("message__thread", "message__sender", "user").order_by("remind_at")[:limit])
    sent = 0
    for r in due:
        claimed = MessageReminder.objects.filter(pk=r.pk, sent_at__isnull=True).update(sent_at=now)
        if not claimed:
            continue
        thread = r.message.thread
        if not thread.participants.filter(user=r.user, is_deleted=False).exists():
            continue
        title = thread.display_title_for(r.user)
        text = r.note or preview_text(r.message)
        notify(r.user, title=f"Reminder: {title}"[:255], body=text[:300],
               url=reverse("messaging:thread", args=[thread.pk]) + f"#m{r.message_id}",
               category=Notification.MESSAGE, metadata={"reminder_id": r.pk, "message_id": r.message_id})
        sent += 1
    return sent
