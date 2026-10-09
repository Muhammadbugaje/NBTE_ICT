import json

from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel

GLOBAL_SCOPE = "global"


class ConfigEntry(TimeStampedModel):
    """One admin-editable business-rule number/limit/list (doc 30 §5.3, NR-06).
    NEVER for secrets or deployment identity - those are env vars only.

    `clean()` runs on every `save()` (see the override below), so a programmatic
    `ConfigEntry.objects.create(key="brevo.api_key", ...)` is rejected the same
    way the admin form would reject it - the `FORBIDDEN_FRAGMENTS` check is not
    bypassable by sidestepping the admin."""

    AUDITED = True
    TYPES = [
        ("int", "Integer"),
        ("float", "Decimal"),
        ("bool", "Yes/No"),
        ("str", "Text"),
        ("json", "JSON (list/object)"),
    ]

    key = models.CharField(max_length=150)
    scope = models.CharField(
        max_length=100,
        default=GLOBAL_SCOPE,
        help_text="'global', or e.g. 'department:ict' for overrides.",
    )
    value = models.TextField(blank=True)
    value_type = models.CharField(max_length=5, choices=TYPES, default="str")
    description = models.CharField(max_length=255, blank=True)
    group = models.CharField(max_length=100, blank=True,
                             help_text="Section heading in the settings UI.")

    class Meta:
        unique_together = ("key", "scope")
        ordering = ["group", "key", "scope"]
        verbose_name_plural = "config entries"

    # Substrings that must never appear in a key - they belong in env vars.
    FORBIDDEN_FRAGMENTS = (
        "secret", "password", "api_key", "apikey", "token", "site_url", "logo_url",
        "credential", "private_key",
    )

    def __str__(self):
        return f"{self.key} [{self.scope}]"

    def parsed(self):
        t, v = self.value_type, self.value
        if t == "int":
            return int(v)
        if t == "float":
            return float(v)
        if t == "bool":
            return v.strip().lower() in ("1", "true", "yes", "on")
        if t == "json":
            return json.loads(v)
        return v

    def clean(self):
        lowered = self.key.lower()
        if any(f in lowered for f in self.FORBIDDEN_FRAGMENTS):
            raise ValidationError(
                "Secrets and site identity belong in environment variables, not the config registry."
            )
        try:
            self.parsed()
        except (ValueError, json.JSONDecodeError):
            raise ValidationError(
                {"value": f"Value is not a valid {self.get_value_type_display()}."}
            )

    def save(self, *args, **kwargs):
        # Django never calls clean() automatically; do it here so programmatic
        # creates (seed commands, shell scripts, future migrations) are validated
        # the same way admin form saves are. This is the whole protection against
        # a secret being written into the config registry.
        self.clean()
        super().save(*args, **kwargs)