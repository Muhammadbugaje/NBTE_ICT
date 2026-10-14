"""Forms for the core app."""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from staff.models import Department

from .models import ContactMessage


class ContactForm(forms.ModelForm):
    """Public contact form. Includes a honeypot field that must stay empty —
    a simple, dependency-free way to deflect the most naive bots."""

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
    )

    class Meta:
        model = ContactMessage
        fields = ("name", "email", "subject", "message", "handling_department")
        widgets = {
            "message": forms.Textarea(attrs={"rows": 5}),
            "handling_department": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["handling_department"].queryset = Department.objects.filter(
            is_active=True
        ).order_by("name")
        self.fields["handling_department"].required = False

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Bot detected.")
        return ""

    def clean_message(self):
        msg = (self.cleaned_data.get("message") or "").strip()
        if len(msg) < 10:
            raise forms.ValidationError("Message is too short — please give us a bit more detail.")
        if len(msg) > 5000:
            raise forms.ValidationError("Message is too long — please keep it under 5000 characters.")
        return msg

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if len(name) < 2:
            raise forms.ValidationError("Please provide your name.")
        return name


class ContactAssignmentForm(forms.Form):
    assigned_to = forms.ModelChoiceField(
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label="Unassigned",
        label="Assigned handler",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *, contact_message, **kwargs):
        super().__init__(**kwargs)
        user_model = get_user_model()
        candidates = user_model.objects.filter(
            is_active=True,
        ).select_related("role")
        eligible_ids = [
            user.pk
            for user in candidates
            if user.has_perm("core.can_handle_contact_messages")
            and (
                contact_message.handling_department_id is None
                or user.department_id == contact_message.handling_department_id
                or user.has_perm("accounts.access_all_departments")
            )
        ]
        eligible = Q(pk__in=eligible_ids)
        if contact_message.assigned_to_id:
            eligible |= Q(pk=contact_message.assigned_to_id)
        self.fields["assigned_to"].queryset = user_model.objects.filter(
            eligible
        ).order_by("last_name", "first_name", "email")
        self.fields["assigned_to"].initial = contact_message.assigned_to_id


class ContactNoteForm(forms.Form):
    details = forms.CharField(
        label="Internal note",
        min_length=3,
        max_length=5000,
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 4,
            "placeholder": "Add an update for other authorized staff",
        }),
    )

    def clean_details(self):
        details = self.cleaned_data["details"].strip()
        if len(details) < 3:
            raise forms.ValidationError("Enter an internal note.")
        return details