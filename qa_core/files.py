"""Restricted QA files (Master Plan §5.2 InstitutionDocument, §10.4 proofs).

Every QA upload (statutory documents, proofs of payment, slips, advices,
statements) lives in ``restricted_storage`` — never under /media/ — and is
served only through a view that calls the row's ``can_download(user)``:

* staff: ``core:protected_download`` on the internal host (the model opts in
  with ``PROTECTED_DOWNLOAD = True``);
* institutions: ``qa_portal:file`` on the portal host (the same check).

``can_download`` is the single row-level decision; views never add a
parallel check. Uploads are checked for extension, size and a matching file
signature, and a SHA-256 is kept (duplicate-proof detection, §10.4).
"""
from __future__ import annotations

import hashlib
import os

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from core.storage import capture_original_filename, restricted_storage, uuid_upload_to

# Leading bytes of the formats QA accepts. Anything else is refused even when
# the extension looks right (a renamed .exe is not a PDF).
_SIGNATURES = {
    "pdf": [b"%PDF-"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "csv": [],   # text: checked separately
    "xlsx": [b"PK\x03\x04"],
    "docx": [b"PK\x03\x04"],
}


def extension_of(name):
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def validate_upload(upload, *, extensions, max_mb, label="file"):
    """Raise ValidationError unless `upload` is an allowed, honest file."""
    if not upload:
        raise ValidationError(f"Choose a {label} to upload.")
    exts = [str(e).lower().lstrip(".") for e in (extensions or [])]
    ext = extension_of(getattr(upload, "name", ""))
    if ext not in exts:
        raise ValidationError(f"Files of type '.{ext or '?'}' are not accepted here. "
                              f"Allowed: {', '.join('.' + e for e in exts)}.")
    size = getattr(upload, "size", 0) or 0
    try:
        limit = float(max_mb)
    except (TypeError, ValueError):
        limit = 5.0
    if size > limit * 1024 * 1024:
        raise ValidationError(f"The {label} is {size / 1048576:.1f} MB; the limit is {limit:g} MB.")
    if size == 0:
        raise ValidationError(f"The {label} is empty.")
    sigs = _SIGNATURES.get(ext)
    if sigs:
        pos = upload.tell() if hasattr(upload, "tell") else 0
        try:
            upload.seek(0)
            head = upload.read(16)
        finally:
            try:
                upload.seek(pos)
            except Exception:
                pass
        if not any(head.startswith(sig) for sig in sigs):
            raise ValidationError(f"The {label} does not look like a real .{ext} file.")
    return ext


def sha256_of(upload):
    digest = hashlib.sha256()
    pos = upload.tell() if hasattr(upload, "tell") else 0
    try:
        upload.seek(0)
        for chunk in iter(lambda: upload.read(65536), b""):
            digest.update(chunk)
    finally:
        try:
            upload.seek(pos)
        except Exception:
            pass
    return digest.hexdigest()


class QAFileMixin(models.Model):
    """Abstract restricted file. Subclasses implement ``can_download``."""

    PROTECTED_DOWNLOAD = True
    MEDIA_SUBDIR = "qa"

    file = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage, max_length=255)
    original_name = models.CharField(max_length=255, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        field_file = getattr(self, "file", None)
        if field_file and not getattr(field_file, "_committed", True):
            capture_original_filename(self, "file", "original_name")
            try:
                self.size_bytes = field_file.size or 0
                self.sha256 = sha256_of(field_file.file)
            except Exception:
                pass
        super().save(*args, **kwargs)

    def can_download(self, user):  # pragma: no cover - always overridden
        return False

    def download_url(self):
        return reverse("core:protected_download", args=[self._meta.label_lower, self.pk])

    def portal_download_url(self):
        """Path of the portal download (portal URLconf, whatever the current one is)."""
        from .hosts import portal_reverse

        return portal_reverse("qa_portal:file", self._meta.label_lower, self.pk)

    @property
    def display_name(self):
        return self.original_name or os.path.basename(self.file.name or "")

    @property
    def size_label(self):
        size = self.size_bytes or 0
        if size >= 1048576:
            return f"{size / 1048576:.1f} MB"
        if size >= 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size} B"
