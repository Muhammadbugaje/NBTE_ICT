"""v2.1 Phase E web endpoints: group info/settings/members, invite links,
in-chat pins and reminders. Thin: every rule is in messaging.group_services.

Answers JSON (with fresh HTML of the info sheet / pinned bar) to fetch()
callers, and redirects back to the chat for plain form posts (no JS).
"""
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import chat_services as cs
from . import group_services as gs


def _is_ajax(request):
    return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.headers.get("HX-Request") == "true"
            or "application/json" in (request.headers.get("Accept") or ""))


def _no_store(resp):
    resp["Cache-Control"] = "no-store"
    return resp


def _error(request, thread_pk, exc):
    status = 403 if isinstance(exc, PermissionDenied) else 400
    text = " ".join(getattr(exc, "messages", None) or [str(exc) or "Not allowed."])
    if _is_ajax(request):
        return JsonResponse({"ok": False, "error": text}, status=status)
    messages.error(request, text)
    return redirect("messaging:thread", pk=thread_pk) if thread_pk else redirect("messaging:inbox")


def info_context(request, thread):
    info = gs.group_info(thread, request.user)
    thread.display_title = thread.display_title_for(request.user)
    info["invite_links"] = [(i, gs.invite_url(i, request)) for i in info["invites"]]
    info["thread_color"] = cs.avatar_color(thread.pk)
    info["invite_days_default"] = gs.invite_days_default()
    info["invite_day_choices"] = sorted({1, 3, 7, 14, 30, info["invite_days_default"]})
    info["pins_limit"] = gs.pins_per_chat()
    return info


def render_info(request, thread):
    return render_to_string("messaging/partials/group_info.html", info_context(request, thread), request=request)


def render_pinned_bar(request, thread, user=None):
    pinned = gs.pinned_messages(thread, user or request.user)
    return render_to_string("messaging/partials/pinned_bar.html",
                            {"thread": thread, "pinned": pinned}, request=request)


def head_state(request, thread):
    """Header bits a live poll refreshes: subtitle, permissions, pinned bar."""
    participant = cs.get_participant(thread, request.user)
    perms = gs.permissions_for(thread, participant)
    counts = gs.member_counts(thread)
    return {
        "subtitle": subtitle(thread, counts),
        "can_send": perms["can_send"],
        "can_pin": perms["can_pin"],
        "read_only": perms["read_only"],
        "pinned_html": render_pinned_bar(request, thread),
    }


def subtitle(thread, counts=None):
    if thread.thread_type != thread.GROUP:
        return ""
    counts = counts or gs.member_counts(thread)
    admins = counts["admins"]
    text = f"{counts['members']} members, {admins} admin{'s' if admins != 1 else ''}"
    if thread.archived_at:
        text += " · archived"
    return text


def _ok(request, thread, *, notice="", extra=None, back=True):
    if _is_ajax(request):
        data = {"ok": True, "notice": notice}
        try:
            data["html"] = render_info(request, thread)
            data["head"] = head_state(request, thread)
        except Http404:  # the user just left the group
            data["left"] = True
        data.update(extra or {})
        return _no_store(JsonResponse(data))
    if notice:
        messages.success(request, notice)
    return redirect("messaging:thread", pk=thread.pk) if back else redirect("messaging:inbox")


