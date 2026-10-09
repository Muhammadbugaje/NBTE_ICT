"""File-storage discipline (doc 34 §2.1).

* Every FileField/ImageField uses `uuid_upload_to` -> `<subdir>/<uuid4><ext>`.
  The user-supplied name is NEVER part of the on-disk path; keep it in a DB
  column via `capture_original_filename`.
* `MEDIA_SUBDIR` on the model IS the entire path under the media root (not
  just a hint). It may contain slashes for finer categorization, e.g.
  `"assets/photos"` or `"workflow/attachments"`. If a model doesn't declare
  one, files land under `<app_label>/uploads/` so nothing is ever loose in
  the media root.
* All file I/O goes through Django's storage API (default_storage / the
  `restricted` storage) - never raw open()/os.path in views.
* Restricted files use `RestrictedMediaStorage`, rooted outside MEDIA_ROOT,
  so no nginx-servable /media/ URL exists for them.
"""
import os
import re
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

_SAFE_EXT = re.compile(r"^\.[a-z0-9]{1,10}$")


class PublicMediaStorage(FileSystemStorage):
    """Stock FileSystemStorage on MEDIA_ROOT/MEDIA_URL (resolved from settings
    at use time). nginx serves /media/ directly; never proxy these through
    Python."""


class RestrictedMediaStorage(FileSystemStorage):
    """Rooted at PROTECTED_MEDIA_ROOT, OUTSIDE MEDIA_ROOT, with no public URL:
    files are reachable only via core.views.protected_download
    (X-Accel-Redirect)."""

    @property
    def base_location(self):
        return str(settings.PROTECTED_MEDIA_ROOT)

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None

    def url(self, name):
        raise ValueError(
            "Restricted files have no public URL; use the protected download view."
        )


def restricted_storage():
    """Callable for FileField(storage=...) so the storage resolves lazily and
    migrations don't embed an absolute path."""
    from django.core.files.storage import storages

    return storages["restricted"]


def _subdir(instance):
    """Return the on-disk folder for this model's uploads.

    Rules:
      - `MEDIA_SUBDIR` on the model wins, used verbatim (slashes allowed).
      - Otherwise fall back to `<app_label>/uploads`.
      - Leading/trailing slashes are stripped so paths are always relative.
    """
    declared = (getattr(instance, "MEDIA_SUBDIR", None) or "").strip("/")
    if declared:
        return declared
    return f"{instance._meta.app_label}/uploads"


def uuid_upload_to(instance, filename):
    """Every FileField/ImageField routes through here.

    Produces `<MEDIA_SUBDIR>/<uuid4hex><ext>` relative to the media root of
    the storage the field uses. The original filename is deliberately NOT
    part of the path — capture it via `capture_original_filename` before
    save().
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if not _SAFE_EXT.match(ext):
        ext = ""
    return f"{_subdir(instance)}/{uuid.uuid4().hex}{ext}"


def capture_original_filename(instance, field_name, target_attr):
    """Call from save() *before* super().save(): if a new upload is pending on
    `field_name`, store its human-readable name on `target_attr`.

    Never derive a user-facing name from the on-disk path — the on-disk name
    is a UUID with no relation to what the user uploaded.
    """
    field_file = getattr(instance, field_name, None)
    if field_file and not getattr(field_file, "_committed", True):
        name = os.path.basename(getattr(field_file.file, "name", "") or "")
        setattr(instance, target_attr, name[:255])
    elif not field_file:
        setattr(instance, target_attr, "")