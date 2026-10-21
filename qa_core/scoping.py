"""Institution scoping machinery (Master Plan §4).

The rule: no queryset in the QA apps may touch a model that has an
institution path without passing through ``scope_to_institution``.

* Every institution-bearing model declares ``INSTITUTION_PATH`` (``""`` for
  ``Institution`` itself, ``"institution"`` for a direct FK, or a longer path
  such as ``"programme__institution"``) and uses
  :class:`InstitutionScopedManager` as its **default** manager.
* ``Model.objects.for_user(user)`` returns a scoped queryset.
* Evaluating a plain ``Model.objects.all()`` / ``.filter()`` raises
  :class:`UnscopedQueryError` when ``settings.QA_SCOPING_STRICT`` is true
  (development and tests) and logs at ERROR otherwise (production).
* System code (Celery jobs, seeders, service functions that already hold a
  scoped object) uses the explicit ``Model.unscoped`` manager. The base manager
  is ``unscoped`` too, so Django internals (deletion collector, related
  descriptors) keep working.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging

from django.conf import settings
from django.db import models

from accounts.scoping import scope_to_institution

logger = logging.getLogger(__name__)

__all__ = [
    "UnscopedQueryError",
    "InstitutionScopedQuerySet",
    "InstitutionScopedManager",
    "UnscopedInstitutionManager",
    "scope_to_institution",
    "institution_bearing_models",
]


class UnscopedQueryError(RuntimeError):
    """An institution-bearing queryset was evaluated without ``for_user()``."""


_system_context = contextvars.ContextVar("qa_scoping_system_context", default=False)


@contextlib.contextmanager
def system_context():
    """Allow unscoped evaluation inside framework-level code paths that cannot
    call ``for_user`` (model uniqueness validation). Never use it in views."""
    token = _system_context.set(True)
    try:
        yield
    finally:
        _system_context.reset(token)


class InstitutionScopedModelMixin:
    """Mixin for institution-bearing models: Django's own uniqueness and
    constraint validation queries the default manager, so run it in the
    system context (it only ever answers "does a clash exist?")."""

    def validate_unique(self, exclude=None):
        with system_context():
            return super().validate_unique(exclude=exclude)

    def validate_constraints(self, exclude=None):
        with system_context():
            return super().validate_constraints(exclude=exclude)


def _strict():
    return bool(getattr(settings, "QA_SCOPING_STRICT", settings.DEBUG))


class InstitutionScopedQuerySet(models.QuerySet):
    """QuerySet that refuses to be evaluated until it has been scoped."""

    _qa_scoped = False

    def _clone(self):
        clone = super()._clone()
        clone._qa_scoped = self._qa_scoped
        return clone

    # -- guard -----------------------------------------------------------
    def _qa_guard(self):
        if self._qa_scoped or _system_context.get():
            return
        message = (
            f"Unscoped query on {self.model._meta.label}: use "
            f"{self.model.__name__}.objects.for_user(user) or, in system code, "
            f"{self.model.__name__}.unscoped (Master Plan §4)."
        )
        if _strict():
            raise UnscopedQueryError(message)
        logger.error(message)

    def _mark_scoped(self):
        self._qa_scoped = True
        return self

    # -- every evaluation path goes through the guard ----------------------
    def _fetch_all(self):
        if self._result_cache is None:
            self._qa_guard()
        super()._fetch_all()

    def count(self):
        if self._result_cache is None:
            self._qa_guard()
        return super().count()

    def exists(self):
        if self._result_cache is None:
            self._qa_guard()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._qa_guard()
        return super().aggregate(*args, **kwargs)

    def iterator(self, *args, **kwargs):
        self._qa_guard()
        return super().iterator(*args, **kwargs)

    def update(self, **kwargs):
        self._qa_guard()
        return super().update(**kwargs)

    def delete(self):
        self._qa_guard()
        return super().delete()

    # -- the sanctioned entry point -----------------------------------------
    def for_user(self, user):
        path = getattr(self.model, "INSTITUTION_PATH", None)
        if path is None:
            raise TypeError(f"{self.model._meta.label} does not declare INSTITUTION_PATH.")
        qs = self._clone()._mark_scoped()
        return scope_to_institution(qs, user, field=path or None)


class InstitutionScopedManager(models.Manager.from_queryset(InstitutionScopedQuerySet)):
    """Default manager for every institution-bearing model."""

    def for_user(self, user):
        return self.get_queryset().for_user(user)


class UnscopedInstitutionManager(models.Manager.from_queryset(InstitutionScopedQuerySet)):
    """Explicit escape hatch for system code (and Django's base manager)."""

    def get_queryset(self):
        return super().get_queryset()._mark_scoped()


def institution_bearing_models():
    """Every installed model that declares ``INSTITUTION_PATH``."""
    from django.apps import apps

    return [m for m in apps.get_models() if hasattr(m, "INSTITUTION_PATH")]
