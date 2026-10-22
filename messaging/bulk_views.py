"""Bulk actions for conversations and memos (Phase 8 UX).

Conversations — "delete for me":
  * the user's ThreadParticipant row is marked deleted (and un-archived) and
    every message in the thread up to now is hidden for that user
    (MessageDeletion), so if someone writes again the thread comes back with
    only the new messages;
  * when EVERY participant has deleted the conversation it is erased for
    real: attachments (files on disk) first, then the thread and its
    messages. An audit entry records who removed what and when.

Memos — the memo is an official record once it leaves the sender:
  * drafts (and cancelled memos) the user may manage → deleted for real,
    attachments included (existing ``memos.delete_memo`` rules);
  * received memos → "Remove from my list" (MemoRecipient.hidden_at) or
    archive; the memo itself, its approvals and everyone else's copy stay;
  * published / in-approval memos the user sent are never deleted here —
    they are skipped with an explanation.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import audit
from core import bulk as bulk_util

from . import memos as memo_service
from .models import Memo, MemoRecipient, Message, MessageAttachment, MessageDeletion, ThreadParticipant


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
def _purge_thread(thread, actor):
    """Every participant has deleted it: erase files, then the rows."""
    for attachment in MessageAttachment.all_objects.filter(message__thread=thread).iterator():
        attachment.hard_delete()
    audit.log("delete", thread, actor=actor,
              note="conversation erased: every participant deleted it",
              changes={"messages": thread.messages.count(),
                       "participants": thread.participants.count()})
    thread.delete()


@transaction.atomic
def delete_thread_for(user, thread):
    """Hide ``thread`` for ``user``; erase it when nobody is left. Returns
    "hidden", "erased" or None (not a participant)."""
    participant = ThreadParticipant.objects.select_for_update().filter(
        thread=thread, user=user, is_deleted=False).first()
    if participant is None:
        return None
    message_ids = list(Message.objects.filter(thread=thread).values_list("pk", flat=True))
    if message_ids:
        already = set(MessageDeletion.objects.filter(user=user, message_id__in=message_ids)
                      .values_list("message_id", flat=True))
        MessageDeletion.objects.bulk_create(
            [MessageDeletion(message_id=m, user=user) for m in message_ids if m not in already],
            ignore_conflicts=True)
    participant.is_deleted = True
    participant.is_archived = False
    participant.save(update_fields=["is_deleted", "is_archived"])
    audit.log("other", thread, actor=user, note="conversation deleted for one participant")
    if not thread.participants.filter(is_deleted=False).exists():
        _purge_thread(thread, user)
        return "erased"
    return "hidden"


@login_required
@require_POST
def bulk_threads(request):
    action = request.POST.get("action", "")
    fallback = reverse("messaging:inbox")
    if action not in ("delete", "archive", "unarchive"):
        return bulk_util.bad_request(request, "Choose what to do with the selected conversations.", fallback)
    ids = bulk_util.parse_ids(request)
    if not ids:
        return bulk_util.bad_request(request, "Select at least one conversation.", fallback)

    mine = {p.thread_id: p for p in ThreadParticipant.objects.filter(
        user=request.user, thread_id__in=ids, is_deleted=False).select_related("thread")}
    done, skipped = [], []
    erased = 0
    for pk in ids:
        participant = mine.get(pk)
        if participant is None:
            skipped.append({"id": pk, "reason": "You are not in some of these conversations."})
            continue
        if action == "delete":
            result = delete_thread_for(request.user, participant.thread)
            if result is None:
                skipped.append({"id": pk, "reason": "Already deleted."})
                continue
            erased += result == "erased"
        else:
            participant.is_archived = action == "archive"
            participant.save(update_fields=["is_archived"])
        done.append(pk)

    if action != "delete" and done:
        audit.log("update", request.user, actor=request.user,
                  note=f"conversations {action}d: {len(done)}", changes={"threads": done[:100]})
    noun = bulk_util.plural(len(done), "conversation")
    if action == "delete":
        msg = f"{noun} deleted for you."
        if erased:
            msg += f" {bulk_util.plural(erased, 'conversation')} had no one left and {'was' if erased == 1 else 'were'} erased."
    elif action == "archive":
        msg = f"{noun} archived."
    else:
        msg = f"{noun} moved back to your inbox."
    return bulk_util.respond(request, action=action, done=done, skipped=skipped, message=msg, fallback=fallback)


# ---------------------------------------------------------------------------
# Memos
# ---------------------------------------------------------------------------
DELETABLE_MEMO_STATES = (Memo.DRAFT, Memo.CANCELLED)


@login_required
@require_POST
def bulk_memos(request):
    action = request.POST.get("action", "")
    fallback = reverse("messaging:memo_list")
    if action not in ("delete", "archive", "unarchive", "hide"):
        return bulk_util.bad_request(request, "Choose what to do with the selected memos.", fallback)
    ids = bulk_util.parse_ids(request)
    if not ids:
        return bulk_util.bad_request(request, "Select at least one memo.", fallback)

    memos = {m.pk: m for m in Memo.objects.filter(pk__in=ids)}
    done, skipped = [], []
    now = timezone.now()
    for pk in ids:
        memo = memos.get(pk)
        if memo is None or not memo_service.can_view_memo(memo, request.user):
            skipped.append({"id": pk, "reason": "Some memos are not yours and were left alone."})
            continue
        receipt = MemoRecipient.objects.filter(memo=memo, user=request.user).first()
        if action == "delete":
            # Real delete: only drafts/cancelled memos the user may manage.
            if memo.status in DELETABLE_MEMO_STATES and memo_service.can_manage_memo(memo, request.user) \
                    and (memo.sender_id == request.user.pk or request.user.has_perm("messaging.manage_memo")):
                ref = memo.reference
                try:
                    memo_service.delete_memo(memo, request.user)
                except PermissionDenied:
                    skipped.append({"id": pk, "reason": "You can't delete some of these memos."})
                    continue
                audit.log("delete", request.user, actor=request.user, note=f"memo draft deleted: {ref or pk}")
                done.append(pk)
            elif receipt is not None:
                # A memo someone sent you: take it off your list, keep the record.
                if receipt.hidden_at is None:
                    receipt.hidden_at = now
                    receipt.save(update_fields=["hidden_at"])
                    audit.log("other", memo, actor=request.user, note="memo removed from recipient's list")
                done.append(pk)
            else:
                skipped.append({"id": pk, "reason": "Memos that have been sent are official records and are "
                                                    "kept. Only drafts can be deleted."})
            continue
        if receipt is None:
            skipped.append({"id": pk, "reason": "Only memos you received can be archived."})
            continue
        if action == "hide":
            if receipt.hidden_at is None:
                receipt.hidden_at = now
                receipt.save(update_fields=["hidden_at"])
                audit.log("other", memo, actor=request.user, note="memo removed from recipient's list")
        else:
            archived = action == "archive"
            if receipt.is_archived != archived:
                receipt.is_archived = archived
                receipt.save(update_fields=["is_archived"])
        done.append(pk)

    noun = bulk_util.plural(len(done), "memo")
    msg = {"delete": f"{noun} deleted.", "hide": f"{noun} removed from your list.",
           "archive": f"{noun} archived.", "unarchive": f"{noun} moved back to your inbox."}[action]
    if action == "delete" and done and any(memos.get(i) and memos[i].status not in DELETABLE_MEMO_STATES
                                           for i in done if i in memos):
        msg = f"{noun} deleted or removed from your list. Received memos stay on record."
    return bulk_util.respond(request, action=action, done=done, skipped=skipped, message=msg, fallback=fallback)
