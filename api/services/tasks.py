"""tasks/ - the website's Tasks app for the mobile "Work" tab.

Reuses (never copies):
* visibility       tasks.views._visible_tasks_qs (detail, actions, downloads)
* list tabs        tasks.views.task_list_base(scope) + _apply_list_filters + apply_sort
* create / edit    tasks.forms.TaskCreateForm / TaskEditForm -> services.create_task
* permissions      tasks.services.available_actions (the detail page's can_* flags)
* transitions      tasks.services.* (open/accept/start/ask/resume/return/resend/
                   mark_assignee_done/review_task/cancel_task/reopen_completed_task/
                   delete_task/timers/log_time/comments/attachments)
* people picker    tasks.services.assignable_users_for + capacity_for_user
"""
import datetime

from django.db.models import Q
from django.http import QueryDict
from django.utils import timezone

from .home import due_label
from .work import activity_ref, attachment_ref, form_errors, iso, run, user_ref


class _ListRequest:
    """The bits of an HttpRequest that tasks.views._apply_list_filters reads."""

    def __init__(self, user, params):
        self.user = user
        self.GET = params


def _ref(obj):
    return {"id": obj.pk, "name": obj.name} if obj is not None else None


def category_ref(cat):
    return {"id": cat.pk, "code": cat.code, "name": cat.name} if cat is not None else None


def task_ref(task):
    return {"id": task.pk, "reference": task.reference, "title": task.title, "state": task.state,
            "state_display": task.get_state_display()}


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------
def list_queryset(user, params):
    from core.sorting import apply_sort
    from tasks.views import SORTABLE_TASKS, _apply_list_filters, task_list_base

    from tasks.models import Task

    scope = params.get("scope") or "assigned"
    qs = task_list_base(user, scope)
    qd = QueryDict(mutable=True)
    for key in ("q", "priority", "category"):
        if params.get(key):
            qd[key] = params[key]
    state = params.get("state") or "active"
    qd["state"] = "" if state == "all" else state
    qs, _ = _apply_list_filters(qs, _ListRequest(user, qd))

    today = timezone.localdate()
    due = params.get("due")
    if due == "overdue":
        qs = qs.filter(due_date__lt=today).exclude(state__in=Task.CLOSED_STATES)
    elif due == "today":
        qs = qs.filter(due_date=today)
    elif due == "week":
        qs = qs.filter(due_date__gte=today, due_date__lte=today + datetime.timedelta(days=7))
    elif due == "none":
        qs = qs.filter(due_date__isnull=True)

    default_sort = "due" if scope == "assigned" else "created"
    qs, _key, _dir = apply_sort(qs, params.get("sort"), params.get("direction"), SORTABLE_TASKS,
                                default=default_sort)
    return qs.select_related("category", "created_by", "owner_department").prefetch_related("assignee_rows")


def list_item(task, user, today=None):
    rows = list(task.assignee_rows.all())
    mine = next((r for r in rows if r.user_id == user.pk), None)
    return {
        "id": task.pk,
        "reference": task.reference,
        "title": task.title,
        "state": task.state,
        "state_display": task.get_state_display(),
        "priority": task.priority,
        "priority_display": task.get_priority_display(),
        "category": category_ref(task.category),
        "due_date": task.due_date,
        "due_label": due_label(task.due_date, today) if task.due_date and not task.is_closed else "",
        "is_overdue": task.is_overdue,
        "created_by": user_ref(task.created_by),
        "owner_department": _ref(task.owner_department),
        "progress_percent": task.progress_percent,
        "assignee_count": len(rows),
        "my_state": mine.state if mine else None,
        "updated_at": iso(task.updated_at),
    }


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
def visible(user):
    from tasks.views import _visible_tasks_qs

    return _visible_tasks_qs(user)


def time_entry(entry):
    return {"id": entry.pk, "user": user_ref(entry.user), "started_at": iso(entry.started_at),
            "ended_at": iso(entry.ended_at), "duration_seconds": entry.duration_seconds,
            "hours": entry.hours, "note": entry.note or "", "is_running": entry.ended_at is None}


