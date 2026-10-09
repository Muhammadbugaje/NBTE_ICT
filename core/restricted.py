"""Restricted-file support (doc 34 §2.1 / §10).

Subclass `RestrictedFileModel` for any upload whose visibility is scoped. The
file lives in PROTECTED_MEDIA_ROOT (no /media/ URL exists for it) and is served
only by `core.views.protected_download`, which calls `can_download()` - by
default the SAME `user.has_perm()` RBAC check used everywhere else. Override
`can_download` for row-level scoping (e.g. own office only); never write a
separate parallel check in a view.
"""
from django.db import models
from django.urls import reverse

from .models import SoftDeleteModel
from .storage import restricted_storage, uuid_upload_to


class RestrictedFileModel(SoftDeleteModel):
    DOWNLOAD_PERMISSION = None  # e.g. "staff.view_staffattachment"

    file = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage)
    original_name = models.CharField(max_length=255, blank=True)

    class Meta:
        abstract = True

    def can_download(self, user):
        perm = self.DOWNLOAD_PERMISSION
        if not perm:
            return False  # fail closed if a subclass forgot to declare one
        return user.is_authenticated and user.has_perm(perm)

    def download_url(self):
        return reverse("core:protected_download", args=[self._meta.label_lower, self.pk])

    def save(self, *args, **kwargs):
        from .storage import capture_original_filename

        capture_original_filename(self, "file", "original_name")
        super().save(*args, **kwargs)
