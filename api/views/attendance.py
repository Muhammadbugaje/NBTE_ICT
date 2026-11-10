"""attendance/ (staff door). See api/services/attendance.py."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import serializers_attendance as s
from ..exceptions import ApiError
from ..pagination import StandardPagination
from ..services import attendance as svc
from ..services.work import paginate
from .common import AUTHED_ERRORS, errors


class MockLocation(ApiError):
    status_code, code = 400, "mock_location"


class CheckInRefused(ApiError):
    status_code, code = 400, "check_in_refused"


class CheckOutRefused(ApiError):
    status_code, code = 400, "check_out_refused"


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


class CodeCheckInView(APIView):
    @extend_schema(operation_id="attendance_check_in_code", request=s.CodeCheckInRequestSerializer,
                   responses={201: s.CheckInResultSerializer,
                              400: OpenApiResponse(description="validation_error on `code` (wrong code, too "
                                                               "many wrong codes, empty); check_in_refused "
                                                               "(non-working day, already checked in, on "
                                                               "approved leave, office codes switched off)"),
                              **AUTHED_ERRORS})
    def post(self, request):
        """Check in with the rotating office code, exactly like the website's
        "Check in with a code" page (attendance.codes.check_in_with_code)."""
        from attendance import codes

        body = s.CodeCheckInRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        result = codes.check_in_with_code(request.user, body.validated_data["code"])
        if not result.ok:
            if result.error == "code":
                raise serializers.ValidationError({"code": [result.message]})
            raise CheckInRefused(result.message)
        rec = result.record
        return Response(s.CheckInResultSerializer({
            "status": result.status, "status_display": rec.get_status_display(), "message": result.message,
            "distance_meters": None, "location": result.location.name, "record": svc.record_item(rec),
        }).data, status=201)


class CheckOutView(APIView):
    @extend_schema(operation_id="attendance_check_out", request=s.CheckOutRequestSerializer,
                   responses={200: s.CheckOutResultSerializer,
                              400: OpenApiResponse(description="validation_error (code / token / reading); "
                                                               "check_out_refused (not checked in, already "
                                                               "checked out, outside every office: use the "
                                                               "code or QR)"),
                              **AUTHED_ERRORS})
    def post(self, request):
        """v2.1 G1/G2: check out after proving presence by GPS, the current
        office code or the office QR (attendance.checkout.check_out, the
        website's button uses it too). The day counts from the check-in."""
        from attendance import checkout

        body = s.CheckOutRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        v = body.validated_data
        token = (v.get("token") or "").strip()
        if "/scan/" in token:
            token = token.rstrip("/").rsplit("/scan/", 1)[-1]
        out = checkout.check_out(request.user, method=v["method"], lat=v.get("latitude"), lng=v.get("longitude"),
                                 accuracy=v.get("accuracy"), code=v.get("code") or "", token=token)
        if not out.ok:
            if out.error in ("code", "qr"):
                raise serializers.ValidationError({"code" if out.error == "code" else "token": [out.message]})
            raise CheckOutRefused(out.message)
        return Response(s.CheckOutResultSerializer({"message": out.message,
                                                     "record": svc.record_item(out.record)}).data)


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
