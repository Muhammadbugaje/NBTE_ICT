from datetime import timedelta

from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User

from .models import (
    Message, MessageAttachment, MessageDeletion, MessageRead, Thread,
    ThreadParticipant, start_thread,
)

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
EDIT_WINDOW = timedelta(minutes=15)
READ_EDIT_WINDOW = timedelta(minutes=5)

# Deterministic avatar colour palette. Both the full render and the AJAX
# fragment go through _prepare_message_for_render, so a message keeps the
# same colour whether it arrived by page load or by fetch.
_AVATAR_COLORS = ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7"]


def _avatar_color(seed):
    return _AVATAR_COLORS[(seed or 0) % len(_AVATAR_COLORS)]


def _thread_participant(thread, user):
    participant = thread.participants.filter(user=user, is_deleted=False).first()
    if participant is None:
        raise Http404
    return participant


def _message_can_be_changed_by_everyone(message, thread, now=None):
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


def _prepare_message_for_render(message, thread, user):
    """Populate the derived attributes the message partial needs.

    Used by both thread_detail (full render) and reply (AJAX fragment) so
    the two paths produce identical markup.
    """
    message.can_change_for_everyone = (
        message.sender_id == user.pk
        and _message_can_be_changed_by_everyone(message, thread)
    )
    if message.reply_to_id:
        quoted = message.reply_to
        quoted_is_hidden = (
            quoted.is_deleted_for_everyone
            or quoted.hidden_for.filter(user=user).exists()
        )
        if quoted_is_hidden:
            message.reply_preview = "Message unavailable"
            message.reply_sender = "Unavailable"
        else:
            message.reply_preview = quoted.body
            message.reply_sender = (
                quoted.sender.get_full_name() or quoted.sender.email
                if quoted.sender_id else "System"
            )
    message.seen_by_someone = any(
        receipt.user_id != message.sender_id
        for receipt in message.read_receipts.all()
    )
    # Stable avatar colour — same for page-load and AJAX renders.
    message.msg_color = _avatar_color(message.sender_id or message.pk or 0)
    return message


def _render_message_fragment(message, thread, user, request):
    """Render one message article to an HTML string, for AJAX insertion."""
    _prepare_message_for_render(message, thread, user)
    return render_to_string(
        "messaging/partials/message.html",
        {"m": message, "thread": thread},
        request=request,
    )


def _uploaded_attachments(request):
    files = request.FILES.getlist("attachments")
    if sum(upload.size for upload in files) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Attachments must total no more than 5 MB.")
    return files


def _attach_files(message, files):
    return [
        MessageAttachment.objects.create(message=message, file=upload)
        for upload in files
    ]


def _purge_thread_attachments(thread):
    for attachment in MessageAttachment.objects.filter(message__thread=thread).iterator():
        attachment.hard_delete()


def _my_threads(user):
    """Threads the user is a participant of and hasn't archived."""
    return (
        Thread.objects
        .filter(
            participants__user=user,
            participants__is_archived=False,
            participants__is_deleted=False,
        )
        .distinct()
        .select_related("created_by")
        .prefetch_related("participants__user", "messages__sender")
    )


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------
@login_required
def inbox(request):
    threads = list(_my_threads(request.user))
    for t in threads:
        t.unread = t.unread_count_for(request.user)
        t.last_msg = t.last_message(request.user)
        t.display_title = t.display_title_for(request.user)

    threads.sort(
        key=lambda t: (t.last_msg.sent_at if t.last_msg else t.updated_at),
        reverse=True,
    )

    unread_threads = [t for t in threads if t.unread > 0]
    read_threads   = [t for t in threads if t.unread == 0]

    return render(request, "messaging/inbox.html", {
        "unread_threads": unread_threads,
        "read_threads":   read_threads,
        "total_unread":   sum(t.unread for t in threads),
        "total_threads":  len(threads),
    })


