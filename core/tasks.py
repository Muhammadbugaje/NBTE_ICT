"""Shared Celery tasks for the core app."""
import logging
from pathlib import Path

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task
def generate_thumbnails(label, pk, field_name):
    """Unchanged: fires on image upload to produce resized variants."""
    from django.apps import apps

    from . import images

    model = apps.get_model(label)
    obj = model._base_manager.filter(pk=pk).first()
    field_file = getattr(obj, field_name, None) if obj else None
    if not field_file:
        return "skipped"
    import config
    sizes = [int(s) for s in config.get("media.thumbnail_sizes", images.DEFAULT_SIZES)]
    try:
        created = images.make_variants(field_file.storage, field_file.name, sizes)
    except Exception:
        logger.exception("Thumbnail generation failed for %s#%s.%s", label, pk, field_name)
        return "failed"
    return created


@shared_task
def cleanup_import_tmp(max_age_hours=24):
    """Delete files under PROTECTED_MEDIA_ROOT/import-tmp older than `max_age_hours`.

    Runs hourly via Celery Beat. Cleans up:
      - uploads that were previewed but never committed
      - rejected-rows reports that were generated but never downloaded

    Uses direct filesystem access since the media roots are local (per doc 34).
    Review L5: the folder moved to PROTECTED_MEDIA_ROOT; the old public
    MEDIA_ROOT/import-tmp is still swept so leftovers from before the move
    are removed. Returns a small dict for logging/inspection.
    """
    tmp_dirs = [
        d for d in (
            Path(settings.PROTECTED_MEDIA_ROOT) / "import-tmp",
            Path(settings.MEDIA_ROOT) / "import-tmp",
        ) if d.exists()
    ]
    if not tmp_dirs:
        return {"removed": 0, "failed": 0, "skipped": "no import-tmp directory"}

    import time as _time
    cutoff = _time.time() - (max_age_hours * 3600)
    removed = 0
    failed = 0

    for tmp_dir in tmp_dirs:
        for entry in tmp_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except Exception:
                logger.exception("cleanup_import_tmp: could not remove %s", entry)
                failed += 1

    logger.info("cleanup_import_tmp: removed=%d failed=%d (older than %dh)",
                removed, failed, max_age_hours)
    return {"removed": removed, "failed": failed}

@shared_task
def purge_media_quarantine():
    """Remove quarantine day folders older than files.quarantine_retention_days.

    Only touches MEDIA_ROOT/.quarantine and PROTECTED_MEDIA_ROOT/.quarantine;
    the orphan scanner never deletes anything directly (Phase 8)."""
    from . import media_scanner

    result = media_scanner.purge_quarantine()
    logger.info("purge_media_quarantine: removed %s (%d files)", result.days_removed, result.files_removed)
    return {"days": result.days_removed, "files": result.files_removed, "errors": result.errors}
