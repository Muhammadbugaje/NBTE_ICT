"""Request validation and response shapes for attendance/ (staff door)."""
from rest_framework import serializers

STATUSES = ["present", "late", "absent", "excused", "holiday", "on_leave"]


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
    check_out_supported = serializers.BooleanField()


class CheckInRequestSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    accuracy = serializers.FloatField(help_text="Metres, as reported by the device.")
    is_mocked = serializers.BooleanField(required=False, default=False,
                                         help_text="True when the OS reports a mock / fake location.")


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
