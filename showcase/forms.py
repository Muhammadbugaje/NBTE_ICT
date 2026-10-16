"""Form for showcase images.

The image lives in restricted storage, whose ``url()`` raises by design.
Django's ``ClearableFileInput`` calls ``.url`` on the value while rendering
the change form, which crashed the admin. ``ShowcaseImageInput`` (in
``showcase/widgets.py``) avoids that call.
"""
from django import forms

from .models import ShowcaseImage
from .widgets import ShowcaseImageInput


class ShowcaseImageForm(forms.ModelForm):
    placements = forms.MultipleChoiceField(
        choices=ShowcaseImage.PLACEMENT_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="Where this image appears. Pick one or more.",
    )

    class Meta:
        model = ShowcaseImage
        fields = [
            "image",
            "title",
            "caption",
            "alt_text",
            "placements",
            "order",
            "is_active",
            "start_at",
            "end_at",
        ]
        widgets = {
            "image": ShowcaseImageInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial["placements"] = self.instance.placement_list

    def clean_placements(self):
        chosen = self.cleaned_data["placements"]
        return ",".join(c for c in ShowcaseImage.PLACEMENT_CODES if c in chosen)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_at")
        end = cleaned.get("end_at")
        if start and end and end <= start:
            self.add_error("end_at", "End must be after the start.")
        if not cleaned.get("placements"):
            self.add_error("placements", "Choose at least one placement.")
        return cleaned