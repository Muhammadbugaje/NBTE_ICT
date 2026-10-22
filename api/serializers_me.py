"""me/preferences/, me/devices/, me/push-token/, me/deletion-request/,
me/staff-id/ - request validation and response shapes."""
from rest_framework import serializers

PORTAL_HIDDEN_KINDS = ("tasks_tickets", "approvals_memos")  # notifications.preferences.KINDS doors


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
class PreferenceKindSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    help = serializers.CharField()


class PreferencesSerializer(serializers.Serializer):
    chats = serializers.BooleanField()
    tasks_tickets = serializers.BooleanField(required=False, help_text="Staff only (absent for portal accounts).")
    approvals_memos = serializers.BooleanField(required=False, help_text="Staff only (absent for portal accounts).")
    payments = serializers.BooleanField()
    qa_cases = serializers.BooleanField()
    announcements = serializers.BooleanField()
    general = serializers.BooleanField()
    email_digest = serializers.BooleanField()
    quiet_hours_enabled = serializers.BooleanField()
    quiet_from = serializers.CharField(help_text="HH:MM")
    quiet_to = serializers.CharField(help_text="HH:MM")
    allow_urgent_during_quiet = serializers.BooleanField()
    show_previews = serializers.CharField(help_text="always | unlocked | never")
    transcribe_voice_notes = serializers.BooleanField()
    timezone = serializers.CharField()
    kinds = PreferenceKindSerializer(many=True, help_text="The kind switches this account has, in display order.")
    timezones = serializers.ListField(child=serializers.CharField())


class PreferencesUpdateSerializer(serializers.Serializer):
    chats = serializers.BooleanField(required=False)
    tasks_tickets = serializers.BooleanField(required=False)
    approvals_memos = serializers.BooleanField(required=False)
    payments = serializers.BooleanField(required=False)
    qa_cases = serializers.BooleanField(required=False)
    announcements = serializers.BooleanField(required=False)
    general = serializers.BooleanField(required=False)
    email_digest = serializers.BooleanField(required=False)
    quiet_hours_enabled = serializers.BooleanField(required=False)
    quiet_from = serializers.TimeField(required=False, input_formats=["%H:%M", "%H:%M:%S"])
    quiet_to = serializers.TimeField(required=False, input_formats=["%H:%M", "%H:%M:%S"])
    allow_urgent_during_quiet = serializers.BooleanField(required=False)
    show_previews = serializers.ChoiceField(choices=["always", "unlocked", "never"], required=False)
    transcribe_voice_notes = serializers.BooleanField(required=False)
    timezone = serializers.CharField(max_length=64, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Send at least one setting to change.")
        user = self.context["request"].user
        if getattr(user, "user_type", "staff") != "staff":
            extra = [k for k in attrs if k in PORTAL_HIDDEN_KINDS]
            if extra:
                raise serializers.ValidationError({k: ["This setting is not available for your account."]
                                                   for k in extra})
        return attrs


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
class DeviceRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    label = serializers.CharField()
    kind = serializers.CharField(help_text="app | web")
    platform = serializers.CharField(help_text="android / ios / web")
    door = serializers.CharField(allow_blank=True, help_text="internal or portal")
    app_version = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    last_seen_at = serializers.DateTimeField(allow_null=True)
    current = serializers.BooleanField()


class PushTokenSerializer(serializers.Serializer):
    push_token = serializers.CharField(max_length=4096, allow_blank=True, allow_null=True,
                                       help_text="Empty or null clears it (push switched off).")
    device_id = serializers.CharField(max_length=100, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Deletion request
# ---------------------------------------------------------------------------
class DeletionRequestCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class DeletionRequestRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField(help_text="pending | approved | declined | completed | withdrawn")
    status_label = serializers.CharField()
    reason = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    handled_at = serializers.DateTimeField(allow_null=True)
    note = serializers.CharField(allow_blank=True)
    can_withdraw = serializers.BooleanField()


class DeletionStatusSerializer(serializers.Serializer):
    request = DeletionRequestRowSerializer(allow_null=True, help_text="The latest request (any status), or null.")
    open = serializers.BooleanField(help_text="True while a request is pending or approved.")
    removed_items = serializers.ListField(child=serializers.CharField())
    kept_items = serializers.ListField(child=serializers.CharField())


# ---------------------------------------------------------------------------
# Staff ID
# ---------------------------------------------------------------------------
class StaffCardSerializer(serializers.Serializer):
    full_name = serializers.CharField()
    initials = serializers.CharField()
    designation = serializers.CharField(allow_blank=True)
    department = serializers.CharField(allow_blank=True)
    staff_number = serializers.CharField(allow_blank=True)
    photo_url = serializers.CharField(allow_null=True)


class StaffIdSerializer(serializers.Serializer):
    token = serializers.CharField()
    verify_url = serializers.URLField()
    expires_in = serializers.IntegerField(help_text="Seconds until the app should fetch a new code.")
    valid_for = serializers.IntegerField(help_text="Seconds a scanned code stays valid on the verify page.")
    qr_svg = serializers.CharField(help_text="Inline <svg> of the QR code for verify_url.")
    card = StaffCardSerializer()
