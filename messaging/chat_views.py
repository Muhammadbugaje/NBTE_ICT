"""Phase 9 chat endpoints for the web UI.

Thin by design: every view parses the request, calls messaging.chat_services /
messaging.voice, and answers with JSON (fetch) or a redirect (no-JS forms).
The future REST API should call the same service functions.
"""
import mimetypes
import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from . import chat_services, voice as voice_service
from .models import ChatPoll, Thread


def _is_ajax(request):
    return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in (request.headers.get("Accept") or ""))


def _no_store(response):
    response["Cache-Control"] = "no-store"
    return response


def _render_messages(request, thread, messages_, participants):
    html = []
    for m in messages_:
        chat_services.prepare_message(m, request.user, participants)
        html.append(render_to_string("messaging/partials/message.html",
                                     {"m": m, "thread": thread}, request=request))
    return html


def _message_response(request, thread, message, *, extra=None):
    """JSON with fresh HTML + payload for one message (after an action)."""
    participants = chat_services.thread_participants(thread)
    fresh = chat_services.get_visible_message(thread, request.user, message.pk)
    html = _render_messages(request, thread, [fresh], participants)[0]
    data = {"ok": True, "message_id": fresh.pk, "html": html,
            "message": chat_services.message_payload(fresh, request.user, participants)}
    data.update(extra or {})
    return _no_store(JsonResponse(data))


def _fail(request, thread_pk, exc, status=400):
    err = " ".join(getattr(exc, "messages", None) or [str(exc)])
    if _is_ajax(request):
        return JsonResponse({"ok": False, "error": err}, status=status)
    messages.error(request, err)
    if thread_pk:
        return redirect("messaging:thread", pk=thread_pk)
    return redirect("messaging:inbox")


def _back(request, thread_pk, fallback="thread"):
    nxt = request.POST.get("next") or ""
    if nxt.startswith("/messages/") and "//" not in nxt and "\\" not in nxt:
        return redirect(nxt)
    if fallback == "inbox":
        return redirect("messaging:inbox")
    return redirect("messaging:thread", pk=thread_pk)


