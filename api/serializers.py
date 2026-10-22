"""Request validation and response shapes (also drive the OpenAPI schema).

Response serializers are applied to plain dicts built in api/services, so the
schema and the payload cannot drift apart.
"""
from rest_framework import serializers


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ErrorBodySerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField(child=serializers.ListField(child=serializers.CharField()), required=False)
    correct_door = serializers.ChoiceField(choices=["staff", "portal"], required=False)
    retry_after = serializers.IntegerField(required=False)


class ErrorSerializer(serializers.Serializer):
    error = ErrorBodySerializer()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class DeviceSerializer(serializers.Serializer):
    platform = serializers.ChoiceField(choices=["android", "ios", "web", "other"], required=False)
    model = serializers.CharField(max_length=120, required=False, allow_blank=True)
    os_version = serializers.CharField(max_length=40, required=False, allow_blank=True)
    app_version = serializers.CharField(max_length=40, required=False, allow_blank=True)
    device_id = serializers.CharField(max_length=100, required=False, allow_blank=True)
    push_token = serializers.CharField(max_length=4096, required=False, allow_null=True, allow_blank=True)


class TokenRequestSerializer(serializers.Serializer):
    # CharField, not EmailField: staff without an e-mail sign in with their
    # file number on the website too (accounts.backends).
    email = serializers.CharField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})
    device = DeviceSerializer(required=False, allow_null=True)


class RefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False,
                                         help_text="Required unless must_change_password is true.")
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class PasswordForgotSerializer(serializers.Serializer):
    email = serializers.EmailField()


# ---------------------------------------------------------------------------
# Me
# ---------------------------------------------------------------------------
class RefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class InstitutionRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()


class CapabilitiesSerializer(serializers.Serializer):
    qa_workspace = serializers.BooleanField()
    approve_leave = serializers.BooleanField()
    post_announcements = serializers.BooleanField()
    finance_queue = serializers.BooleanField()
    insights = serializers.BooleanField()


class UnreadSerializer(serializers.Serializer):
    chats = serializers.IntegerField()
    alerts = serializers.IntegerField()
    announcements = serializers.IntegerField()
    memos = serializers.IntegerField()


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    initials = serializers.CharField()
    photo_url = serializers.URLField(allow_null=True)
    user_type = serializers.ChoiceField(choices=["staff", "institution", "resource_person"])
    door = serializers.ChoiceField(choices=["staff", "portal"])
    designation = serializers.CharField(allow_null=True)
    department = RefSerializer(allow_null=True)
    institution = InstitutionRefSerializer(allow_null=True)
    must_change_password = serializers.BooleanField()
    capabilities = CapabilitiesSerializer()
    unread = UnreadSerializer()
    profile_completeness = serializers.IntegerField(allow_null=True,
                                                    help_text="Percent; null for portal accounts.")


class TokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = MeSerializer()
    door = serializers.ChoiceField(choices=["staff", "portal"])


class MeUpdateSerializer(serializers.Serializer):
    """PATCH me/: the fields the website's profile page lets a user change
    (staff: accounts.forms.ProfileForm without e-mail; portal accounts:
    qa_core.portal_forms.AccountProfileForm). Multipart for ``photo``."""

    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    contact_preference = serializers.ChoiceField(choices=["email", "phone", "inapp"], required=False)
    emergency_contact_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    emergency_contact_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    subsection = serializers.CharField(max_length=100, required=False, allow_blank=True)
    office_location = serializers.CharField(max_length=150, required=False, allow_blank=True)
    duties = serializers.CharField(required=False, allow_blank=True)
    photo = serializers.ImageField(required=False)

    STAFF_ONLY = ("phone", "contact_preference", "emergency_contact_name", "emergency_contact_phone",
                  "subsection", "office_location", "duties", "photo")

    def validate(self, attrs):
        user = self.context["request"].user
        if not attrs:
            raise serializers.ValidationError("Send at least one field to change.")
        if user.user_type != "staff":
            extra = [k for k in attrs if k in self.STAFF_ONLY]
            if extra:
                raise serializers.ValidationError({k: ["This field cannot be changed for portal accounts."]
                                                   for k in extra})
        return attrs


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------
class AttendanceSerializer(serializers.Serializer):
    checked_in = serializers.BooleanField()
    checked_in_at = serializers.DateTimeField(allow_null=True)
    location_name = serializers.CharField(allow_null=True)
    can_check_in = serializers.BooleanField()


