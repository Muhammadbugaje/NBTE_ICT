"""Externally managed tasks (QA, Master Plan §8 step 4).

A task created by another system (the QA task bridge, Phase 4) is completed
from that system, never from the Tasks UI. Other apps register a predicate:

    from tasks.hooks import register_external_manager
    register_external_manager(lambda task: hasattr(task, "qa_link"))

`is_externally_managed(task)` is True when any registered predicate says so.
`tasks.services` honours it: such tasks cannot be cancelled or deleted
through the Tasks app. In Phase 1 the registry is empty, so every task
behaves exactly as before.
"""
import contextlib
import contextvars
import logging

logger = logging.getLogger(__name__)

_managers = []
_EXTERNAL_CLOSE = contextvars.ContextVar("tasks_external_close", default=False)


@contextlib.contextmanager
def external_close():
    """Used by the managing system (QA, Phase 4) while it completes its own
    tasks: inside this block `mark_assignee_done` is allowed on externally
    managed tasks."""
    token = _EXTERNAL_CLOSE.set(True)
    try:
        yield
    finally:
        _EXTERNAL_CLOSE.reset(token)


def external_close_active():
    return _EXTERNAL_CLOSE.get()


def register_external_manager(predicate):
    """`predicate(task) -> bool`. Returns the predicate (usable as a decorator)."""
    if predicate not in _managers:
        _managers.append(predicate)
    return predicate


def unregister_external_manager(predicate):
    if predicate in _managers:
        _managers.remove(predicate)


def is_externally_managed(task):
    for predicate in list(_managers):
        try:
            if predicate(task):
                return True
        except Exception:  # a broken predicate must never break the Tasks app
            logger.exception("External-manager predicate failed for task %s", getattr(task, "pk", None))
    return False