def detail(task, user, request=None):
    from tasks import services
    from tasks.models import Task, TaskTimeEntry

    today = timezone.localdate()
    my_row = task.assignee_rows.filter(user=user).first()
    running = TaskTimeEntry.objects.filter(task=task, user=user, ended_at__isnull=True).first()
    flags = services.available_actions(task, user, my_row=my_row, running_timer=running)
    closed = task.state in Task.CLOSED_STATES

    data = list_item(task, user, today)
    comments = task.comments.select_related("author").order_by("created_at")
    data.update({
        "description": task.description or "",
        "start_date": task.start_date,
        "estimated_hours": f"{task.estimated_hours:.2f}" if task.estimated_hours is not None else None,
        "logged_hours": task.logged_hours,
        "requires_review": task.requires_review,
        "reviewer": user_ref(task.reviewer),
        "reviewed_by": user_ref(task.reviewed_by),
        "reviewed_at": iso(task.reviewed_at),
        "review_notes": task.review_notes or "",
        "tags": list(task.tags or []),
        "recurrence_type": task.recurrence_type,
        "recurrence_rule": task.recurrence_rule or "",
        "parent": task_ref(task.parent) if task.parent_id else None,
        "created_at": iso(task.created_at),
        "completed_at": iso(task.completed_at),
        "cancelled_at": iso(task.cancelled_at),
        "assignees": [{
            "user": user_ref(r.user), "state": r.state, "state_display": r.get_state_display(),
            "note": r.note or "", "assigned_by": user_ref(r.assigned_by), "assigned_at": iso(r.assigned_at),
            "accepted_at": iso(r.accepted_at), "started_at": iso(r.started_at),
            "completed_at": iso(r.completed_at),
        } for r in task.assignee_rows.select_related("user", "assigned_by").order_by("assigned_at")],
        "subtasks": [list_item(t, user, today) for t in
                     task.subtasks.select_related("category", "created_by", "owner_department")
                     .prefetch_related("assignee_rows").order_by("created_at")],
        "dependencies": [{
            "id": link.pk, "depends_on": task_ref(link.depends_on), "note": link.note or "",
            "met": link.depends_on.state == Task.COMPLETED,
        } for link in task.dependency_links.select_related("depends_on")],
        "dependencies_met": services.dependencies_met(task),
        "comments": [{
            "id": c.pk, "author": user_ref(c.author), "body": c.body, "is_internal": c.is_internal,
            "is_pinned": c.is_pinned, "created_at": iso(c.created_at),
            "can_delete": services._can_delete_content(user, c.author, task),
        } for c in comments],
        "attachments": [attachment_ref(a, "tasks", task.pk, request)
                        for a in task.attachments.select_related("uploaded_by").order_by("-created_at")],
        "time_entries": [time_entry(e) for e in
                         task.time_entries.select_related("user").order_by("-started_at")[:20]],
        "running_timer": time_entry(running) if running else None,
        "activity": [activity_ref(a) for a in task.activities.select_related("actor").order_by("created_at")],
        "qa_link_url": flags["qa_link_url"],
    })
    actions = {k: flags[k] for k in (
        "is_creator", "is_assignee", "can_edit", "can_cancel", "can_delete", "can_review", "can_assign",
        "can_accept", "can_start", "can_reopen", "can_start_timer", "can_stop_timer", "can_ask",
        "can_resume", "can_return", "can_mark_done", "can_resend", "can_comment", "can_attach")}
    actions["can_open"] = bool(flags["can_edit"] and task.state == Task.DRAFT and not flags["qa_link_url"])
    actions["can_log_time"] = bool(my_row is not None and not closed)
    data["actions"] = actions
    return data


# ---------------------------------------------------------------------------
# Create / edit (through the website's forms)
# ---------------------------------------------------------------------------
def _form_data(values):
    """API values -> the POST dict TaskCreateForm expects."""
    data = {}
    for key in ("title", "description", "priority", "recurrence_type", "recurrence_rule"):
        if values.get(key) is not None:
            data[key] = values[key]
    for key in ("category", "reviewer"):
        if values.get(key) is not None:
            data[key] = str(values[key])
    for key in ("start_date", "due_date"):
        if values.get(key) is not None:
            data[key] = values[key].isoformat()
    if values.get("estimated_hours") is not None:
        data["estimated_hours"] = str(values["estimated_hours"])
    if values.get("requires_review"):
        data["requires_review"] = "on"
    if values.get("tags"):
        data["tags"] = ", ".join(values["tags"])
    if values.get("assignees"):
        data["assignees"] = [str(x) for x in values["assignees"]]
    return data


def create(user, values):
    from tasks import services
    from tasks.forms import TaskCreateForm
    from tasks.models import TaskCategory

    values = dict(values)
    category = None
    if values.get("category") is not None:
        category = TaskCategory.objects.filter(pk=values["category"], is_active=True).first()
    if values.get("priority") is None:
        values["priority"] = category.default_priority if category else "normal"
    if values.get("requires_review") is None:
        # The website's checkbox starts ticked.
        values["requires_review"] = True
    if not values.get("recurrence_type"):
        # The website's select always submits a value; "none" is its initial.
        values["recurrence_type"] = "none"
    form = TaskCreateForm(_form_data(values), user=user)
    if not form.is_valid():
        form_errors(form)
    cd = form.cleaned_data
    task = run(services.create_task,
               created_by=user, title=cd["title"], description=cd.get("description") or "",
               category=cd.get("category"), priority=cd.get("priority"), start_date=cd.get("start_date"),
               due_date=cd.get("due_date"), requires_review=cd.get("requires_review", True),
               reviewer=cd.get("reviewer") or None, tags=cd.get("tags") or [],
               estimated_hours=cd.get("estimated_hours"), recurrence_type=cd.get("recurrence_type"),
               recurrence_rule=cd.get("recurrence_rule") or "", assignees=list(cd.get("assignees") or []))
    warnings = []
    if values.get("from_message"):
        from messaging.chat_services import link_message_to_task

        link_message_to_task(values["from_message"], task, user)
    if values.get("open_now"):
        try:
            run(services.open_task, task, user)
        except Exception as exc:  # the task exists; report why it stayed a draft
            warnings.append(f"Could not open: {_err_text(exc)}")
    task.refresh_from_db()
    return task, warnings


