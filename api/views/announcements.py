"""announcements/ (staff door): read, acknowledge, vote, download
attachments; post / edit / publish for users who may post; the
acknowledgement report (api/docs/announcements.md)."""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import FileResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ..pagination import StandardPagination
from ..serializers_announcements import (
    AcknowledgeResponseSerializer, AnnouncementDetailSerializer, AnnouncementListQuerySerializer,
    AnnouncementListSerializer, AnnouncementReportSerializer, AnnouncementWriteSerializer, PollSerializer,
    PublishResponseSerializer, VoteRequestSerializer,
)
from ..services import announcements as svc
from .common import AUTHED_ERRORS, errors

WRITE_REQUEST = {"application/json": AnnouncementWriteSerializer, "multipart/form-data": AnnouncementWriteSerializer}


def _staff_only(user):
    if getattr(user, "user_type", None) != "staff":
        raise PermissionDenied()


def _load(user, pk, *, include_expired=True):
    """(announcement, in_audience) or 404 - the website detail page's rule."""
    from announcements.models import Announcement
    from announcements.services import access_for

    _staff_only(user)
    ann = Announcement.objects.filter(pk=pk).first()
    if ann is None:
        raise NotFound()
    may_open, in_audience = access_for(user, ann, include_expired=include_expired)
    if not may_open:
        raise NotFound()
    return ann, in_audience


