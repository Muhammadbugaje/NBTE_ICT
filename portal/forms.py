"""Portal forms — announcements, events, reminders, documents, awards,
holidays, gallery."""
from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from staff.models import Staff

from .models import (
    Announcement, AnnouncementCategory, AnnouncementLink,
    Award, AwardCategory,
    Document, DocumentCategory, DocumentVersion,
    Event, EventCategory,
    GalleryAlbum, GalleryCategory,
    Holiday,
    Reminder,
    Scope,
)

User = get_user_model()
INPUT = "input"


# ============================================================================
# VISIBILITY HELPERS
# ============================================================================
def _visibility_choices():
    """Visibility choices minus the Roles option (hidden until Groups exist)."""
    return [c for c in Scope.CHOICES if c[0] != Scope.ROLE]


def _apply_staff_scope(form, user):
    """Restrict department dropdown + populate the recipients picker."""
    if user and not user.is_superuser and getattr(user, "department_id", None):
        form.fields["department"].queryset = (
            form.fields["department"].queryset.filter(pk=user.department_id)
        )
        if not form.instance.pk:
            form.fields["department"].initial = user.department_id

    if "recipients" in form.fields:
        if user and not user.is_superuser and getattr(user, "department_id", None):
            form.fields["recipients"].queryset = User.objects.filter(
                is_active=True, department_id=user.department_id,
            ).order_by("first_name", "last_name")
        else:
            form.fields["recipients"].queryset = User.objects.filter(
                is_active=True,
            ).order_by("first_name", "last_name")


# ============================================================================
# MULTIPLE-FILE FIELD (used by gallery upload)
# ============================================================================
class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_single = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_single(d, initial) for d in data]
        return clean_single(data, initial)


