"""Chats: request validation and response shapes (staff door)."""
from rest_framework import serializers

from messaging import chat_services as cs

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


class PersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    initials = serializers.CharField()
    color = serializers.CharField(help_text="Avatar colour slot c0..c7.")


class AvatarSerializer(serializers.Serializer):
    initials = serializers.CharField()
    color = serializers.CharField()
    seed = serializers.IntegerField()


class ChatAttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    size_bytes = serializers.IntegerField()
    content_type = serializers.CharField()
    download_path = serializers.CharField()
    expires_at = serializers.DateTimeField()


class VoiceNoteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    duration_ms = serializers.IntegerField()
    waveform = serializers.ListField(child=serializers.IntegerField(), help_text="48 bars, 0-100.")
    size_bytes = serializers.IntegerField()
    mime = serializers.CharField()
    audio_path = serializers.CharField()
    played = serializers.BooleanField()
    transcript_status = serializers.ChoiceField(choices=["pending", "done", "failed", "disabled", "unavailable"])
    transcript = serializers.CharField(allow_blank=True)
    transcript_language = serializers.CharField(allow_blank=True)
    transcript_edited = serializers.BooleanField()


class ChatPollOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()
    votes = serializers.IntegerField()
    percent = serializers.IntegerField()
    mine = serializers.BooleanField()
    leading = serializers.BooleanField()
    voters = serializers.ListField(child=serializers.CharField())


class ChatPollSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    question = serializers.CharField()
    allow_multiple = serializers.BooleanField()
    closed = serializers.BooleanField()
    closes_at = serializers.DateTimeField(allow_null=True)
    voter_count = serializers.IntegerField()
    voted = serializers.BooleanField()
    options = ChatPollOptionSerializer(many=True)


class ReactionSerializer(serializers.Serializer):
    emoji = serializers.CharField()
    count = serializers.IntegerField()
    users = serializers.ListField(child=serializers.CharField(), help_text='Names; the viewer is "You".')
    mine = serializers.BooleanField()


class ReceiptSerializer(serializers.Serializer):
    state = serializers.CharField(help_text="One of: sent | delivered | read.")
    read_by = serializers.IntegerField()
    delivered_to = serializers.IntegerField()
    total = serializers.IntegerField()
    label = serializers.CharField()


class ReplyPreviewSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sender_name = serializers.CharField()
    preview = serializers.CharField(allow_blank=True)


class LinkedRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    route = serializers.CharField()


class SenderSerializer(PersonSerializer):
    pass


class MessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    thread_id = serializers.IntegerField()
    kind = serializers.CharField(help_text="One of: text | voice | poll | system | deleted.")
    sender = SenderSerializer(allow_null=True)
    is_mine = serializers.BooleanField()
    is_system = serializers.BooleanField()
    body = serializers.CharField(allow_blank=True)
    sent_at = serializers.DateTimeField()
    edited = serializers.BooleanField()
    edited_at = serializers.DateTimeField(allow_null=True)
    changed_at = serializers.DateTimeField(allow_null=True)
    deleted = serializers.BooleanField()
    forwarded = serializers.BooleanField()
    reply_to = ReplyPreviewSerializer(allow_null=True)
    attachments = ChatAttachmentSerializer(many=True)
    voice_note = VoiceNoteSerializer(allow_null=True)
    poll = ChatPollSerializer(allow_null=True)
    reactions = ReactionSerializer(many=True)
    my_reaction = serializers.CharField(allow_null=True)
    starred = serializers.BooleanField()
    receipt = ReceiptSerializer(allow_null=True, help_text="Only on your own messages.")
    linked_task = LinkedRefSerializer(allow_null=True)
    linked_memo = LinkedRefSerializer(allow_null=True)
    can_edit = serializers.BooleanField()
    can_delete_for_everyone = serializers.BooleanField()


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------
class ThreadListQuerySerializer(serializers.Serializer):
    filter = serializers.ChoiceField(choices=["all", "unread", "groups", "direct", "archived"], required=False,
                                     default="all")
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)


class LastMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField(help_text="One of: text | voice | poll | attachment | system | deleted.")
    preview = serializers.CharField(allow_blank=True)
    sender_name = serializers.CharField()
    sent_at = serializers.DateTimeField()


class ThreadRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    kind = serializers.CharField(help_text="One of: direct | group.")
    avatar = AvatarSerializer()
    participants = PersonSerializer(many=True, help_text="Up to 5 other participants.")
    participant_count = serializers.IntegerField()
    last_message = LastMessageSerializer(allow_null=True)
    last_message_at = serializers.DateTimeField(allow_null=True)
    unread = serializers.IntegerField()
    pinned = serializers.BooleanField()
    muted = serializers.BooleanField()
    muted_until = serializers.DateTimeField(allow_null=True)
    archived = serializers.BooleanField()
    typing = serializers.ListField(child=serializers.CharField(), help_text="First names typing now.")


class ThreadParticipantSerializer(PersonSerializer):
    role = serializers.ChoiceField(choices=["creator", "member"])
    is_me = serializers.BooleanField()
    designation = serializers.CharField(allow_null=True)
    department = serializers.CharField(allow_null=True)
    joined_at = serializers.DateTimeField()


class ThreadLimitsSerializer(serializers.Serializer):
    attachments_total_bytes = serializers.IntegerField()
    voice_max_seconds = serializers.IntegerField()
    poll_options_min = serializers.IntegerField()
    poll_options_max = serializers.IntegerField()
    edit_window_minutes = serializers.IntegerField()


class ThreadDetailSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    subject = serializers.CharField(allow_blank=True)
    kind = serializers.CharField(help_text="One of: direct | group.")
    avatar = AvatarSerializer()
    created_by = PersonSerializer(allow_null=True)
    created_at = serializers.DateTimeField()
    participants = ThreadParticipantSerializer(many=True)
    pinned = serializers.BooleanField()
    muted = serializers.BooleanField()
    muted_until = serializers.DateTimeField(allow_null=True)
    archived = serializers.BooleanField()
    unread = serializers.IntegerField()
    can_post = serializers.BooleanField()
    can_create_poll = serializers.BooleanField()
    reaction_emojis = serializers.ListField(child=serializers.CharField())
    limits = ThreadLimitsSerializer()


class ThreadCreateSerializer(serializers.Serializer):
    participants = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    subject = serializers.CharField(required=False, allow_blank=True, max_length=255)
    body = serializers.CharField(trim_whitespace=True, max_length=20000)
    attachments = serializers.ListField(child=serializers.FileField(), required=False)


class ContactSerializer(PersonSerializer):
    email = serializers.CharField()
    department = serializers.CharField(allow_null=True)
    designation = serializers.CharField(allow_null=True)


class SearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=100)


class SearchRequiredQuerySerializer(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=100)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=50)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
class MessagesQuerySerializer(serializers.Serializer):
    before = serializers.IntegerField(required=False, min_value=1)
    after = serializers.IntegerField(required=False, min_value=1)
    around = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=cs.MAX_PAGE, default=cs.DEFAULT_PAGE)

    def validate(self, attrs):
        if sum(1 for k in ("before", "after", "around") if attrs.get(k)) > 1:
            raise serializers.ValidationError("Use only one of before, after and around.")
        return attrs


class MessagePageSerializer(serializers.Serializer):
    results = MessageSerializer(many=True, help_text="Chronological (oldest first).")
    next = serializers.CharField(allow_null=True, help_text="Older page (before=<first id>).")
    previous = serializers.CharField(allow_null=True, help_text="Newer page (after=<last id>).")
    count = serializers.IntegerField(allow_null=True)
    has_older = serializers.BooleanField()
    has_newer = serializers.BooleanField()
    server_time = serializers.DateTimeField()
    last_id = serializers.IntegerField(help_text="Newest visible message id: pass as `after` to updates/.")


class UpdatesQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(required=False, min_value=0, default=0)
    since = serializers.CharField(required=False, allow_blank=True)
    visible = serializers.BooleanField(required=False, default=True)


class TypingPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    first_name = serializers.CharField()


class UpdatesSerializer(serializers.Serializer):
    server_time = serializers.DateTimeField()
    last_id = serializers.IntegerField()
    new = MessageSerializer(many=True)
    changed = MessageSerializer(many=True)
    receipts = serializers.DictField(child=ReceiptSerializer(), help_text="Keyed by message id (string).")
    typing = TypingPersonSerializer(many=True)


