from django import forms

from .models import HistoryMilestone


class HistoryMilestoneForm(forms.ModelForm):
    class Meta:
        model = HistoryMilestone
        fields = ["year", "title", "body", "led_by", "image", "is_published", "is_current", "is_placeholder"]
        widgets = {
            "year": forms.TextInput(attrs={"class": "input", "placeholder": "1977", "maxlength": 12}),
            "title": forms.TextInput(attrs={"class": "input"}),
            "body": forms.Textarea(attrs={"class": "input", "rows": 3}),
            "led_by": forms.TextInput(attrs={"class": "input", "placeholder": "Who led this reform"}),
        }
        labels = {"is_placeholder": "Still to be confirmed by NBTE", "is_current": "Highlight as today",
                  "is_published": "Published"}

    def clean_image(self):
        img = self.cleaned_data.get("image")
        name = getattr(img, "name", "") or ""
        if img and hasattr(img, "content_type") and name.lower().endswith(".svg"):
            raise forms.ValidationError("Upload a PNG, JPEG or WebP image.")
        return img
