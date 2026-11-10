"""Completed steps lock; reopening is audited (v2.1 X7, P.7; spec G1.d "Locking").

A model opts in with :class:`Lockable` and two fields, ``locked_at`` and
``locked_by`` (WorkAssignment has them; DocumentReview, ReviewStep and the
visit-report sections add them when they arrive). Once ``locked_at`` is set,
``save()`` refuses any change to a business field — services, views, the API
and the admin all go through ``save()`` — unless the change runs inside
:func:`reopen` (or the model's own ``LOCK_ALLOWED_FIELDS``).

    from assignments import locks
    locks.lock(obj, actor)                         # on completion
    locks.reopen_completed_step(obj, actor, reason) # director of the owning department / ES only

Reopening needs ``assignments.reopen_completed_step`` and the director seat of
the record's department (or ES / super admin), and a reason. It writes
``core.audit.log("reopen")`` and an internal timeline event for the institution.
"""
from __future__ import annotations

import contextlib
import contextvars

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

_reopening = contextvars.ContextVar("asg_reopening", default=frozenset())


class StepLocked(ValidationError):
    """A completed step was changed outside an audited reopen."""


def _key(obj):
    return (obj._meta.label_lower, str(obj.pk))


class Lockable:
    """Mixin: refuse changes to a locked record. Fields that may still change
    after locking (bookkeeping) go in ``LOCK_ALLOWED_FIELDS``."""

    LOCK_ALLOWED_FIELDS = ("locked_at", "locked_by", "updated_at", "meta")

    def _locked_in_db(self):
        if self.pk is None:
            return None
        return type(self)._base_manager.filter(pk=self.pk).first()

    def save(self, *args, **kwargs):
        old = self._locked_in_db()
        if old is not None and getattr(old, "locked_at", None) is not None and _key(self) not in _reopening.get():
            update_fields = kwargs.get("update_fields")
            changed = []
            for f in self._meta.concrete_fields:
                if f.primary_key or f.name in self.LOCK_ALLOWED_FIELDS or f.attname in self.LOCK_ALLOWED_FIELDS:
                    continue
                if update_fields is not None and f.name not in update_fields and f.attname not in update_fields:
                    continue
                if f.value_from_object(old) != f.value_from_object(self):
                    changed.append(f.name)
            if changed or (getattr(self, "locked_at", None) is None and (
                    update_fields is None or "locked_at" in update_fields)):
                raise StepLocked(
                    f"This step is completed and locked ({', '.join(changed) or 'unlock'}). "
                    "A director must reopen it, with a reason, before it can change.")
        return super().save(*args, **kwargs)


def is_locked(obj):
    return getattr(obj, "locked_at", None) is not None


def lock(obj, actor=None, *, save=True):
    """Lock a completed step (idempotent)."""
    if is_locked(obj):
        return obj
    obj.locked_at = timezone.now()
    if hasattr(obj, "locked_by"):
        obj.locked_by = actor if getattr(actor, "pk", None) else None
    if save:
        with _allow(obj):
            obj.save(update_fields=["locked_at", "locked_by"] if hasattr(obj, "locked_by") else ["locked_at"])
    return obj


@contextlib.contextmanager
def _allow(obj):
    token = _reopening.set(_reopening.get() | {_key(obj)})
    try:
        yield
    finally:
        _reopening.reset(token)


def _department_id(obj):
    path = getattr(type(obj), "DEPARTMENT_PATH", None)
    if not path:
        return None
    value = obj
    for part in path.split("__"):
        value = getattr(value, part, None)
        if value is None:
            return None
    return getattr(value, "pk", value)


def can_reopen(actor, obj):
    """(allowed, reason). Director of the owning department or ES / super admin,
    holding ``assignments.reopen_completed_step``."""
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        return False, "Sign in first."
    if getattr(actor, "user_type", "staff") != "staff":
        return False, "Only NBTE staff can reopen a step."
    import org
    if actor.is_superuser or org.is_es(actor):
        return True, ""
    if not actor.has_perm("assignments.reopen_completed_step"):
        return False, "You need permission to reopen completed steps."
    dept = _department_id(obj)
    if dept is None or not org.is_director(actor, dept):
        return False, "Only the director of the department that owns this step (or the ES) can reopen it."
    return True, ""


@contextlib.contextmanager
def reopen(obj, actor, reason):
    """Inside the block the locked record may change; it stays unlocked after
    (the service locks it again when the step completes)."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason for reopening this step.")
    allowed, why = can_reopen(actor, obj)
    if not allowed:
        raise PermissionDenied(why)
    from core import audit
    was = obj.locked_at
    with _allow(obj):
        obj.locked_at = None
        if hasattr(obj, "locked_by"):
            obj.locked_by = None
        obj.save(update_fields=["locked_at", "locked_by"] if hasattr(obj, "locked_by") else ["locked_at"])
        audit.log("reopen", obj, actor=actor, changes={"locked_at": {"old": was.isoformat() if was else None,
                                                                   "new": None}}, note=reason[:255])
        _timeline(obj, actor, reason)
        yield obj


def reopen_completed_step(obj, actor, reason, **changes):
    """Reopen a locked step and apply ``changes`` (field=value) in one audited
    operation. Returns the object (unlocked)."""
    with reopen(obj, actor, reason):
        if changes:
            for k, v in changes.items():
                setattr(obj, k, v)
            obj.save()
    _notify(obj, actor, reason)
    return obj


def _timeline(obj, actor, reason):
    try:
        from assignments.access import institution_of
        from qa_core import timeline
        inst = institution_of(obj) if hasattr(type(obj), "INSTITUTION_PATH") else None
        if inst is not None:
            timeline.record(inst, "step_reopened", internal_text=f"Step reopened: {obj}. Reason: {reason}",
                            visibility="internal", subject=obj, actor=actor)
    except Exception:  # pragma: no cover - the audit row is the record
        import logging
        logging.getLogger(__name__).exception("Could not write the reopen timeline event")


def _notify(obj, actor, reason):
    """G1.f: original actor, director of the owning department, ICT supervisor."""
    try:
        import org
        from notifications.services import notify
        people = set()
        for attr in ("assignee", "decided_by", "actor"):
            u = getattr(obj, attr, None)
            if u is not None and getattr(u, "pk", None):
                people.add(u)
        dept = _department_id(obj)
        if dept:
            d = org.director_of(dept)
            if d is not None:
                people.add(d)
        from assignments.access import institution_of
        from assignments.models import WorkAssignment
        inst = institution_of(obj) if hasattr(type(obj), "INSTITUTION_PATH") else None
        if inst is not None:
            for wa in WorkAssignment.objects.filter(institution=inst, kind=WorkAssignment.OVERSIGHT_ICT,
                                                    status__in=WorkAssignment.WORKING).select_related("assignee"):
                people.add(wa.assignee)
        for u in people:
            if u.pk != getattr(actor, "pk", None):
                notify(u, title="A completed step was reopened", body=f"{obj}: {reason}", actor=actor)
    except Exception:  # pragma: no cover
        import logging
        logging.getLogger(__name__).exception("Could not notify about a reopened step")
