"""Move existing institution-linked files into per-institution folders.

Used by ``manage.py organize_institution_media`` (see MEDIA_STORAGE.md).
For every row of every model whose file field uses ``qa_upload_to`` and that
has an owner folder (``qa_core.files.media_folder_for``), the file is moved
from wherever it is now to ``<owner folder>/<same file name>`` inside the
SAME root (public or restricted) and the database field is updated with a
queryset ``update()`` (so no save signals run and the cleanup hooks never
delete the "old" file). Files already in place are skipped, an existing
target is never overwritten (a ``-1``, ``-2`` … suffix is added) and a
missing source is reported, not guessed at. Safe to re-run.
"""
from __future__ import annotations

import csv
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from django.db import models, transaction

from .files import media_folder_for, qa_upload_to

logger = logging.getLogger(__name__)

REPORT_FIELDS = ["model", "pk", "field", "root", "old_path", "new_path", "action", "note"]


@dataclass
class OrganizeResult:
    rows: list = field(default_factory=list)

    def count(self, action):
        return sum(1 for r in self.rows if r["action"] == action)

    def summary(self):
        out = {}
        for r in self.rows:
            out[r["action"]] = out.get(r["action"], 0) + 1
        return out


def institution_file_fields():
    """[(model, field)] for every file field routed through qa_upload_to."""
    from django.apps import apps

    out = []
    for model in apps.get_models():
        if model._meta.proxy:
            continue
        for f in model._meta.concrete_fields:
            if isinstance(f, models.FileField) and getattr(f.upload_to, "__name__", "") == qa_upload_to.__name__ \
                    and getattr(f.upload_to, "__module__", "") == qa_upload_to.__module__:
                out.append((model, f))
    return out


def _root_kind(storage):
    from core.media_scanner import _kind_for_storage

    return _kind_for_storage(storage)


def _free_name(root: Path, name: str) -> str:
    """`name`, or `name` with -1, -2 … before the extension if taken."""
    if not (root / name).exists():
        return name
    p = PurePosixPath(name)
    n = 1
    while True:
        candidate = str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
        if not (root / candidate).exists():
            return candidate
        n += 1


def _variant_sizes():
    try:
        from core.media_scanner import _sizes
        return _sizes()
    except Exception:
        return [300, 64]


def organize(apply=False, stdout=None):
    """Plan (and with ``apply=True`` perform) the moves. Returns OrganizeResult."""
    from core.images import variant_name

    result = OrganizeResult()
    for model, fld in institution_file_fields():
        label = model._meta.label
        storage = fld.storage
        root = Path(storage.location)
        kind = _root_kind(storage)
        qs = models.QuerySet(model=model).exclude(**{fld.name: ""}).exclude(**{f"{fld.name}__isnull": True})
        for obj in qs.order_by("pk").iterator(chunk_size=500):
            old = getattr(obj, fld.attname)
            old = old.name if hasattr(old, "name") else str(old or "")
            if not old:
                continue
            row = {"model": label, "pk": obj.pk, "field": fld.name, "root": kind,
                   "old_path": old, "new_path": "", "action": "", "note": ""}
            result.rows.append(row)
            try:
                folder = media_folder_for(obj)
            except Exception as exc:  # pragma: no cover - defensive
                row.update(action="error", note=f"could not work out the folder: {exc}")
                continue
            if not folder:
                row.update(action="skipped", note="no institution or owner for this row")
                continue
            if PurePosixPath(old).parent.as_posix() == folder:
                row.update(action="in_place", new_path=old)
                continue
            src = root / old
            if not src.is_file():
                row.update(action="missing", note="file not found on disk; database left unchanged")
                continue
            target = f"{folder}/{PurePosixPath(old).name}"
            free = _free_name(root, target)
            if free != target:
                row["note"] = "a different file already had that name; suffix added"
            row["new_path"] = free
            if len(free) > (fld.max_length or 100):
                row.update(action="error", note=f"new path is longer than {fld.max_length} characters")
                continue
            if not apply:
                row["action"] = "would_move"
                continue
            dst = root / free
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
            except OSError as exc:
                row.update(action="error", note=f"move failed: {exc}")
                continue
            try:
                with transaction.atomic():
                    models.QuerySet(model=model).filter(pk=obj.pk).update(**{fld.attname: free})
            except Exception as exc:
                shutil.move(str(dst), str(src))   # put it back; DB still points at old
                row.update(action="error", note=f"database update failed, file put back: {exc}")
                continue
            row["action"] = "moved"
            for size in _variant_sizes():
                v_old, v_new = variant_name(old, size), variant_name(free, size)
                if (root / v_old).is_file() and not (root / v_new).exists():
                    try:
                        shutil.move(str(root / v_old), str(root / v_new))
                    except OSError:
                        logger.warning("organize: could not move variant %s", v_old)
            _prune_empty_dirs(root, src.parent)
    return result


def _prune_empty_dirs(root: Path, start: Path):
    """Remove now-empty folders between `start` and the root (never the root)."""
    try:
        root = root.resolve()
        current = start.resolve()
        while current != root and root in current.parents:
            if any(current.iterdir()):
                break
            current.rmdir()
            current = current.parent
    except OSError:
        pass


def write_report(result, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REPORT_FIELDS)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(row)
    return path