# ---------------------------------------------------------------------------
# Group info sheet
# ---------------------------------------------------------------------------
@login_required
@require_GET
def group_info(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    ctx = info_context(request, thread)
    if _is_ajax(request):
        return _no_store(JsonResponse({"ok": True, "html": render_to_string(
            "messaging/partials/group_info.html", ctx, request=request)}))
    return render(request, "messaging/group_info_page.html", ctx)


@login_required
@require_POST
def group_edit(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        gs.update_info(thread, request.user,
                       title=request.POST.get("title"),
                       description=request.POST.get("description"),
                       photo=request.FILES.get("photo"),
                       remove_photo=request.POST.get("remove_photo") == "1")
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _ok(request, thread, notice="Group info saved.")


@login_required
@require_POST
def group_settings(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        gs.update_settings(thread, request.user,
                           send_policy=request.POST.get("send_policy"),
                           edit_info_policy=request.POST.get("edit_info_policy"),
                           add_members_policy=request.POST.get("add_members_policy"))
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _ok(request, thread, notice="Group settings saved.")


@login_required
@require_GET
def group_photo(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    if not thread.photo:
        raise Http404
    try:
        fh = thread.photo.open("rb")
    except (OSError, ValueError):
        raise Http404
    ctype = mimetypes.guess_type(thread.photo.name)[0] or "image/jpeg"
    resp = FileResponse(fh, content_type=ctype)
    resp["Cache-Control"] = "private, max-age=86400"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
@login_required
@require_GET
def member_candidates(request, pk):
    thread, participant = cs.get_thread_for(request.user, pk)
    if not gs.permissions_for(thread, participant)["can_add_members"]:
        return JsonResponse({"ok": False, "error": "Only admins can add members to this group."}, status=403)
    rows = gs.add_member_candidates(thread, request.user, request.GET.get("q", ""))[:20]
    return _no_store(JsonResponse({"ok": True, "people": [
        {"id": u.pk, "name": cs.user_name(u), "initials": cs.user_initials(u),
         "department": getattr(getattr(u, "department", None), "name", "") or ""}
        for u in rows]}))


@login_required
@require_POST
def members_add(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        added = gs.add_members(thread, request.user, request.POST.getlist("users"))
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _ok(request, thread, notice=f"Added {len(added)} {'person' if len(added) == 1 else 'people'}.")


@login_required
@require_POST
def member_remove(request, pk, user_pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        removed = gs.remove_member(thread, request.user, user_pk)
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _ok(request, thread, notice=f"Removed {cs.user_name(removed)}.")


@login_required
@require_POST
def member_role(request, pk, user_pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        row = gs.set_role(thread, request.user, user_pk, request.POST.get("role", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    label = "is now an admin" if row.role == gs.ADMIN else "is no longer an admin"
    return _ok(request, thread, notice=f"{cs.user_name(row.user)} {label}.")


@login_required
@require_POST
def leave(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        gs.leave_group(thread, request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    if _is_ajax(request):
        from django.urls import reverse

        return _no_store(JsonResponse({"ok": True, "left": True, "redirect": reverse("messaging:inbox")}))
    messages.success(request, "You left the group.")
    return redirect("messaging:inbox")


# ---------------------------------------------------------------------------
# Invite links
# ---------------------------------------------------------------------------
@login_required
@require_POST
def invite_create(request, pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        invite = gs.create_invite(thread, request.user, days=request.POST.get("days"),
                                  max_uses=request.POST.get("max_uses"))
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _ok(request, thread, notice="Invite link created.",
               extra={"invite_url": gs.invite_url(invite, request)})


@login_required
@require_POST
def invite_revoke(request, pk, invite_pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    try:
        gs.revoke_invite(thread, request.user, invite_pk)
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _ok(request, thread, notice="Invite link revoked.")


@login_required
@require_http_methods(["GET", "POST"])
def join(request, token):
    invite, state = gs.invite_status(token, request.user)
    if request.method == "POST":
        try:
            thread = gs.join_by_invite(token, request.user)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, " ".join(getattr(exc, "messages", None) or [str(exc)]))
            return redirect("messaging:join", token=token)
        return redirect("messaging:thread", pk=thread.pk)
    if state == "member":
        return redirect("messaging:thread", pk=invite.thread_id)
    thread = invite.thread
    counts = gs.member_counts(thread)
    return render(request, "messaging/join.html", {
        "invite": invite, "state": state, "thread": thread, "counts": counts,
    }, status=200 if state == "ok" else (403 if state == "not_staff" else 410))


# ---------------------------------------------------------------------------
# In-chat pins and reminders
# ---------------------------------------------------------------------------
def _message(request, pk, message_pk):
    thread, _ = cs.get_thread_for(request.user, pk)
    return thread, cs.get_visible_message(thread, request.user, message_pk)


def _message_ok(request, thread, message, notice):
    if _is_ajax(request):
        participants = cs.thread_participants(thread)
        fresh = cs.get_visible_message(thread, request.user, message.pk)
        cs.prepare_message(fresh, request.user, participants)
        html = render_to_string("messaging/partials/message.html", {"m": fresh, "thread": thread},
                                request=request)
        return _no_store(JsonResponse({"ok": True, "notice": notice, "message_id": fresh.pk, "html": html,
                                       "pinned_html": render_pinned_bar(request, thread)}))
    messages.success(request, notice)
    return redirect("messaging:thread", pk=thread.pk)


@login_required
@require_POST
def pin_message(request, pk, message_pk):
    thread, message = _message(request, pk, message_pk)
    pinned = request.POST.get("pinned", "1") != "0"
    try:
        gs.pin_message(message, request.user, pinned)
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _message_ok(request, thread, message, "Message pinned." if pinned else "Message unpinned.")


@login_required
@require_POST
def remind(request, pk, message_pk):
    thread, message = _message(request, pk, message_pk)
    try:
        if request.POST.get("cancel") == "1":
            gs.cancel_reminder(message, request.user)
            notice = "Reminder removed."
        else:
            at = None
            raw = (request.POST.get("remind_at") or "").strip()
            if raw:
                at = parse_datetime(raw)
                if at is None:
                    raise ValidationError("Choose a valid date and time.")
            r = gs.set_reminder(message, request.user, remind_at=at,
                                preset=request.POST.get("preset") or None, note=request.POST.get("note", ""))
            from django.utils import timezone
            from django.utils.formats import date_format

            notice = "Reminder set for " + date_format(timezone.localtime(r.remind_at), "D j M, H:i") + "."
    except (ValidationError, PermissionDenied) as exc:
        return _error(request, pk, exc)
    return _message_ok(request, thread, message, notice)