# ============================================================================
# ANNOUNCEMENTS
# ============================================================================
class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = [
            "title", "summary", "body", "image",
            "category", "priority", "is_pinned",
            "department", "visibility", "recipients",
            "publish_at", "expires_at", "is_draft",
        ]
        widgets = {
            "title":      forms.TextInput(attrs={"class": INPUT, "autofocus": True,
                                                  "placeholder": "e.g. Scheduled maintenance this Friday"}),
            "summary":    forms.TextInput(attrs={"class": INPUT,
                                                  "placeholder": "One-line summary"}),
            "body":       forms.Textarea(attrs={"class": INPUT, "rows": 10}),
            "image":      forms.ClearableFileInput(attrs={"class": INPUT, "accept": "image/*",
                                                           "data-image-preview": "1"}),
            "category":   forms.Select(attrs={"class": INPUT}),
            "priority":   forms.Select(attrs={"class": INPUT}),
            "department": forms.Select(attrs={"class": INPUT}),
            "visibility": forms.Select(attrs={"class": INPUT}),
            "recipients": forms.SelectMultiple(attrs={
                "class": INPUT + " js-picker-source", "size": 6,
                "data-picker-label": "people",
            }),
            "publish_at": forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
            "expires_at": forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
        }
        help_texts = {
            "summary": "Shown on cards. If blank, we'll take the first 140 chars of the body.",
            "publish_at": "Publish now, or pick a future time to schedule it.",
            "expires_at": "Optional. The announcement auto-archives after this time.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = _visibility_choices()
        self.fields["publish_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["expires_at"].input_formats = ["%Y-%m-%dT%H:%M"]

        if not self.instance.pk:
            self.fields["publish_at"].initial = timezone.now().strftime("%Y-%m-%dT%H:%M")

        if user and not user.is_superuser:
            if not user.has_perm("portal.publish_public_announcement"):
                self.fields["visibility"].choices = [
                    c for c in self.fields["visibility"].choices if c[0] != Scope.PUBLIC
                ]

        _apply_staff_scope(self, user)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("publish_at")
        end = cleaned.get("expires_at")
        if start and end and end <= start:
            self.add_error("expires_at", "Expiry must be after the publish time.")
        return cleaned


AnnouncementLinkFormSet = forms.inlineformset_factory(
    Announcement, AnnouncementLink,
    fields=["kind", "label", "url", "sort_order"],
    extra=2, can_delete=True,
    widgets={
        "kind":  forms.Select(attrs={"class": INPUT}),
        "label": forms.TextInput(attrs={"class": INPUT, "placeholder": "e.g. Join the Zoom call"}),
        "url":   forms.URLInput(attrs={"class": INPUT, "placeholder": "https://…"}),
        "sort_order": forms.NumberInput(attrs={"class": INPUT, "min": 0}),
    },
)


# ============================================================================
# EVENTS
# ============================================================================
class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "title", "summary", "description", "image",
            "category",
            "starts_at", "ends_at", "all_day",
            "location_name", "location_address", "virtual_url",
            "rsvp_required", "reminder_lead_minutes",
            "department", "visibility", "recipients",
            "is_cancelled", "cancellation_reason", "is_draft",
        ]
        widgets = {
            "title":       forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "summary":     forms.TextInput(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": INPUT, "rows": 8}),
            "image":       forms.ClearableFileInput(attrs={"class": INPUT, "accept": "image/*",
                                                            "data-image-preview": "1"}),
            "category":    forms.Select(attrs={"class": INPUT}),
            "starts_at":   forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
            "ends_at":     forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
            "location_name":    forms.TextInput(attrs={"class": INPUT,
                                                        "placeholder": "e.g. Block B — Conference Room"}),
            "location_address": forms.TextInput(attrs={"class": INPUT}),
            "virtual_url":      forms.URLInput(attrs={"class": INPUT,
                                                       "placeholder": "https://zoom.us/j/… or https://meet.google.com/…"}),
            "reminder_lead_minutes": forms.NumberInput(attrs={"class": INPUT, "min": 0, "step": 5}),
            "department":   forms.Select(attrs={"class": INPUT}),
            "visibility":   forms.Select(attrs={"class": INPUT}),
            "recipients":   forms.SelectMultiple(attrs={
                "class": INPUT + " js-picker-source", "size": 6,
                "data-picker-label": "people",
            }),
            "cancellation_reason": forms.TextInput(attrs={"class": INPUT}),
        }
        help_texts = {
            "reminder_lead_minutes": "Minutes before the start to fire a notification.",
            "virtual_url": "Zoom / Meet / Teams link, if virtual or hybrid.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = _visibility_choices()
        for f in ("starts_at", "ends_at"):
            self.fields[f].input_formats = ["%Y-%m-%dT%H:%M"]

        if user and not user.is_superuser:
            if not user.has_perm("portal.publish_public_event"):
                self.fields["visibility"].choices = [
                    c for c in self.fields["visibility"].choices if c[0] != Scope.PUBLIC
                ]

        _apply_staff_scope(self, user)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("starts_at")
        end = cleaned.get("ends_at")
        if start and end and end < start:
            self.add_error("ends_at", "End must be after the start.")
        return cleaned


# ============================================================================
# REMINDERS
# ============================================================================
class ReminderForm(forms.ModelForm):
    class Meta:
        model = Reminder
        fields = [
            "title", "body", "url", "kind",
            "department", "recipients",
            "remind_at", "expires_at",
        ]
        widgets = {
            "title":      forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "body":       forms.Textarea(attrs={"class": INPUT, "rows": 4}),
            "url":        forms.TextInput(attrs={"class": INPUT,
                                                  "placeholder": "/portal/announcements/12/  or  https://…"}),
            "kind":       forms.Select(attrs={"class": INPUT}),
            "department": forms.Select(attrs={"class": INPUT}),
            "recipients": forms.SelectMultiple(attrs={
                "class": INPUT + " js-picker-source", "size": 6,
                "data-picker-label": "people",
            }),
            "remind_at":  forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
            "expires_at": forms.DateTimeInput(
                attrs={"class": INPUT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
        }
        help_texts = {
            "url": "Where this reminder links. Can be a portal path or a full URL.",
            "remind_at": "The notification fires at this moment.",
            "expires_at": "Optional. The reminder stops appearing after this time.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("remind_at", "expires_at"):
            self.fields[f].input_formats = ["%Y-%m-%dT%H:%M"]

        if not self.instance.pk:
            self.fields["remind_at"].initial = timezone.now().strftime("%Y-%m-%dT%H:%M")

        if user and not user.is_superuser:
            self.fields["kind"].choices = [
                c for c in self.fields["kind"].choices
                if c[0] != getattr(Reminder, "Kind", object()).__dict__.get("ORGANISATION", "org")
            ]

        _apply_staff_scope(self, user)

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get("kind") == getattr(Reminder, "Kind", object()).__dict__.get("DEPARTMENT", "dept")
                and not cleaned.get("department")):
            self.add_error("department", "Pick a department for department-wide reminders.")
        return cleaned


# ============================================================================
# DOCUMENTS
# ============================================================================
class DocumentForm(forms.ModelForm):
    """Create/edit Document metadata. File is only asked on create."""

    initial_file = forms.FileField(
        required=False,
        label="Document file",
        help_text="Required for new file documents. Leave blank when editing.",
        widget=forms.ClearableFileInput(attrs={"class": INPUT}),
    )

    class Meta:
        model = Document
        fields = [
            "title", "summary", "description",
            "category",
            "department", "visibility", "recipients",
            "source_type", "external_url",
            "cover_image",
            "effective_date", "review_date", "review_reminder_days",
            "download_tracking_enabled", "is_featured",
            "status",
        ]
        widgets = {
            "title":       forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "summary":     forms.TextInput(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": INPUT, "rows": 6}),
            "category":    forms.Select(attrs={"class": INPUT}),
            "department":  forms.Select(attrs={"class": INPUT}),
            "visibility":  forms.Select(attrs={"class": INPUT}),
            "recipients":  forms.SelectMultiple(attrs={
                "class": INPUT + " js-picker-source", "size": 6,
                "data-picker-label": "people",
            }),
            "source_type": forms.Select(attrs={"class": INPUT}),
            "external_url": forms.URLInput(attrs={"class": INPUT,
                                                   "placeholder": "https://drive.google.com/file/d/…"}),
            "cover_image":  forms.ClearableFileInput(attrs={"class": INPUT, "accept": "image/*"}),
            "effective_date": forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "review_date":    forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "review_reminder_days": forms.NumberInput(attrs={"class": INPUT, "min": 0}),
            "status":       forms.Select(attrs={"class": INPUT}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = _visibility_choices()

        if user and not user.is_superuser:
            if not user.has_perm("portal.publish_public_document"):
                self.fields["visibility"].choices = [
                    c for c in self.fields["visibility"].choices if c[0] != Scope.PUBLIC
                ]
                self.fields["is_featured"].disabled = True

        if self.instance.pk:
            self.fields["initial_file"].disabled = True
            self.fields["source_type"].disabled = True

        _apply_staff_scope(self, user)

    def clean(self):
        cleaned = super().clean()
        st = cleaned.get("source_type") or self.instance.source_type
        url = cleaned.get("external_url")

        if st == Document.SOURCE_EXTERNAL and not url:
            self.add_error("external_url", "Required for external-link documents.")
        if st == Document.SOURCE_FILE and not self.instance.pk and not cleaned.get("initial_file"):
            self.add_error("initial_file", "Please upload a file.")
        if cleaned.get("is_featured") and cleaned.get("visibility") != Scope.PUBLIC:
            self.add_error("is_featured", "Only public documents can be featured.")
        return cleaned


class DocumentVersionForm(forms.ModelForm):
    class Meta:
        model = DocumentVersion
        fields = ["file", "change_note"]
        widgets = {
            "file": forms.ClearableFileInput(attrs={"class": INPUT}),
            "change_note": forms.TextInput(attrs={"class": INPUT,
                                                   "placeholder": "What changed in this version?"}),
        }
        labels = {"file": "New version file"}

    def clean_file(self):
        f = self.cleaned_data.get("file")
        if not f:
            raise forms.ValidationError("Please upload a file.")
        return f


# ============================================================================
# AWARDS
# ============================================================================
class AwardForm(forms.ModelForm):
    class Meta:
        model = Award
        fields = [
            "kind", "title", "description", "image", "certificate",
            "category", "recipient", "department", "awarded_by",
            "awarded_on", "years", "is_recurring",
            "visibility", "recipients", "is_featured",
        ]
        widgets = {
            "kind":         forms.Select(attrs={"class": INPUT}),
            "title":        forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "description":  forms.Textarea(attrs={"class": INPUT, "rows": 5}),
            "category":     forms.Select(attrs={"class": INPUT}),
            "recipient":    forms.Select(attrs={"class": INPUT}),
            "department":   forms.Select(attrs={"class": INPUT}),
            "awarded_by":   forms.Select(attrs={"class": INPUT}),
            "awarded_on":   forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "years":        forms.NumberInput(attrs={"class": INPUT, "min": 1, "max": 80}),
            "visibility":   forms.Select(attrs={"class": INPUT}),
            "recipients":   forms.SelectMultiple(attrs={
                "class": INPUT + " js-picker-source", "size": 6,
                "data-picker-label": "people",
            }),
            "is_recurring": forms.CheckboxInput(),
            "is_featured":  forms.CheckboxInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = _visibility_choices()

        if not self.instance.pk:
            self.fields["awarded_on"].initial = timezone.localdate()

        staff_qs = Staff.objects.filter(is_deleted=False)
        if user and not user.is_superuser and getattr(user, "department_id", None):
            staff_qs = staff_qs.filter(department_id=user.department_id)
        self.fields["recipient"].queryset = staff_qs.order_by("last_name", "first_name")

        _apply_staff_scope(self, user)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_featured") and cleaned.get("visibility") != Scope.PUBLIC:
            self.add_error("is_featured", "Only public-visibility awards can be featured.")
        if (cleaned.get("kind") == Award.KIND_SERVICE_ANNIVERSARY
                and not cleaned.get("years")):
            self.add_error("years", "Required for service anniversaries.")
        return cleaned


# ============================================================================
# HOLIDAYS
# ============================================================================
class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = [
            "name", "description", "kind",
            "date_start", "date_end",
            "department", "is_recurring",
            "visibility", "is_active",
        ]
        widgets = {
            "name":         forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "description":  forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "kind":         forms.Select(attrs={"class": INPUT}),
            "date_start":   forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "date_end":     forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "department":   forms.Select(attrs={"class": INPUT}),
            "visibility":   forms.Select(attrs={"class": INPUT}),
            "is_recurring": forms.CheckboxInput(),
            "is_active":    forms.CheckboxInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = _visibility_choices()
        if not self.instance.pk and not self.initial.get("date_end"):
            self.fields["date_end"].initial = self.initial.get("date_start")
        _apply_staff_scope(self, user)


# ============================================================================
# GALLERY
# ============================================================================
class GalleryAlbumForm(forms.ModelForm):
    class Meta:
        model = GalleryAlbum
        fields = [
            "title", "description", "category",
            "cover_image", "event_date",
            "department", "visibility", "recipients",
            "is_featured", "is_published",
        ]
        widgets = {
            "title":       forms.TextInput(attrs={"class": INPUT, "autofocus": True,
                                                   "placeholder": "e.g. ICT Team Retreat 2026"}),
            "description": forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "category":    forms.Select(attrs={"class": INPUT}),
            "cover_image": forms.ClearableFileInput(attrs={"class": INPUT, "accept": "image/*"}),
            "event_date":  forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "department":  forms.Select(attrs={"class": INPUT}),
            "visibility":  forms.Select(attrs={"class": INPUT}),
            "recipients":  forms.SelectMultiple(attrs={
                "class": INPUT + " js-picker-source", "size": 6,
                "data-picker-label": "people",
            }),
            "is_featured": forms.CheckboxInput(),
            "is_published": forms.CheckboxInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visibility"].choices = _visibility_choices()
        _apply_staff_scope(self, user)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_featured") and cleaned.get("visibility") != Scope.PUBLIC:
            self.add_error("is_featured", "Only public albums can be featured.")
        return cleaned


class GalleryItemUploadForm(forms.Form):
    files = MultipleFileField(
        widget=MultipleFileInput(attrs={
            "multiple": True,
            "accept": "image/*,video/*",
            "class": "gallery-upload-input",
        }),
        help_text="Select one or more images/videos.",
    )
    caption = forms.CharField(
        required=False, max_length=300,
        widget=forms.TextInput(attrs={"class": INPUT,
                                       "placeholder": "Optional caption applied to all files"}),
    )
    credit = forms.CharField(
        required=False, max_length=200,
        widget=forms.TextInput(attrs={"class": INPUT,
                                       "placeholder": "Photographer / source (optional)"}),
    )