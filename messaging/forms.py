"""Messaging forms — memo compose, memo attachment. Threads don't need forms."""
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from staff.models import Department

from .models import Memo, MemoAttachment


# ===========================================================================
# Multiple-file helpers (Django doesn't ship one)
# ===========================================================================
class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """A FileField that accepts a list of files and returns a list."""
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data]
        return [single_clean(data, initial)] if data else []


# ===========================================================================
# MEMO — compose / edit form
# ===========================================================================
class MemoForm(forms.ModelForm):
    """Compose or edit a memo.

    `roles`, `recipients` and `attachments` are handled outside ModelForm
    save() so the view can wire them conditionally on `visibility`.
    """
    attachments = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True}),
        help_text="Optional. Up to 5 MB total.",
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Group.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Roles",
    )
    recipients = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="People",
    )

    class Meta:
        model = Memo
        fields = [
            "subject", "body", "priority", "visibility",
            "department", "requires_acknowledgment", "expires_at",
        ]
        widgets = {
            "subject": forms.TextInput(attrs={
                "placeholder": "Memo subject",
                "autocomplete": "off",
            }),
            "body": forms.Textarea(attrs={
                "rows": 12,
                "placeholder": "Write the memo body…",
            }),
            "expires_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Roles picker
        self.fields["roles"].queryset = Group.objects.all().order_by("name")

        # Recipients picker
        User = get_user_model()
        qs = User.objects.filter(is_active=True)
        if user is not None and not user.is_superuser:
            qs = qs.exclude(pk=user.pk)
        self.fields["recipients"].queryset = qs.order_by(
            "first_name", "last_name", "email",
        )

        # Departments
        self.fields["department"].queryset = (
            Department.objects.filter(is_active=True)
            .order_by("sort_order", "name")
        )
        self.fields["department"].required = False

        # Datetime format
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["expires_at"].required = False

    def clean(self):
        cleaned = super().clean()
        visibility = cleaned.get("visibility")

        if visibility == Memo.DEPARTMENT and not cleaned.get("department"):
            self.add_error("department", "Pick a department.")

        if visibility == Memo.ROLE and not cleaned.get("roles"):
            self.add_error("roles", "Pick at least one role.")

        if visibility == Memo.INDIVIDUALS and not cleaned.get("recipients"):
            self.add_error("recipients", "Pick at least one person.")

        # 5 MB cap
        files = cleaned.get("attachments") or []
        total = sum(getattr(f, "size", 0) for f in files)
        if total > 5 * 1024 * 1024:
            self.add_error("attachments", "Attachments must total no more than 5 MB.")

        return cleaned


class MemoAttachmentForm(forms.ModelForm):
    class Meta:
        model = MemoAttachment
        fields = ["file", "description"]
        widgets = {
            "description": forms.TextInput(attrs={
                "placeholder": "Short description (optional)",
                "autocomplete": "off",
            }),
        }