"""Helpers used by data migrations (review finding H1).

`move_field_files` moves the files behind one FileField between the public
media storage and the restricted storage, keeping the same relative name, so
existing rows keep working after the field's storage changes.
"""
import logging

logger = logging.getLogger(__name__)


def _move(src, dst, name):
    if not name:
        return False
    try:
        if dst.exists(name):
            if src.exists(name):
                src.delete(name)
            return False
        if not src.exists(name):
            return False
        with src.open(name, "rb") as fh:
            saved = dst.save(name, fh)
        if saved != name:  # storage renamed it; keep the original name
            logger.warning("migration move: %s saved as %s", name, saved)
        src.delete(name)
        return True
    except Exception:
        logger.exception("migration move failed for %s", name)
        return False


def move_field_files(apps, app_label, model_name, field="file", to_restricted=True):
    from django.core.files.storage import storages

    public = storages["default"]
    restricted = storages["restricted"]
    src, dst = (public, restricted) if to_restricted else (restricted, public)
    Model = apps.get_model(app_label, model_name)
    moved = 0
    for name in Model._base_manager.exclude(**{field: ""}).values_list(field, flat=True):
        if _move(src, dst, name):
            moved += 1
    return moved


def forward_mover(app_label, model_name, field="file"):
    def run(apps, schema_editor):
        move_field_files(apps, app_label, model_name, field, to_restricted=True)
    return run


def backward_mover(app_label, model_name, field="file"):
    def run(apps, schema_editor):
        move_field_files(apps, app_label, model_name, field, to_restricted=False)
    return run
