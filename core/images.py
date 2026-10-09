"""Server-side image variants (doc 34 §2.1): Pillow, run by Celery on upload.
Variant files sit next to the original: `<dir>/<uuid>_w<size>.jpg` - same
directory and UUID convention, derivable without a DB column."""
import os
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from PIL import Image, ImageOps

import config

DEFAULT_SIZES = [300, 64]


def variant_name(original_name, size):
    root, _ext = os.path.splitext(original_name)
    return f"{root}_w{size}.jpg"


def variant_url(field_file, size):
    """URL of a generated variant, or None if it doesn't exist (yet)."""
    if not field_file:
        return None
    name = variant_name(field_file.name, size)
    return field_file.storage.url(name) if field_file.storage.exists(name) else None


def make_variants(storage, name, sizes):
    """Create one JPEG per size (longest edge = size). Returns created names."""
    with storage.open(name, "rb") as fh:
        img = Image.open(fh)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):  # flatten transparency onto white
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, "white")
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        created = []
        for size in sizes:
            copy = img.copy()
            copy.thumbnail((size, size), Image.LANCZOS)
            buf = BytesIO()
            copy.save(buf, "JPEG", quality=85, optimize=True)
            out = variant_name(name, size)
            if storage.exists(out):
                storage.delete(out)  # so the name stays deterministic
            storage.save(out, ContentFile(buf.getvalue()))
            created.append(out)
    return created


def schedule_thumbnails(instance, field_name):
    """Call after saving an instance whose image field got a NEW upload."""
    from .tasks import generate_thumbnails

    args = (instance._meta.label_lower, instance.pk, field_name)
    transaction.on_commit(lambda: generate_thumbnails.delay(*args))
