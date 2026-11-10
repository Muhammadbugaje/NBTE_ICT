"""Chats (staff door). Thin views over messaging.chat_services / voice:
participant scoping comes from chat_services.get_participant (Http404 for
anyone outside the conversation), rule violations are the services' own
ValidationErrors (400 validation_error). See api/docs/chats.md."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from messaging import chat_services as cs
from messaging import voice as voice_service
from messaging.models import Message, MessageStar, VoiceNote

from .. import serializers_chats as s
from ..pagination import StandardPagination
from ..services import chats as svc
from .common import AUTHED_ERRORS, errors

TAGS = ["chats"]
JSON_OR_MULTIPART = lambda ser: {"application/json": ser, "multipart/form-data": ser}  # noqa: E731
NO_CONTENT = {204: OpenApiResponse(description="Done.")}
FILE = {200: OpenApiResponse(OpenApiTypes.BINARY, description="The file bytes.")}


def _query(serializer_class, request):
    ser = serializer_class(data=request.query_params)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


def _body(serializer_class, request):
    ser = serializer_class(data=request.data)
    ser.is_valid(raise_exception=True)
    return ser.validated_data


def _thread(request, pk):
    return cs.get_thread_for(request.user, pk)


def _message(request, pk):
    """(message, thread, participant) for a message the user can see."""
    return cs.get_message_for(request.user, pk)


def _msg_response(thread, user, message_id, code=200):
    data = svc.fresh_message_json(thread, user, message_id)
    return Response(s.MessageSerializer(data).data, status=code)


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------
class ThreadListView(APIView):
    @extend_schema(operation_id="chats_threads_list", tags=TAGS,
                   parameters=[s.ThreadListQuerySerializer],
                   responses={200: s.ThreadRowSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        params = _query(s.ThreadListQuerySerializer, request)
        rows = svc.thread_rows(request.user, kind=params["filter"], q=params.get("q", ""))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        data = [svc.thread_row_json(t, request.user) for t in page]
        return paginator.get_paginated_response(s.ThreadRowSerializer(data, many=True).data)

    @extend_schema(operation_id="chats_threads_create", tags=TAGS,
                   request=JSON_OR_MULTIPART(s.ThreadCreateSerializer),
                   responses={201: s.ThreadDetailSerializer, **errors(400), **AUTHED_ERRORS})
    def post(self, request):
        data = _body(s.ThreadCreateSerializer, request)
        thread = cs.start_conversation(request.user, data["participants"], subject=data.get("subject", ""),
                                       body=data["body"], uploads=data.get("attachments") or [])
        participant = cs.get_participant(thread, request.user)
        return Response(s.ThreadDetailSerializer(svc.thread_detail_json(thread, participant, request.user)).data,
                        status=201)


class ContactsView(APIView):
    @extend_schema(operation_id="chats_contacts", tags=TAGS, parameters=[s.SearchQuerySerializer],
                   responses={200: s.ContactSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        params = _query(s.SearchQuerySerializer, request)
        qs = cs.messageable_users(request.user, params.get("q", "")).select_related("staff_profile__designation")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(
            s.ContactSerializer([svc.contact_json(u) for u in page], many=True).data)


class ThreadDetailView(APIView):
    @extend_schema(operation_id="chats_thread_detail", tags=TAGS,
                   responses={200: s.ThreadDetailSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        thread, participant = _thread(request, pk)
        return Response(s.ThreadDetailSerializer(svc.thread_detail_json(thread, participant, request.user)).data)

    @extend_schema(operation_id="chats_thread_delete", tags=TAGS,
                   description="Delete the chat for me (the website rule: hidden for you; erased with its "
                               "files once every participant deleted it).",
                   responses={**NO_CONTENT, **errors(404), **AUTHED_ERRORS})
    def delete(self, request, pk):
        from messaging.bulk_views import delete_thread_for

        thread, _ = _thread(request, pk)
        delete_thread_for(request.user, thread)
        return Response(status=204)


class MessagesView(APIView):
    @extend_schema(operation_id="chats_messages_list", tags=TAGS, parameters=[s.MessagesQuerySerializer],
                   responses={200: s.MessagePageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def get(self, request, pk):
        thread, _ = _thread(request, pk)
        params = _query(s.MessagesQuerySerializer, request)
        page = cs.message_page(thread, request.user, before=params.get("before"), after=params.get("after"),
                               around=params.get("around"), limit=params["limit"])
        msgs = page["messages"]
        base = reverse("api:chats_messages", args=[thread.pk])
        limit = params["limit"]
        older = (request.build_absolute_uri(f"{base}?before={msgs[0].pk}&limit={limit}")
                 if page["has_older"] else None)
        newer = (request.build_absolute_uri(f"{base}?after={msgs[-1].pk}&limit={limit}")
                 if page["has_newer"] else None)
        data = {
            "results": [svc.message_json(m, request.user, page["participants"]) for m in msgs],
            "next": older, "previous": newer, "count": None,
            "has_older": page["has_older"], "has_newer": page["has_newer"],
            "server_time": svc._iso(page["server_time"]), "last_id": page["last_id"],
        }
        return Response(s.MessagePageSerializer(data).data)

    @extend_schema(operation_id="chats_messages_send", tags=TAGS,
                   request=JSON_OR_MULTIPART(s.SendMessageSerializer),
                   responses={201: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        data = _body(s.SendMessageSerializer, request)
        message = cs.send_message(thread, request.user, body=data.get("body", ""),
                                  reply_to_id=data.get("reply_to"), uploads=data.get("attachments") or [])
        return _msg_response(thread, request.user, message.pk, 201)


class UpdatesView(APIView):
    @extend_schema(operation_id="chats_thread_updates", tags=TAGS, parameters=[s.UpdatesQuerySerializer],
                   description="Short poll (every 3 s while the chat is open). Answers at once.",
                   responses={200: s.UpdatesSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def get(self, request, pk):
        thread, _ = _thread(request, pk)
        params = _query(s.UpdatesQuerySerializer, request)
        data = cs.thread_updates(thread, request.user, after_id=params["after"], since=params.get("since"),
                                 visible=params["visible"], version=params.get("v") or None)
        resp = Response(s.UpdatesSerializer(svc.updates_json(thread, request.user, data, after=params["after"],
                                                              since=params.get("since"))).data)
        resp["Cache-Control"] = "no-store"
        return resp


class TypingView(APIView):
    @extend_schema(operation_id="chats_thread_typing", tags=TAGS, request=s.TypingSerializer,
                   responses={**NO_CONTENT, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        data = _body(s.TypingSerializer, request)
        if data["state"] == "stop":
            cs.clear_typing(thread, request.user)
        else:
            cs.set_typing(thread, request.user)
        return Response(status=204)


class ReadView(APIView):
    @extend_schema(operation_id="chats_thread_read", tags=TAGS, request=None,
                   responses={200: s.ReadResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, participant = _thread(request, pk)
        ids = cs.mark_read(thread, request.user, participant)
        return Response(s.ReadResponseSerializer(
            {"marked": len(ids), "unread": thread.unread_count_for(request.user)}).data)


class PinView(APIView):
    @extend_schema(operation_id="chats_thread_pin", tags=TAGS, request=s.PinSerializer,
                   responses={200: s.PinResponseSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        _, participant = _thread(request, pk)
        data = _body(s.PinSerializer, request)
        cs.set_pinned(participant, data["pinned"])
        return Response({"pinned": participant.is_pinned})


class MuteView(APIView):
    @extend_schema(operation_id="chats_thread_mute", tags=TAGS, request=s.MuteSerializer,
                   responses={200: s.MuteResponseSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        _, participant = _thread(request, pk)
        data = _body(s.MuteSerializer, request)
        cs.set_muted(participant, data["duration"])
        muted = cs.is_muted(participant)
        return Response(s.MuteResponseSerializer(
            {"muted": muted, "muted_until": svc._iso(participant.muted_until) if muted else None}).data)


class ArchiveView(APIView):
    archived = True

    @extend_schema(operation_id="chats_thread_archive", tags=TAGS, request=None,
                   responses={200: s.ArchiveResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        _, participant = _thread(request, pk)
        cs.set_archived(participant, self.archived)
        return Response({"archived": participant.is_archived})


class UnarchiveView(ArchiveView):
    archived = False

    @extend_schema(operation_id="chats_thread_unarchive", tags=TAGS, request=None,
                   responses={200: s.ArchiveResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        return super().post(request, pk)


# ---------------------------------------------------------------------------
# Message actions
# ---------------------------------------------------------------------------
class MessageDetailView(APIView):
    @extend_schema(operation_id="chats_message_detail", tags=TAGS,
                   responses={200: s.MessageSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        message, thread, _ = _message(request, pk)
        return _msg_response(thread, request.user, message.pk)

    @extend_schema(operation_id="chats_message_edit", tags=TAGS, request=s.EditMessageSerializer,
                   responses={200: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def patch(self, request, pk):
        message, thread, _ = _message(request, pk)
        data = _body(s.EditMessageSerializer, request)
        cs.edit_message(message, request.user, data["body"])
        return _msg_response(thread, request.user, message.pk)

    @extend_schema(operation_id="chats_message_delete", tags=TAGS,
                   parameters=[OpenApiParameter("scope", str, enum=["me", "everyone"], required=True)],
                   responses={200: OpenApiResponse(s.MessageSerializer, description="scope=everyone"),
                              204: OpenApiResponse(description="scope=me"), **errors(400, 404),
                              **AUTHED_ERRORS})
    def delete(self, request, pk):
        message, thread, _ = _message(request, pk)
        payload = request.data if hasattr(request.data, "get") and request.data.get("scope") else request.query_params
        ser = s.DeleteMessageSerializer(data={"scope": payload.get("scope")})
        ser.is_valid(raise_exception=True)
        scope = ser.validated_data["scope"]
        cs.delete_message(message, request.user, scope)
        if scope == "me":
            return Response(status=204)
        return _msg_response(thread, request.user, message.pk)


class ReactView(APIView):
    @extend_schema(operation_id="chats_message_react", tags=TAGS, request=s.ReactSerializer,
                   description="Same emoji again removes your reaction; another emoji replaces it.",
                   responses={200: s.ReactResponseSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, thread, _ = _message(request, pk)
        data = _body(s.ReactSerializer, request)
        current = cs.toggle_reaction(message, request.user, data["emoji"])
        return Response(s.ReactResponseSerializer(
            {"my_reaction": current, "message": svc.fresh_message_json(thread, request.user, message.pk)}).data)


class StarView(APIView):
    @extend_schema(operation_id="chats_message_star", tags=TAGS, request=None,
                   description="Toggles your star.",
                   responses={200: s.StarResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, thread, _ = _message(request, pk)
        starred = cs.toggle_star(message, request.user)
        return Response(s.StarResponseSerializer(
            {"starred": starred, "message": svc.fresh_message_json(thread, request.user, message.pk)}).data)


class ForwardView(APIView):
    @extend_schema(operation_id="chats_message_forward", tags=TAGS, request=s.ForwardSerializer,
                   responses={201: s.ForwardResponseSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, _, _ = _message(request, pk)
        data = _body(s.ForwardSerializer, request)
        created = cs.forward_message(message, request.user, data["thread_ids"])
        return Response({"count": len(created), "message_ids": [m.pk for m in created]}, status=201)


class InfoView(APIView):
    @extend_schema(operation_id="chats_message_info", tags=TAGS,
                   responses={200: s.MessageInfoSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        message, _, _ = _message(request, pk)
        return Response(s.MessageInfoSerializer(svc.info_json(cs.message_info(message, request.user))).data)


class TaskPrefillView(APIView):
    @extend_schema(operation_id="chats_message_task_prefill", tags=TAGS, request=None,
                   description="Prefill for the app's own task form. Send `from_message` with the task "
                               "create call to link the message to the task.",
                   responses={200: s.PrefillSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, _, _ = _message(request, pk)
        p = cs.task_prefill_params(message)
        return Response(s.PrefillSerializer(
            {"title": p["title"], "description": p["description"], "from_message": p["from_message"]}).data)


class MemoPrefillView(APIView):
    @extend_schema(operation_id="chats_message_memo_prefill", tags=TAGS, request=None,
                   description="Prefill for the app's memo form (title = subject, description = body). "
                               "Send `from_message` with POST memos/ to link the message to the memo.",
                   responses={200: s.PrefillSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, _, _ = _message(request, pk)
        p = cs.memo_prefill_params(message)
        return Response(s.PrefillSerializer(
            {"title": p["subject"], "description": p["body"], "from_message": p["from_message"]}).data)


class StarredView(APIView):
    @extend_schema(operation_id="chats_starred", tags=TAGS,
                   responses={200: s.StarredSerializer(many=True), **AUTHED_ERRORS})
    def get(self, request):
        qs = cs.starred_messages(request.user)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(
            s.StarredSerializer([svc.star_json(st, request.user) for st in page], many=True).data)


class SearchView(APIView):
    @extend_schema(operation_id="chats_search", tags=TAGS, parameters=[s.SearchRequiredQuerySerializer],
                   description="Across all my chats: message text, voice-note transcripts and poll questions.",
                   responses={200: s.SearchResultsSerializer, **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        params = _query(s.SearchRequiredQuerySerializer, request)
        hits = cs.search_all(request.user, params["q"], params["limit"])
        rows = [svc.search_hit_json(m, request.user, params["q"]) for m in hits]
        return Response(s.SearchResultsSerializer({"results": rows, "count": len(rows)}).data)


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------
class PollCreateView(APIView):
    @extend_schema(operation_id="chats_poll_create", tags=TAGS, request=s.PollCreateSerializer,
                   responses={201: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        data = _body(s.PollCreateSerializer, request)
        message = cs.create_poll(thread, request.user, question=data["question"], options=data["options"],
                                 allow_multiple=data["allow_multiple"])
        return _msg_response(thread, request.user, message.pk, 201)


class PollVoteView(APIView):
    @extend_schema(operation_id="chats_poll_vote", tags=TAGS, request=s.PollVoteSerializer,
                   description="Single-choice: one id replaces your vote; your current answer again "
                               "withdraws it. Multiple-choice: the ids are exactly your answers.",
                   responses={200: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        poll = cs.get_poll_for(request.user, pk)
        data = _body(s.PollVoteSerializer, request)
        cs.vote_poll(poll, request.user, data["option_ids"])
        return _msg_response(poll.message.thread, request.user, poll.message_id)


# ---------------------------------------------------------------------------
# Voice notes
# ---------------------------------------------------------------------------
class VoiceUploadView(APIView):
    @extend_schema(operation_id="chats_voice_upload", tags=TAGS,
                   request={"multipart/form-data": s.VoiceUploadSerializer},
                   description="AAC .m4a or Opus (.ogg/.webm/.opus), at most 20 MB; normalised by ffmpeg. "
                               "Transcription runs in the background (transcript_status pending -> done).",
                   responses={201: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        data = _body(s.VoiceUploadSerializer, request)
        message = voice_service.create_voice_note(thread, request.user, data["audio"],
                                                  duration_ms=data.get("duration_ms"),
                                                  reply_to_id=data.get("reply_to"))
        return _msg_response(thread, request.user, message.pk, 201)


class VoiceAudioView(APIView):
    @extend_schema(operation_id="chats_voice_audio", tags=TAGS,
                   parameters=[OpenApiParameter("Range", str, OpenApiParameter.HEADER, required=False)],
                   responses={**FILE, 206: OpenApiResponse(OpenApiTypes.BINARY, description="Partial content"),
                              **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        from messaging.chat_views import voice_audio_response

        voice = voice_service.get_voice_for(request.user, pk)
        return voice_audio_response(request._request, voice)


class VoicePlayedView(APIView):
    @extend_schema(operation_id="chats_voice_played", tags=TAGS, request=None,
                   responses={**NO_CONTENT, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        voice = voice_service.get_voice_for(request.user, pk)
        voice_service.mark_played(voice, request.user)
        return Response(status=204)


class VoiceTranscriptView(APIView):
    @extend_schema(operation_id="chats_voice_transcript", tags=TAGS, request=s.TranscriptSerializer,
                   description="Sender only: correct the automatic transcript.",
                   responses={200: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def patch(self, request, pk):
        from rest_framework.exceptions import PermissionDenied

        voice = voice_service.get_voice_for(request.user, pk)
        data = _body(s.TranscriptSerializer, request)
        if voice.message.sender_id != request.user.pk:
            raise PermissionDenied("Only the sender can edit this transcript.")
        voice_service.update_transcript(voice, request.user, data["text"])
        return _msg_response(voice.message.thread, request.user, voice.message_id)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
class AttachmentDownloadView(APIView):
    @extend_schema(operation_id="chats_attachment_download", tags=TAGS,
                   parameters=[OpenApiParameter("inline", int, required=False,
                                                description="1 = inline preview (PDF, images, text)")],
                   responses={**FILE, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        from core.views import restricted_file_response

        attachment = cs.get_attachment_for(request.user, pk)
        return restricted_file_response(request._request, attachment)


# ---------------------------------------------------------------------------
# v2.1 Phase E: group administration (L3), invite links (L4), pins (L6),
# reminders (L7). Every rule is in messaging.group_services: non-admins get
# 403 permission_denied, rule violations 400, non-participants 404.
# ---------------------------------------------------------------------------
from django.http import FileResponse  # noqa: E402

from messaging import group_services as gs  # noqa: E402
from messaging.models import MessageReminder  # noqa: E402


def _group(request, pk, code=200):
    thread, _ = _thread(request, pk)
    return Response(s.GroupSerializer(svc.group_json(thread, request.user)).data, status=code)


class GroupView(APIView):
    @extend_schema(operation_id="chats_group_detail", tags=TAGS,
                   description="Group info for the info sheet: settings, my role and permissions, members "
                               "(owner/admin/member), pinned messages. Direct chats answer with is_group=false.",
                   responses={200: s.GroupSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        return _group(request, pk)

    @extend_schema(operation_id="chats_group_update", tags=TAGS, request=JSON_OR_MULTIPART(s.GroupUpdateSerializer),
                   description="Name/description/photo need edit-info permission; the three policies need an admin.",
                   responses={200: s.GroupSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def patch(self, request, pk):
        thread, _ = _thread(request, pk)
        data = _body(s.GroupUpdateSerializer, request)
        info = {k: data[k] for k in ("title", "description") if k in data}
        if info or data.get("photo") or data.get("remove_photo"):
            gs.update_info(thread, request.user, title=info.get("title"), description=info.get("description"),
                           photo=data.get("photo"), remove_photo=data.get("remove_photo", False))
        policies = {k: data[k] for k in ("send_policy", "edit_info_policy", "add_members_policy") if k in data}
        if policies:
            gs.update_settings(thread, request.user, **policies)
        thread.refresh_from_db()
        return _group(request, pk)


class GroupPhotoView(APIView):
    @extend_schema(operation_id="chats_group_photo", tags=TAGS, responses={**FILE, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        thread, _ = _thread(request, pk)
        if not thread.photo:
            raise Http404
        return FileResponse(thread.photo.open("rb"), content_type="image/jpeg")


class MembersView(APIView):
    @extend_schema(operation_id="chats_members_add", tags=TAGS, request=s.MembersAddSerializer,
                   responses={200: s.GroupSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        gs.add_members(thread, request.user, _body(s.MembersAddSerializer, request)["users"])
        return _group(request, pk)


class MemberRemoveView(APIView):
    @extend_schema(operation_id="chats_member_remove", tags=TAGS, request=None,
                   responses={200: s.GroupSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk, user_pk):
        thread, _ = _thread(request, pk)
        gs.remove_member(thread, request.user, user_pk)
        return _group(request, pk)


class MemberRoleView(APIView):
    @extend_schema(operation_id="chats_member_role", tags=TAGS, request=s.RoleSerializer,
                   responses={200: s.GroupSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk, user_pk):
        thread, _ = _thread(request, pk)
        gs.set_role(thread, request.user, user_pk, _body(s.RoleSerializer, request)["role"])
        return _group(request, pk)


class LeaveView(APIView):
    @extend_schema(operation_id="chats_group_leave", tags=TAGS, request=None,
                   responses={**NO_CONTENT, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        gs.leave_group(thread, request.user)
        return Response(status=204)


class InvitesView(APIView):
    @extend_schema(operation_id="chats_invites_list", tags=TAGS,
                   responses={200: s.InviteSerializer(many=True), **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        thread, participant = _thread(request, pk)
        if not gs.permissions_for(thread, participant)["can_invite"]:
            raise PermissionDenied("Only admins can see this group's invite links.")
        rows = thread.invites.filter(revoked_at__isnull=True).order_by("-created_at")
        return Response(s.InviteSerializer([svc.invite_json(i, request) for i in rows if i.is_usable()],
                                           many=True).data)

    @extend_schema(operation_id="chats_invites_create", tags=TAGS, request=s.InviteCreateSerializer,
                   description="Staff-only join link; expires after `days` (default messaging.invite_link_days).",
                   responses={201: s.InviteSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        thread, _ = _thread(request, pk)
        data = _body(s.InviteCreateSerializer, request)
        invite = gs.create_invite(thread, request.user, days=data.get("days"), max_uses=data.get("max_uses"))
        return Response(s.InviteSerializer(svc.invite_json(invite, request)).data, status=201)


class InviteRevokeView(APIView):
    @extend_schema(operation_id="chats_invite_revoke", tags=TAGS, request=None,
                   responses={200: s.InviteSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk, invite_pk):
        thread, _ = _thread(request, pk)
        invite = gs.revoke_invite(thread, request.user, invite_pk)
        return Response(s.InviteSerializer(svc.invite_json(invite, request)).data)


class JoinView(APIView):
    @extend_schema(operation_id="chats_join_status", tags=TAGS,
                   responses={200: s.JoinStatusSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, token):
        invite, state = gs.invite_status(token, request.user)
        t = invite.thread
        return Response(s.JoinStatusSerializer({"state": state, "thread_id": t.pk, "title": t.subject,
                                                "member_count": gs.member_counts(t)["members"]}).data)

    @extend_schema(operation_id="chats_join", tags=TAGS, request=None,
                   responses={200: s.GroupSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, token):
        thread = gs.join_by_invite(token, request.user)
        return _group(request, thread.pk)


class MessagePinView(APIView):
    @extend_schema(operation_id="chats_message_pin", tags=TAGS, request=s.MessagePinSerializer,
                   description="Pin inside the chat (group admins; both people in a direct chat). "
                               "At most messaging.pins_per_chat (3).",
                   responses={200: s.MessageSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, thread, _ = _message(request, pk)
        gs.pin_message(message, request.user, _body(s.MessagePinSerializer, request)["pinned"])
        return _msg_response(thread, request.user, message.pk)


class MessageReminderView(APIView):
    @extend_schema(operation_id="chats_message_remind", tags=TAGS, request=s.ReminderSetSerializer,
                   description="Remind me about this message: a notification at `remind_at` (or a preset).",
                   responses={200: s.ReminderSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        message, _, _ = _message(request, pk)
        data = _body(s.ReminderSetSerializer, request)
        r = gs.set_reminder(message, request.user, remind_at=data.get("remind_at"), preset=data.get("preset"),
                            note=data.get("note", ""))
        return Response(s.ReminderSerializer(svc.reminder_json(r)).data)

    @extend_schema(operation_id="chats_message_remind_cancel", tags=TAGS,
                   responses={**NO_CONTENT, **errors(404), **AUTHED_ERRORS})
    def delete(self, request, pk):
        message, _, _ = _message(request, pk)
        gs.cancel_reminder(message, request.user)
        return Response(status=204)


class RemindersView(APIView):
    @extend_schema(operation_id="chats_reminders", tags=TAGS,
                   responses={200: s.ReminderSerializer(many=True), **AUTHED_ERRORS})
    def get(self, request):
        rows = gs.my_reminders(request.user)
        return Response(s.ReminderSerializer([svc.reminder_json(r) for r in rows], many=True).data)