# ---------------------------------------------------------------------------
# Live updates (short polling - answers immediately, never sleeps)
# ---------------------------------------------------------------------------
@login_required
@require_GET
def thread_updates(request, pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    data = chat_services.thread_updates(
        thread, request.user,
        after_id=request.GET.get("after"),
        since=request.GET.get("since"),
        visible=request.GET.get("visible", "1") != "0",
        version=request.GET.get("v") or None,
    )
    if data.get("unchanged"):
        return _no_store(JsonResponse({"ok": True, "unchanged": True, "v": data["v"],
                                       "typing": data["typing"]}))
    participants = chat_services.thread_participants(thread)
    new_html = _render_messages(request, thread, data["new"], participants)
    changed_html = _render_messages(request, thread, data["changed"], participants)
    payload = {
        "ok": True,
        "server_time": data["server_time"].isoformat(),
        "last_id": data["last_id"],
        "new": [
            {"id": m.pk, "html": h,
             "message": chat_services.message_payload(m, request.user, participants)}
            for m, h in zip(data["new"], new_html)
        ],
        "changed": [
            {"id": m.pk, "html": h,
             "message": chat_services.message_payload(m, request.user, participants)}
            for m, h in zip(data["changed"], changed_html)
        ],
        "receipts": {str(k): v for k, v in data["receipts"].items()},
        "typing": data["typing"],
        "v": data["v"],
    }
    if data["new"] or data["changed"] or request.GET.get("head") == "1":
        from .group_views import head_state

        payload["head"] = head_state(request, thread)
    return _no_store(JsonResponse(payload))


@login_required
@require_GET
def inbox_updates(request):
    return _no_store(JsonResponse(chat_services.inbox_updates(request.user,
                                                              version=request.GET.get("v") or None)))


@login_required
@require_POST
def typing(request, pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    if request.POST.get("state") == "stop":
        chat_services.clear_typing(thread, request.user)
    else:
        chat_services.set_typing(thread, request.user)
    return _no_store(JsonResponse({"ok": True}))


# ---------------------------------------------------------------------------
# Message actions
# ---------------------------------------------------------------------------
@login_required
@require_GET
def message_fragment(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    return _message_response(request, thread, message)


@login_required
@require_POST
def react(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    try:
        current = chat_services.toggle_reaction(message, request.user,
                                                request.POST.get("emoji", ""))
    except ValidationError as exc:
        return _fail(request, pk, exc)
    if not _is_ajax(request):
        return redirect(f"/messages/{pk}/#m{message.pk}")
    return _message_response(request, thread, message, extra={"my_reaction": current})


@login_required
@require_POST
def star(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    starred = chat_services.toggle_star(message, request.user)
    if not _is_ajax(request):
        return _back(request, pk)
    return _message_response(request, thread, message, extra={"starred": starred})


@login_required
@require_POST
def forward(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    try:
        created = chat_services.forward_message(message, request.user,
                                                request.POST.getlist("threads"))
    except ValidationError as exc:
        return _fail(request, pk, exc)
    n = len(created)
    note = f"Forwarded to {n} chat{'s' if n != 1 else ''}."
    if _is_ajax(request):
        return JsonResponse({"ok": True, "count": n, "notice": note,
                             "message_ids": [m.pk for m in created]})
    messages.success(request, note)
    return redirect("messaging:thread", pk=pk)


@login_required
@require_GET
def message_info(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    info = chat_services.message_info(message, request.user)
    html = render_to_string("messaging/partials/message_info.html",
                            {"info": info, "thread": thread,
                             "is_group": thread.thread_type == Thread.GROUP},
                            request=request)
    if _is_ajax(request):
        def row(r):
            return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                    for k, v in r.items()}
        return _no_store(JsonResponse({
            "ok": True, "html": html, "receipt": info["receipt"],
            "read": [row(r) for r in info["read"]],
            "delivered": [row(r) for r in info["delivered"]],
            "pending": [row(r) for r in info["pending"]],
        }))
    return HttpResponse(html)


@login_required
@require_GET
def make_task(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    return redirect(chat_services.task_prefill_url(message))


@login_required
@require_GET
def make_memo(request, pk, message_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    message = chat_services.get_visible_message(thread, request.user, message_pk)
    return redirect(chat_services.memo_prefill_url(message))


# ---------------------------------------------------------------------------
# Pin / mute
# ---------------------------------------------------------------------------
@login_required
@require_POST
def pin(request, pk):
    _, participant = chat_services.get_thread_for(request.user, pk)
    want = request.POST.get("pinned")
    pinned = (not participant.is_pinned) if want is None else want in ("1", "true", "on")
    try:
        chat_services.set_pinned(participant, pinned)
    except ValidationError as exc:
        return _fail(request, None if request.POST.get("next") == "/messages/" else pk, exc)
    if _is_ajax(request):
        return JsonResponse({"ok": True, "pinned": participant.is_pinned})
    messages.success(request, "Chat pinned." if participant.is_pinned else "Chat unpinned.")
    return _back(request, pk)


@login_required
@require_POST
def mute(request, pk):
    _, participant = chat_services.get_thread_for(request.user, pk)
    try:
        chat_services.set_muted(participant, request.POST.get("duration", "off"))
    except ValidationError as exc:
        return _fail(request, pk, exc)
    muted = chat_services.is_muted(participant)
    if _is_ajax(request):
        return JsonResponse({"ok": True, "muted": muted,
                             "muted_until": participant.muted_until.isoformat()
                             if participant.muted_until else None})
    messages.success(request, "Chat muted." if muted else "Chat unmuted.")
    return _back(request, pk)


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------
@login_required
@require_POST
def poll_create(request, pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    try:
        message = chat_services.create_poll(
            thread, request.user,
            question=request.POST.get("question", ""),
            options=request.POST.getlist("options"),
            allow_multiple=request.POST.get("allow_multiple") in ("1", "true", "on"),
        )
    except ValidationError as exc:
        return _fail(request, pk, exc)
    if _is_ajax(request):
        return _message_response(request, thread, message)
    return redirect("messaging:thread", pk=pk)


@login_required
@require_POST
def poll_vote(request, pk, poll_pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    poll = ChatPoll.objects.select_related("message").filter(
        pk=poll_pk, message__thread=thread).first()
    if poll is None:
        raise Http404
    chat_services.get_visible_message(thread, request.user, poll.message_id)
    try:
        chat_services.vote_poll(poll, request.user, request.POST.getlist("option"))
    except ValidationError as exc:
        return _fail(request, pk, exc)
    if _is_ajax(request):
        return _message_response(request, thread, poll.message)
    return redirect(f"/messages/{pk}/#m{poll.message_id}")


# ---------------------------------------------------------------------------
# Voice notes
# ---------------------------------------------------------------------------
@login_required
@require_POST
def voice_upload(request, pk):
    thread, _ = chat_services.get_thread_for(request.user, pk)
    try:
        message = voice_service.create_voice_note(
            thread, request.user, request.FILES.get("audio"),
            duration_ms=request.POST.get("duration_ms"),
            reply_to_id=request.POST.get("reply_to"),
        )
    except ValidationError as exc:
        return _fail(request, pk, exc)
    if _is_ajax(request):
        return _message_response(request, thread, message)
    return redirect("messaging:thread", pk=pk)


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


@login_required
@require_GET
def voice_audio(request, voice_pk):
    """Stream a voice note (participants only) with HTTP Range support so the
    browser can seek. In production nginx serves the bytes (X-Accel-Redirect)."""
    voice = voice_service.get_voice_for(request.user, voice_pk)
    return voice_audio_response(request, voice)


def voice_audio_response(request, voice):
    """The audio bytes of a voice note the caller already checked access to
    (shared with the mobile API's voice/<id>/audio/)."""
    ctype = voice.mime or mimetypes.guess_type(voice.file.name)[0] or "audio/mp4"
    if settings.USE_X_ACCEL_REDIRECT:
        from urllib.parse import quote

        response = HttpResponse(content_type=ctype)
        response["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + quote(voice.file.name)
    else:
        size = voice.file.size
        match = _RANGE_RE.match(request.headers.get("Range", "").strip())
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
                end = size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                response = HttpResponse(status=416)
                response["Content-Range"] = f"bytes */{size}"
                return response
            with voice.file.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read(end - start + 1)
            response = HttpResponse(chunk, status=206, content_type=ctype)
            response["Content-Range"] = f"bytes {start}-{end}/{size}"
            response["Content-Length"] = str(len(chunk))
        else:
            response = FileResponse(voice.file.open("rb"), content_type=ctype)
            response["Content-Length"] = str(size)
    response["Accept-Ranges"] = "bytes"
    response["Content-Disposition"] = f'inline; filename="voice-note-{voice.pk}{os.path.splitext(voice.file.name)[1]}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
@require_POST
def voice_played(request, voice_pk):
    voice = voice_service.get_voice_for(request.user, voice_pk)
    voice_service.mark_played(voice, request.user)
    return JsonResponse({"ok": True})


@login_required
@require_POST
def voice_transcript(request, voice_pk):
    voice = voice_service.get_voice_for(request.user, voice_pk)
    thread = voice.message.thread
    try:
        voice_service.update_transcript(voice, request.user, request.POST.get("transcript", ""))
    except ValidationError as exc:
        return _fail(request, thread.pk, exc, status=403)
    if _is_ajax(request):
        return _message_response(request, thread, voice.message)
    return redirect(f"/messages/{thread.pk}/#m{voice.message_id}")


# ---------------------------------------------------------------------------
# Starred messages
# ---------------------------------------------------------------------------
@login_required
def starred(request):
    stars = list(chat_services.starred_messages(request.user)[:300])
    for s in stars:
        m = s.message
        m.preview = chat_services.preview_text(m)
        m.thread_title = m.thread.display_title_for(request.user)
        m.sender_name = ("You" if m.sender_id == request.user.pk
                         else chat_services.user_name(m.sender))
        m.color = chat_services.avatar_color(m.sender_id or 0)
        m.initials = chat_services.user_initials(m.sender)
    return render(request, "messaging/starred.html", {"stars": stars})
