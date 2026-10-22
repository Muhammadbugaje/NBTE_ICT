"""leave/ (staff door) - annual leave. See api/services/leave.py."""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_work as s
from ..pagination import StandardPagination
from ..services import leave as svc
from ..services.work import paginate
from .common import AUTHED_ERRORS, errors

LIST_ERRORS = {**errors(400), **AUTHED_ERRORS}
OBJ_ERRORS = {**errors(400, 404), **AUTHED_ERRORS}


class LeaveOverviewView(APIView):
    @extend_schema(operation_id="leave_overview", parameters=[s.LeaveYearQuerySerializer],
                   responses={200: s.LeaveOverviewSerializer, **LIST_ERRORS})
    def get(self, request):
        """My balance per leave type (annual leave only on the website), the
        leave I am on now / next, and the resumption date."""
        q = s.LeaveYearQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        return Response(s.LeaveOverviewSerializer(svc.overview(request.user, q.validated_data.get("year"))).data)


class LeaveApplicationsView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="leave_applications", parameters=[s.LeaveListQuerySerializer],
                   responses={200: s.LeaveApplicationSerializer(many=True), **LIST_ERRORS})
    def get(self, request):
        q = s.LeaveListQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        return paginate(self, request, svc.applications(request.user, q.validated_data["status"]),
                        s.LeaveApplicationSerializer, lambda a: svc.application_item(a, request.user))

    @extend_schema(operation_id="leave_apply", request=s.LeaveApplySerializer,
                   responses={201: s.LeaveApplicationSerializer, **LIST_ERRORS})
    def post(self, request):
        """Apply: raises an annual-leave request (same validation as the
        website: staff record, working days, no overlap; routed to the
        approvers of the annual-leave chain)."""
        body = s.LeaveApplySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        app = svc.apply(request.user, body.validated_data)
        return Response(s.LeaveApplicationSerializer(svc.application_item(app, request.user)).data, status=201)


class LeavePreviewView(APIView):
    @extend_schema(operation_id="leave_preview", parameters=[s.LeavePreviewQuerySerializer],
                   responses={200: s.LeavePreviewSerializer, **LIST_ERRORS})
    def get(self, request):
        """Check dates before applying: working days, resumption date and the
        balance left (400 with the website's message on overlap / no working
        days / no staff record)."""
        q = s.LeavePreviewQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        data = svc.preview(request.user, q.validated_data["start_date"], q.validated_data["end_date"])
        return Response(s.LeavePreviewSerializer(data).data)


class LeaveCancelView(APIView):
    @extend_schema(operation_id="leave_cancel", request=s.ReasonSerializer,
                   responses={200: s.LeaveApplicationSerializer, **OBJ_ERRORS})
    def post(self, request, pk):
        app = get_object_or_404(svc.applications(request.user), pk=pk)
        body = s.ReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        svc.cancel(request.user, app, body.validated_data.get("reason") or "")
        app.request.refresh_from_db()
        return Response(s.LeaveApplicationSerializer(svc.application_item(app, request.user)).data)


class LeaveApprovalsView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="leave_approvals", responses={200: s.LeaveApprovalSerializer(many=True),
                                                             **AUTHED_ERRORS})
    def get(self, request):
        """Leave applications waiting for my decision. Decide with
        requests/<request_id>/approve/, reject/, return/ or
        return-to-requester/ (the website does it on the request page)."""
        return paginate(self, request, svc.approvals(request.user), s.LeaveApprovalSerializer, lambda x: x)


class LeaveHolidaysView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="leave_holidays", parameters=[s.LeaveYearQuerySerializer],
                   responses={200: s.HolidaySerializer(many=True), **LIST_ERRORS})
    def get(self, request):
        """Holidays I see (public, all staff, my department, my role) in a
        year; recurring ones carry their original dates and is_recurring."""
        q = s.LeaveYearQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        year = q.validated_data.get("year") or timezone.localdate().year
        return paginate(self, request, svc.holidays(request.user, year), s.HolidaySerializer, svc.holiday_item)
