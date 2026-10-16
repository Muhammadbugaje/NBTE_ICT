"""Custom widget for showcase images.

The image file lives in restricted storage, whose ``url()`` raises by
design (files are only reachable through ``showcase.views.image``).
Django's ``ClearableFileInput`` accesses ``.url`` on the value to decide
whether to render the "Currently: <link>" preview and to fill the
``<a href>``, which crashed the admin change form.

``ShowcaseImageInput`` overrides both the "is there an initial value?"
check and ``format_value`` so the template receives a small wrapper with
a safe ``.url`` property instead of the raw ``FieldFile``.
"""
from django.forms.widgets import ClearableFileInput


class _SafePreview:
    """Wraps a FieldFile so the template can render a preview link."""

    def __init__(self, url, name):
        self.url = url
        self._name = name

    def __str__(self):
        return self._name


class ShowcaseImageInput(ClearableFileInput):
    """``ClearableFileInput`` that never calls ``FieldFile.url``."""

    def is_initial(self, value):
        # Only check that a name is present; never touch .url here.
        return bool(value and getattr(value, "name", None))

    def format_value(self, value):
        if not value or not getattr(value, "name", None):
            return None
        url = ""
        try:
            from .models import ShowcaseImage

            obj = ShowcaseImage.objects.filter(image=value.name).first()
            if obj is not None:
                url = obj.get_image_url()
        except Exception:
            url = ""
        if not url:
            return None
        return _SafePreview(url, value.name)