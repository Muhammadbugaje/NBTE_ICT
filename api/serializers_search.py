"""search/ (staff door) - request validation and response shapes."""
from rest_framework import serializers

from .serializers_notifications import NotificationTargetSerializer
from .services.search import DEFAULT_LIMIT, KIND_CHOICES, MAX_LIMIT, MIN_QUERY


class SearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(min_length=MIN_QUERY, max_length=200, trim_whitespace=True,
                              help_text=f"At least {MIN_QUERY} characters.")
    limit = serializers.IntegerField(required=False, default=DEFAULT_LIMIT, min_value=1, max_value=MAX_LIMIT,
                                     help_text="Results per group (1-10, default 5).")


class SearchResultSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField(allow_blank=True)
    subtitle = serializers.CharField(allow_blank=True)
    badge = serializers.CharField(allow_null=True)
    target = NotificationTargetSerializer(allow_null=True,
                                          help_text="Same format as a notification's target.")
    web_url = serializers.CharField(allow_null=True, help_text="Website path (relative to the website host).")


class SearchGroupSerializer(serializers.Serializer):
    kind = serializers.CharField(help_text=" | ".join(KIND_CHOICES))
    label = serializers.CharField()
    count = serializers.IntegerField()
    results = SearchResultSerializer(many=True)


class SearchResponseSerializer(serializers.Serializer):
    query = serializers.CharField()
    total = serializers.IntegerField()
    groups = SearchGroupSerializer(many=True)
