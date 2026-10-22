from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.conf import settings
from django.core.exceptions import ValidationError

from accounts.models import User

from .models import (
    REACTION_EMOJIS, Message, MessageAttachment, Thread, ThreadParticipant,
    start_thread,
)

from . import chat_services
from .chat_services import (  # noqa: F401  (re-exported for older imports)
    EDIT_WINDOW, MAX_ATTACHMENT_BYTES, READ_EDIT_WINDOW,
)

# Phase 9: the chat logic now lives in messaging/chat_services.py so the web
# views and the mobile API share it. The helpers below are thin wrappers kept
# for backwards compatibility.


def _avatar_color(seed):
    return chat_services.avatar_color(seed)


def _thread_participant(thread, user):
    return chat_services.get_participant(thread, user)


def _message_can_be_changed_by_everyone(message, thread, now=None):
    return chat_services.can_change_for_everyone(message, now)


def _prepare_message_for_render(message, thread, user):
    participants = chat_services.thread_participants(thread)
    return chat_services.prepare_message(message, user, participants)


def _render_message_fragment(message, thread, user, request, participants=None):
    """Render one message article to an HTML string, for AJAX insertion."""
    if participants is None:
        participants = chat_services.thread_participants(thread)
    message = chat_services.get_visible_message(thread, user, message.pk) \
        if not getattr(message, "_prepared_for", None) else message
    chat_services.prepare_message(message, user, participants)
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
    kind = request.GET.get("filter", "all")
    if kind not in chat_services.INBOX_KINDS:
        kind = "all"
    threads = chat_services.inbox_threads(request.user)
    pinned_threads = [t for t in threads if t.is_pinned]
    other_threads = [t for t in threads if not t.is_pinned]
    unread_threads = [t for t in threads if t.unread > 0]
    read_threads = [t for t in threads if t.unread == 0]

    return render(request, "messaging/inbox.html", {
        "pinned_threads": pinned_threads,
        "other_threads": other_threads,
        "unread_threads": unread_threads,
        "read_threads": read_threads,
        "total_unread": sum(t.unread for t in threads),
        "total_threads": len(threads),
        "inbox_kind": kind,
        "max_pinned": ThreadParticipant.MAX_PINNED,
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

    q = (request.GET.get("q") or "").strip()[:100]
    snapshot = chat_services.open_thread(thread, request.user, q=q)
    participant = snapshot["participant"]
    thread_is_archived = bool(getattr(participant, "is_archived", False))
    thread.display_title = thread.display_title_for(request.user)

    flash_messages = list(get_messages(request))
    thread_errors = [
        str(message) for message in flash_messages
        if message.level >= messages.ERROR
    ]
    thread_notices = [
        str(message) for message in flash_messages
        if message.level < messages.ERROR
    ]

    return render(request, "messaging/thread.html", {
        "thread": thread,
        "thread_messages": snapshot["messages"],
        "participants": snapshot["participants"],
        "participant": participant,
        "is_group": snapshot["is_group"],
        "is_muted": chat_services.is_muted(participant),
        "thread_is_archived": thread_is_archived,
        "thread_errors": thread_errors,
        "thread_notices": thread_notices,
        "suppress_global_toasts": True,
        "current_user_color": _avatar_color(request.user.pk),
        "search_q": q,
        "search_ids": snapshot["search_ids"],
        "server_time": snapshot["server_time"].isoformat(),
        "last_message_id": max([m.pk for m in snapshot["messages"]] or [0]),
        "forward_threads": chat_services.forwardable_threads(request.user),
        "reaction_emojis": REACTION_EMOJIS,
        "voice_max_seconds": getattr(settings, "VOICE_MAX_SECONDS", 900),
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

    # Only active users who also have a Staff profile - messaging is
    # staff-to-staff (shared with the mobile API's contacts/ endpoint).
    users = chat_services.messageable_users(request.user)

    return render(request, "messaging/compose.html", {
        "users": users,
        "preselected": preselected,
    })


@login_required
@require_POST
def compose_send(request):
    try:
        thread = chat_services.start_conversation(
            request.user,
            request.POST.getlist("recipients"),
            subject=request.POST.get("subject") or "",
            body=request.POST.get("body") or "",
            uploads=request.FILES.getlist("attachments"),
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("messaging:compose")
    messages.success(request, "Message sent.")
    return redirect("messaging:thread", pk=thread.pk)


# ---------------------------------------------------------------------------
# Reply — full-page POST and AJAX JSON share this handler
# ---------------------------------------------------------------------------
@login_required
@require_POST
def reply(request, pk):
    thread = get_object_or_404(Thread, pk=pk)
    _thread_participant(thread, request.user)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        message = chat_services.send_message(
            thread, request.user,
            body=request.POST.get("body") or "",
            reply_to_id=request.POST.get("reply_to"),
            uploads=request.FILES.getlist("attachments"),
        )
    except ValidationError as exc:
        err = " ".join(exc.messages)
        if is_ajax:
            return JsonResponse({"ok": False, "error": err}, status=400)
        messages.error(request, err)
        return redirect("messaging:thread", pk=pk)

    if is_ajax:
        participants = chat_services.thread_participants(thread)
        message = chat_services.get_visible_message(thread, request.user, message.pk)
        html = _render_message_fragment(message, thread, request.user, request, participants)
        return JsonResponse({
            "ok": True,
            "message_id": message.pk,
            "html": html,
            "sent_at_iso": message.sent_at.isoformat(),
            "message": chat_services.message_payload(message, request.user, participants),
        })

    return redirect("messaging:thread", pk=pk)


@login_required
@require_POST
def edit_message(request, pk, message_pk):
    thread = get_object_or_404(Thread, pk=pk)
    _thread_participant(thread, request.user)
    message = get_object_or_404(Message, pk=message_pk, thread=thread)
    try:
        chat_services.edit_message(message, request.user, request.POST.get("body"))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("messaging:thread", pk=pk)


@login_required
@require_POST
def delete_message(request, pk, message_pk):
    thread = get_object_or_404(Thread, pk=pk)
    _thread_participant(thread, request.user)
    message = get_object_or_404(Message, pk=message_pk, thread=thread)
    try:
        chat_services.delete_message(message, request.user, request.POST.get("scope"))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("messaging:thread", pk=pk)


def _purge_message_attachments(message):
    chat_services.purge_message_files(message)


@login_required
@require_POST
def delete_chat(request, pk):
    from .bulk_views import delete_thread_for

    thread = get_object_or_404(Thread, pk=pk)
    _thread_participant(thread, request.user)
    # Hidden for this user; erased (attachments too) once everyone deleted it.
    delete_thread_for(request.user, thread)
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