# ---------------------------------------------------------------------------
# Thread detail
# ---------------------------------------------------------------------------
@login_required
def thread_detail(request, pk):
    thread = get_object_or_404(Thread, pk=pk)

    participant = thread.participants.filter(
        user=request.user, is_deleted=False,
    ).first()
    if participant is None:
        if request.user.is_superuser:
            messages.error(
                request,
                "You can't read messages you're not a part of. "
                "This is a private conversation between its participants.",
            )
            return redirect("messaging:inbox")
        raise Http404

    now = timezone.now()
    visible_incoming = (
        thread.messages.filter(is_deleted_for_everyone=False)
        .exclude(sender=request.user)
        .exclude(hidden_for__user=request.user)
        .exclude(read_receipts__user=request.user)
        .distinct()
        .values_list("pk", flat=True)
    )
    MessageRead.objects.bulk_create(
        [
            MessageRead(message_id=message_id, user=request.user, read_at=now)
            for message_id in visible_incoming
        ],
        ignore_conflicts=True,
    )
    participant.last_read_at = now
    participant.save(update_fields=["last_read_at"])

    thread_messages = (
        thread.messages.exclude(hidden_for__user=request.user)
        .distinct()
        .select_related("sender", "reply_to__sender")
        .prefetch_related("attachments", "read_receipts")
        .order_by("sent_at", "id")
    )

    thread_is_archived = bool(getattr(participant, "is_archived", False))
    thread.display_title = thread.display_title_for(request.user)
    participants = list(thread.participants.select_related("user"))

    for message in thread_messages:
        _prepare_message_for_render(message, thread, request.user)

    flash_messages = list(get_messages(request))
    thread_errors = [
        str(message) for message in flash_messages
        if message.level >= messages.ERROR
    ]

    return render(request, "messaging/thread.html", {
        "thread": thread,
        "thread_messages": thread_messages,
        "participants": participants,
        "thread_is_archived": thread_is_archived,
        "thread_errors": thread_errors,
        "suppress_global_toasts": True,
        "current_user_color": _avatar_color(request.user.pk),
    })


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
@login_required
def compose(request):
    preselected = None
    to_param = request.GET.get("to")
    if to_param:
        try:
            preselected = User.objects.filter(
                pk=int(to_param), is_active=True,
            ).first()
        except (TypeError, ValueError):
            preselected = None

    # Only active users who also have a Staff profile — messaging is
    # staff-to-staff. If Staff uses a different FK name, adjust user_id.
    from staff.models import Staff
    staff_user_ids = Staff.objects.values_list("user_id", flat=True)

    users = (
        User.objects
        .filter(pk__in=staff_user_ids, is_active=True)
        .exclude(pk=request.user.pk)
        .select_related("department")
        .order_by("first_name", "last_name", "email")
    )

    return render(request, "messaging/compose.html", {
        "users": users,
        "preselected": preselected,
    })


