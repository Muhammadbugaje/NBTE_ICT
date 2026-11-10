"""Forms for the assets module."""
from django import forms

from core.uploads import validate_attachment_upload

from accounts.models import User
from staff.models import Department

from .access import can_view_all_assets
from .models import (
    Asset,
    AssetAttachment,
    AssetCategory,
    AssetIssue,
    CONDITION_CHOICES,
    STATE_CHOICES,
)


class AssetForm(forms.ModelForm):
    """Create / edit an asset.

    The `spec` field is filled dynamically from the category's `spec_fields`
    schema — the view handles rendering, this form just accepts the dict.
    """

    class Meta:
        model = Asset
        fields = (
            "asset_tag", "serial_number", "category",
            "brand", "model", "color", "description",
            "owner_department", "current_location",
            "state", "acquired_at", "cost", "warranty_until",
            "notes",
        )
        widgets = {
            "asset_tag": forms.TextInput(attrs={
                "class": "input",
                "placeholder": "Leave blank to auto-generate",
            }),
            "serial_number": forms.TextInput(attrs={"class": "input"}),
            "category": forms.Select(attrs={"class": "input"}),
            "brand": forms.TextInput(attrs={"class": "input"}),
            "model": forms.TextInput(attrs={"class": "input"}),
            "color": forms.TextInput(attrs={"class": "input"}),
            "description": forms.Textarea(attrs={"class": "input", "rows": 3}),
            "owner_department": forms.Select(attrs={"class": "input"}),
            "current_location": forms.TextInput(attrs={"class": "input"}),
            "state": forms.Select(attrs={"class": "input"}),
            "acquired_at": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "cost": forms.NumberInput(attrs={"class": "input", "step": "0.01"}),
            "warranty_until": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "notes": forms.Textarea(attrs={"class": "input", "rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Scoping: only holders of "view all assets" (and super admin) can pick
        # owner_department freely. Not `access_all_departments`: every director
        # carries that, which let directors file assets under other departments.
        if user is not None and not can_view_all_assets(user):
            self.fields["owner_department"].queryset = (
                Department.objects.filter(pk=user.department_id)
                if user.department_id else Department.objects.none()
            )
            if not self.instance.pk:
                self.fields["owner_department"].initial = user.department

        # The asset_tag is immutable after creation.
        if self.instance.pk:
            self.fields["asset_tag"].disabled = True
            self.fields["asset_tag"].help_text = "Cannot be changed after creation."

    def clean_asset_tag(self):
        tag = (self.cleaned_data.get("asset_tag") or "").strip()
        # Empty is allowed on creation — the service generates one.
        if not tag:
            return ""
        if self.instance.pk and tag != self.instance.asset_tag:
            raise forms.ValidationError("The asset tag cannot be changed.")
        return tag.upper()


class AssignAssetForm(forms.Form):
    """Assign an asset to a user."""

    expected_return_at = forms.DateField(
        required=False,
        label="Expected return date",
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="— Select a person —",
        label="Person",
        widget=forms.Select(attrs={"class": "input"}),
    )
    department = forms.ModelChoiceField(
        queryset=None,
        required=False,
        empty_label="— Select a department —",
        label="Department",
        widget=forms.Select(attrs={"class": "input"}),
    )
    condition_on_assign = forms.ChoiceField(
        choices=CONDITION_CHOICES,
        initial="good",
        widget=forms.Select(attrs={"class": "input"}),
    )
    location_on_assign = forms.CharField(
        required=False, max_length=200,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "e.g. Block B, Room 12",
        }),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2,
            "placeholder": "Optional note",
        }),
    )

    def __init__(self, *args, asset=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.selectors import staff_users
        qs = staff_users()  # Phase 0 (T-SEC-2)
        # Scope assignee list to the asset's owning department when possible.
        if asset is not None and asset.owner_department_id:
            scoped = qs.filter(department_id=asset.owner_department_id)
            if scoped.exists():
                qs = scoped
        # Only holders of "view all assets" can assign across departments.
        if user is not None and not can_view_all_assets(user):
            if user.department_id:
                qs = qs.filter(department_id=user.department_id)
            else:
                qs = qs.none()
        self.fields["assigned_to"].queryset = qs.order_by(
            "first_name", "last_name", "email"
        )
        # K1: department mode — own department unless the user sees all assets.
        from staff.models import Department
        depts = Department.objects.filter(is_active=True).order_by("name")
        if user is not None and not can_view_all_assets(user):
            depts = depts.filter(pk=user.department_id) if user.department_id else depts.none()
        self.fields["department"].queryset = depts
        self.asset = asset
        if asset is not None and asset.assignment_mode == "department":
            self.fields["department"].required = True
            if asset.owner_department_id and depts.filter(pk=asset.owner_department_id).exists():
                self.fields["department"].initial = asset.owner_department_id
        else:
            self.fields["assigned_to"].required = True


class AssignmentModeForm(forms.Form):
    """K1/K2: how the asset is held."""
    assignment_mode = forms.ChoiceField(
        choices=[("individual", "Individual"), ("department", "Department"), ("shared", "Shared")],
        widget=forms.Select(attrs={"class": "input"}),
        label="How it is held",
    )


class ReturnAssetForm(forms.Form):
    condition_on_return = forms.ChoiceField(
        choices=CONDITION_CHOICES,
        initial="good",
        widget=forms.Select(attrs={"class": "input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2,
            "placeholder": "Optional condition notes",
        }),
    )
    assignment = forms.IntegerField(required=False, widget=forms.HiddenInput,
                                    help_text="Which holder's share to return (shared assets).")


class SetStateForm(forms.Form):
    state = forms.ChoiceField(
        choices=STATE_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
    reason = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Optional note",
        }),
    )


class AssetIssueForm(forms.ModelForm):
    """Report an issue on an asset."""

    class Meta:
        model = AssetIssue
        fields = ("summary", "description")
        widgets = {
            "summary": forms.TextInput(attrs={
                "class": "input",
                "placeholder": "Short summary of the problem",
            }),
            "description": forms.Textarea(attrs={
                "class": "input", "rows": 4,
                "placeholder": "What's wrong? Any details that will help.",
            }),
        }

    def clean_summary(self):
        s = (self.cleaned_data.get("summary") or "").strip()
        if len(s) < 5:
            raise forms.ValidationError("Give the issue a short but descriptive summary.")
        return s


class ResolveIssueForm(forms.Form):
    resolution_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "How was the issue resolved?",
        }),
    )
    restore_state = forms.ChoiceField(
        choices=STATE_CHOICES,
        initial="in_service",
        widget=forms.Select(attrs={"class": "input"}),
    )


class AssetAttachmentForm(forms.Form):
    file = forms.FileField(
        validators=[validate_attachment_upload],
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    kind = forms.ChoiceField(
        choices=AssetAttachment.KIND_CHOICES,
        initial="photo",
        widget=forms.Select(attrs={"class": "input"}),
    )
    description = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Optional description",
        }),
    )