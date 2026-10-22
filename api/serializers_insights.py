"""insights/ (staff door) - the "My workspace" personal insights."""
from rest_framework import serializers


class InsightsQuerySerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=["week", "month", "quarter"], required=False, default="month")


class ArrivalBucketSerializer(serializers.Serializer):
    label = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    days = serializers.IntegerField()
    average_minutes = serializers.IntegerField(allow_null=True, help_text="Minutes after midnight.")
    average = serializers.CharField(allow_null=True, help_text="HH:MM")


class ArrivalStatsSerializer(serializers.Serializer):
    period = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    days_recorded = serializers.IntegerField()
    late_days = serializers.IntegerField()
    average_minutes = serializers.IntegerField(allow_null=True)
    average = serializers.CharField(allow_null=True)
    earliest = serializers.CharField(allow_null=True)
    latest = serializers.CharField(allow_null=True)
    buckets = ArrivalBucketSerializer(many=True)


class TaskStateSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="done | in_progress | waiting | overdue")
    label = serializers.CharField()
    count = serializers.IntegerField()


class TaskStateCountsSerializer(serializers.Serializer):
    period = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    done = serializers.IntegerField()
    in_progress = serializers.IntegerField()
    waiting = serializers.IntegerField()
    overdue = serializers.IntegerField()
    total = serializers.IntegerField()
    states = TaskStateSerializer(many=True)


class TicketBucketSerializer(serializers.Serializer):
    label = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    raised = serializers.IntegerField()
    resolved = serializers.IntegerField()


class TicketTrendSerializer(serializers.Serializer):
    period = serializers.CharField()
    start = serializers.DateField()
    end = serializers.DateField()
    raised_total = serializers.IntegerField()
    resolved_total = serializers.IntegerField()
    buckets = TicketBucketSerializer(many=True)


class InsightsSerializer(serializers.Serializer):
    period = serializers.CharField(help_text="week | month | quarter")
    period_label = serializers.CharField()
    arrivals = ArrivalStatsSerializer()
    tasks = TaskStateCountsSerializer()
    tickets = TicketTrendSerializer()
