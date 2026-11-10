"""Division routing of tasks (Master Plan v2.1 §9.6: H17, H23, H24, B8).

    route_to_division(task, actor, division, members=None, note="")
    accept_division_task(task, head, members=None)
    decline_division_task(task, head, reason)
    complete_division_task(task, head, note="")
    divisions_for_routing(actor, task=None)  -> [(division, open, max, has_capacity)]
    queue_for(user)                          -> division queue data

A group task is a Task with ``division`` set. The director (or another lead of
the department, the ES or a superuser) routes it; the division head must
accept before the members are added (all active members unless the router or
the head named people); ``Division.max_open_tasks`` is enforced through
``org.division_has_capacity``; the head can mark the whole group task complete.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import org

from .models import Task, TaskAssignee, TaskTimeEntry
from . import services


def _name(u):
    return u.get_full_name() or u.email


def can_route(task, actor) -> bool:
    """The creator when they lead the task's department (director, deputy,
    office head), or the ES / superuser."""
    if task.state in Task.CLOSED_STATES or task.division_id:
        return False
    if services.is_override(actor):
        return True
    dept_id = task.owner_department_id or getattr(task.created_by, "department_id", None)
    return bool(services.can_edit_task(task, actor) and dept_id and org.is_department_lead(actor, dept_id))


def is_division_head(task, user) -> bool:
    return bool(task.division_id and user.is_authenticated and task.division.head_id == user.pk)


def can_act_as_head(task, user) -> bool:
    return bool(task.division_id and (is_division_head(task, user) or services.is_override(user)))


def division_members(division):
    """Active staff accounts posted to the division (head included)."""
    from accounts.selectors import staff_users
    return staff_users().filter(staff_profile__division=division).select_related("role")


def divisions_for_routing(actor, task=None):
    """Divisions ``actor`` may route to, with their load: [(div, open, max, ok)]."""
    from org.models import Division

    qs = Division.objects.filter(is_active=True).select_related("department", "head")
    if not services.is_override(actor):
        dept_id = (task.owner_department_id if task is not None and task.owner_department_id
                   else actor.department_id)
        qs = qs.filter(department_id=dept_id)
    out = []
    for div in qs.order_by("department__name", "name"):
        used = org.division_open_task_count(div)
        out.append((div, used, div.max_open_tasks, org.division_has_capacity(div)))
    return out


@transaction.atomic
def route_to_division(task, actor, division, members=None, note=""):
    if not can_route(task, actor):
        raise PermissionDenied("Only the director (or a lead) of the task's department can route it to a division.")
    if not division.is_active:
        raise ValidationError("This division is not active.")
    dept_id = task.owner_department_id or getattr(task.created_by, "department_id", None)
    if not services.is_override(actor) and division.department_id != dept_id:
        raise ValidationError("Route only to a division of the task's own department.")
    if not org.division_has_capacity(division):
        used = org.division_open_task_count(division)
        raise ValidationError(
            f"{division.name} is full: {used}/{division.max_open_tasks} open group tasks. "
            "Wait for one to finish or ask the director to raise the limit.")
    member_ids = [m.pk for m in (members or [])]
    allowed = set(division_members(division).values_list("pk", flat=True))
    if any(pk not in allowed for pk in member_ids):
        raise ValidationError("Choose people from this division only.")
    if division.head_id is None:
        raise ValidationError(f"{division.name} has no head to accept the task yet.")

    old = task.state
    now = timezone.now()
    task.division = division
    task.division_state = Task.DIV_PENDING
    task.division_routed_by = actor
    task.division_routed_at = now
    task.division_note = (note or "")[:500]
    if not task.owner_department_id:
        task.owner_department_id = division.department_id
    if task.state == Task.DRAFT:
        task.state = Task.OPEN
    task.save(update_fields=["division", "division_state", "division_routed_by", "division_routed_at",
                             "division_note", "owner_department", "state", "updated_at"])
    services._log_override(task, actor, f"route the task to {division.name}")
    services.log_activity(task, "division_routed", actor=actor,
                          message=f"Routed to {division.name}; waiting for {_name(division.head)} to accept.",
                          metadata={"division_id": division.pk, "members": member_ids})
    services._notify_assignee(task, division.head, actor, event="division_routed",
                              body=f"{task.title} — accept it to add your team.")
    if old != task.state:
        services._emit_state_changed(task, old, actor, "route_to_division", division_id=division.pk)
    return task


def _routed_members(task):
    act = task.activities.filter(kind="division_routed").order_by("-created_at").first()
    return list((act.metadata or {}).get("members") or []) if act else []


@transaction.atomic
def accept_division_task(task, head, members=None):
    """H23: the head accepts; the named people (or every member) are added."""
    if not can_act_as_head(task, head):
        raise PermissionDenied("Only the head of the division can accept this task.")
    if task.division_state != Task.DIV_PENDING:
        raise ValidationError("This task is not waiting for the division.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")
    pool = division_members(task.division)
    ids = [m.pk for m in (members or [])] or _routed_members(task)
    people = list(pool.filter(pk__in=ids)) if ids else list(pool.exclude(pk=task.division.head_id))
    if not people:
        people = list(pool)  # a one-person division: the head does the work
    task.division_state = Task.DIV_ACCEPTED
    task.division_accepted_at = timezone.now()
    task.save(update_fields=["division_state", "division_accepted_at", "updated_at"])
    added, skipped = [], []
    for u in people:
        if task.assignee_rows.filter(user=u).exists():
            continue
        if org.is_es(u):
            continue
        used, cap, remaining = services.capacity_for_user(u)
        if remaining <= 0:
            skipped.append(f"{_name(u)} (full {used}/{cap})")
            continue
        TaskAssignee.objects.create(task=task, user=u, state=TaskAssignee.ASSIGNED, assigned_by=head)
        added.append(u)
        services._notify_assignee(task, u, head, event="division_member")
    msg = f"{task.division.name} accepted the task; added {len(added)} member(s)."
    if skipped:
        msg += " Skipped (at capacity): " + ", ".join(skipped[:5]) + "."
    services.log_activity(task, "division_accepted", actor=head, message=msg,
                          metadata={"added": [u.pk for u in added]})
    services._notify_creator(task, head, msg)
    return added


@transaction.atomic
def decline_division_task(task, head, reason):
    if not can_act_as_head(task, head):
        raise PermissionDenied("Only the head of the division can decline this task.")
    if task.division_state != Task.DIV_PENDING:
        raise ValidationError("This task is not waiting for the division.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please say why the division cannot take it.")
    div = task.division
    task.division_state = Task.DIV_DECLINED
    task.division_note = reason[:500]
    task.division = None
    task.save(update_fields=["division", "division_state", "division_note", "updated_at"])
    services.log_activity(task, "division_declined", actor=head,
                          message=f"{div.name} declined: {reason[:200]}", metadata={"division_id": div.pk})
    services._notify_creator(task, head, f"{div.name} declined the task: {reason[:200]}",
                             title_override=f"Division declined — {task.reference}")
    if task.division_routed_by_id and task.division_routed_by_id != task.created_by_id:
        services._notify_assignee(task, task.division_routed_by, head, event="division_routed",
                                  body=f"{div.name} declined: {reason[:200]}")
    return task


@transaction.atomic
def complete_division_task(task, head, note=""):
    """H24: the head marks the whole group task complete."""
    if not can_act_as_head(task, head):
        raise PermissionDenied("Only the head of the division can complete the group task.")
    if task.division_state != Task.DIV_ACCEPTED:
        raise ValidationError("Accept the task for the division first.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is already closed.")
    services._refuse_if_externally_managed(task, "completed")
    now = timezone.now()
    for entry in TaskTimeEntry.objects.filter(task=task, ended_at__isnull=True):
        entry.ended_at = now
        entry.duration_seconds = int((now - entry.started_at).total_seconds())
        entry.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
    task.assignee_rows.exclude(state=TaskAssignee.DECLINED).update(
        state=TaskAssignee.DONE, completed_at=now, review_state=TaskAssignee.REVIEW_CONFIRMED, reviewed_at=now)
    old = task.state
    task.state = Task.COMPLETED
    task.completed_at = now
    task.reviewed_by = head
    task.reviewed_at = now
    task.review_notes = (note or "").strip()[:2000]
    task.save(update_fields=["state", "completed_at", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])
    services.log_activity(task, "completed", actor=head,
                          message=f"Group task completed by the head of {task.division.name}.")
    services._on_task_completed(task, approver=head)
    services._emit_state_changed(task, old, head, "complete_division_task")
    return task


def queue_for(user):
    """The division queue: divisions the user heads (or, for a director / ES,
    every division of their department / all), with pending and active
    group tasks and the capacity."""
    from org.models import Division

    if services.is_override(user):
        divs = Division.objects.filter(is_active=True)
    else:
        dept = org.directed_department(user)
        q = Division.objects.filter(is_active=True, head=user)
        if dept is not None:
            q = q | Division.objects.filter(is_active=True, department=dept)
        divs = q.distinct()
    out = []
    for div in divs.select_related("department", "head").order_by("department__name", "name"):
        tasks = (Task.objects.filter(division=div).select_related("created_by", "division_routed_by")
                 .prefetch_related("assignee_rows__user"))
        out.append({
            "division": div,
            "open": org.division_open_task_count(div),
            "max": div.max_open_tasks,
            "has_capacity": org.division_has_capacity(div),
            "is_head": div.head_id == user.pk,
            "pending": list(tasks.filter(division_state=Task.DIV_PENDING).exclude(state__in=Task.CLOSED_STATES)),
            "active": list(tasks.filter(division_state=Task.DIV_ACCEPTED).exclude(state__in=Task.CLOSED_STATES)
                           .order_by("due_date", "-updated_at")),
            "done_month": tasks.filter(state=Task.COMPLETED,
                                       completed_at__gte=timezone.localtime().replace(
                                           day=1, hour=0, minute=0, second=0, microsecond=0)).count(),
            "members": list(division_members(div)),
        })
    return out
