"""notifications/ (both doors) - request validation and response shapes."""
from rest_framework import serializers

from .services.notifications import TARGET_TYPES

KINDS = ["chats", "tasks_tickets", "approvals_memos", "payments", "qa_cases", "announcements", "general"]


class NotificationListQuerySerializer(serializers.Serializer):
    filter = serializers.ChoiceField(choices=["all", "unread", "read"], required=False, default="all")
    kind = serializers.ChoiceField(choices=KINDS, required=False)
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)


class NotificationTargetSerializer(serializers.Serializer):
    type = serializers.CharField(help_text=" | ".join(TARGET_TYPES))
    id = serializers.IntegerField(allow_null=True)
    route = serializers.CharField(allow_null=True, help_text="App route; null for type web.")
    url = serializers.CharField(allow_null=True, help_text="Absolute website link (open in a browser for web).")


class NotificationActorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class NotificationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    body = serializers.CharField(allow_blank=True)
    level = serializers.CharField(help_text="info | success | warning | error")
    kind = serializers.CharField(help_text=" | ".join(KINDS))
    category = serializers.CharField()
    created_at = serializers.DateTimeField()
    read = serializers.BooleanField()
    read_at = serializers.DateTimeField(allow_null=True)
    actor = NotificationActorSerializer(allow_null=True)
    target = NotificationTargetSerializer(allow_null=True)


class NotificationUnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


class NotificationMarkResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    read = serializers.BooleanField()
    unread = serializers.IntegerField(help_text="Unread total after the change.")


class NotificationReadAllResponseSerializer(serializers.Serializer):
    marked = serializers.IntegerField()
    unread = serializers.IntegerField()


class NotificationBulkRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=500)
    action = serializers.ChoiceField(choices=["mark_read", "mark_unread", "delete"])


class NotificationSkippedSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reason = serializers.CharField()


class NotificationBulkResponseSerializer(serializers.Serializer):
    action = serializers.CharField()
    done = serializers.ListField(child=serializers.IntegerField())
    skipped = NotificationSkippedSerializer(many=True)
    message = serializers.CharField()
    unread = serializers.IntegerField()
