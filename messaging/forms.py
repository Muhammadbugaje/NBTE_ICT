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

    `target_user`, `roles`, `recipients` and `attachments` are wired by
    the view based on the chosen visibility. `allowed_kinds` restricts
    which visibility options the user may pick.
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
    target_user = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        widget=forms.HiddenInput(),          # was: forms.Select(attrs={"class": "input"})
        label="Send to",
    )

    class Meta:
        model = Memo
        fields = [
            "subject", "body", "priority", "visibility",
            "target_user", "department", "requires_acknowledgment", "expires_at",
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
            "visibility": forms.Select(
                attrs={"class": "input", "data-memo-visibility": ""}
            ),
            "expires_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, user=None, allowed_kinds=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Roles picker
        self.fields["roles"].queryset = Group.objects.all().order_by("name")

        # Users picker — for both single target and the multi-recipient list.
        User = get_user_model()
        qs = User.objects.filter(is_active=True)
        if user is not None:
            qs = qs.exclude(pk=user.pk)
        qs = qs.order_by("first_name", "last_name", "email")

        self.fields["target_user"].queryset = qs
        self.fields["recipients"].queryset = qs

        # Departments
        self.fields["department"].queryset = (
            Department.objects.filter(is_active=True)
            .order_by("sort_order", "name")
        )
        self.fields["department"].required = False

        # Datetime format
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["expires_at"].required = False

        # Broadcast gating — filter the visibility choices.
        if allowed_kinds is not None:
            allowed = set(allowed_kinds)
            current = self.fields["visibility"].choices
            self.fields["visibility"].choices = [
                (v, l) for (v, l) in current if v in allowed
            ]

    def clean(self):
        cleaned = super().clean()
        visibility = cleaned.get("visibility")

        if visibility == Memo.SPECIFIC_USER and not cleaned.get("target_user"):
            self.add_error("target_user", "Pick a recipient.")

        if visibility == Memo.DEPARTMENT and not cleaned.get("department"):
            self.add_error("department", "Pick a department.")

        if visibility == Memo.ROLE and not cleaned.get("roles"):
            self.add_error("roles", "Pick at least one role.")

        if visibility == Memo.INDIVIDUALS and not cleaned.get("recipients"):
            self.add_error("recipients", "Pick at least one person.")

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