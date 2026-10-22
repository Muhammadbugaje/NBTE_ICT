"""Request validation and response shapes for assets/, staff/ and
workspace/ (staff door)."""
from rest_framework import serializers

from .serializers import RefSerializer
from .serializers_work import WorkAttachmentSerializer, LeaveOverviewSerializer, UserRefSerializer


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
class AssetRefSerializer(serializers.Serializer):
    tag = serializers.CharField()
    name = serializers.CharField()
    category = serializers.CharField()
    state = serializers.CharField()
    state_display = serializers.CharField()


class MyAssetSerializer(serializers.Serializer):
    asset = AssetRefSerializer()
    assigned_at = serializers.DateTimeField()
    assigned_by = UserRefSerializer(allow_null=True)
    expected_return_at = serializers.DateField(allow_null=True)
    returned_at = serializers.DateTimeField(allow_null=True)
    is_overdue = serializers.BooleanField()
    condition_on_assign = serializers.CharField(allow_blank=True)
    condition_on_return = serializers.CharField(allow_blank=True)


class MyAssetsQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["current", "returned"], required=False, default="current")
    q = serializers.CharField(required=False, allow_blank=True)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class AssetIssueItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    summary = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    reported_by = UserRefSerializer(allow_null=True)
    reported_at = serializers.DateTimeField()
    resolved_at = serializers.DateTimeField(allow_null=True)
    resolution_notes = serializers.CharField(allow_blank=True)


class AssetDetailSerializer(AssetRefSerializer):
    serial_number = serializers.CharField(allow_blank=True)
    brand = serializers.CharField(allow_blank=True)
    model = serializers.CharField(allow_blank=True)
    color = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    location = serializers.CharField(allow_blank=True)
    owner_department = RefSerializer(allow_null=True)
    custodian = UserRefSerializer(allow_null=True)
    assigned_since = serializers.DateTimeField(allow_null=True)
    expected_return_at = serializers.DateField(allow_null=True)
    acquired_at = serializers.DateField(allow_null=True)
    warranty_until = serializers.DateField(allow_null=True)
    is_mine = serializers.BooleanField(help_text="I am the custodian / current holder.")
    issues = AssetIssueItemSerializer(many=True)
    attachments = WorkAttachmentSerializer(many=True)
    can_edit = serializers.BooleanField(help_text="assets.change_asset on this asset (website edit rights).")
    can_report_issue = serializers.BooleanField()
    can_attach_photo = serializers.BooleanField(help_text="May send a photo with an issue report.")


class AssetIssueRequestSerializer(serializers.Serializer):
    """JSON, or multipart/form-data with ``photo``."""
    summary = serializers.CharField(max_length=255, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    photo = serializers.FileField(required=False,
                                  help_text="Only for users with edit rights (can_attach_photo); others raise "
                                            "a ticket with asset_tag + photo, as the website's button does.")


# ---------------------------------------------------------------------------
# Staff directory
# ---------------------------------------------------------------------------
class DirectoryQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True)
    department = serializers.IntegerField(required=False)
    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)


class DirectoryStaffCardSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Staff record id.")
    user_id = serializers.IntegerField(allow_null=True, help_text="Account id (for chat), if any.")
    full_name = serializers.CharField()
    initials = serializers.CharField()
    photo_url = serializers.CharField(allow_null=True)
    designation = serializers.CharField(allow_null=True)
    department = RefSerializer(allow_null=True)
    office = serializers.CharField(allow_null=True)
    state = serializers.CharField(allow_null=True)
    email = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)


class StaffProfileSerializer(DirectoryStaffCardSerializer):
    level = serializers.ChoiceField(choices=["admin", "manager", "self", "colleague"])
    subsection = serializers.CharField(allow_blank=True)
    office_location = serializers.CharField(allow_blank=True)
    category = serializers.CharField(allow_null=True)
    grade_level = serializers.CharField(allow_blank=True)
    reports_to = serializers.CharField(allow_null=True)
    date_joined_service = serializers.DateField(allow_null=True)
    contact_preference = serializers.CharField(allow_blank=True)
    duties = serializers.CharField(allow_blank=True)
    can_message = serializers.BooleanField()


class DepartmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField(allow_null=True)
    name = serializers.CharField()


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------
class WidgetItemSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="Object id (asset tag for assets).")
    kind = serializers.ChoiceField(choices=["ticket", "task", "request", "asset"])
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    status = serializers.CharField(allow_blank=True)
    route = serializers.CharField()


class WidgetSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=["tickets", "tasks", "requests", "assets"])
    title = serializers.CharField()
    count = serializers.IntegerField()
    count_label = serializers.CharField()
    items = WidgetItemSerializer(many=True)


class PendingApprovalSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    subject = serializers.CharField()
    step = serializers.CharField()
    requester = UserRefSerializer()
    route = serializers.CharField()


class WorkspaceProfileSerializer(serializers.Serializer):
    staff_id = serializers.IntegerField()
    employee_no = serializers.CharField()
    designation = serializers.CharField(allow_null=True)
    department = RefSerializer(allow_null=True)
    state = serializers.CharField(allow_null=True)


class WorkspaceSerializer(serializers.Serializer):
    profile = WorkspaceProfileSerializer(allow_null=True)
    widgets = WidgetSerializer(many=True)
    leave = LeaveOverviewSerializer(allow_null=True, help_text="Same as GET leave/; null without a staff record.")
    pending_approvals = PendingApprovalSerializer(many=True)
