"""Centralised file lifecycle for the project.

Registry is stored on Django's `apps` singleton so it survives any
module reimport. Registered models get four signals wired:

    pre_save     Snapshot the DB's current file names + `deleted_at`.
    post_save    Delete old file on replace. (Soft delete keeps files so a
                 restore works - review M14.)
    pre_delete   Snapshot on-disk names before the row disappears.
    post_delete  Delete every registered file (hard delete).

Every deletion also removes the generated image variants for that file
(`<name>_w<size>.jpg`, produced by `core.images.make_variants`). The
variant naming convention lives in `core.images`; this module never
duplicates it.

All deletions go through the field's own storage backend — never
`os.remove()` — so public vs restricted storage rules are preserved.
"""
from __future__ import annotations

import json
import logging

from django.apps import apps as django_apps
from django.db.models.signals import (
    post_delete,
    post_save,
    pre_delete,
    pre_save,
)

logger = logging.getLogger(__name__)

_REGISTRY_ATTR = "_nbte_file_cleanup_registry"
_DEFAULT_SIZES = (300, 64)


# ===========================================================================
# Registry — stored on Django's apps singleton
# ===========================================================================
def _registry():
    reg = getattr(django_apps, _REGISTRY_ATTR, None)
    if reg is None:
        reg = {}
        setattr(django_apps, _REGISTRY_ATTR, reg)
    return reg


def register_file_cleanup(model, fields):
    """Wire cleanup signals for one model's file fields. Idempotent."""
    if not fields:
        return

    reg = _registry()
    existing = reg.get(model, set())
    new_fields = set(fields) - existing

    if not new_fields:
        logger.info(  # review L7: routine
            "file cleanup: %s already registered (fields=%s)",
            model._meta.label, sorted(existing),
        )
        return

    reg[model] = existing | new_fields
    _install_signals(model, sorted(reg[model]))
    logger.info(  # review L7: routine
        "file cleanup: registered %s -> %s",
        model._meta.label, sorted(new_fields),
    )


def registered_models():
    return list(_registry().keys())


def registered_fields(model):
    return set(_registry().get(model, ()))


# ===========================================================================
# Signal installation
# ===========================================================================
def _install_signals(model, fields):
    label = model._meta.label
    fields = tuple(fields)

    def _on_pre_save(sender, instance, **kw):
        _pre_save(model, fields, instance)

    def _on_post_save(sender, instance, created, update_fields=None, **kw):
        _post_save(model, fields, instance, update_fields=update_fields)

    def _on_pre_delete(sender, instance, **kw):
        _pre_delete(model, fields, instance)

    def _on_post_delete(sender, instance, **kw):
        _post_delete(model, fields, instance)

    pre_save.connect(
        _on_pre_save, sender=model, weak=False,
        dispatch_uid=f"file_cleanup_pre_save.{label}",
    )
    post_save.connect(
        _on_post_save, sender=model, weak=False,
        dispatch_uid=f"file_cleanup_post_save.{label}",
    )
    pre_delete.connect(
        _on_pre_delete, sender=model, weak=False,
        dispatch_uid=f"file_cleanup_pre_delete.{label}",
    )
    post_delete.connect(
        _on_post_delete, sender=model, weak=False,
        dispatch_uid=f"file_cleanup_post_delete.{label}",
    )


# ===========================================================================
# Helpers
# ===========================================================================
def _has_field(model, name):
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


def _manager(model):
    return getattr(model, "_base_manager", model._default_manager)


def _variant_sizes():
    """Read the configured thumbnail sizes. Never raises.

    Accepts a Python list, a JSON string, or a single int.
    Falls back to [300, 64] — the default in `core.images`.
    """
    try:
        import config
        raw = config.get("media.thumbnail_sizes", list(_DEFAULT_SIZES))
    except Exception:
        return list(_DEFAULT_SIZES)

    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw

    if isinstance(parsed, int):
        parsed = [parsed]

    if not isinstance(parsed, (list, tuple)):
        return list(_DEFAULT_SIZES)

    sizes = []
    for item in parsed:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in sizes:
            sizes.append(value)

    return sizes or list(_DEFAULT_SIZES)


def _delete_safely(storage, name, reason=""):
    """Delete a single file via its storage backend. Never raises."""
    if not name:
        return
    try:
        if storage.exists(name):
            storage.delete(name)
            logger.info(  # review L7: routine
                "file cleanup: DELETED %s (%s)", name, reason or "no reason",
            )
        else:
            logger.info(  # review L7: routine
                "file cleanup: %s already gone (%s)", name, reason or "no reason",
            )
    except Exception:
        logger.exception(
            "file cleanup: FAILED to delete %s from %s",
            name, type(storage).__name__,
        )


