"""Phase 9: the notification-settings form (both front doors)."""
from django import forms

from .models import NotificationPreference
from .preferences import TIMEZONE_CHOICES, fields_for_door

COMMON_FIELDS = (
    "email_digest", "quiet_hours_enabled", "quiet_from", "quiet_to", "allow_urgent_during_quiet",
    "show_previews", "transcribe_voice_notes", "timezone",
)


class NotificationPreferenceForm(forms.ModelForm):
    """Only the kind switches shown on this door are fields, so saving on the
    portal never switches off an internal-only kind (unchecked boxes are
    absent from a POST)."""

    class Meta:
        model = NotificationPreference
        fields = COMMON_FIELDS
        widgets = {
            "quiet_from": forms.TimeInput(attrs={"type": "time", "class": "input"}, format="%H:%M"),
            "quiet_to": forms.TimeInput(attrs={"type": "time", "class": "input"}, format="%H:%M"),
            "show_previews": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, door="internal", **kwargs):
        super().__init__(*args, **kwargs)
        self.kinds = fields_for_door(door)
        for name, label, help_text, _doors in self.kinds:
            self.fields[name] = forms.BooleanField(required=False, label=label, help_text=help_text,
                                                   initial=getattr(self.instance, name, True))
            if not self.is_bound:
                self.initial[name] = getattr(self.instance, name, True)
        current = getattr(self.instance, "timezone", "") or "Africa/Lagos"
        zones = list(TIMEZONE_CHOICES)
        if current not in zones:
            zones.insert(0, current)
        self.fields["timezone"] = forms.ChoiceField(
            choices=[(z, z.replace("_", " ")) for z in zones], label="Time zone",
            widget=forms.Select(attrs={"class": "input"}))
        self.fields["timezone"].initial = current

    def kind_fields(self):
        return [self[name] for name, *_rest in self.kinds]

    def save(self, commit=True):
        pref = super().save(commit=False)
        for name, *_rest in self.kinds:
            setattr(pref, name, bool(self.cleaned_data.get(name)))
        if commit:
            pref.save()
        return pref