EDIT_FIELDS = ("title", "description", "category", "priority", "start_date", "due_date", "estimated_hours",
               "requires_review", "reviewer", "recurrence_type", "recurrence_rule")


def update(task, user, changes):
    """PATCH semantics on top of tasks.views.task_edit."""
    from tasks import services
    from tasks.forms import TaskEditForm
    from tasks.models import Task

    from .work import Forbidden

    if not services.can_edit_task(task, user):
        raise Forbidden("You cannot edit this task.")
    if task.state in Task.CLOSED_STATES:
        from rest_framework import serializers

        raise serializers.ValidationError({"non_field_errors": ["Closed tasks cannot be edited."]})
    current = {
        "title": task.title, "description": task.description, "category": task.category_id,
        "priority": task.priority, "start_date": task.start_date, "due_date": task.due_date,
        "estimated_hours": task.estimated_hours, "requires_review": task.requires_review,
        "reviewer": task.reviewer_id, "tags": list(task.tags or []), "recurrence_type": task.recurrence_type,
        "recurrence_rule": task.recurrence_rule,
    }
    current.update(changes)
    form = TaskEditForm(_form_data(current), user=user, instance=task)
    if not form.is_valid():
        form_errors(form)
    changed = []
    for field in EDIT_FIELDS:
        new_val = form.cleaned_data.get(field)
        if getattr(task, field) != new_val:
            setattr(task, field, new_val)
            changed.append(field)
    tags = form.cleaned_data.get("tags") or []
    if task.tags != tags:
        task.tags = tags
        changed.append("tags")
    if changed:
        task.save(update_fields=changed + ["updated_at"])
        services.log_activity(task, "edited", actor=user, message=f"Edited: {', '.join(changed)}")
    return task


def add_assignees(task, user, user_ids):
    """tasks.views.task_assign: add each, report the ones skipped."""
    from accounts.models import User
    from tasks import services

    added, skipped = [], []
    for uid in dict.fromkeys(user_ids):
        u = User.objects.filter(pk=uid, is_active=True).first()
        if u is None:
            skipped.append(f"user {uid} not found")
            continue
        try:
            run(services.add_assignee, task, user, u)
            added.append(uid)
        except Exception as exc:
            skipped.append(_err_text(exc))
    return added, skipped


def _err_text(exc):
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return "; ".join(str(v) for vals in detail.values() for v in (vals if isinstance(vals, list) else [vals]))
    return str(detail if detail is not None else exc)


# ---------------------------------------------------------------------------
# Picker
# ---------------------------------------------------------------------------
def people(user, q="", category_id=None, task=None):
    """tasks.views.eligible_assignees, plus a name search."""
    from tasks import services
    from tasks.models import TaskCategory

    category = None
    if task is not None and task.category_id:
        category = task.category
    elif category_id:
        category = TaskCategory.objects.filter(pk=category_id).first()
    department = None
    if task is not None and task.owner_department_id:
        department = task.owner_department
    elif category is not None and category.handling_department_id:
        department = category.handling_department
    qs = services.assignable_users_for(user, department=department, category=category)
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    return qs


def person(u):
    from tasks import services

    used, cap, remaining = services.capacity_for_user(u)
    ref = user_ref(u)
    return {**ref, "email": u.email or "", "role": u.role.name if u.role_id else "",
            "department": u.department.name if u.department_id else "", "used": used, "max": cap,
            "remaining": remaining, "at_capacity": remaining <= 0}


def categories_for(user):
    """The create form's category choices (rank-filtered)."""
    from tasks.forms import TaskCreateForm

    return TaskCreateForm(user=user).fields["category"].queryset.select_related("handling_department")


def category_item(c):
    return {"id": c.pk, "code": c.code, "name": c.name, "description": c.description or "",
            "handling_department": _ref(c.handling_department), "default_priority": c.default_priority,
            "default_due_days": c.default_due_days, "requires_review": c.requires_review,
            "icon": c.icon or "", "colour": c.colour or ""}
