"""Audit signal mechanism (doc 31 §4 / doc 34 §4). Works identically on any DB
backend because it is plain Django signals, not triggers.

Opt a model in with `AUDITED = True` (SoftDeleteModel does this by default).
Sensitive fields: list them in `AUDIT_EXCLUDE_FIELDS` on the model - they are
recorded as changed ("***") but their values are never written.
"""
import contextvars
import datetime
import decimal
import uuid

from django.db.models.signals import post_delete, post_save, pre_save

_current_actor = contextvars.ContextVar("audit_actor", default=None)
_REDACTED = "***"


def set_current_actor(user):
    _current_actor.set(user if getattr(user, "is_authenticated", False) else None)


def get_current_actor():
    return _current_actor.get()


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    return str(value)


def _field_values(instance):
    """Raw JSON-safe values, used for comparison. Redaction happens on write."""
    ignored = {"created_at", "updated_at", *getattr(instance, "AUDIT_IGNORE_FIELDS", ())}
    return {
        f.name: _jsonable(f.value_from_object(instance))
        for f in instance._meta.concrete_fields
        if f.name not in ignored
    }


def _redact(instance, key, value):
    if key in getattr(instance, "AUDIT_EXCLUDE_FIELDS", ()):
        return _REDACTED
    return value


def log(action, instance, actor=None, changes=None, note=""):
    """Write one entry explicitly (used for state changes and other named events)."""
    from .models import AuditLogEntry

    actor = actor or get_current_actor()
    return AuditLogEntry.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_repr=str(actor) if actor else "system",
        action=action,
        model_label=instance._meta.label,
        object_pk=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=changes or {},
        note=note[:255],
    )


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw:
        return
    instance._audit_old = None
    if instance.pk:
        old = sender._base_manager.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_old = _field_values(old)


def _post_save(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    new = _field_values(instance)
    if created:
        changes = {k: {"new": _redact(instance, k, v)} for k, v in new.items() if v not in (None, "")}
        log(_action("create"), instance, changes=changes)
        return
    old = getattr(instance, "_audit_old", None) or {}
    diff = {
        k: {"old": _redact(instance, k, old.get(k)), "new": _redact(instance, k, v)}
        for k, v in new.items()
        if old.get(k) != v
    }
    if not diff:
        return
    action = _action("update")
    if "is_deleted" in diff:
        action = _action("delete") if new.get("is_deleted") else _action("restore")
    log(action, instance, changes=diff)


def _post_delete(sender, instance, **kwargs):
    log(_action("delete"), instance, note="hard delete")


def _action(name):
    from .models import AuditLogEntry

    return getattr(AuditLogEntry, name.upper())


def register(model):
    uid = f"audit-{model._meta.label}"
    pre_save.connect(_pre_save, sender=model, dispatch_uid=f"{uid}-pre", weak=False)
    post_save.connect(_post_save, sender=model, dispatch_uid=f"{uid}-post", weak=False)
    post_delete.connect(_post_delete, sender=model, dispatch_uid=f"{uid}-del", weak=False)
