"""dashboard/executive/ (staff door) - response shape."""
from rest_framework import serializers

from .serializers_notifications import NotificationTargetSerializer

TONES = ["good", "warn", "bad", "neutral"]


class KpiSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    value = serializers.FloatField()
    display = serializers.CharField(help_text="Ready-to-show value, e.g. \"₦12.5m\", \"42%\", \"18 of 25\".")
    unit = serializers.CharField(allow_null=True, help_text="NGN | % | null")
    delta = serializers.FloatField(allow_null=True)
    delta_label = serializers.CharField(allow_null=True)
    tone = serializers.ChoiceField(choices=TONES)
    hint = serializers.CharField(allow_null=True)
    target = NotificationTargetSerializer(allow_null=True)


class PointSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.FloatField()


class SeriesSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    kind = serializers.ChoiceField(choices=["line", "bar"])
    unit = serializers.CharField(allow_null=True)
    points = PointSerializer(many=True)


class ListItemSerializer(serializers.Serializer):
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    value = serializers.CharField(allow_null=True)
    tone = serializers.ChoiceField(choices=TONES, allow_null=True)
    target = NotificationTargetSerializer(allow_null=True)


class ListSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    items = ListItemSerializer(many=True)


class ExecutiveDashboardSerializer(serializers.Serializer):
    title = serializers.CharField()
    board = serializers.ChoiceField(choices=["es", "director", "deputy", "chairman"],
                                    help_text="Which website board this mirrors.")
    scope = serializers.ChoiceField(choices=["organisation", "department"])
    scope_label = serializers.CharField()
    generated_at = serializers.DateTimeField()
    kpis = KpiSerializer(many=True)
    series = SeriesSerializer(many=True)
    lists = ListSerializer(many=True)
