from django import forms
from django.utils import timezone

from accounts.models import Role
from core.uploads import validate_attachment_upload
from staff.models import Department

from .models import Announcement

MAX_POLL_OPTIONS = 6
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

_DT_WIDGET = {"type": "datetime-local", "class": "input"}


def _dt_input():
    return forms.DateTimeInput(attrs=_DT_WIDGET, format="%Y-%m-%dT%H:%M")


class AnnouncementForm(forms.ModelForm):
    attachment = forms.FileField(
        required=False, validators=[validate_attachment_upload],
        widget=forms.ClearableFileInput(attrs={"class": "input ann-file"}),
        help_text="Optional. PDF, Word, Excel or image, up to 10 MB.",
    )
    poll_question = forms.CharField(
        required=False, max_length=255, label="Poll question",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. Which date suits you for the town hall?"}),
    )
    poll_allow_multiple = forms.BooleanField(required=False, label="Allow more than one choice")
    poll_closes_at = forms.DateTimeField(
        required=False, label="Poll closes", widget=_dt_input(),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"],
    )

    class Meta:
        model = Announcement
        fields = ["title", "body", "kind", "requires_ack", "audience", "department", "roles",
                  "pinned", "expires_at"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input", "maxlength": 200,
                                            "placeholder": "A short, clear headline"}),
            "body": forms.Textarea(attrs={"class": "input", "rows": 9,
                                          "placeholder": "Write the announcement. Line breaks are kept."}),
            "kind": forms.RadioSelect,
            "audience": forms.RadioSelect,
            "department": forms.Select(attrs={"class": "input"}),
            "roles": forms.CheckboxSelectMultiple,
            "expires_at": _dt_input(),
        }
        labels = {"requires_ack": "Ask staff to acknowledge", "pinned": "Pin to the top",
                  "expires_at": "Expires (optional)"}

    def __init__(self, *args, poll_locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.poll_locked = poll_locked
        self.fields["department"].queryset = Department.objects.filter(is_active=True).order_by("name")
        self.fields["department"].empty_label = "Choose a department…"
        # Only roles held by internal staff (institution / resource-person
        # roles can never receive an announcement).
        from accounts.models import User

        external_roles = User.objects.exclude(user_type="staff").filter(role__isnull=False).values("role_id")
        self.fields["roles"].queryset = Role.objects.exclude(pk__in=external_roles).order_by("-rank", "name")
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"]
        for i in range(1, MAX_POLL_OPTIONS + 1):
            self.fields[f"poll_option_{i}"] = forms.CharField(
                required=False, max_length=200, label=f"Option {i}",
                widget=forms.TextInput(attrs={"class": "input", "placeholder": f"Option {i}"}),
            )
        poll = self.instance.poll_or_none() if self.instance.pk else None
        if poll and not self.is_bound:
            self.initial.update({
                "poll_question": poll.question,
                "poll_allow_multiple": poll.allow_multiple,
                "poll_closes_at": poll.closes_at,
            })
            for i, opt in enumerate(poll.options.all()[:MAX_POLL_OPTIONS], start=1):
                self.initial[f"poll_option_{i}"] = opt.text
        if poll_locked:
            for i in range(1, MAX_POLL_OPTIONS + 1):
                self.fields[f"poll_option_{i}"].disabled = True

    def option_fields(self):
        return [self[f"poll_option_{i}"] for i in range(1, MAX_POLL_OPTIONS + 1)]

    def poll_options(self):
        return [self.cleaned_data.get(f"poll_option_{i}") or "" for i in range(1, MAX_POLL_OPTIONS + 1)]

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if f and getattr(f, "size", 0) > MAX_ATTACHMENT_BYTES:
            raise forms.ValidationError("The file is larger than 10 MB.")
        return f

    def clean_expires_at(self):
        value = self.cleaned_data.get("expires_at")
        if value and value <= timezone.now():
            raise forms.ValidationError("Choose a time in the future.")
        return value

    def clean(self):
        data = super().clean()
        audience = data.get("audience")
        if audience == Announcement.DEPARTMENT and not data.get("department"):
            self.add_error("department", "Choose the department this is for.")
        if audience == Announcement.ROLES and not data.get("roles"):
            self.add_error("roles", "Choose at least one role.")
        if audience != Announcement.DEPARTMENT:
            data["department"] = None
        if audience != Announcement.ROLES:
            data["roles"] = Role.objects.none()

        if not self.poll_locked:
            question = (data.get("poll_question") or "").strip()
            options = [o.strip() for o in self.poll_options() if o.strip()]
            if question and len(options) < 2:
                self.add_error("poll_option_2", "A poll needs at least two options.")
            if options and not question:
                self.add_error("poll_question", "Add the poll question, or clear the options.")
            if len({o.lower() for o in options}) != len(options):
                self.add_error("poll_option_1", "Each option must be different.")
        return data
