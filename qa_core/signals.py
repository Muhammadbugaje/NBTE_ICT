"""qa_core signal wiring.

Phase 1 deliberately connects nothing to `tasks.signals.task_state_changed`
and registers no `tasks.hooks` external manager: the QA task bridge that uses
both ships in Phase 4 (Master Plan §8). This module exists so later phases add
their receivers in one obvious place.
"""


# Phase 4: the task bridge (§8) — externally-managed hook and task_state_changed receivers.
from .task_bridge import connect as _connect_task_bridge  # noqa: E402

_connect_task_bridge()
