"""Forms for the assets module."""
from django import forms

from accounts.models import User
from staff.models import Department

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

        # Scoping: only cross-department users can pick owner_department freely.
        if user is not None and not user.is_superuser \
                and not user.has_perm("accounts.access_all_departments"):
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
        empty_label="— Select a user —",
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
        qs = User.objects.filter(is_active=True)
        # Scope assignee list to the asset's owning department when possible.
        if asset is not None and asset.owner_department_id:
            scoped = qs.filter(department_id=asset.owner_department_id)
            if scoped.exists():
                qs = scoped
        # Non-privileged users can only assign within their own department.
        if user is not None and not user.is_superuser \
                and not user.has_perm("accounts.access_all_departments"):
            if user.department_id:
                qs = qs.filter(department_id=user.department_id)
            else:
                qs = qs.none()
        self.fields["assigned_to"].queryset = qs.order_by(
            "first_name", "last_name", "email"
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