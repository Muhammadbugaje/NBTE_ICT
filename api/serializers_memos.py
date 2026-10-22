"""Memos: request validation and response shapes (staff door)."""
from rest_framework import serializers

from messaging import memos as memo_service
from messaging.models import Memo

from .serializers_chats import PersonSerializer

STATUSES = [c for c, _ in Memo.STATUS_CHOICES]
PRIORITIES = [c for c, _ in Memo.PRIORITY_CHOICES]
VISIBILITIES = [c for c, _ in Memo.VISIBILITY_CHOICES]


class MemoListQuerySerializer(serializers.Serializer):
    tab = serializers.ChoiceField(choices=list(memo_service.MEMO_TABS), required=False, default="inbox")
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False)


class MemoRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    subject = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    priority = serializers.ChoiceField(choices=PRIORITIES)
    status = serializers.ChoiceField(choices=STATUSES)
    status_label = serializers.CharField()
    sender = PersonSerializer()
    audience = serializers.CharField()
    created_at = serializers.DateTimeField()
    published_at = serializers.DateTimeField(allow_null=True)
    updated_at = serializers.DateTimeField()
    requires_acknowledgment = serializers.BooleanField()
    unread = serializers.BooleanField()
    acknowledged = serializers.BooleanField()
    current_step = serializers.CharField(allow_null=True)
    attachment_count = serializers.IntegerField()


class MemoCountsSerializer(serializers.Serializer):
    inbox = serializers.IntegerField()
    awaiting = serializers.IntegerField()
    sent = serializers.IntegerField()
    drafts = serializers.IntegerField()
    archived = serializers.IntegerField()


class MemoListSerializer(serializers.Serializer):
    results = MemoRowSerializer(many=True)
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    count = serializers.IntegerField()
    counts = MemoCountsSerializer()
    can_compose = serializers.BooleanField()


class MemoAttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    size_bytes = serializers.IntegerField()
    content_type = serializers.CharField()
    download_path = serializers.CharField()
    expires_at = serializers.DateTimeField()
    expired = serializers.BooleanField()


class RouteStepSerializer(serializers.Serializer):
    position = serializers.IntegerField()
    name = serializers.CharField()
    state = serializers.CharField(help_text="One of: approved | returned | rejected | skipped | current | parked | pending | will_skip | blocked | not_reached.")
    is_current = serializers.BooleanField()
    actor = PersonSerializer(allow_null=True)
    reason = serializers.CharField(allow_blank=True)
    decided_by = PersonSerializer(allow_null=True)
    decided_at = serializers.DateTimeField(allow_null=True)
    comment = serializers.CharField(allow_blank=True)


class CurrentStepSerializer(serializers.Serializer):
    name = serializers.CharField()
    instructions = serializers.CharField(allow_blank=True)
    actor = PersonSerializer(allow_null=True)


class DecisionSerializer(serializers.Serializer):
    by = PersonSerializer(allow_null=True)
    at = serializers.DateTimeField(allow_null=True)
    comment = serializers.CharField(allow_blank=True)


class MyReceiptSerializer(serializers.Serializer):
    read_at = serializers.DateTimeField(allow_null=True)
    acknowledged_at = serializers.DateTimeField(allow_null=True)
    archived = serializers.BooleanField()


class MemoStatsSerializer(serializers.Serializer):
    recipients = serializers.IntegerField()
    read = serializers.IntegerField()
    acknowledged = serializers.IntegerField()
    pending_acknowledgment = serializers.ListField(child=serializers.CharField(), help_text="First 50 names.")


class DocumentsSerializer(serializers.Serializer):
    pdf_path = serializers.CharField()
    docx_path = serializers.CharField()
    watermarked = serializers.BooleanField()


class DepartmentRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


ACTIONS = ["approve", "return", "reject", "resubmit", "publish", "acknowledge", "archive", "unarchive", "cancel"]


class MemoDetailSerializer(MemoRowSerializer):
    body = serializers.CharField()
    visibility = serializers.ChoiceField(choices=VISIBILITIES)
    visibility_label = serializers.CharField()
    target_user = PersonSerializer(allow_null=True)
    department = DepartmentRefSerializer(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)
    attachments = MemoAttachmentSerializer(many=True)
    route = serializers.CharField(allow_null=True)
    route_steps = RouteStepSerializer(many=True)
    current_step_detail = CurrentStepSerializer(allow_null=True)
    acting_for_someone = serializers.BooleanField()
    parked_reason = serializers.CharField(allow_blank=True)
    returned = DecisionSerializer(allow_null=True)
    rejected = DecisionSerializer(allow_null=True)
    my_receipt = MyReceiptSerializer(allow_null=True)
    is_sender = serializers.BooleanField()
    actions = serializers.ListField(child=serializers.ChoiceField(choices=ACTIONS))
    stats = MemoStatsSerializer(allow_null=True)
    documents = DocumentsSerializer(allow_null=True)


class MemoActionSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=5000,
                                   help_text="cancel only")
    subject = serializers.CharField(required=False, allow_blank=True, max_length=255, help_text="resubmit only")
    body = serializers.CharField(required=False, allow_blank=True, max_length=50000, help_text="resubmit only")


class MemoActionResponseSerializer(serializers.Serializer):
    notice = serializers.CharField()
    memo = MemoDetailSerializer(allow_null=True, help_text="null if you can no longer see the memo.")


class MemoCreateSerializer(serializers.Serializer):
    """Fields of the website's MemoForm (validated by that form)."""
    subject = serializers.CharField(max_length=255)
    body = serializers.CharField()
    priority = serializers.ChoiceField(choices=PRIORITIES, required=False, default=Memo.NORMAL)
    visibility = serializers.ChoiceField(choices=VISIBILITIES)
    target_user = serializers.IntegerField(required=False, allow_null=True, help_text="specific_user")
    department = serializers.IntegerField(required=False, allow_null=True, help_text="department")
    roles = serializers.ListField(child=serializers.IntegerField(), required=False, help_text="role (group ids)")
    recipients = serializers.ListField(child=serializers.IntegerField(), required=False, help_text="individuals")
    requires_acknowledgment = serializers.BooleanField(required=False, default=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    attachments = serializers.ListField(child=serializers.FileField(), required=False)
    action = serializers.ChoiceField(choices=["draft", "publish"], required=False, default="draft")
    from_message = serializers.IntegerField(required=False, allow_null=True,
                                            help_text="Chat message this memo was made from (links it).")


class MemoCreateResponseSerializer(MemoActionResponseSerializer):
    notices = serializers.ListField(child=serializers.DictField(child=serializers.CharField()))


class ChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class ComposeOptionsSerializer(serializers.Serializer):
    visibility = ChoiceSerializer(many=True)
    priorities = ChoiceSerializer(many=True)
    departments = DepartmentRefSerializer(many=True)
    roles = DepartmentRefSerializer(many=True)
    can_broadcast = serializers.BooleanField()
    attachments_total_bytes = serializers.IntegerField()


class MemoPersonSerializer(PersonSerializer):
    email = serializers.CharField()
    department = serializers.CharField(allow_null=True)


class PeopleQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)