def _messages(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


class AnnouncementListView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(operation_id="announcements_list", parameters=[AnnouncementListQuerySerializer],
                   responses={200: AnnouncementListSerializer, **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        from announcements.models import Announcement
        from announcements.permissions import can_post

        user = request.user
        _staff_only(user)
        s = AnnouncementListQuerySerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        qs, current = svc.list_queryset(user, s.validated_data.get("filter") or "all",
                                        (s.validated_data.get("q") or "").strip())
        paginator = StandardPagination()
        items = list(paginator.paginate_queryset(qs, request, view=self))
        in_audience = set(Announcement.objects.visible_to(user).filter(pk__in=[a.pk for a in items])
                          .values_list("pk", flat=True))
        data = {
            "results": [svc.summary_dict(a, user, in_audience=a.pk in in_audience) for a in items],
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "count": paginator.page.paginator.count,
            "filter": current,
            "counts": svc.counts(user),
            "can_post": can_post(user),
        }
        return Response(AnnouncementListSerializer(data).data)

    @extend_schema(operation_id="announcements_create", request=WRITE_REQUEST,
                   responses={201: AnnouncementDetailSerializer, **errors(400), **AUTHED_ERRORS})
    def post(self, request):
        from announcements.models import Announcement
        from announcements.permissions import can_post

        _staff_only(request.user)
        if not can_post(request.user):
            raise PermissionDenied("You don't have permission to post announcements.")
        ann = _save(request, Announcement(), creating=True)
        return Response(AnnouncementDetailSerializer(svc.detail_dict(ann, request.user, request, in_audience=_in_audience(
            request.user, ann))).data, status=status.HTTP_201_CREATED)


def _in_audience(user, ann):
    from announcements.models import Announcement

    return Announcement.objects.visible_to(user, include_expired=True).filter(pk=ann.pk).exists()


def _save(request, ann, *, creating):
    """Validate with the API serializer, then with the website's
    AnnouncementForm, then save through announcements.services.save_from_form."""
    from announcements.forms import AnnouncementForm
    from announcements.services import save_from_form

    s = AnnouncementWriteSerializer(data=request.data, partial=not creating, context={"creating": creating})
    s.is_valid(raise_exception=True)
    changes = dict(s.validated_data)
    values = svc.form_initial(ann)
    values.update({k: v for k, v in changes.items() if k in svc.FORM_FIELDS or k == "poll_options"})
    poll = ann.poll_or_none() if ann.pk else None
    locked = bool(poll and poll.votes.exists())
    if locked and "poll_options" in changes:
        raise serializers.ValidationError({"poll_options": ["People have voted, so the options can't change."]})
    files = {"attachment": changes["attachment"]} if changes.get("attachment") else None
    form = AnnouncementForm(svc.form_data(values), files, instance=ann, poll_locked=locked)
    if not form.is_valid():
        raise serializers.ValidationError(svc.form_errors(form))
    with transaction.atomic():
        saved, _notified = save_from_form(
            form, request.user, creating=creating, publish_now=bool(changes.get("publish")),
            remove_attachment_ids=changes.get("remove_attachment_ids") or (),
        )
    return saved


class AnnouncementDetailView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(operation_id="announcements_retrieve",
                   responses={200: AnnouncementDetailSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        from announcements import services

        ann, in_audience = _load(request.user, pk)
        if in_audience:
            services.mark_read(request.user, ann)
        return Response(AnnouncementDetailSerializer(
            svc.detail_dict(ann, request.user, request, in_audience=in_audience)).data)

    @extend_schema(operation_id="announcements_update", request=WRITE_REQUEST,
                   responses={200: AnnouncementDetailSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def patch(self, request, pk):
        from announcements.permissions import can_edit

        ann, _in = _load(request.user, pk)
        if not can_edit(request.user, ann):
            raise PermissionDenied("You cannot edit this announcement.")
        ann = _save(request, ann, creating=False)
        return Response(AnnouncementDetailSerializer(svc.detail_dict(
            ann, request.user, request, in_audience=_in_audience(request.user, ann))).data)


class AnnouncementPublishView(APIView):
    @extend_schema(operation_id="announcements_publish", request=None,
                   responses={200: PublishResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        from announcements import services
        from announcements.permissions import can_edit

        ann, _in = _load(request.user, pk)
        if not can_edit(request.user, ann):
            raise PermissionDenied("You cannot publish this announcement.")
        notified = services.publish(ann, actor=request.user)
        return Response(PublishResponseSerializer({"notified": notified, "announcement": svc.detail_dict(
            ann, request.user, request, in_audience=_in_audience(request.user, ann))}).data)


class AnnouncementAcknowledgeView(APIView):
    @extend_schema(operation_id="announcements_acknowledge", request=None,
                   responses={200: AcknowledgeResponseSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        from announcements import services

        ann, _in = _load(request.user, pk, include_expired=False)
        try:
            ack, created = services.acknowledge(request.user, ann)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"non_field_errors": [_messages(exc)]}) from None
        except DjangoPermissionDenied:
            raise NotFound() from None  # managers outside the audience: nothing to acknowledge
        return Response(AcknowledgeResponseSerializer({"id": ann.pk, "acknowledged": True,
                                                       "acknowledged_at": ack.acknowledged_at,
                                                       "created": created}).data)


class AnnouncementVoteView(APIView):
    @extend_schema(operation_id="announcements_vote", request=VoteRequestSerializer,
                   responses={200: PollSerializer, **errors(400, 404), **AUTHED_ERRORS})
    def post(self, request, pk):
        from announcements import services

        ann, in_audience = _load(request.user, pk, include_expired=False)
        poll = ann.poll_or_none()
        if poll is None:
            raise NotFound()
        s = VoteRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            services.vote(request.user, poll, s.validated_data["option_ids"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"option_ids": [_messages(exc)]}) from None
        except DjangoPermissionDenied:
            raise NotFound() from None
        fresh = svc.with_related(type(ann).objects.all()).get(pk=ann.pk)
        return Response(PollSerializer(svc.poll_dict(fresh.poll, request.user, in_audience=in_audience)).data)


class AnnouncementAttachmentView(APIView):
    @extend_schema(operation_id="announcements_attachment_download",
                   responses={(200, "application/octet-stream"): OpenApiResponse(OpenApiTypes.BINARY),
                              **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk, aid):
        from announcements.models import AnnouncementAttachment

        from core import audit

        _staff_only(request.user)
        att = AnnouncementAttachment.objects.filter(pk=aid, announcement_id=pk).select_related("announcement").first()
        if att is None or not att.file or not att.can_download(request.user):
            raise NotFound()
        import mimetypes

        name = att.original_name or att.file.name.rsplit("/", 1)[-1]
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            audit.log("other", att, actor=request.user, note="downloaded (mobile app)")
        except Exception:
            pass
        return FileResponse(att.file.open("rb"), content_type=ctype, as_attachment=True, filename=name)


class AnnouncementReportView(APIView):
    @extend_schema(operation_id="announcements_report",
                   responses={200: AnnouncementReportSerializer, **errors(404), **AUTHED_ERRORS})
    def get(self, request, pk):
        from announcements.permissions import can_view_report

        ann, _in = _load(request.user, pk)
        if not can_view_report(request.user, ann):
            raise PermissionDenied("You cannot see this report.")
        return Response(AnnouncementReportSerializer(svc.report_dict(ann)).data)
