import datetime
import decimal
import json

from django.core.exceptions import ValidationError
from django.db import models

from core.models import TimeStampedModel

GLOBAL_SCOPE = "global"

# --- QA extension (Master Plan §14) ------------------------------------------
# A permission label that no role, group or user is ever given. A row whose
# `editable_by` is this sentinel is "Fixed by policy": the settings page shows
# it without an edit control and `save()` refuses any change to it.
LOCKED_PERMISSION = "config.locked_by_policy"

# Keys whose value is fixed in code (v1.1, Amendment 1). Changing one needs a
# code release, by design. key -> (value, value_type)
LOCKED_KEYS = {
    "qa.payment.provider_confirmation_is_evidence_only": ("true", "bool"),
}

# Exact keys allowed despite containing a forbidden fragment. They are policy
# numbers about credential *links*, not secrets (§14 "Credentials" block).
FORBIDDEN_FRAGMENT_EXEMPT_KEYS = frozenset({
    "qa.credentials.resend_cooldown_minutes",
    "qa.credentials.link_valid_hours",
})

# Extra validators per value type, registered by other apps at startup
# (qa_core registers the formula evaluator) so `config` never imports them.
_VALUE_VALIDATORS = {}


def register_value_validator(value_type, func):
    """`func(entry)` raises ValidationError if `entry`'s value is unacceptable."""
    _VALUE_VALIDATORS[value_type] = func


class ConfigEntry(TimeStampedModel):
    """One admin-editable business-rule number/limit/list (doc 30 §5.3, NR-06).
    NEVER for secrets or deployment identity - those are env vars only.

    `clean()` runs on every `save()` (see the override below), so a programmatic
    `ConfigEntry.objects.create(key="brevo.api_key", ...)` is rejected the same
    way the admin form would reject it - the `FORBIDDEN_FRAGMENTS` check is not
    bypassable by sidestepping the admin.

    QA (Master Plan §14) adds three value types (`decimal`, `date`, `formula`)
    and two columns: `dual_control` (saving through the QA settings surface
    creates a PendingAction a second officer must confirm) and `editable_by`
    (the permission needed to edit the row; LOCKED_PERMISSION = fixed)."""

    AUDITED = True
    TYPES = [
        ("int", "Integer"),
        ("float", "Decimal"),
        ("bool", "Yes/No"),
        ("str", "Text"),
        ("json", "JSON (list/object)"),
        # QA, Master Plan §14
        ("decimal", "Exact decimal"),
        ("date", "Date (YYYY-MM-DD)"),
        ("formula", "Formula"),
    ]

    key = models.CharField(max_length=150)
    scope = models.CharField(
        max_length=100,
        default=GLOBAL_SCOPE,
        help_text="'global', or e.g. 'department:ict' for overrides.",
    )
    value = models.TextField(blank=True)
    value_type = models.CharField(max_length=10, choices=TYPES, default="str")
    description = models.CharField(max_length=255, blank=True)
    group = models.CharField(max_length=100, blank=True,
                             help_text="Section heading in the settings UI.")
    dual_control = models.BooleanField(
        default=False,
        help_text="Changes need a second officer's confirmation (QA settings).",
    )
    editable_by = models.CharField(
        max_length=120, blank=True,
        help_text="Permission label needed to edit this row (blank = config.change_configentry).",
    )

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

    @property
    def is_locked(self):
        return self.editable_by == LOCKED_PERMISSION or self.key in LOCKED_KEYS

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
        if t == "decimal":
            try:
                value = decimal.Decimal(v.strip())
            except decimal.InvalidOperation:
                raise ValueError("not a decimal")
            if not value.is_finite():
                raise ValueError("not a finite decimal")
            return value
        if t == "date":
            return datetime.date.fromisoformat(v.strip())
        if t == "formula":
            data = json.loads(v)
            if not isinstance(data, dict) or not isinstance(data.get("expr"), str):
                raise ValueError('formula must be {"expr": "...", "vars": [...], "returns": "..."}')
            data.setdefault("vars", [])
            data.setdefault("returns", "decimal")
            if not isinstance(data["vars"], list) or not all(isinstance(x, str) for x in data["vars"]):
                raise ValueError("formula vars must be a list of names")
            return data
        return v

    def clean(self):
        lowered = self.key.lower()
        if (any(f in lowered for f in self.FORBIDDEN_FRAGMENTS)
                and self.key not in FORBIDDEN_FRAGMENT_EXEMPT_KEYS):
            raise ValidationError(
                "Secrets and site identity belong in environment variables, not the config registry."
            )
        try:
            self.parsed()
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValidationError(
                {"value": f"Value is not a valid {self.get_value_type_display()}."}
            )
        validator = _VALUE_VALIDATORS.get(self.value_type)
        if validator is not None:
            validator(self)
        self._check_lock()

    # -- locked keys (v1.1) ----------------------------------------------------
    def _check_lock(self):
        """A locked key can be created (by seed_startup) with its fixed value
        and never changed afterwards — not by the settings page, the admin or
        a direct `.save()`."""
        locked = LOCKED_KEYS.get(self.key)
        if locked is not None:
            value, value_type = locked
            if (self.value.strip().lower() != value or self.value_type != value_type
                    or self.editable_by != LOCKED_PERMISSION or self.dual_control):
                raise ValidationError(
                    f"{self.key} is fixed by policy and cannot be changed."
                )
        if self.pk:
            old = type(self)._base_manager.filter(pk=self.pk).first()
            if old is not None and old.is_locked and (
                old.key != self.key or old.scope != self.scope or old.value != self.value
                or old.value_type != self.value_type or old.editable_by != self.editable_by
                or old.dual_control != self.dual_control
            ):
                raise ValidationError(f"{old.key} is fixed by policy and cannot be changed.")

    def save(self, *args, **kwargs):
        # Django never calls clean() automatically; do it here so programmatic
        # creates (seed commands, shell scripts, future migrations) are validated
        # the same way admin form saves are. This is the whole protection against
        # a secret being written into the config registry.
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_locked:
            raise ValidationError(f"{self.key} is fixed by policy and cannot be deleted.")
        return super().delete(*args, **kwargs)
