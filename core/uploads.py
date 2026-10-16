"""Upload allow-list for user attachments (review finding H1).

Workflow, task, ticket and asset attachments are restricted to the file
types in the config key `uploads.allowed_extensions`. The same check runs in
three places so no path can skip it:

  * form fields (`validators=[validate_attachment_upload]`)
  * the model's save() (`enforce_allowed_extension`)
  * the protected download view, which always sends
    `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff`
"""
import os

from django.core.exceptions import ValidationError

DEFAULT_ALLOWED_EXTENSIONS = ["pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png"]


def allowed_extensions():
    """Lower-case extensions without dots, from config (falls back to default)."""
    try:
        import config
        raw = config.get("uploads.allowed_extensions", DEFAULT_ALLOWED_EXTENSIONS)
    except Exception:
        raw = DEFAULT_ALLOWED_EXTENSIONS
    if isinstance(raw, str):
        raw = [part for part in raw.replace(";", ",").split(",")]
    out = []
    for item in raw or []:
        ext = str(item).strip().lower().lstrip(".")
        if ext and ext not in out:
            out.append(ext)
    return out or list(DEFAULT_ALLOWED_EXTENSIONS)


def _extension_of(name):
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def check_filename(name):
    """Raise ValidationError if `name` has an extension that is not allowed."""
    ext = _extension_of(name)
    allowed = allowed_extensions()
    if ext not in allowed:
        raise ValidationError(
            "Files of type '.%(ext)s' are not allowed. Allowed types: %(allowed)s.",
            code="file_type_not_allowed",
            params={"ext": ext or "(none)", "allowed": ", ".join(allowed)},
        )


def validate_attachment_upload(upload):
    """Form-field validator."""
    if upload:
        check_filename(getattr(upload, "name", ""))


def enforce_allowed_extension(instance, field_name="file"):
    """Model-layer check: call from save() before super().save()."""
    field_file = getattr(instance, field_name, None)
    if field_file and not getattr(field_file, "_committed", True):
        check_filename(getattr(field_file.file, "name", "") or field_file.name)
