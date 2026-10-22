"""announcements/ (staff door) - request validation and response shapes."""
from rest_framework import serializers

KINDS = ["notice", "action_needed", "urgent"]
AUDIENCES = ["all_staff", "department", "roles"]


class AnnouncementListQuerySerializer(serializers.Serializer):
    filter = serializers.ChoiceField(choices=["all", "unread", "needs_ack", "drafts", "sent"], required=False,
                                     default="all", help_text="drafts and sent: only for users who may post.")
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)


class AnnouncementPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AnnouncementTargetRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AnnouncementSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    body_preview = serializers.CharField(allow_blank=True)
    kind = serializers.CharField(help_text="notice | action_needed | urgent")
    kind_label = serializers.CharField()
    requires_ack = serializers.BooleanField()
    pinned = serializers.BooleanField()
    status = serializers.CharField(help_text="draft | published")
    published_at = serializers.DateTimeField(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    author = AnnouncementPersonSerializer(allow_null=True)
    audience_label = serializers.CharField()
    read = serializers.BooleanField()
    acknowledged = serializers.BooleanField()
    has_poll = serializers.BooleanField()
    attachments_count = serializers.IntegerField()
    in_audience = serializers.BooleanField()
    can_edit = serializers.BooleanField()
    can_report = serializers.BooleanField()
    route = serializers.CharField()


class AnnouncementCountsSerializer(serializers.Serializer):
    all = serializers.IntegerField()
    unread = serializers.IntegerField()
    needs_ack = serializers.IntegerField()
    drafts = serializers.IntegerField()
    sent = serializers.IntegerField()


class AnnouncementListSerializer(serializers.Serializer):
    results = AnnouncementSummarySerializer(many=True)
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    count = serializers.IntegerField()
    filter = serializers.CharField()
    counts = AnnouncementCountsSerializer()
    can_post = serializers.BooleanField()


class PollOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()
    chosen = serializers.BooleanField()
    votes = serializers.IntegerField(allow_null=True, help_text="null until results are shown")
    percent = serializers.IntegerField(allow_null=True)


class PollSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    question = serializers.CharField()
    allow_multiple = serializers.BooleanField()
    closes_at = serializers.DateTimeField(allow_null=True)
    is_open = serializers.BooleanField()
    has_voted = serializers.BooleanField()
    can_vote = serializers.BooleanField()
    show_results = serializers.BooleanField()
    voters = serializers.IntegerField(allow_null=True)
    options = PollOptionSerializer(many=True)


class AttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    size = serializers.IntegerField(allow_null=True)
    download_url = serializers.URLField()


class AnnouncementDetailSerializer(AnnouncementSummarySerializer):
    body = serializers.CharField(allow_blank=True)
    acknowledged_at = serializers.DateTimeField(allow_null=True)
    audience = serializers.CharField(help_text="all_staff | department | roles")
    department = AnnouncementTargetRefSerializer(allow_null=True)
    roles = AnnouncementTargetRefSerializer(many=True)
    attachments = AttachmentSerializer(many=True)
    poll = PollSerializer(allow_null=True)
    can_acknowledge = serializers.BooleanField()


class AcknowledgeResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    acknowledged = serializers.BooleanField()
    acknowledged_at = serializers.DateTimeField()
    created = serializers.BooleanField(help_text="False when it was already acknowledged.")


class VoteRequestSerializer(serializers.Serializer):
    option_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=6)


class AnnouncementWriteSerializer(serializers.Serializer):
    """POST (create) and PATCH (edit). JSON, or multipart when sending
    ``attachment`` (then ``roles`` / ``poll_options`` / ``remove_attachment_ids``
    are repeated fields). Validation is the website's AnnouncementForm."""

    title = serializers.CharField(max_length=200, required=False)
    body = serializers.CharField(required=False)
    kind = serializers.ChoiceField(choices=KINDS, required=False)
    requires_ack = serializers.BooleanField(required=False)
    audience = serializers.ChoiceField(choices=AUDIENCES, required=False)
    department = serializers.IntegerField(required=False, allow_null=True, help_text="Department id (audience=department).")
    roles = serializers.ListField(child=serializers.IntegerField(), required=False, help_text="Role ids (audience=roles).")
    pinned = serializers.BooleanField(required=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    poll_question = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                          help_text="Blank removes the poll (while nobody has voted).")
    poll_options = serializers.ListField(child=serializers.CharField(max_length=200, allow_blank=True),
                                         required=False, max_length=6)
    poll_allow_multiple = serializers.BooleanField(required=False)
    poll_closes_at = serializers.DateTimeField(required=False, allow_null=True)
    attachment = serializers.FileField(required=False, help_text="PDF, Word, Excel or image, up to 10 MB.")
    remove_attachment_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    publish = serializers.BooleanField(required=False, default=False,
                                       help_text="Publish now (drafts only) and notify the audience.")

    def validate(self, attrs):
        if self.context.get("creating"):
            missing = {k: ["This field is required."] for k in ("title", "body") if not attrs.get(k)}
            if missing:
                raise serializers.ValidationError(missing)
        return attrs


class PublishResponseSerializer(serializers.Serializer):
    notified = serializers.IntegerField(help_text="Staff notified now (0 if it was already published).")
    announcement = AnnouncementDetailSerializer()


class ReportPendingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    department = serializers.CharField(allow_null=True)
    role = serializers.CharField(allow_null=True)


class ReportPollOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()
    votes = serializers.IntegerField()
    percent = serializers.IntegerField()


class ReportPollSerializer(serializers.Serializer):
    question = serializers.CharField()
    voters = serializers.IntegerField()
    total_votes = serializers.IntegerField()
    options = ReportPollOptionSerializer(many=True)


class AnnouncementReportSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    requires_ack = serializers.BooleanField()
    audience_size = serializers.IntegerField()
    read = serializers.IntegerField()
    read_percent = serializers.IntegerField()
    acknowledged = serializers.IntegerField()
    acknowledged_percent = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    pending = ReportPendingSerializer(many=True, help_text="Not acknowledged (if asked), else not read.")
    poll = ReportPollSerializer(allow_null=True)
