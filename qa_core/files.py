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
import re

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


def qa_upload_to(instance, filename):
    """``upload_to`` of every QA file field (see ``institution_upload_to``
    below): per-institution folder when the row has one, else the classic
    ``<MEDIA_SUBDIR>/<uuid><ext>``."""
    return institution_upload_to(instance, filename)


class QAFileMixin(models.Model):
    """Abstract restricted file. Subclasses implement ``can_download``."""

    PROTECTED_DOWNLOAD = True
    MEDIA_SUBDIR = "qa"

    file = models.FileField(upload_to=qa_upload_to, storage=restricted_storage, max_length=255)
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


# ---------------------------------------------------------------------------
# Per-institution media folders (Phase 8)
# ---------------------------------------------------------------------------
# Everything that belongs to an institution is stored under
#
#     institutions/<CODE> - <Institution Name>/<kind>/<uuid><ext>
#
# in whichever root the field uses (public MEDIA_ROOT or the restricted
# PROTECTED_MEDIA_ROOT). A model opts in by declaring ``INSTITUTION_MEDIA_KIND``
# (e.g. ``"payments/slips"``); the institution is found through the model's
# ``INSTITUTION_PATH`` (the same path scoping uses) or ``media_institution()``.
# Resource-person files declare ``media_owner_folder()`` instead and land
# under ``resource-persons/<name>/``. With no institution the file keeps the
# old ``<MEDIA_SUBDIR>/<uuid><ext>`` path, so nothing ever fails to upload.

INSTITUTIONS_ROOT = "institutions"
RESOURCE_PERSONS_ROOT = "resource-persons"
_FOLDER_MAX = 80
_UNSAFE_CHARS = re.compile(r"[^\w\s\-.,&()'+]", re.UNICODE)


def safe_folder_name(text, max_length=_FOLDER_MAX):
    """A filesystem-safe folder name: keeps letters, digits, spaces and a few
    harmless marks; drops slashes, control and odd characters; never starts
    with a dot; trimmed to ``max_length``."""
    text = str(text or "").replace("/", " ").replace("\\", " ")
    text = _UNSAFE_CHARS.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip(".").strip()
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .-")
    return text or "unnamed"


def institution_folder(institution):
    """``"<CODE> - <Name>"`` for an institution (safe for any filesystem)."""
    code = safe_folder_name(getattr(institution, "code", "") or "", 12)
    name = getattr(institution, "name", "") or ""
    label = f"{code} - {name}" if name else code
    return safe_folder_name(label)


def institution_of(instance):
    """Find the institution behind a file row, or None.

    Order: an explicit ``media_institution()`` hook, then the model's
    ``INSTITUTION_PATH`` (``"obligation__institution"`` style), then a direct
    ``institution`` attribute."""
    from institutions.models import Institution

    hook = getattr(instance, "media_institution", None)
    if callable(hook):
        try:
            found = hook()
        except Exception:
            found = None
        if found is not None:
            return found
    path = getattr(type(instance), "INSTITUTION_PATH", None)
    candidates = [path] if path else []
    candidates.append("institution")
    for candidate in candidates:
        obj = instance
        try:
            for part in candidate.split("__"):
                obj = getattr(obj, part, None)
                if obj is None:
                    break
        except Exception:
            obj = None
        if isinstance(obj, Institution):
            return obj
    return None


def _ext_of(filename):
    from core.storage import _SAFE_EXT

    ext = os.path.splitext(filename or "")[1].lower()
    return ext if _SAFE_EXT.match(ext) else ""


def media_folder_for(instance):
    """The folder (no file name) a row's file belongs in, or None when the
    model has no per-owner layout or the owner cannot be found."""
    owner = getattr(instance, "media_owner_folder", None)
    if callable(owner):
        try:
            folder = owner()
        except Exception:
            folder = None
        if folder:
            return folder.strip("/")
    kind = (getattr(type(instance), "INSTITUTION_MEDIA_KIND", "") or "").strip("/")
    if not kind:
        return None
    institution = institution_of(instance)
    if institution is None:
        return None
    return f"{INSTITUTIONS_ROOT}/{institution_folder(institution)}/{kind}"


def institution_upload_to(instance, filename):
    """``upload_to`` for every QA file: per-institution folder when known,
    otherwise the classic ``<MEDIA_SUBDIR>/<uuid><ext>``."""
    import uuid

    from core.storage import uuid_upload_to

    folder = media_folder_for(instance)
    if not folder:
        return uuid_upload_to(instance, filename)
    return f"{folder}/{uuid.uuid4().hex}{_ext_of(filename)}"
