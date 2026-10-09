"""Forms for the core app."""
from django import forms

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
        fields = ("name", "email", "subject", "message")
        widgets = {
            "message": forms.Textarea(attrs={"rows": 5}),
        }

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