"""attendance/ (staff door). See api/services/attendance.py."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_attendance as s
from ..exceptions import ApiError, NotReady
from ..pagination import StandardPagination
from ..services import attendance as svc
from ..services.work import paginate
from .common import AUTHED_ERRORS, errors


class MockLocation(ApiError):
    status_code, code = 400, "mock_location"


class CheckInRefused(ApiError):
    status_code, code = 400, "check_in_refused"


class TodayView(APIView):
    @extend_schema(operation_id="attendance_today", responses={200: s.TodaySerializer, **AUTHED_ERRORS})
    def get(self, request):
        """Today's record, whether I can check in (and why not), and the
        attendance locations that serve my department."""
        return Response(s.TodaySerializer(svc.today(request.user)).data)


class CheckInView(APIView):
    @extend_schema(operation_id="attendance_check_in", request=s.CheckInRequestSerializer,
                   responses={201: s.CheckInResultSerializer,
                              400: OpenApiResponse(description="validation_error (bad / imprecise reading), "
                                                               "mock_location, check_in_refused (message says "
                                                               "why: non-working day, already checked in, no "
                                                               "department, outside every location)"),
                              **AUTHED_ERRORS})
    def post(self, request):
        from attendance import services

        body = s.CheckInRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        v = body.validated_data
        if v["is_mocked"]:
            services.log(request.user, timezone.localdate(),
                         f"Refused mobile check-in: mock location reported at {v['latitude']},{v['longitude']} "
                         f"(accuracy {v['accuracy']} m)", kind="mock_location")
            raise MockLocation("Your phone reports a mock (fake) location. Turn off any location-faking app "
                               "or developer setting and try again.")
        try:
            lat, lng, _acc = services.validate_gps_reading(v["latitude"], v["longitude"], v["accuracy"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"non_field_errors": exc.messages})
        result = services.check_in_user(request.user, lat, lng)
        if not result.ok:
            raise CheckInRefused(result.message)
        rec = result.record
        return Response(s.CheckInResultSerializer({
            "status": result.status, "status_display": rec.get_status_display(), "message": result.message,
            "distance_meters": result.distance_meters,
            "location": result.location.name if result.location else None, "record": svc.record_item(rec),
        }).data, status=201)


class CheckOutView(APIView):
    @extend_schema(operation_id="attendance_check_out", request=None,
                   responses={501: OpenApiResponse(description="not_ready: the website records check-in only."),
                              **AUTHED_ERRORS})
    def post(self, request):
        raise NotReady("Check-out is not recorded: attendance only records your check-in.")


class HistoryView(APIView):
    pagination_class = StandardPagination

    @extend_schema(operation_id="attendance_history", parameters=[s.HistoryQuerySerializer],
                   responses={200: s.RecordSerializer(many=True), **errors(400), **AUTHED_ERRORS})
    def get(self, request):
        """My records, newest first (year defaults to this year; past working
        days without a record are filled as absent, as on the website)."""
        q = s.HistoryQuerySerializer(data=request.query_params)
        q.is_valid(raise_exception=True)
        v = q.validated_data
        qs = svc.history_queryset(request.user, v.get("year") or timezone.localdate().year, v.get("month"),
                                  v.get("status") or "")
        return paginate(self, request, qs, s.RecordSerializer, svc.record_item)


class SummaryView(APIView):
    @extend_schema(operation_id="attendance_summary", responses={200: s.SummarySerializer, **AUTHED_ERRORS})
    def get(self, request):
        return Response(s.SummarySerializer(svc.summary(request.user)).data)