class StaffCountsSerializer(serializers.Serializer):
    tasks_open = serializers.IntegerField()
    tasks_due_today = serializers.IntegerField()
    approvals_waiting = serializers.IntegerField()
    unread_chats = serializers.IntegerField()


class PinnedChatSerializer(serializers.Serializer):
    thread_id = serializers.IntegerField()
    title = serializers.CharField()
    initials = serializers.CharField()
    unread = serializers.IntegerField()


class UpNextSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["task", "meeting", "approval", "memo"])
    id = serializers.IntegerField()
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    due_label = serializers.CharField(allow_blank=True)
    route = serializers.CharField()


class AnnouncementRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    kind = serializers.CharField()
    requires_ack = serializers.BooleanField()
    acknowledged = serializers.BooleanField()


class StaffHomeSerializer(serializers.Serializer):
    greeting = serializers.CharField()
    date_label = serializers.CharField()
    attendance = AttendanceSerializer()
    counts = StaffCountsSerializer()
    pinned_chats = PinnedChatSerializer(many=True)
    up_next = UpNextSerializer(many=True)
    announcement = AnnouncementRefSerializer(allow_null=True)


class ApplicationSummarySerializer(serializers.Serializer):
    stage = serializers.IntegerField()
    stages = serializers.IntegerField()
    headline = serializers.CharField()
    route = serializers.CharField()


class BillSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    amount = serializers.CharField(help_text="Naira, two decimals.")
    amount_display = serializers.CharField()
    due_date = serializers.DateField(allow_null=True)
    status = serializers.CharField()


class ExpiringProgrammeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    level = serializers.CharField(allow_null=True)
    expires_on = serializers.DateField()
    days_left = serializers.IntegerField()


class MonthCountSerializer(serializers.Serializer):
    month = serializers.CharField(help_text="YYYY-MM")
    count = serializers.IntegerField()


class ActionItemSerializer(serializers.Serializer):
    tone = serializers.CharField(help_text="danger | warn | info")
    title = serializers.CharField()
    body = serializers.CharField(allow_blank=True)
    cta = serializers.CharField(allow_blank=True)
    route = serializers.CharField(help_text="App route, e.g. /payments/12, /qa-requests/4/ssq/9")


class ActivitySerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    text = serializers.CharField(allow_blank=True)


class BoardCountSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField()


class PortalSectionSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    route = serializers.CharField()


class InstitutionHomeSerializer(serializers.Serializer):
    institution = InstitutionRefSerializer(allow_null=True)
    application = ApplicationSummarySerializer(allow_null=True)
    bills_due = BillSerializer(many=True)
    programmes_expiring = ExpiringProgrammeSerializer(many=True)
    payments_by_month = MonthCountSerializer(many=True)
    action_items = ActionItemSerializer(many=True)
    recent_activity = ActivitySerializer(many=True)
    programme_board = BoardCountSerializer(many=True, help_text="Programmes per status.")
    programme_count = serializers.IntegerField()
    sections = PortalSectionSerializer(many=True, help_text="Portal sections this account has, in order.")


class NextVisitSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    institution = serializers.CharField()
    date = serializers.DateField()
    role = serializers.CharField()
    programmes = serializers.IntegerField()
    route = serializers.CharField()


class TodoSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["ssq_review", "report"])
    id = serializers.IntegerField()
    title = serializers.CharField()
    due_label = serializers.CharField(allow_blank=True)


class HistorySerializer(serializers.Serializer):
    institution = serializers.CharField()
    date = serializers.DateField()
    allowance_status = serializers.ChoiceField(choices=["paid", "pending"])


class ResourcePersonHomeSerializer(serializers.Serializer):
    next_visit = NextVisitSerializer(allow_null=True)
    todo = TodoSerializer(many=True)
    history = HistorySerializer(many=True)
    sections = PortalSectionSerializer(many=True)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    min_app_version = serializers.CharField()
    server_time = serializers.DateTimeField()
