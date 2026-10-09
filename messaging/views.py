from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import User

from .models import Message, Thread, ThreadParticipant, start_thread


def _my_threads(user):
    """Threads the user is a participant of and hasn't archived."""
    return (
        Thread.objects
        .filter(participants__user=user, participants__is_archived=False)
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
        # Attach derived data (templates can't access underscore-prefixed attrs).
        t.unread = t.unread_count_for(request.user)
        t.last_msg = t.last_message()
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
    participant = thread.participants.filter(user=request.user).first()
    if not participant:
        raise Http404

    participant.last_read_at = timezone.now()
    participant.save(update_fields=["last_read_at"])

    thread_messages = (
        thread.messages
        .select_related("sender")
        .order_by("sent_at", "id")
    )

    thread_is_archived = bool(getattr(participant, "is_archived", False))
    thread.display_title = thread.display_title_for(request.user)  

    return render(request, "messaging/thread.html", {
        "thread": thread,
        "messages": thread_messages,
        "participants": thread.participants.select_related("user"),
        "thread_is_archived": thread_is_archived,
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
            preselected = User.objects.filter(pk=int(to_param), is_active=True).first()
        except (TypeError, ValueError):
            preselected = None

    users = (
        User.objects.filter(is_active=True)
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
    messages.success(request, "Message sent.")
    return redirect("messaging:thread", pk=thread.pk)


# ---------------------------------------------------------------------------
# Reply
# ---------------------------------------------------------------------------
@login_required
@require_POST
def reply(request, pk):
    thread = get_object_or_404(Thread, pk=pk)
    participant = thread.participants.filter(user=request.user).first()
    if not participant:
        raise Http404

    body = (request.POST.get("body") or "").strip()
    if not body:
        messages.error(request, "Reply cannot be empty.")
        return redirect("messaging:thread", pk=pk)

    Message.objects.create(thread=thread, sender=request.user, body=body)
    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])
    participant.last_read_at = timezone.now()
    participant.save(update_fields=["last_read_at"])

    return redirect("messaging:thread", pk=pk)


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------
@login_required
@require_POST
def archive(request, pk):
    participant = get_object_or_404(ThreadParticipant, thread_id=pk, user=request.user)
    participant.is_archived = True
    participant.save(update_fields=["is_archived"])
    messages.success(request, "Thread archived.")
    return redirect("messaging:inbox")


@login_required
def archived(request):
    qs = (
        Thread.objects
        .filter(participants__user=request.user, participants__is_archived=True)
        .distinct()
        .select_related("created_by")
        .prefetch_related("participants__user", "messages__sender")
    )

    threads = list(qs)
    for t in threads:
        t.unread = t.unread_count_for(request.user)
        t.last_msg = t.last_message()
        t.display_title = t.display_title_for(request.user)   # <-- ADD

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
    thread = get_object_or_404(Thread, pk=pk, participants__user=request.user)

    # Flip the participant flag for this user only.
    ThreadParticipant.objects.filter(
        thread=thread, user=request.user
    ).update(is_archived=False)

    messages.success(request, "Thread restored to your inbox.")
    return redirect("messaging:thread", pk=thread.pk)