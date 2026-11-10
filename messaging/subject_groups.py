"""Groups that belong to a subject (v2.1 X41, generic part).

A subject is any model row that has a team: an SSQ team (officer + resource
person), a visit panel, a case. The system owns these groups:

* ``ensure_group(subject, purpose=..., title=..., members=..., owner=...)``
  creates the group the first time and, on every later call, syncs its
  members with the team: new people are added, people no longer on the team
  are removed, and each change posts a system message.
* ``archive_group(subject, purpose=...)`` makes the group read-only when the
  subject closes (nobody can post; history stays).
* Institution users are never added, whatever the caller passes.
* Auto-managed groups refuse manual add / remove / leave / invite links
  (messaging.group_services); admins can still rename, pin and set the photo.

Hooks for later phases are documented in
docs/master_plan_v2_1/notes/subject_groups.md.
"""
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from . import group_services as gs
from .models import Thread, ThreadParticipant

PURPOSES = {
    Thread.PURPOSE_SSQ_TEAM: "SSQ team",
    Thread.PURPOSE_VISIT_PANEL: "Visit panel",
}


def _ct_and_id(subject):
    ct = ContentType.objects.get_for_model(subject, for_concrete_model=True)
    return ct, str(subject.pk)


def find_group(subject, purpose):
    ct, sid = _ct_and_id(subject)
    return Thread.objects.filter(subject_ct=ct, subject_id=sid, purpose=purpose).first()


def eligible(users):
    """Active people who may sit in an internal group: never institution users."""
    seen, out = set(), []
    for u in users or []:
        if u is None or not getattr(u, "pk", None) or u.pk in seen:
            continue
        if not u.is_active or getattr(u, "user_type", "staff") == "institution":
            continue
        seen.add(u.pk)
        out.append(u)
    return out


@transaction.atomic
def ensure_group(subject, *, purpose, title, members, owner=None, admins=()):
    """Create or sync the subject's group. Returns the Thread.

    ``members``: everyone who should be in the group now (the owner and admins
    are added even when missing from it). Owner/admin roles follow the call.
    """
    if not purpose or len(purpose) > 20:
        raise ValueError("purpose must be a short code such as 'ssq_team' or 'visit_panel'.")
    ct, sid = _ct_and_id(subject)
    team = eligible(list(members or []) + [owner] + list(admins or []))
    admin_ids = {u.pk for u in eligible(admins)}
    owner = owner if owner in team else None
    now = timezone.now()
    thread = (Thread.objects.select_for_update()
              .filter(subject_ct=ct, subject_id=sid, purpose=purpose).first())
    created = thread is None
    if created:
        thread = Thread.objects.create(
            subject=str(title)[:255], thread_type=Thread.GROUP, created_by=owner,
            subject_ct=ct, subject_id=sid, purpose=purpose, auto_managed=True,
            # System groups: everyone posts; only admins edit the info; nobody adds by hand.
            send_policy=Thread.ALL, edit_info_policy=Thread.ADMINS, add_members_policy=Thread.ADMINS,
        )
    elif thread.archived_at is not None:
        # The subject reopened: the group comes back.
        thread.archived_at = None
        thread.save(update_fields=["archived_at", "updated_at"])
        gs.post_system_message(thread, "This group is open again")

    wanted = {u.pk: u for u in team}
    current = {p.user_id: p for p in thread.participants.select_related("user")}

    def role_for(uid):
        if owner is not None and uid == owner.pk:
            return ThreadParticipant.OWNER
        if uid in admin_ids:
            return ThreadParticipant.ADMIN
        return ThreadParticipant.MEMBER

    added = []
    for uid, user in wanted.items():
        row = current.get(uid)
        if row is None:
            ThreadParticipant.objects.create(thread=thread, user=user, role=role_for(uid), joined_at=now)
            added.append(user)
        elif row.role != role_for(uid):
            row.role = role_for(uid)
            row.save(update_fields=["role"])
    removed = [p.user for uid, p in current.items() if uid not in wanted]
    if removed:
        ThreadParticipant.objects.filter(thread=thread, user__in=removed).delete()

    if created:
        names = gs._names(added) if added else "nobody yet"
        gs.post_system_message(thread, f"Group for {title}. Members: {names}")
    else:
        if added:
            gs.post_system_message(thread, f"{gs._names(added)} joined the team")
        if removed:
            gs.post_system_message(thread, f"{gs._names(removed)} left the team")
    if not created and str(title)[:255] != thread.subject and title:
        thread.subject = str(title)[:255]
        thread.save(update_fields=["subject", "updated_at"])
    return thread


def sync_members(subject, *, purpose, members, owner=None, admins=()):
    """Member sync only (keeps the current title)."""
    thread = find_group(subject, purpose)
    if thread is None:
        return None
    return ensure_group(subject, purpose=purpose, title=thread.subject, members=members,
                        owner=owner, admins=admins)


def archive_group(subject, *, purpose, note="The work is closed; this group is now read-only."):
    """The subject closed: the group becomes read-only (history kept)."""
    thread = find_group(subject, purpose)
    if thread is None or thread.archived_at is not None:
        return thread
    with transaction.atomic():
        thread.archived_at = timezone.now()
        thread.save(update_fields=["archived_at", "updated_at"])
        gs.post_system_message(thread, note)
    return thread


def groups_for(subject):
    ct, sid = _ct_and_id(subject)
    return Thread.objects.filter(subject_ct=ct, subject_id=sid)
