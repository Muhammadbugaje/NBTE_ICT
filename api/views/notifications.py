"""notifications/ (both doors): the signed-in user's own in-app
notifications - list, read, read-all, bulk, unread count (api/docs/notifications.md)."""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from ..pagination import StandardPagination
from ..serializers_notifications import (
    NotificationBulkRequestSerializer, NotificationBulkResponseSerializer, NotificationMarkResponseSerializer, NotificationListQuerySerializer,
    NotificationSerializer, NotificationReadAllResponseSerializer, NotificationUnreadCountSerializer,
)
from ..services import notifications as svc
from .common import AUTHED_ERRORS, errors


class NotificationListView(APIView):
    @extend_schema(operation_id="notifications_list", parameters=[NotificationListQuerySerializer],
                   responses={200: NotificationSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        s = NotificationListQuerySerializer(data=request.query_params)
        s.is_valid(raise_exception=True)
        qs = svc.mine(request.user).select_related("actor")
        filter_ = s.validated_data.get("filter") or "all"
        if filter_ == "unread":
            qs = qs.filter(is_read=False)
        elif filter_ == "read":
            qs = qs.filter(is_read=True)
        if s.validated_data.get("kind"):
            qs = qs.filter(svc.kind_q(s.validated_data["kind"]))
        q = (s.validated_data.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))
        qs = qs.order_by("-created_at", "-id")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = [svc.notification_dict(n, request) for n in page]
        return paginator.get_paginated_response(NotificationSerializer(data, many=True).data)


class NotificationReadView(APIView):
    @extend_schema(operation_id="notifications_read", request=None,
                   responses={200: NotificationMarkResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        n = get_object_or_404(svc.mine(request.user), pk=pk)
        n.mark_read()
        return Response(NotificationMarkResponseSerializer(
            {"id": n.pk, "read": True, "unread": svc.unread_count(request.user)}).data)


class NotificationUnreadView(APIView):
    @extend_schema(operation_id="notifications_unread", request=None,
                   responses={200: NotificationMarkResponseSerializer, **errors(404), **AUTHED_ERRORS})
    def post(self, request, pk):
        n = get_object_or_404(svc.mine(request.user), pk=pk)
        n.mark_unread()
        return Response(NotificationMarkResponseSerializer(
            {"id": n.pk, "read": False, "unread": svc.unread_count(request.user)}).data)


class NotificationReadAllView(APIView):
    @extend_schema(operation_id="notifications_read_all", request=None,
                   responses={200: NotificationReadAllResponseSerializer, **AUTHED_ERRORS})
    def post(self, request):
        from notifications.services import mark_all_read

        marked = mark_all_read(request.user)
        return Response(NotificationReadAllResponseSerializer({"marked": marked, "unread": svc.unread_count(request.user)}).data)


class NotificationBulkView(APIView):
    """The website's bulk action (notifications.services.bulk_apply): ids that
    are not the user's own are skipped, never touched."""

    @extend_schema(operation_id="notifications_bulk", request=NotificationBulkRequestSerializer,
                   responses={200: NotificationBulkResponseSerializer, **errors(400), **AUTHED_ERRORS})
    def post(self, request):
        from core.bulk import plural
        from notifications.services import bulk_apply

        s = NotificationBulkRequestSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        ids = list(dict.fromkeys(s.validated_data["ids"]))
        action = s.validated_data["action"]
        result = bulk_apply(request.user, action, ids)
        return Response(NotificationBulkResponseSerializer({
            "action": action, "done": result["done"], "skipped": result["skipped"],
            "message": f"{plural(len(result['done']), 'notification')} {result['verb']}.",
            "unread": svc.unread_count(request.user),
        }).data)


class NotificationUnreadCountView(APIView):
    @extend_schema(operation_id="notifications_unread_count", responses={200: NotificationUnreadCountSerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(NotificationUnreadCountSerializer({"unread": svc.unread_count(request.user)}).data)