class SendMessageSerializer(serializers.Serializer):
    body = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    reply_to = serializers.IntegerField(required=False, allow_null=True)
    attachments = serializers.ListField(child=serializers.FileField(), required=False)


class EditMessageSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=20000)


class DeleteMessageSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["me", "everyone"])


class ReactSerializer(serializers.Serializer):
    emoji = serializers.CharField(max_length=16)


class ReactResponseSerializer(serializers.Serializer):
    my_reaction = serializers.CharField(allow_null=True)
    message = MessageSerializer()


class StarResponseSerializer(serializers.Serializer):
    starred = serializers.BooleanField()
    message = MessageSerializer()


class ForwardSerializer(serializers.Serializer):
    thread_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False, max_length=50)


class ForwardResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    message_ids = serializers.ListField(child=serializers.IntegerField())


class InfoRowSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    name = serializers.CharField()
    initials = serializers.CharField()
    color = serializers.CharField()
    read_at = serializers.DateTimeField(allow_null=True)
    delivered = serializers.BooleanField()
    delivered_at = serializers.DateTimeField(allow_null=True)


class InfoReceiptSerializer(serializers.Serializer):
    state = serializers.CharField(help_text="One of: sent | delivered | read.")
    read_by = serializers.IntegerField()
    delivered_to = serializers.IntegerField()
    total = serializers.IntegerField()


class MessageInfoSerializer(serializers.Serializer):
    message_id = serializers.IntegerField()
    sent_at = serializers.DateTimeField()
    receipt = InfoReceiptSerializer()
    read = InfoRowSerializer(many=True)
    delivered = InfoRowSerializer(many=True)
    pending = InfoRowSerializer(many=True)


class PrefillSerializer(serializers.Serializer):
    title = serializers.CharField()
    description = serializers.CharField()
    from_message = serializers.IntegerField()


class StarredSerializer(serializers.Serializer):
    starred_at = serializers.DateTimeField()
    thread_id = serializers.IntegerField()
    thread_title = serializers.CharField()
    message = MessageSerializer()


class SearchHitSerializer(serializers.Serializer):
    thread_id = serializers.IntegerField()
    thread_title = serializers.CharField()
    message_id = serializers.IntegerField()
    kind = serializers.CharField()
    snippet = serializers.CharField(allow_blank=True)
    sender_name = serializers.CharField()
    sent_at = serializers.DateTimeField()


class SearchResultsSerializer(serializers.Serializer):
    results = SearchHitSerializer(many=True)
    count = serializers.IntegerField()


# ---------------------------------------------------------------------------
# Thread actions
# ---------------------------------------------------------------------------
class TypingSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["typing", "stop"], required=False, default="typing")


class ReadResponseSerializer(serializers.Serializer):
    marked = serializers.IntegerField(help_text="Messages newly marked read.")
    unread = serializers.IntegerField()


class PinSerializer(serializers.Serializer):
    pinned = serializers.BooleanField()


class PinResponseSerializer(serializers.Serializer):
    pinned = serializers.BooleanField()


class MuteSerializer(serializers.Serializer):
    duration = serializers.ChoiceField(choices=["8h", "1w", "always", "off"])


class MuteResponseSerializer(serializers.Serializer):
    muted = serializers.BooleanField()
    muted_until = serializers.DateTimeField(allow_null=True)


class ArchiveResponseSerializer(serializers.Serializer):
    archived = serializers.BooleanField()


class PollCreateSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=300)
    options = serializers.ListField(child=serializers.CharField(max_length=120, allow_blank=True),
                                    min_length=1, max_length=20)
    allow_multiple = serializers.BooleanField(required=False, default=False)


class PollVoteSerializer(serializers.Serializer):
    option_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=True,
                                       help_text="Empty = withdraw your vote.")


class VoiceUploadSerializer(serializers.Serializer):
    audio = serializers.FileField()
    duration_ms = serializers.IntegerField(required=False, min_value=0)
    reply_to = serializers.IntegerField(required=False, allow_null=True)


class TranscriptSerializer(serializers.Serializer):
    text = serializers.CharField(allow_blank=True, max_length=10000, trim_whitespace=True)