@login_required
@require_POST
def compose_send(request):
    recipient_ids = request.POST.getlist("recipients")
    subject = (request.POST.get("subject") or "").strip()
    body    = (request.POST.get("body") or "").strip()
    try:
        uploads = _uploaded_attachments(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("messaging:compose")

    if not recipient_ids:
        messages.error(request, "Choose at least one recipient.")
        return redirect("messaging:compose")
    if not body:
        messages.error(request, "Message body cannot be empty.")
        return redirect("messaging:compose")

    recipients = list(User.objects.filter(pk__in=recipient_ids, is_active=True))
    if not recipients:
        messages.error(request, "No valid recipients.")
        return redirect("messaging:compose")

    thread = start_thread(
        creator=request.user,
        participants=recipients,
        subject=subject,
        initial_body=body,
    )
    initial_message = thread.messages.order_by("-sent_at", "-id").first()
    _attach_files(initial_message, uploads)
    messages.success(request, "Message sent.")
    return redirect("messaging:thread", pk=thread.pk)


# ---------------------------------------------------------------------------
# Reply — full-page POST and AJAX JSON share this handler
# ---------------------------------------------------------------------------
@login_required
@require_POST
def reply(request, pk):
    thread = get_object_or_404(Thread, pk=pk)
    participant = _thread_participant(thread, request.user)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    body = (request.POST.get("body") or "").strip()
    try:
        uploads = _uploaded_attachments(request)
    except ValueError as exc:
        if is_ajax:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("messaging:thread", pk=pk)

    if not body and not uploads:
        err = "Write a message or attach a file."
        if is_ajax:
            return JsonResponse({"ok": False, "error": err}, status=400)
        messages.error(request, err)
        return redirect("messaging:thread", pk=pk)

    reply_to_id = request.POST.get("reply_to")
    reply_to = None
    if reply_to_id:
        try:
            reply_to = Message.objects.filter(
                thread=thread, is_deleted_for_everyone=False,
            ).exclude(hidden_for__user=request.user).get(pk=reply_to_id)
        except (Message.DoesNotExist, ValueError):
            err = "The message you're replying to is no longer available."
            if is_ajax:
                return JsonResponse({"ok": False, "error": err}, status=400)
            messages.error(request, err)
            return redirect("messaging:thread", pk=pk)

    now = timezone.now()
    message = Message.objects.create(
        thread=thread, sender=request.user, body=body, reply_to=reply_to,
        sent_at=now,
    )
    _attach_files(message, uploads)
    thread.last_message_at = now
    thread.save(update_fields=["last_message_at"])
    participant.last_read_at = now
    participant.save(update_fields=["last_read_at"])

    if is_ajax:
        html = _render_message_fragment(message, thread, request.user, request)
        return JsonResponse({
            "ok": True,
            "message_id": message.pk,
            "html": html,
            "sent_at_iso": now.isoformat(),
        })

    return redirect("messaging:thread", pk=pk)


@login_required
@require_POST
def edit_message(request, pk, message_pk):
    thread = get_object_or_404(Thread, pk=pk)
    _thread_participant(thread, request.user)
    message = get_object_or_404(Message, pk=message_pk, thread=thread)
    if message.sender_id != request.user.pk or not _message_can_be_changed_by_everyone(message, thread):
        messages.error(request, "This message can no longer be edited for everyone.")
        return redirect("messaging:thread", pk=pk)

    body = (request.POST.get("body") or "").strip()
    if not body:
        messages.error(request, "A message cannot be empty.")
        return redirect("messaging:thread", pk=pk)

    message.body = body
    message.edited_at = timezone.now()
    message.save(update_fields=["body", "edited_at"])
    return redirect("messaging:thread", pk=pk)


@login_required
@require_POST
def delete_message(request, pk, message_pk):
    thread = get_object_or_404(Thread, pk=pk)
    _thread_participant(thread, request.user)
    message = get_object_or_404(Message, pk=message_pk, thread=thread)
    scope = request.POST.get("scope")

    if scope == "everyone":
        if (
            message.sender_id != request.user.pk
            or not _message_can_be_changed_by_everyone(message, thread)
        ):
            messages.error(request, "This message can no longer be deleted for everyone.")
            return redirect("messaging:thread", pk=pk)
        message.body = "This message was deleted."
        message.is_deleted_for_everyone = True
        message.edited_at = None
        message.save(update_fields=["body", "is_deleted_for_everyone", "edited_at"])
        _purge_message_attachments(message)
    elif scope == "me":
        MessageDeletion.objects.get_or_create(message=message, user=request.user)
    else:
        messages.error(request, "Choose where to delete this message.")
    return redirect("messaging:thread", pk=pk)


def _purge_message_attachments(message):
    for attachment in message.attachments.all().iterator():
        attachment.hard_delete()


@login_required
@require_POST
def delete_chat(request, pk):
    thread = get_object_or_404(Thread, pk=pk)
    participant = _thread_participant(thread, request.user)
    participant.is_deleted = True
    participant.save(update_fields=["is_deleted"])
    if not thread.participants.filter(is_deleted=False).exists():
        _purge_thread_attachments(thread)
    return redirect("messaging:inbox")


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------
@login_required
@require_POST
def archive(request, pk):
    participant = get_object_or_404(
        ThreadParticipant, thread_id=pk, user=request.user, is_deleted=False,
    )
    participant.is_archived = True
    participant.save(update_fields=["is_archived"])
    messages.success(request, "Thread archived.")
    return redirect("messaging:inbox")


@login_required
def archived(request):
    qs = (
        Thread.objects
        .filter(
            participants__user=request.user,
            participants__is_archived=True,
            participants__is_deleted=False,
        )
        .distinct()
        .select_related("created_by")
        .prefetch_related("participants__user", "messages__sender")
    )

    threads = list(qs)
    for t in threads:
        t.unread = t.unread_count_for(request.user)
        t.last_msg = t.last_message(request.user)
        t.display_title = t.display_title_for(request.user)

    threads.sort(
        key=lambda t: (t.last_msg.sent_at if t.last_msg else t.updated_at),
        reverse=True,
    )

    return render(request, "messaging/archived.html", {
        "threads": threads,
    })


@login_required
@require_POST
def unarchive(request, pk):
    """Restore an archived thread to the inbox for this user."""
    thread = get_object_or_404(
        Thread, pk=pk, participants__user=request.user,
        participants__is_deleted=False,
    )

    ThreadParticipant.objects.filter(
        thread=thread, user=request.user
    ).update(is_archived=False)

    messages.success(request, "Thread restored to your inbox.")
    return redirect("messaging:thread", pk=thread.pk)   