def _delete_with_variants(storage, name, reason=""):
    """Delete `name` and every generated variant of it.

    Variants follow the naming convention in `core.images`:
        <dir>/<uuid>_w<size>.jpg
    The name pattern is never reimplemented here — we ask
    `core.images.variant_name()` for it so the two stay in sync.
    """
    if not name:
        return

    # 1. Delete the original file.
    _delete_safely(storage, name, reason=reason)

    # 2. Delete every generated variant.
    try:
        from core.images import variant_name
    except Exception:
        logger.exception(
            "file cleanup: could not import variant_name from core.images; "
            "variants for %s will remain on disk", name,
        )
        return

    for size in _variant_sizes():
        try:
            variant = variant_name(name, size)
        except Exception:
            logger.exception(
                "file cleanup: variant_name failed for %s size=%s",
                name, size,
            )
            continue
        _delete_safely(
            storage, variant,
            reason=f"{reason} [variant w{size}]" if reason else f"variant w{size}",
        )


def _file_name_of(instance, field):
    """Return the current on-disk name of `field` (or '')."""
    obj = getattr(instance, field, None)
    if not obj:
        return ""
    try:
        return obj.name or ""
    except Exception:
        return ""


# ===========================================================================
# pre_save
# ===========================================================================
def _pre_save(model, fields, instance):
    if instance.pk is None:
        return

    columns = list(fields)
    has_deleted = _has_field(model, "deleted_at")
    if has_deleted:
        columns.append("deleted_at")

    try:
        previous = (
            _manager(model)
            .filter(pk=instance.pk)
            .values(*columns)
            .first()
        )
    except Exception:
        logger.exception(
            "file cleanup: pre_save lookup failed for %s pk=%s",
            model._meta.label, instance.pk,
        )
        return

    if not previous:
        return

    for field in fields:
        setattr(instance, f"_fc_old_{field}", previous.get(field) or None)

    if has_deleted:
        setattr(instance, "_fc_old_deleted_at", previous.get("deleted_at"))


# ===========================================================================
# post_save
# ===========================================================================
def _post_save(model, fields, instance, update_fields=None):
    # ---- 1. File replacement ------------------------------------------
    for field in fields:
        old_name = getattr(instance, f"_fc_old_{field}", None)
        try:
            delattr(instance, f"_fc_old_{field}")
        except AttributeError:
            pass

        if not old_name:
            continue

        if update_fields is not None and field not in update_fields:
            continue

        current_name = _file_name_of(instance, field)
        if old_name == current_name:
            continue

        try:
            model_field = model._meta.get_field(field)
        except Exception:
            continue

        _delete_with_variants(
            model_field.storage, old_name,
            reason=f"{model._meta.label}.{field} replaced",
        )

    # ---- 2. Soft delete ------------------------------------------------
    # Review M14: a soft-deleted row can be restored, so its files are KEPT.
    # They are removed only on hard delete (post_delete below), which also
    # covers QuerySet.hard_delete().
    try:
        delattr(instance, "_fc_old_deleted_at")
    except AttributeError:
        pass


# ===========================================================================
# pre_delete — snapshot before the row disappears
# ===========================================================================
def _pre_delete(model, fields, instance):
    names = []
    if instance.pk is None:
        setattr(instance, "_fc_delete_names", names)
        return

    try:
        previous = (
            _manager(model)
            .filter(pk=instance.pk)
            .values(*fields)
            .first()
        )
    except Exception:
        logger.exception(
            "file cleanup: pre_delete lookup failed for %s pk=%s",
            model._meta.label, instance.pk,
        )
        previous = None

    if previous:
        for field in fields:
            name = previous.get(field) or ""
            if name:
                names.append((field, name))

    setattr(instance, "_fc_delete_names", names)


# ===========================================================================
# post_delete
# ===========================================================================
def _post_delete(model, fields, instance):
    names = getattr(instance, "_fc_delete_names", None)

    if names is None:
        names = []
        for field in fields:
            name = _file_name_of(instance, field)
            if name:
                names.append((field, name))
    else:
        try:
            delattr(instance, "_fc_delete_names")
        except AttributeError:
            pass

    for field, name in names:
        try:
            model_field = model._meta.get_field(field)
        except Exception:
            continue
        _delete_with_variants(
            model_field.storage, name,
            reason=f"{model._meta.label} deleted",
        )