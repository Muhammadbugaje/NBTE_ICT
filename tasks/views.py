"""Tasks views — list, kanban, detail, create, every action, JSON helpers.

All state changes delegate to `tasks.services`. Views handle HTTP shaping
(HTMX detection, redirects, flash messages) and never mutate models directly.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
import logging
import time

from core.sorting import apply_sort
from accounts.models import User


from . import services
from .forms import (
    AskQuestionForm,
    AssignForm,
    AssignToRoleForm,
    AttachmentForm,
    BulkActionForm,
    CancelForm,
    CommentForm,
    DeleteForm,
    DependencyForm,
    ReopenForm, 
    LogTimeForm,
    MarkDoneForm,
    ResendForm,
    ResumeForm,
    ReturnForm,
    ReviewForm,
    StartWorkForm,
    SubtaskFormSet,
    SubtaskFormSet,
    TaskCreateForm,
    TaskEditForm,
)
from .models import (
    Task,
    TaskActivity,
    TaskAssignee,
    TaskAttachment,
    TaskCategory,
    TaskComment,
    TaskDependency,
    TaskTimeEntry,
)

logger = logging.getLogger(__name__)  # review M10: was used but never defined

PER_PAGE = 25
POLL_INTERVAL = 5

SORTABLE_TASKS = {
    "created":  "created_at",
    "updated":  "updated_at",
    "due":      "due_date",
    "priority": "priority",
    "state":    "state",
    "title":    "title",
    "reference": "reference",
}


# ===========================================================================
# Visibility
# ===========================================================================
def _can_see_all(user):
    return user.is_superuser or user.has_perm("accounts.access_all_departments")


def _visible_tasks_qs(user):
    """Return the queryset of tasks this user may see.

      - superuser / access_all_departments → everything
      - tasks they created
      - tasks they're assigned to
      - tasks owned by their department (if they're dept head or higher)
    """
    base = Task.objects.select_related(
        "category", "created_by", "owner_department", "parent",
    )
    if _can_see_all(user):
        return base

    q = Q(created_by=user) | Q(assignee_rows__user=user)
    if user.department_id and user.role_id:
        if user.role.code in (
            "executive-secretary",
            "director", "deputy-director",
            "office-head-admin", "office-head-ts", "office-head-dev",
            "division-head",
        ):
            q |= Q(owner_department_id=user.department_id)
    return base.filter(q).distinct()


def _require_view(request, pk):
    task = get_object_or_404(_visible_tasks_qs(request.user), pk=pk)
    return task


# ===========================================================================
# List — with HTMX filter/sort and Kanban toggle
# ===========================================================================
def _list_querystring(request):
    """Build the query-string dict the templates need to preserve state."""
    return {
        "q": (request.GET.get("q") or "").strip(),
        "state": request.GET.get("state", "active"),
        "category": request.GET.get("category", ""),
        "priority": request.GET.get("priority", ""),
        "scope": request.GET.get("scope", ""),
        "sort": request.GET.get("sort", "created"),
        "direction": request.GET.get("direction", "desc"),
        "view": request.GET.get("view", "list"),
        "assignee": request.GET.get("assignee", ""),
    }


def _apply_list_filters(qs, request):
    qs_dict = _list_querystring(request)

    q = qs_dict["q"]
    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(title__icontains=q)
            | Q(description__icontains=q)
        )

    state = qs_dict["state"]
    if state == "active":
        qs = qs.exclude(state__in=Task.CLOSED_STATES)
    elif state == "open":
        qs = qs.filter(state__in=Task.OPEN_STATES)
    elif state == "completed":
        qs = qs.filter(state=Task.COMPLETED)
    elif state == "cancelled":
        qs = qs.filter(state=Task.CANCELLED)
    elif state == "overdue":
        qs = qs.filter(due_date__lt=timezone.localdate()).exclude(
            state__in=Task.CLOSED_STATES,
        )
    elif state in dict(Task.STATE_CHOICES):
        qs = qs.filter(state=state)

    if qs_dict["category"]:
        qs = qs.filter(category__code=qs_dict["category"])

    if qs_dict["priority"] in dict(Task.PRIORITY_CHOICES):
        qs = qs.filter(priority=qs_dict["priority"])

    assignee_id = request.GET.get("assignee")
    if assignee_id and assignee_id.isdigit():
        qs = qs.filter(assignee_rows__user_id=int(assignee_id)).distinct()

    if qs_dict["scope"] == "mine":
        qs = qs.filter(created_by=request.user)
    elif qs_dict["scope"] == "assigned":
        qs = qs.filter(assignee_rows__user=request.user).distinct()

    return qs, qs_dict


@login_required
def task_list(request):
    qs = _visible_tasks_qs(request.user)
    qs, qdict = _apply_list_filters(qs, request)

    qs, sort_key, sort_dir = apply_sort(
        qs, qdict["sort"], qdict["direction"], SORTABLE_TASKS, default="created",
    )

    total = qs.count()

    # Kanban doesn't paginate — it shows columns.
    if qdict["view"] == "kanban":
        columns = _build_kanban_columns(qs)
        return render(request, "tasks/list.html", {
            "view_mode": "kanban",
            "columns": columns,
            "total": total,
            "bulk_eligible_users": User.objects.filter(is_active=True).order_by("first_name", "last_name", "email",)[:500],
            "q": qdict["q"], "state_filter": qdict["state"],
            "cat_filter": qdict["category"], "prio_filter": qdict["priority"],
            "scope_filter": qdict["scope"],
            "sort_key": sort_key, "sort_dir": sort_dir,
            "categories": TaskCategory.objects.filter(is_active=True).order_by("sort_order", "name"),
            "state_choices": Task.STATE_CHOICES,
            "priority_choices": Task.PRIORITY_CHOICES,
                    "assignee_filter": qdict.get("assignee", ""),
        "all_users": User.objects.filter(is_active=True).order_by("first_name", "last_name")[:300],
        })

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tasks/list.html", {
        "view_mode": "list",
        "page": page,
        "total": total,
        "q": qdict["q"], "state_filter": qdict["state"],
        "cat_filter": qdict["category"], "prio_filter": qdict["priority"],
        "scope_filter": qdict["scope"],
        "sort_key": sort_key, "sort_dir": sort_dir,
        "categories": TaskCategory.objects.filter(is_active=True).order_by("sort_order", "name"),
        "state_choices": Task.STATE_CHOICES,
        "priority_choices": Task.PRIORITY_CHOICES,
    })


def _build_kanban_columns(qs):
    """Return the four kanban columns with their tasks. Uses a small
    number of DB queries — grouped client-side in Python for correctness."""
    qs = qs.select_related("category", "created_by").prefetch_related("assignee_rows__user")
    tasks = list(qs[:300])  # cap for sanity

    COLUMNS = [
        ("open",       "Open",         ["draft", "open"]),
        ("active",     "In progress",  ["in_progress", "awaiting_response"]),
        ("review",     "Review",       ["awaiting_review", "returned"]),
        ("done",       "Completed",    ["completed", "cancelled"]),
    ]
    columns = []
    for key, label, states in COLUMNS:
        bucket = [t for t in tasks if t.state in states]
        columns.append({"key": key, "label": label, "tasks": bucket, "count": len(bucket)})
    return columns


@login_required
def my_tasks(request):
    qs = (
        Task.objects
        .filter(created_by=request.user)
        .select_related("category", "owner_department")
    )
    qdict = _list_querystring(request)
    qs, qdict = _apply_list_filters(qs, request)

    qs, sort_key, sort_dir = apply_sort(
        qs, qdict["sort"], qdict["direction"], SORTABLE_TASKS, default="created",
    )
    total = qs.count()

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tasks/my_tasks.html", {
        "page": page, "total": total,
        "q": qdict["q"], "state_filter": qdict["state"],
        "sort_key": sort_key, "sort_dir": sort_dir,
        "state_choices": Task.STATE_CHOICES,
    })


@login_required
def assigned_to_me(request):
    qs = (
        Task.objects
        .filter(assignee_rows__user=request.user)
        .exclude(state__in=Task.CLOSED_STATES)
        .select_related("category", "created_by", "owner_department")
        .prefetch_related("assignee_rows")
        .distinct()
    )
    qdict = _list_querystring(request)
    qs, qdict = _apply_list_filters(qs, request)

    qs, sort_key, sort_dir = apply_sort(
        qs, qdict["sort"], qdict["direction"], SORTABLE_TASKS, default="due",
    )
    total = qs.count()

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tasks/assigned_to_me.html", {
        "page": page, "total": total,
        "q": qdict["q"], "state_filter": qdict["state"],
        "sort_key": sort_key, "sort_dir": sort_dir,
        "state_choices": Task.STATE_CHOICES,
    })


@login_required
def review_queue(request):
    """Tasks waiting on MY review."""
    qs = (
        Task.objects
        .filter(state=Task.AWAITING_REVIEW)
        .filter(Q(reviewer=request.user) | Q(reviewer__isnull=True, created_by=request.user))
        .select_related("category", "created_by", "owner_department")
        .prefetch_related("assignee_rows__user")
        .distinct()
    )
    qdict = _list_querystring(request)
    qs, qdict = _apply_list_filters(qs, request)
    total = qs.count()

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tasks/review_queue.html", {
        "page": page, "total": total,
        "q": qdict["q"],
    })


# ===========================================================================
# Create / edit / clone
# ===========================================================================
@login_required
def task_create(request, category_code=None):
    locked_category = None
    if category_code:
        locked_category = get_object_or_404(
            TaskCategory.objects.filter(is_active=True),
            code=category_code,
        )

    if request.method == "POST":
        form = TaskCreateForm(
            request.POST, user=request.user, locked_category=locked_category,
        )
        if form.is_valid():
            try:
                task = services.create_task(
                    created_by=request.user,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description") or "",
                    category=form.cleaned_data.get("category"),
                    priority=form.cleaned_data.get("priority"),
                    start_date=form.cleaned_data.get("start_date"),
                    due_date=form.cleaned_data.get("due_date"),
                    requires_review=form.cleaned_data.get("requires_review", True),
                    reviewer=form.cleaned_data.get("reviewer") or None,
                    tags=form.cleaned_data.get("tags") or [],
                    estimated_hours=form.cleaned_data.get("estimated_hours"),
                    recurrence_type=form.cleaned_data.get("recurrence_type"),
                    recurrence_rule=form.cleaned_data.get("recurrence_rule") or "",
                    assignees=list(form.cleaned_data.get("assignees") or []),
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, f"Task {task.reference} created as draft.")
                if request.POST.get("_open_now") == "1":
                    try:
                        services.open_task(task, request.user)
                        messages.success(request, "Task opened and assignees notified.")
                    except ValidationError as exc:
                        messages.warning(request, f"Could not open: {'; '.join(exc.messages)}")
                return redirect("tasks:detail", pk=task.pk)
    else:
        form = TaskCreateForm(user=request.user, locked_category=locked_category)

    # No category chosen yet AND no POST — show the picker.
    if category_code is None and request.method == "GET":
        return render(request, "tasks/create_pick.html", {
            "categories": TaskCategory.objects.filter(
                is_active=True
            ).order_by("sort_order", "name"),
        })

    # Otherwise render the full form.
    return render(request, "tasks/create.html", {
        "form": form,
        "locked_category": locked_category,
    })

@login_required
def task_edit(request, pk):
    task = _require_view(request, pk)
    if not services.can_edit_task(task, request.user):
        raise PermissionDenied("You cannot edit this task.")
    if task.state in Task.CLOSED_STATES:
        messages.error(request, "Closed tasks cannot be edited.")
        return redirect("tasks:detail", pk=task.pk)

    if request.method == "POST":
        form = TaskEditForm(request.POST, user=request.user, instance=task)
        if form.is_valid():
            changed = []
            for field, attr in [
                ("title", "title"),
                ("description", "description"),
                ("category", "category"),
                ("priority", "priority"),
                ("start_date", "start_date"),
                ("due_date", "due_date"),
                ("estimated_hours", "estimated_hours"),
                ("requires_review", "requires_review"),
                ("reviewer", "reviewer"),
                ("recurrence_type", "recurrence_type"),
                ("recurrence_rule", "recurrence_rule"),
            ]:
                new_val = form.cleaned_data.get(field)
                if getattr(task, attr) != new_val:
                    setattr(task, attr, new_val)
                    changed.append(attr)

            tags = form.cleaned_data.get("tags") or []
            if task.tags != tags:
                task.tags = tags
                changed.append("tags")

            if changed:
                task.save(update_fields=changed + ["updated_at"])
                services.log_activity(
                    task, "edited", actor=request.user,
                    message=f"Edited: {', '.join(changed)}",
                )
                messages.success(request, "Task updated.")
            else:
                messages.info(request, "No changes made.")
            return redirect("tasks:detail", pk=task.pk)
    else:
        form = TaskEditForm(user=request.user, instance=task)

    current_assignee_ids = ",".join(
        str(uid) for uid in task.assignee_rows.values_list("user_id", flat=True)
    )

    return render(request, "tasks/edit.html", {
        "form": form,
        "task": task,
        "current_assignee_ids": current_assignee_ids,
    })


@login_required
@require_POST
def task_clone(request, pk):
    task = _require_view(request, pk)
    try:
        new = services.clone_task(task, request.user, reset_assignees=True)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        return redirect("tasks:detail", pk=task.pk)
    messages.success(request, f"Cloned to {new.reference}.")
    return redirect("tasks:detail", pk=new.pk)


# ===========================================================================
# Detail
# ===========================================================================
@login_required
def task_detail(request, pk):
    task = _require_view(request, pk)

    activities = task.activities.select_related("actor").order_by("created_at")
    comments = task.comments.select_related("author", "pinned_by").order_by("created_at")
    attachments = task.attachments.select_related("uploaded_by").order_by("-created_at")
    assignee_rows = task.assignee_rows.select_related("user", "assigned_by").order_by("assigned_at")
    subtasks = (
        task.subtasks.select_related("category")
        .prefetch_related("assignee_rows__user")
        .order_by("created_at")
    )
    dependencies = task.dependency_links.select_related("depends_on")
    time_entries = task.time_entries.select_related("user").order_by("-started_at")[:20]
    pinned_comments = [c for c in comments if c.is_pinned]

    my_row = task.assignee_rows.filter(user=request.user).first()
    running_timer = TaskTimeEntry.objects.filter(
        task=task, user=request.user, ended_at__isnull=True,
    ).first()

    user = request.user
    is_creator = task.created_by_id == user.pk
    is_assignee = my_row is not None

    can_edit = services.can_edit_task(task, user)
    can_cancel = services.can_cancel_task(task, user)
    can_delete = services.can_delete_task(task, user)
    can_review = services.can_review(task, user) and task.state == Task.AWAITING_REVIEW
    can_assign = can_edit and task.state in (Task.DRAFT, Task.OPEN, Task.RETURNED)

    # --- Assignee-specific --------------------------------------------------
    # Accept is only for someone who hasn't accepted yet.
    can_accept = bool(
        is_assignee
        and my_row.state == TaskAssignee.ASSIGNED
        and task.state not in Task.CLOSED_STATES
    )

    # Start work only until the row is IN_PROGRESS.
    can_start = bool(
        is_assignee
        and my_row.state in (TaskAssignee.ASSIGNED, TaskAssignee.ACCEPTED)
        and task.state in Task.ACTIVE_STATES
    )

    can_reopen = bool(
        task.state == Task.COMPLETED
        and (
            user.is_superuser
            or is_creator
            or (task.reviewer_id and task.reviewer_id == user.pk)
            or (task.owner_department_id
                and services.is_department_head(user, task.owner_department_id))
        )
    )

    # Timer controls — visible only while this user's row is IN_PROGRESS.
    can_start_timer = bool(
        is_assignee
        and my_row.state == TaskAssignee.IN_PROGRESS
        and running_timer is None
        and task.state not in Task.CLOSED_STATES
    )
    can_stop_timer = bool(
        is_assignee
        and running_timer is not None
        and task.state not in Task.CLOSED_STATES
    )

    can_ask = bool(
        is_assignee
        and task.state in (Task.OPEN, Task.IN_PROGRESS)
        and my_row.state != TaskAssignee.DONE
    )
    can_resume = bool(
        is_assignee
        and task.state in (Task.AWAITING_RESPONSE, Task.RETURNED)
    )
    can_return = bool(
        is_assignee
        and task.state in (Task.OPEN, Task.IN_PROGRESS, Task.AWAITING_RESPONSE)
        and my_row.state != TaskAssignee.DONE
    )
    can_mark_done = bool(
        is_assignee
        and my_row.state in (TaskAssignee.ACCEPTED, TaskAssignee.IN_PROGRESS, TaskAssignee.BLOCKED)
        and task.state in Task.ACTIVE_STATES
    )
    can_resend = can_edit and task.state == Task.RETURNED
    can_split = can_edit and not task.subtasks.exists() and task.state != Task.CANCELLED

    dependencies_met = services.dependencies_met(task)

    can_comment = not task.is_closed
    can_attach = not task.is_closed

    ctx = {
        "task": task,
        "activities": activities,
        "comments": comments,
        "pinned_comments": pinned_comments,
        "attachments": attachments,
        "assignee_rows": assignee_rows,
        "subtasks": subtasks,
        "dependencies": dependencies,
        "time_entries": time_entries,
        "running_timer": running_timer,
        "my_row": my_row,
        "can_comment": can_comment,
        "can_attach": can_attach,
        "is_creator": is_creator,
        "is_assignee": is_assignee,
        "can_edit": can_edit, "can_cancel": can_cancel,
        "can_delete": can_delete, "can_review": can_review,
        "can_assign": can_assign,
        "can_accept": can_accept,
        "can_start": can_start,
        "can_reopen": can_reopen,
        "can_start_timer": can_start_timer,
        "can_stop_timer": can_stop_timer,
        "can_ask": can_ask, "can_resume": can_resume,
        "can_return": can_return, "can_mark_done": can_mark_done,
        "can_resend": can_resend, "can_split": can_split,
        "dependencies_met": dependencies_met,
        "timeline_last_pk": (
            TaskActivity.objects
            .filter(task=task)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first() or 0
        ),
        "subtask_formset": SubtaskFormSet(parent_task=task),
        "assign_role_form": AssignToRoleForm(),
        "start_form": StartWorkForm(),
        "ask_form": AskQuestionForm(),
        "resume_form": ResumeForm(),
        "return_form": ReturnForm(),
        "resend_form": ResendForm(),
        "reopen_form": ReopenForm(),
        "mark_done_form": MarkDoneForm(),
        "review_form": ReviewForm(),
        "cancel_form": CancelForm(),
        "delete_form": DeleteForm(),
        "comment_form": CommentForm(),
        "attach_form": AttachmentForm(),
        "log_time_form": LogTimeForm(),
        "dependency_form": DependencyForm(task=task, user=request.user),
    }
    return render(request, "tasks/detail.html", ctx)


# ===========================================================================
# Action helper — common shape for every POST endpoint below
# ===========================================================================
def _action(request, pk, form_class, service_fn, success_msg,
            view="tasks:detail", form_kwargs=None):
    task = get_object_or_404(_visible_tasks_qs(request.user), pk=pk)
    form = form_class(request.POST, request.FILES, **(form_kwargs or {}))
    if not form.is_valid():
        errs = []
        for field, field_errors in form.errors.items():
            for e in field_errors:
                errs.append(f"{field}: {e}")
        messages.error(request, "; ".join(errs) or "Please check the form.")
        return redirect(view, pk=task.pk)
    try:
        service_fn(task, request.user, **form.cleaned_data)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, success_msg)
    return redirect(view, pk=task.pk)


# ===========================================================================
# Actions
# ===========================================================================
@login_required
@require_POST
def task_open(request, pk):
    task = _require_view(request, pk)
    try:
        services.open_task(task, request.user)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Task opened and assignees notified.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_assign(request, pk):
    """Add one or more assignees. Accepts `assignee` (single) or
    `assignees` (list, from the picker)."""
    task = _require_view(request, pk)
    single = request.POST.get("assignee")
    multi = request.POST.getlist("assignees")

    ids = []
    if single and str(single).isdigit():
        ids.append(int(single))
    for x in multi:
        if str(x).isdigit():
            ids.append(int(x))
    ids = list({*ids})

    if not ids:
        messages.error(request, "Please select at least one assignee.")
        return redirect("tasks:detail", pk=task.pk)

    added, failed = 0, []
    for uid in ids:
        u = User.objects.filter(pk=uid, is_active=True).first()
        if u is None:
            failed.append(f"user {uid} not found")
            continue
        try:
            services.add_assignee(task, request.user, u)
            added += 1
        except (PermissionDenied, ValidationError) as exc:
            failed.append(str(exc))

    if added:
        messages.success(request, f"Added {added} assignee{'s' if added != 1 else ''}.")
    if failed:
        messages.warning(request, "Skipped: " + "; ".join(failed[:3]))
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_sync_assignees(request, pk):
    """Reconcile the task's assignee set with the list submitted."""
    task = _require_view(request, pk)
    if not services.can_edit_task(task, request.user):
        raise PermissionDenied("You cannot edit this task.")
    if task.state in Task.CLOSED_STATES:
        messages.error(request, "Closed tasks cannot be reassigned.")
        return redirect("tasks:detail", pk=task.pk)

    raw = request.POST.getlist("assignees")
    target_ids = {int(x) for x in raw if str(x).isdigit()}

    current_ids = set(task.assignee_rows.values_list("user_id", flat=True))
    to_add = target_ids - current_ids
    to_remove = current_ids - target_ids

    errors = []
    for uid in to_add:
        u = User.objects.filter(pk=uid, is_active=True).first()
        if u is None:
            continue
        try:
            services.add_assignee(task, request.user, u)
        except (PermissionDenied, ValidationError) as exc:
            errors.append(str(exc))
    for uid in to_remove:
        u = User.objects.filter(pk=uid, is_active=True).first()
        if u is None:
            continue
        try:
            services.remove_assignee(task, request.user, u)
        except (PermissionDenied, ValidationError) as exc:
            errors.append(str(exc))

    if errors:
        messages.warning(request, "Some changes were skipped: " + "; ".join(errors[:3]))
    else:
        messages.success(request, "Assignees updated.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_assign_to_role(request, pk):
    task = _require_view(request, pk)
    form = AssignToRoleForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a role code.")
        return redirect("tasks:detail", pk=task.pk)
    try:
        services.assign_to_role(task, request.user, form.cleaned_data["role_code"])
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Auto-assigned to an eligible user.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_unassign(request, pk, user_id):
    task = _require_view(request, pk)
    from accounts.models import User
    assignee = get_object_or_404(User, pk=user_id)
    try:
        services.remove_assignee(task, request.user, assignee)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Assignee removed.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_reassign(request, pk, user_id):
    task = _require_view(request, pk)
    from accounts.models import User
    from_user = get_object_or_404(User, pk=user_id)
    to_id = request.POST.get("to_user")
    to_user = get_object_or_404(User, pk=to_id)
    try:
        services.reassign(task, request.user, from_user, to_user)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Reassigned.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_accept(request, pk):
    return _action(request, pk, StartWorkForm,
                   lambda t, u, note="": services.accept_task(t, u),
                   "Task accepted.")


@login_required
@require_POST
def task_start(request, pk):
    return _action(request, pk, StartWorkForm,
                   lambda t, u, note="": services.start_work(t, u),
                   "Work started.")


@login_required
@require_POST
def task_ask(request, pk):
    return _action(request, pk, AskQuestionForm,
                   lambda t, u, note="": services.ask_question(t, u, note),
                   "Question sent to the creator.")


@login_required
@require_POST
def task_resume(request, pk):
    return _action(request, pk, ResumeForm,
                   lambda t, u, note="": services.resume_work(t, u, note=note),
                   "Resumed.")


@login_required
@require_POST
def task_return(request, pk):
    return _action(request, pk, ReturnForm,
                   lambda t, u, reason="": services.return_to_assigner(t, u, reason),
                   "Task returned to the assigner.")


@login_required
@require_POST
def task_resend(request, pk):
    return _action(request, pk, ResendForm,
                   lambda t, u, note="": services.resend_task(t, u, note=note),
                   "Task resent.")


@login_required
@require_POST
def task_mark_done(request, pk):
    return _action(request, pk, MarkDoneForm,
                   lambda t, u, note="": services.mark_assignee_done(t, u, note=note),
                   "Marked done.")


@login_required
@require_POST
def task_review(request, pk):
    task = _require_view(request, pk)
    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("tasks:detail", pk=task.pk)
    try:
        services.review_task(
            task, request.user,
            approve=form.cleaned_data["approve"],
            notes=form.cleaned_data.get("notes") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(
            request,
            "Task approved." if form.cleaned_data["approve"] else "Sent back for revision.",
        )
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_cancel(request, pk):
    return _action(request, pk, CancelForm,
                   lambda t, u, reason="": services.cancel_task(t, u, reason=reason),
                   "Task cancelled.")


@login_required
@require_POST
def task_reopen(request, pk):
    task = _require_view(request, pk)
    if task.state != Task.COMPLETED:
        messages.error(request, "Only completed tasks can be reopened.")
        return redirect("tasks:detail", pk=task.pk)

    form = ReopenForm(request.POST)
    if not form.is_valid():
        errs = []
        for field, errors in form.errors.items():
            for e in errors:
                errs.append(f"{field}: {e}")
        messages.error(request, "; ".join(errs) or "Please check the form.")
        return redirect("tasks:detail", pk=task.pk)

    try:
        services.reopen_completed_task(
            task, request.user,
            reason=form.cleaned_data["reason"],
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Task reopened. Assignees notified.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_delete(request, pk):
    task = _require_view(request, pk)
    form = DeleteForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(
            f"{k}: {' '.join(v)}" for k, v in form.errors.items()
        ))
        return redirect("tasks:detail", pk=task.pk)
    try:
        services.delete_task(task, request.user, reason=form.cleaned_data["reason"])
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
        return redirect("tasks:detail", pk=task.pk)
    messages.success(request, "Task deleted.")
    return redirect("tasks:list")


@login_required
@require_POST
def task_split(request, pk):
    task = _require_view(request, pk)
    formset = SubtaskFormSet(request.POST, parent_task=task)
    if not formset.is_valid():
        messages.error(request, "Please check the subtask rows.")
        return redirect("tasks:detail", pk=task.pk)
    specs = []
    for form in formset:
        if not form.cleaned_data.get("title"):
            continue
        specs.append({
            "title": form.cleaned_data["title"],
            "description": form.cleaned_data.get("description") or "",
            "assignee": form.cleaned_data.get("assignee"),
            "due_date": form.cleaned_data.get("due_date"),
            "priority": form.cleaned_data.get("priority") or None,
        })
    try:
        services.break_into_subtasks(task, request.user, specs)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, f"Split into {len(specs)} subtask(s).")
    return redirect("tasks:detail", pk=task.pk)


# ===========================================================================
# Dependencies
# ===========================================================================
@login_required
@require_POST
def task_add_dependency(request, pk):
    task = _require_view(request, pk)
    form = DependencyForm(request.POST, task=task, user=request.user)
    if not form.is_valid():
        messages.error(request, "Please pick a valid task.")
        return redirect("tasks:detail", pk=task.pk)
    try:
        services.add_dependency(
            task, form.cleaned_data["depends_on"],
            note=form.cleaned_data.get("note") or "",
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Dependency added.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_remove_dependency(request, pk, dependency_id):
    task = _require_view(request, pk)
    link = get_object_or_404(TaskDependency, pk=dependency_id, task=task)
    services.remove_dependency(task, link.depends_on)
    messages.success(request, "Dependency removed.")
    return redirect("tasks:detail", pk=task.pk)


# ===========================================================================
# Comments / attachments
# ===========================================================================
@login_required
@require_POST
def task_comment(request, pk):
    task = _require_view(request, pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Comment cannot be empty.")
        return redirect("tasks:detail", pk=task.pk)
    services.add_comment(
        task, request.user,
        body=form.cleaned_data["body"],
        internal=form.cleaned_data.get("is_internal", False),
        pinned=form.cleaned_data.get("pinned", False),
    )
    messages.success(request, "Comment posted.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_pin_comment(request, pk, comment_id):
    task = _require_view(request, pk)
    comment = get_object_or_404(TaskComment, pk=comment_id, task=task)
    try:
        services.pin_comment(task, request.user, comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Pinned.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_unpin_comment(request, pk, comment_id):
    task = _require_view(request, pk)
    comment = get_object_or_404(TaskComment, pk=comment_id, task=task)
    try:
        services.unpin_comment(task, request.user, comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Unpinned.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_delete_comment(request, pk, comment_id):
    task = _require_view(request, pk)
    comment = get_object_or_404(TaskComment, pk=comment_id, task=task)
    try:
        services.delete_comment(task, request.user, comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Comment deleted.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_attach(request, pk):
    task = _require_view(request, pk)
    form = AttachmentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Please choose a file.")
        return redirect("tasks:detail", pk=task.pk)
    services.add_attachment(
        task, request.user,
        file=form.cleaned_data["file"],
        description=form.cleaned_data.get("description") or "",
    )
    messages.success(request, "Attachment added.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_delete_attachment(request, pk, attachment_id):
    task = _require_view(request, pk)
    attachment = get_object_or_404(TaskAttachment, pk=attachment_id, task=task)
    try:
        services.delete_attachment(task, request.user, attachment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Attachment deleted.")
    return redirect("tasks:detail", pk=task.pk)


# ===========================================================================
# Time tracking
# ===========================================================================
@login_required
@require_POST
def task_timer_start(request, pk):
    task = _require_view(request, pk)
    try:
        services.start_timer(task, request.user)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Timer started.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_timer_stop(request, pk):
    task = _require_view(request, pk)
    try:
        entry = services.stop_timer(task, request.user)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, f"Logged {entry.hours}h.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_log_time(request, pk):
    task = _require_view(request, pk)
    form = LogTimeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the time range.")
        return redirect("tasks:detail", pk=task.pk)
    try:
        services.log_time(
            task, request.user,
            started_at=form.cleaned_data["started_at"],
            ended_at=form.cleaned_data["ended_at"],
            note=form.cleaned_data.get("note") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Time logged.")
    return redirect("tasks:detail", pk=task.pk)


# ===========================================================================
# Bulk actions
# ===========================================================================
@login_required
@require_POST
def task_bulk_action(request):
    form = BulkActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the bulk form.")
        return redirect("tasks:list")

    raw_ids = form.cleaned_data["task_ids"] or ""
    try:
        ids = [int(x) for x in raw_ids.split(",") if x.strip().isdigit()]
    except Exception:
        ids = []

    if not ids:
        messages.warning(request, "No tasks selected.")
        return redirect("tasks:list")

    action = form.cleaned_data["action"]
    ok = 0
    fail = []

    for tid in ids:
        task = _visible_tasks_qs(request.user).filter(pk=tid).first()
        if task is None:
            fail.append((tid, "not visible"))
            continue
        try:
            if action == "assign":
                if not form.cleaned_data.get("assignee"):
                    fail.append((tid, "no assignee"))
                    continue
                services.add_assignee(task, request.user, form.cleaned_data["assignee"])
            elif action == "cancel":
                services.cancel_task(task, request.user,
                                     reason=form.cleaned_data.get("reason") or "")
            elif action == "delete":
                services.delete_task(task, request.user,
                                     reason=form.cleaned_data.get("reason") or "")
            elif action == "priority":
                if not form.cleaned_data.get("priority"):
                    fail.append((tid, "no priority"))
                    continue
                if not services.can_edit_task(task, request.user):
                    fail.append((tid, "not permitted"))
                    continue
                task.priority = form.cleaned_data["priority"]
                task.save(update_fields=["priority", "updated_at"])
                services.log_activity(
                    task, "priority_changed", actor=request.user,
                    message=f"Priority set to {task.priority}.",
                )
            ok += 1
        except (PermissionDenied, ValidationError) as exc:
            fail.append((tid, str(getattr(exc, "messages", [exc])[0])))

    messages.success(request, f"{ok} task(s) updated.")
    if fail:
        messages.warning(request, f"{len(fail)} task(s) skipped.")
    return redirect(request.META.get("HTTP_REFERER") or "tasks:list")


# ===========================================================================
# JSON: eligible assignees with capacity
# ===========================================================================
@login_required
@require_GET
def eligible_assignees(request):
    """Return users eligible for assignment, filtered by rank, department,
    and category — plus their current task capacity."""
    category_code = request.GET.get("category") or ""
    task_id = request.GET.get("task") or ""
    include_ids = request.GET.getlist("include")

    task = None
    if task_id.isdigit():
        task = _visible_tasks_qs(request.user).filter(pk=int(task_id)).first()

    category = None
    if task and task.category_id:
        category = task.category
    elif category_code:
        category = TaskCategory.objects.filter(code=category_code).first()

    department = None
    if task and task.owner_department_id:
        department = task.owner_department
    elif category and category.handling_department_id:
        department = category.handling_department

    qs = services.assignable_users_for(
        request.user,
        department=department,
        category=category,
        include_ids=include_ids,
    )

    out = []
    for u in qs[:500]:
        used, cap, remaining = services.capacity_for_user(u)
        out.append({
            "id": u.pk,
            "name": u.get_full_name() or u.email,
            "email": u.email,
            "initials": ((u.first_name[:1] or "") + (u.last_name[:1] or "")).upper() or u.email[:1].upper(),
            "role": u.role.name if u.role_id else "",
            "role_code": u.role.code if u.role_id else "",
            "rank": (u.role.rank if u.role_id else 0),
            "department": u.department.name if u.department_id else "",
            "used": used,
            "max": cap,
            "remaining": remaining,
            "at_capacity": remaining <= 0,
        })

    return JsonResponse({"results": out})



# ===========================================================================
# Timeline partial + SSE
# ===========================================================================
@login_required
@require_GET
def task_timeline_partial(request, pk):
    task = _require_view(request, pk)
    activities = task.activities.select_related("actor").order_by("created_at")
    return render(request, "tasks/_timeline.html", {
        "task": task,
        "activities": activities,
    })


@login_required
@require_GET
def task_stream(request, pk):
    """Timeline poll for one task (was an endless SSE stream - review H2)."""
    from core.polling import activity_response

    task = _require_view(request, pk)
    return activity_response(request, TaskActivity.objects.filter(task_id=task.pk))


@login_required
@require_POST
def task_change_state(request, pk):
    """Called by the Kanban drag-and-drop. Maps a target column to the
    appropriate service transition; validates and returns JSON."""
    task = _require_view(request, pk)
    target = (request.POST.get("target") or "").strip().lower()

    try:
        if target == "open":
            if task.state == Task.DRAFT:
                services.open_task(task, request.user)
            elif task.state != Task.OPEN:
                raise ValidationError("Only draft or open tasks belong here.")
        elif target == "active":
            if task.state in (Task.DRAFT, Task.OPEN):
                if task.state == Task.DRAFT:
                    services.open_task(task, request.user)
                services.start_work(task, request.user)
            elif task.state not in (Task.IN_PROGRESS, Task.AWAITING_RESPONSE):
                raise ValidationError("This task can't move to 'In progress'.")
        elif target == "review":
            if task.state in (Task.IN_PROGRESS, Task.AWAITING_RESPONSE, Task.OPEN):
                services.mark_assignee_done(task, request.user)
            elif task.state != Task.AWAITING_REVIEW:
                raise ValidationError("This task can't move to 'Review'.")
        elif target == "done":
            if task.state == Task.AWAITING_REVIEW:
                if not services.can_review(task, request.user):
                    raise PermissionDenied("You are not the reviewer for this task.")
                services.review_task(task, request.user, approve=True)
            elif task.state in Task.ACTIVE_STATES:
                services.mark_assignee_done(task, request.user)
            else:
                raise ValidationError("This task can't be marked done from here.")
        else:
            raise ValidationError("Unknown target column.")
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        return JsonResponse({"ok": False, "error": "; ".join(msgs)}, status=400)
    except Exception:
        logger.exception("Kanban state change failed for task %s", pk)
        return JsonResponse({"ok": False, "error": "Unexpected error."}, status=500)

    return JsonResponse({"ok": True, "state": task.state})


@login_required
def task_create_from_ticket(request, ticket_id):
    """Prefill the task form from a ticket, so departments can push a
    ticket into their own task board as a reusable work item."""
    # Review M11: only tickets the user may open themselves.
    from tickets.views import _visible_tickets_qs
    ticket = get_object_or_404(
        _visible_tickets_qs(request.user).select_related("category", "raised_by", "asset"),
        pk=ticket_id,
    )
    prefill = {
        "title": f"[{ticket.reference}] {ticket.subject}",
        "description": (
            f"Created from ticket {ticket.reference}.\n\n"
            f"Ticket description:\n{ticket.body}\n\n"
            f"Location: {ticket.current_location or '—'}\n"
            f"Priority: {ticket.get_priority_display()}"
        ),
        "priority": ticket.priority,
    }
    form = TaskCreateForm(
        user=request.user,
        initial=prefill,
    )
    return render(request, "tasks/create.html", {
        "form": form,
        "locked_category": None,
        "prefill_source": {
            "type": "ticket",
            "reference": ticket.reference,
            "url": reverse("tickets:detail", args=[ticket.pk]),
        },
    })

