"""Task signals (QA, Master Plan §8).

`task_state_changed` is sent synchronously by `tasks.services` from the
exits that change who holds a task or whether it is finished:

    open_task, reassign, mark_assignee_done, review_task,
    cancel_task, return_to_assigner

Receivers run inside the service's transaction, so a receiver that raises
(e.g. the Phase 4 QA bridge rejecting a reassignment that breaks R-01) rolls
the whole action back and the user sees the reason.

Arguments: sender=Task, task, old (state before), new (state after),
actor (the user acting), action (the service function name), metadata (dict).
"""
from django.dispatch import Signal

task_state_changed = Signal()
