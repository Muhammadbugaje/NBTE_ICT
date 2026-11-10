"""Cross-department bridge (Master Plan v2.1 §9.5 step 4, H11).

A subtask meant for another department does not get an assignee there (that
would break the rank / department rule); it becomes a ``workflow.Request`` in
that department's workflow instead. The request is raised through the normal
workflow service (``workflow.services.raise_request``), so the receiving
department's approval chain applies unchanged. Both sides are linked
(``Task.linked_request`` / ``Request.metadata["bridged_task_id"]``) and the
outcome flows back: when the request completes the subtask completes; when it
is rejected or cancelled the subtask is returned to its creator.

The request type used is, in order: the ``tasks.bridge.request_type`` setting
(a request-type code; per-department override via ``department:<code>``
scope), else an active type of the department whose code starts with
``task-``, else its first active "general" type, else its first active type.
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

logger = logging.getLogger(__name__)

PRIORITY_MAP = {"urgent": "urgent", "high": "high", "normal": "normal", "low": "low"}


def request_type_for(department):
    """The RequestType a bridged subtask uses in ``department`` (or None)."""
    from workflow.models import RequestType

    qs = RequestType.objects.filter(handling_department=department, is_active=True)
    code = ""
    try:
        import config as _cfg
        getter = getattr(_cfg, "get_for_department", None)
        code = (getter("tasks.bridge.request_type", department, "") if getter
                else _cfg.get("tasks.bridge.request_type", "")) or ""
    except Exception:
        code = ""
    if code:
        rt = qs.filter(code=code).first()
        if rt is not None:
            return rt
    return (qs.filter(code__startswith="task-").order_by("sort_order", "pk").first()
            or qs.filter(category__code="general").order_by("sort_order", "pk").first()
            or qs.order_by("sort_order", "pk").first())


@transaction.atomic
def escalate_to_request(subtask, target_department, actor, *, note=""):
    """Turn ``subtask`` into a Request in ``target_department``'s workflow.
    Returns the Request. Raises ValidationError when the department has no
    request type to receive it."""
    from tasks import services as task_services
    from tasks.models import Task
    from workflow import services as wf

    if subtask.linked_request_id:
        return subtask.linked_request
    if subtask.state in Task.CLOSED_STATES:
        raise ValidationError("Closed subtasks cannot be sent to another department.")
    if actor is None or not actor.is_authenticated:
        raise PermissionDenied("Sign in first.")
    rt = request_type_for(target_department)
    if rt is None:
        raise ValidationError(
            f"{target_department.name} has no request type to receive work yet. "
            "Ask an administrator to add one (Settings → Request types).")
    parent = subtask.parent
    body = "\n\n".join(x for x in (
        f"Work requested from task {parent.reference if parent else subtask.reference}"
        f" ({parent.title if parent else subtask.title}).",
        subtask.description or "",
        f"Due: {subtask.due_date:%d %b %Y}" if subtask.due_date else "",
        note or "",
    ) if x)
    req = wf.raise_request(
        requester=actor, request_type=rt, subject=f"[{subtask.reference}] {subtask.title}"[:255],
        body=body, priority=PRIORITY_MAP.get(subtask.priority, "normal"),
        form_data={},
    )
    meta = dict(req.metadata or {})
    meta.update({"bridged_task_id": subtask.pk, "bridged_task_reference": subtask.reference})
    req.metadata = meta
    req.save(update_fields=["metadata", "updated_at"])

    old = subtask.state
    subtask.linked_request = req
    subtask.state = Task.IN_PROGRESS if old != Task.DRAFT else Task.OPEN
    subtask.save(update_fields=["linked_request", "state", "updated_at"])
    task_services.log_activity(
        subtask, "bridged", actor=actor,
        message=f"Handled as request {req.reference} in {target_department.name}.",
        metadata={"request_id": req.pk, "department_id": target_department.pk})
    if parent is not None:
        task_services.log_activity(
            parent, "bridged", actor=actor,
            message=f"Subtask {subtask.reference} sent to {target_department.name} as request {req.reference}.",
            metadata={"request_id": req.pk, "subtask_id": subtask.pk})
    return req


def sync_from_request(request):
    """Mirror a bridged request's outcome onto its subtask(s)."""
    from tasks import services as task_services
    from tasks.hooks import external_close
    from tasks.models import Task, TaskAssignee
    from django.utils import timezone

    R = type(request)
    for task in Task.objects.filter(linked_request=request).exclude(state__in=Task.CLOSED_STATES):
        if request.status == R.COMPLETED:
            with external_close():
                now = timezone.now()
                task.assignee_rows.exclude(state=TaskAssignee.DECLINED).update(
                    state=TaskAssignee.DONE, completed_at=now, review_state=TaskAssignee.REVIEW_CONFIRMED)
                task.state = Task.COMPLETED
                task.completed_at = now
                task.save(update_fields=["state", "completed_at", "updated_at"])
                task_services.log_activity(task, "completed", actor=None,
                                           message=f"Request {request.reference} completed in "
                                                   f"{request.receiving_department.name}.")
                task_services._on_task_completed(task)
        elif request.status in (R.REJECTED, R.CANCELLED):
            task.state = Task.RETURNED
            task.save(update_fields=["state", "updated_at"])
            task_services.log_activity(task, "returned", actor=None,
                                       message=f"Request {request.reference} was {request.get_status_display().lower()}"
                                               " by the other department.")
            task_services._notify_creator(
                task, None, f"Request {request.reference} was {request.get_status_display().lower()}.",
                title_override=f"Cross-department work not done — {task.reference}")
