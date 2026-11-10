"""Plug-in points of the grant engine.

Two registries keep ``assignments`` free of imports from the apps that use it:

* **Sources** — the kinds of work that cause grants (a Ticket now; a
  WorkAssignment, Complaint, SSQ review in Phase P). For each source model the
  consumer says whether a given row is still open, when it is due, and how to
  label it. The hourly sweep uses this to extend grants of open work and to
  revoke grants whose work closed without the consumer revoking them.

* **Native visibility** — for each granted object model, a function
  ``fn(user, obj, scope) -> bool`` answering "may this person see it without
  any grant?" (assets: ``assets.access.can_view_asset``). ``access.can_see``
  is native OR a live grant.

Apps register from a module called ``grant_sources.py`` (imported by
``autodiscover`` when the app registry is ready), for example::

    from assignments import registry
    registry.register_source("tickets.Ticket", is_open=lambda t: t.is_open,
                             due_at=lambda t: t.resolution_due_at, label=lambda t: t.reference)
    registry.register_native("assets.Asset", lambda user, obj, scope: can_view_asset(user, obj))
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

_SOURCES: dict[str, "SourceSpec"] = {}
_NATIVE: dict[str, Callable] = {}


@dataclass(frozen=True)
class SourceSpec:
    is_open: Callable
    due_at: Optional[Callable] = None
    label: Optional[Callable] = None
    url: Optional[Callable] = None


def _key(model_or_label) -> str:
    if isinstance(model_or_label, str):
        return model_or_label.lower()
    return model_or_label._meta.label_lower


def register_source(model, *, is_open, due_at=None, label=None, url=None):
    _SOURCES[_key(model)] = SourceSpec(is_open=is_open, due_at=due_at, label=label, url=url)


def register_native(model, fn):
    _NATIVE[_key(model)] = fn


def source_spec(model_or_obj):
    if model_or_obj is None:
        return None
    if not isinstance(model_or_obj, str) and not isinstance(model_or_obj, type):
        model_or_obj = type(model_or_obj)
    return _SOURCES.get(_key(model_or_obj))


def native_resolver(model_or_obj):
    if not isinstance(model_or_obj, str) and not isinstance(model_or_obj, type):
        model_or_obj = type(model_or_obj)
    return _NATIVE.get(_key(model_or_obj))


def autodiscover():
    from django.utils.module_loading import autodiscover_modules
    autodiscover_modules("grant_sources")
