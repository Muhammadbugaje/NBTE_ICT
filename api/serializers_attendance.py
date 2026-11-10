"""Request validation and response shapes for attendance/ (staff door)."""
from rest_framework import serializers

STATUSES = ["present", "late", "absent", "excused", "holiday", "on_leave", "on_duty"]


class LocationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    radius_meters = serializers.IntegerField(help_text="Effective geofence radius (radius + GPS offset).")
    work_start_time = serializers.CharField(allow_null=True, help_text="HH:MM; later than start + grace = late.")
    grace_minutes = serializers.IntegerField()


class RecordSerializer(serializers.Serializer):
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=STATUSES)
    status_display = serializers.CharField()
    check_in_at = serializers.DateTimeField(allow_null=True)
    location = serializers.CharField(allow_null=True)
    distance_meters = serializers.IntegerField(allow_null=True)
    source = serializers.CharField()
    source_display = serializers.CharField()
    detail = serializers.CharField(allow_blank=True, help_text="Why a non-present day is credited (leave, excused).")
    status_label = serializers.CharField(help_text="Status with the leave type, e.g. 'On maternity leave' (F6).")
    check_out_at = serializers.DateTimeField(allow_null=True)
    check_out_source = serializers.CharField(allow_blank=True, help_text="gps | code | qr | manual | excel")
    missing_checkout = serializers.BooleanField(help_text="Checked in, never checked out (the day still counts).")


class TodaySerializer(serializers.Serializer):
    date = serializers.DateField()
    is_working_day = serializers.BooleanField()
    checked_in = serializers.BooleanField()
    record = RecordSerializer(allow_null=True)
    can_check_in = serializers.BooleanField()
    reason = serializers.CharField(allow_blank=True, help_text="Why check-in is not possible (empty when it is).")
    locations = LocationSerializer(many=True)
    max_accuracy_meters = serializers.IntegerField()
    manual_check_in_enabled = serializers.BooleanField()
    office_code_enabled = serializers.BooleanField(
        help_text="Office-code check-in (attendance/check-in/code/) is switched on.")
    office_code_reason = serializers.CharField(
        allow_blank=True, help_text="Why a code check-in is not possible today (empty when it is).")
    check_out_supported = serializers.BooleanField()
    can_check_out = serializers.BooleanField(help_text="Checked in today and not yet checked out.")
    check_out_reason = serializers.CharField(allow_blank=True)


class CheckOutRequestSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=["gps", "code", "qr"])
    latitude = serializers.FloatField(required=False)
    longitude = serializers.FloatField(required=False)
    accuracy = serializers.FloatField(required=False)
    code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    token = serializers.CharField(required=False, allow_blank=True, max_length=600,
                                  help_text="The scanned office QR (its URL or the token part).")


class CheckOutResultSerializer(serializers.Serializer):
    message = serializers.CharField()
    record = RecordSerializer()


class CheckInRequestSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    accuracy = serializers.FloatField(help_text="Metres, as reported by the device.")
    is_mocked = serializers.BooleanField(required=False, default=False,
                                         help_text="True when the OS reports a mock / fake location.")


class CodeCheckInRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12, trim_whitespace=True,
                                 help_text="The rotating office code (case and spaces ignored).")


class CheckInResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["present", "late"])
    status_display = serializers.CharField()
    message = serializers.CharField()
    distance_meters = serializers.IntegerField(allow_null=True)
    location = serializers.CharField(allow_null=True)
    record = RecordSerializer()


class HistoryQuerySerializer(serializers.Serializer):
    year = serializers.IntegerField(required=False, min_value=2000, max_value=2100)
    month = serializers.IntegerField(required=False, min_value=1, max_value=12)
    status = serializers.ChoiceField(choices=STATUSES, required=False)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class TallySerializer(serializers.Serializer):
    present = serializers.IntegerField()
    late = serializers.IntegerField()
    absent = serializers.IntegerField()
    excused = serializers.IntegerField()
    holiday = serializers.IntegerField()
    on_leave = serializers.IntegerField()
    credited = serializers.IntegerField(help_text="Everything except absent.")
    expected = serializers.IntegerField(help_text="Recorded working days counted.")
    percent = serializers.IntegerField()


class DaySerializer(serializers.Serializer):
    date = serializers.DateField()
    weekday = serializers.CharField()
    is_working_day = serializers.BooleanField()
    is_future = serializers.BooleanField()
    status = serializers.CharField(allow_null=True)
    status_display = serializers.CharField(allow_null=True)
    check_in_at = serializers.DateTimeField(allow_null=True)


class SummarySerializer(serializers.Serializer):
    week_start = serializers.DateField()
    week_end = serializers.DateField()
    week = TallySerializer()
    days = DaySerializer(many=True)
    last_30_days = TallySerializer()
    streak = serializers.IntegerField(help_text="Consecutive working days attended.")
