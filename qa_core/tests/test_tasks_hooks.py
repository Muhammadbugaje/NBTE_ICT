"""tasks: the task_state_changed signal and the is_externally_managed hook
(Master Plan §8). Phase 1 ships the hook points only; with nothing
registered, every task behaves exactly as before."""
import pytest
from django.core.exceptions import PermissionDenied

from tasks import hooks, services
from tasks.models import Task
from tasks.signals import task_state_changed


@pytest.fixture
def people(qa_seed, make_user):
    return make_user("boss@t.local", role="director", dept="ict"), make_user("w@t.local", role="officer", dept="ict")


@pytest.fixture
def received():
    events = []

    def receiver(sender, task, old, new, actor, action, metadata, **kw):
        events.append((action, old, new, actor.pk, dict(metadata)))

    task_state_changed.connect(receiver)
    yield events
    task_state_changed.disconnect(receiver)


def _task(boss, worker):
    from staff.models import Department
    return services.create_task(created_by=boss, title="Check the switch", assignees=[worker],
                                owner_department=Department.objects.get(code="ict"))


def test_signal_on_open_done_and_cancel(people, received):
    boss, worker = people
    task = _task(boss, worker)
    services.open_task(task, boss)
    assert received[-1][:3] == ("open_task", Task.DRAFT, Task.OPEN)
    task2 = _task(boss, worker)
    services.open_task(task2, boss)
    services.cancel_task(task2, boss, reason="no longer needed")
    assert received[-1][0] == "cancel_task" and received[-1][2] == Task.CANCELLED
    services.mark_assignee_done(task, worker)
    assert received[-1][0] == "mark_assignee_done" and received[-1][4]["assignee_id"] == worker.pk


def test_signal_on_return_to_assigner(people, received):
    boss, worker = people
    task = _task(boss, worker)
    services.open_task(task, boss)
    services.return_to_assigner(task, worker, "I am on leave this week")
    assert received[-1][0] == "return_to_assigner" and received[-1][2] == Task.RETURNED


def test_receiver_that_raises_rolls_the_action_back(people):
    boss, worker = people
    task = _task(boss, worker)
    services.open_task(task, boss)

    def veto(sender, action, **kw):
        if action == "cancel_task":
            raise PermissionDenied("QA says no")

    task_state_changed.connect(veto)
    try:
        with pytest.raises(PermissionDenied):
            from django.db import transaction
            with transaction.atomic():
                services.cancel_task(task, boss, reason="x")
    finally:
        task_state_changed.disconnect(veto)
    task.refresh_from_db()
    assert task.state == Task.OPEN


def test_externally_managed_tasks_cannot_be_cancelled_or_deleted(people):
    boss, worker = people
    task = _task(boss, worker)
    services.open_task(task, boss)
    assert not hooks.is_externally_managed(task)
    predicate = hooks.register_external_manager(lambda t: t.pk == task.pk)
    try:
        assert hooks.is_externally_managed(task)
        with pytest.raises(PermissionDenied, match="Open in QA"):
            services.cancel_task(task, boss, reason="x")
        with pytest.raises(PermissionDenied):
            services.delete_task(task, boss, reason="cleaning up")
    finally:
        hooks.unregister_external_manager(predicate)
    services.cancel_task(task, boss, reason="now allowed")


def test_broken_predicate_never_breaks_tasks(people):
    boss, worker = people
    task = _task(boss, worker)

    def broken(t):
        raise RuntimeError("bug in another module")

    hooks.register_external_manager(broken)
    try:
        assert hooks.is_externally_managed(task) is False
    finally:
        hooks.unregister_external_manager(broken)


def test_qa_notification_category_exists():
    from notifications.models import Notification

    assert ("qa", "Quality assurance") in Notification.CATEGORY_CHOICES
