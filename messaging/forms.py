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

    # v2.1 Phase E (M4): institutions picked one by one (scope = selected).
    target_institutions = forms.ModelMultipleChoiceField(
        queryset=Memo.target_institutions.field.related_model.unscoped.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Institutions",
    )

    class Meta:
        model = Memo
        fields = [
            "kind", "subject", "body", "priority", "visibility",
            "target_user", "department", "requires_acknowledgment", "expires_at",
            "institution_scope",
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
            "kind": forms.RadioSelect(attrs={"data-memo-kind": ""}),
            "institution_scope": forms.RadioSelect(attrs={"data-memo-inst-scope": ""}),
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
        from accounts.selectors import staff_users

        qs = staff_users()  # Phase 0 (M9): internal memos go to staff only
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
        # "No staff" is what an external memo implies; never offered on its own.
        self.fields["visibility"].choices = [
            (v, l) for (v, l) in self.fields["visibility"].choices if v != Memo.NO_STAFF]

        # v2.1 Phase E (M7): memo types this user may pick.
        from . import memos as memo_service
        from institutions.models import Institution

        self.memo_kinds = memo_service.allowed_memo_kinds(user) if user is not None else [Memo.INTERNAL]
        self.fields["kind"].choices = [(v, l) for v, l in Memo.KIND_CHOICES if v in self.memo_kinds]
        self.fields["kind"].required = False
        self.fields["institution_scope"].choices = [
            (v, l) for v, l in Memo.INSTITUTION_SCOPE_CHOICES if v]
        self.fields["institution_scope"].required = False
        self.fields["target_institutions"].queryset = (
            Institution.unscoped.exclude(status=Institution.DELISTED).order_by("name"))
        if self.instance and self.instance.pk:
            from .memo_audience import target_institution_ids
            self.fields["target_institutions"].initial = target_institution_ids(self.instance)

    def clean(self):
        from .memo_audience import parse_institution_filter, parse_staff_filter

        cleaned = super().clean()
        kind = cleaned.get("kind") or Memo.INTERNAL
        cleaned["kind"] = kind
        if kind == Memo.EXTERNAL:
            # The staff audience does not apply: drop any staff-target errors.
            cleaned["visibility"] = Memo.NO_STAFF
            for f in ("visibility", "target_user", "department", "roles", "recipients"):
                self.errors.pop(f, None)
        cleaned["institution_filter"] = parse_institution_filter(self.data)
        cleaned["staff_filter"] = parse_staff_filter(self.data)
        if kind != Memo.INTERNAL:
            scope = cleaned.get("institution_scope") or ""
            if not scope:
                self.add_error("institution_scope", "Choose which institutions receive this memo.")
            elif scope == Memo.INST_SELECTED and not cleaned.get("target_institutions"):
                self.add_error("target_institutions", "Pick at least one institution.")
        visibility = cleaned.get("visibility")
        if visibility == Memo.STAFF_FILTER and not cleaned["staff_filter"]:
            self.add_error("visibility", "Choose at least one staff filter.")

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