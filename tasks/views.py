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
from qa_core.ui import htmx_fragment  # Phase 0 (N2): fragment URLs
from accounts.selectors import staff_users  # Phase 0 (H22): staff pickers
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
    """ES, superusers and holders of tasks.view_all_tasks (H1, H3)."""
    from work.visibility import can_see_all
    return can_see_all(user, Task)


def _visible_tasks_qs(user):
    """The tasks this user may see - one rule for web, API, search and
    dashboards (C.1, ``work.visibility.visible_q``): involved, lower-ranked
    staff of the same department, the director's whole department, the
    division head's routed tasks; ES / view_all_tasks see everything."""
    from work.visibility import visible_q
    base = Task.objects.select_related(
        "category", "created_by", "owner_department", "parent", "division",
    )
    q = visible_q(user, Task)
    return base.filter(q) if q else base


def task_list_base(user, scope):
    """Phase 9: the base queryset of each task list tab, shared by the
    website views below and the mobile API (scope: all / created /
    assigned / review)."""
    if scope == "created":
        return Task.objects.filter(created_by=user).select_related("category", "owner_department")
    if scope == "assigned":
        return (
            Task.objects
            .filter(assignee_rows__user=user)
            .exclude(state__in=Task.CLOSED_STATES)
            .select_related("category", "created_by", "owner_department")
            .prefetch_related("assignee_rows")
            .distinct()
        )
    if scope == "review":
        return (
            Task.objects
            .filter(state=Task.AWAITING_REVIEW)
            .filter(Q(reviewer=user) | Q(reviewer__isnull=True, created_by=user))
            .select_related("category", "created_by", "owner_department")
            .prefetch_related("assignee_rows__user")
            .distinct()
        )
    return _visible_tasks_qs(user)


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
        "kind": request.GET.get("kind", ""),
        "division": request.GET.get("division", ""),
        "since": request.GET.get("since", ""),
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

    # Phase C (H17, I3): group vs individual, a division, completed since a date.
    kind = request.GET.get("kind") or ""
    if kind == "group":
        qs = qs.filter(division__isnull=False)
    elif kind == "individual":
        qs = qs.filter(division__isnull=True)
    division_id = request.GET.get("division") or ""
    if division_id.isdigit():
        qs = qs.filter(division_id=int(division_id))
    since = request.GET.get("since") or ""
    if since:
        from django.utils.dateparse import parse_date
        d = parse_date(since)
        if d:
            qs = qs.filter(completed_at__date__gte=d)

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
            **_phase_c_list_ctx(request, qdict),
            "view_mode": "kanban",
            "columns": columns,
            "total": total,
            "bulk_eligible_users": staff_users().order_by("first_name", "last_name", "email",)[:500],
            "q": qdict["q"], "state_filter": qdict["state"],
            "cat_filter": qdict["category"], "prio_filter": qdict["priority"],
            "scope_filter": qdict["scope"],
            "sort_key": sort_key, "sort_dir": sort_dir,
            "categories": TaskCategory.objects.filter(is_active=True).order_by("sort_order", "name"),
            "state_choices": Task.STATE_CHOICES,
            "priority_choices": Task.PRIORITY_CHOICES,
                    "assignee_filter": qdict.get("assignee", ""),
        "all_users": staff_users().order_by("first_name", "last_name")[:300],
        })

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tasks/list.html", {
        **_phase_c_list_ctx(request, qdict),
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


def _phase_c_list_ctx(request, qdict):
    """Phase C: group / division / since filters and the links to the
    division queue and the performance page."""
    import org

    from . import performance

    user = request.user
    return {
        "kind_filter": qdict.get("kind", ""),
        "division_filter": qdict.get("division", ""),
        "since_filter": qdict.get("since", ""),
        "show_division_queue": bool(org.is_director(user) or services.is_override(user)
                                    or user.headed_divisions.filter(is_active=True).exists()),
        "show_performance": performance.can_view(user),
    }


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
    qs = task_list_base(request.user, "created")
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
        "draft_count": Task.objects.filter(created_by=request.user, state=Task.DRAFT).count(),
        "q": qdict["q"], "state_filter": qdict["state"],
        "sort_key": sort_key, "sort_dir": sort_dir,
        "state_choices": Task.STATE_CHOICES,
    })


@login_required
def assigned_to_me(request):
    qs = task_list_base(request.user, "assigned")
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
    qs = task_list_base(request.user, "review")
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
                # Phase 9: a task made from a chat message links back to it.
                if request.GET.get("from_message"):
                    from messaging.chat_services import link_message_to_task
                    link_message_to_task(request.GET["from_message"], task, request.user)
                if request.POST.get("_open_now") == "1":
                    try:
                        services.open_task(task, request.user)
                        messages.success(request, "Task opened and assignees notified.")
                    except ValidationError as exc:
                        messages.warning(request, f"Could not open: {'; '.join(exc.messages)}")
                return redirect("tasks:detail", pk=task.pk)
    else:
        # Phase 9: accept ?title=&description=&from_message= to prefill (chat
        # "Make a task"); with a prefill the category is chosen in the form.
        prefill = {k: request.GET[k][:5000] for k in ("title", "description") if request.GET.get(k)}
        form = TaskCreateForm(user=request.user, locked_category=locked_category,
                              initial=prefill or None)
        if prefill and category_code is None:
            return render(request, "tasks/create.html", {
                "form": form, "locked_category": None,
                "prefill_source": {"type": "message", "id": request.GET.get("from_message")},
            })

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
        "reviewer_name": (task.reviewer.get_full_name() or task.reviewer.email) if task.reviewer_id else "",
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

    # Phase 0 (H18): internal notes only for the people involved.
    can_internal_notes = services.can_see_internal(task, request.user)
    activities = services.visible_activities(task, request.user).select_related("actor").order_by("created_at")
    comments = (services.visible_comments(task, request.user)
                .select_related("author", "pinned_by").order_by("created_at"))
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

    # Phase 9: the permission flags live in tasks.services.available_actions
    # (shared with the mobile API).
    flags = services.available_actions(task, request.user, my_row=my_row,
                                       running_timer=running_timer)
    is_creator, is_assignee = flags["is_creator"], flags["is_assignee"]
    can_edit, can_cancel, can_delete = flags["can_edit"], flags["can_cancel"], flags["can_delete"]
    can_review, can_assign = flags["can_review"], flags["can_assign"]
    can_accept, can_start, can_reopen = flags["can_accept"], flags["can_start"], flags["can_reopen"]
    can_start_timer, can_stop_timer = flags["can_start_timer"], flags["can_stop_timer"]
    can_ask, can_resume, can_return = flags["can_ask"], flags["can_resume"], flags["can_return"]
    can_mark_done, can_resend, can_split = flags["can_mark_done"], flags["can_resend"], flags["can_split"]
    can_comment, can_attach = flags["can_comment"], flags["can_attach"]
    qa_link_url, qa_link_open = flags["qa_link_url"], flags["qa_link_open"]

    dependencies_met = services.dependencies_met(task)

    # Phase C (H5, H14, H15): people with their open-task load, one query.
    from work.visibility import load_info, with_open_load
    default_max = services._cfg_int("tasks.default_max_open", 5)
    loads = {u.pk: load_info(u, default_max=default_max) for u in with_open_load(
        User.objects.filter(pk__in=[r.user_id for r in assignee_rows]).select_related("role"))}
    people = [{"row": r, "load": loads.get(r.user_id)} for r in assignee_rows]
    public_comments = [c for c in comments if not c.is_internal]
    internal_comments = [c for c in comments if c.is_internal] if can_internal_notes else []
    involved = []
    for u in [task.created_by, task.reviewer] + [r.user for r in assignee_rows]:
        if u is not None and u not in involved:
            involved.append(u)
    from . import division as div_services
    routing = div_services.divisions_for_routing(request.user, task) if flags["can_route_division"] else []
    division_members = (list(div_services.division_members(task.division))
                        if flags["can_division_accept"] else [])
    subtask_list = list(subtasks)

    ctx = {
        "task": task,
        "people": people,
        "reviewer_name": (task.reviewer.get_full_name() or task.reviewer.email) if task.reviewer_id else "",
        "public_comments": public_comments,
        "internal_comments": internal_comments,
        "involved": involved,
        "routing": routing,
        "division_members": division_members,
        "subtasks_done": sum(1 for t in subtask_list if t.state == Task.COMPLETED),
        "flags": flags,
        "activities": activities,
        "comments": comments,
        "pinned_comments": pinned_comments,
        "can_internal_notes": can_internal_notes,
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
        "qa_link_url": qa_link_url, "qa_link_open": qa_link_open,
        "timeline_last_pk": (
            TaskActivity.objects
            .filter(task=task)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first() or 0
        ),
        "subtask_formset": SubtaskFormSet(parent_task=task, user=request.user),
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
        u = staff_users().filter(pk=uid).first()
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
        u = staff_users().filter(pk=uid).first()
        if u is None:
            continue
        try:
            services.add_assignee(task, request.user, u)
        except (PermissionDenied, ValidationError) as exc:
            errors.append(str(exc))
    for uid in to_remove:
        u = staff_users().filter(pk=uid).first()
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
    formset = SubtaskFormSet(request.POST, parent_task=task, user=request.user)
    if not formset.is_valid():
        errs = [f"{k}: {' '.join(v)}" for f in formset.forms for k, v in f.errors.items()]
        errs += list(formset.non_form_errors())
        messages.error(request, "Please check the subtask: " + ("; ".join(errs[:3]) or "missing title"))
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
            "department": form.cleaned_data.get("department"),
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
    try:
        services.add_comment(
            task, request.user,
            body=form.cleaned_data["body"],
            internal=form.cleaned_data.get("is_internal", False),
            pinned=form.cleaned_data.get("pinned", False),
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
        return redirect("tasks:detail", pk=task.pk)
    messages.success(request, "Comment posted.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_pin_comment(request, pk, comment_id):
    task = _require_view(request, pk)
    comment = get_object_or_404(services.visible_comments(task, request.user), pk=comment_id)
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
    comment = get_object_or_404(services.visible_comments(task, request.user), pk=comment_id)
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
    comment = get_object_or_404(services.visible_comments(task, request.user), pk=comment_id)
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

    if request.GET.get("mode") == "reviewer":
        # H20: the reviewer picker - anyone in my department (any rank) or
        # the ES; everyone for the ES / superuser. Load shown the same way.
        qs = services.reviewer_choices(request.user, include_ids=include_ids)
    else:
        qs = services.assignable_users_for(
            request.user,
            department=department,
            category=category,
            include_ids=include_ids,
        )

    from work.visibility import load_info

    default_max = services._cfg_int("tasks.default_max_open", 5)
    out = []
    for u in qs[:500]:  # open_load is annotated: no query per person (T-PERF-1)
        info = load_info(u, default_max=default_max)
        out.append({
            "id": u.pk,
            "name": u.get_full_name() or u.email,
            "email": u.email,
            "initials": ((u.first_name[:1] or "") + (u.last_name[:1] or "")).upper() or u.email[:1].upper(),
            "role": u.role.name if u.role_id else "",
            "role_code": u.role.code if u.role_id else "",
            "rank": (u.role.rank if u.role_id else 0),
            "department": u.department.name if u.department_id else "",
            "used": info["open"],
            "max": info["max"],
            "remaining": info["remaining"],
            "at_capacity": info["full"],
            "load_label": info["label"],
        })

    return JsonResponse({"results": out})



# ===========================================================================
# Timeline partial + SSE
# ===========================================================================
@login_required
@require_GET
@htmx_fragment("tasks:detail")
def task_timeline_partial(request, pk):
    task = _require_view(request, pk)
    activities = services.visible_activities(task, request.user).select_related("actor").order_by("created_at")
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
    return activity_response(request, services.visible_activities(
        task, request.user, TaskActivity.objects.filter(task_id=task.pk)))


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



# ===========================================================================
# Discard my drafts (Phase 8)
# ===========================================================================
@login_required
@require_POST
def task_discard_drafts(request):
    """Delete the user's OWN draft tasks (all of them, or the posted ids).
    Drafts never reached anyone, so no reason is asked; the deletion is
    still logged on the task timeline and in the audit log."""
    from django.urls import reverse

    from core import bulk as bulk_util

    fallback = reverse("tasks:my_tasks")
    drafts = Task.objects.filter(created_by=request.user, state=Task.DRAFT)
    ids = bulk_util.parse_ids(request)
    if ids:
        drafts = drafts.filter(pk__in=ids)
    done, skipped = [], []
    for task in drafts:
        pk = task.pk  # delete() clears it
        try:
            services.delete_task(task, request.user, reason="Draft discarded by its creator.")
            done.append(pk)
        except (PermissionDenied, ValidationError) as exc:
            skipped.append({"id": pk, "reason": str(getattr(exc, "messages", [exc])[0])})
    skipped += [{"id": i, "reason": "Only your own drafts can be discarded."}
                for i in ids if i not in done and i not in {s["id"] for s in skipped}]
    return bulk_util.respond(request, action="delete", done=done, skipped=skipped, fallback=fallback,
                             message=(f"{bulk_util.plural(len(done), 'draft')} discarded."
                                      if done else "There were no drafts to discard."))


# ===========================================================================
# Phase C (H12, H14, H15): accept and start, per-person confirm / return
# ===========================================================================
def _run(request, task, fn, ok_msg):
    try:
        fn()
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
    else:
        messages.success(request, ok_msg)
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_accept_start(request, pk):
    task = _require_view(request, pk)
    return _run(request, task, lambda: services.accept_and_start(task, request.user),
                "Accepted. Your timer is running.")


def _assignee_on(task, user_id):
    row = get_object_or_404(task.assignee_rows.select_related("user"), user_id=user_id)
    return row.user


@login_required
@require_POST
def task_confirm_part(request, pk, user_id):
    task = _require_view(request, pk)
    who = _assignee_on(task, user_id)
    return _run(request, task, lambda: services.confirm_assignee_part(task, request.user, who),
                f"Confirmed {who.get_full_name() or who.email}'s part.")


@login_required
@require_POST
def task_return_part(request, pk, user_id):
    task = _require_view(request, pk)
    who = _assignee_on(task, user_id)
    reason = (request.POST.get("reason") or "").strip()
    return _run(request, task, lambda: services.return_assignee_part(task, request.user, who, reason),
                f"Returned {who.get_full_name() or who.email}'s part. Only they were notified.")


@login_required
@require_POST
def task_confirm_all(request, pk):
    task = _require_view(request, pk)
    notes = (request.POST.get("notes") or "").strip()
    return _run(request, task, lambda: services.confirm_all_parts(task, request.user, notes=notes),
                "Everyone's work confirmed. The task is complete.")


# ===========================================================================
# Phase C (H23, H24, B8): division routing and the division queue
# ===========================================================================
def _division_redirect(request, task):
    nxt = request.POST.get("next") or ""
    if nxt == "queue":
        return redirect("tasks:division_queue")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_route_division(request, pk):
    from org.models import Division

    from . import division as div_services

    task = _require_view(request, pk)
    division = Division.objects.filter(pk=request.POST.get("division") or 0).first()
    if division is None:
        messages.error(request, "Choose a division.")
        return redirect("tasks:detail", pk=task.pk)
    members = list(staff_users().filter(pk__in=[x for x in request.POST.getlist("members") if x.isdigit()]))
    try:
        div_services.route_to_division(task, request.user, division, members=members,
                                       note=request.POST.get("note") or "")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
    else:
        messages.success(request, f"Routed to {division.name}. The head must accept it first.")
    return redirect("tasks:detail", pk=task.pk)


@login_required
@require_POST
def task_division_accept(request, pk):
    from . import division as div_services

    task = _require_view(request, pk)
    members = list(staff_users().filter(pk__in=[x for x in request.POST.getlist("members") if x.isdigit()]))
    try:
        added = div_services.accept_division_task(task, request.user, members=members)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
    else:
        messages.success(request, f"Accepted for the division; {len(added)} member(s) added.")
    return _division_redirect(request, task)


@login_required
@require_POST
def task_division_decline(request, pk):
    from . import division as div_services

    task = _require_view(request, pk)
    try:
        div_services.decline_division_task(task, request.user, request.POST.get("reason") or "")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
        return _division_redirect(request, task)
    messages.success(request, "Declined. The director has been told why.")
    return redirect("tasks:division_queue")


@login_required
@require_POST
def task_division_complete(request, pk):
    from . import division as div_services

    task = _require_view(request, pk)
    try:
        div_services.complete_division_task(task, request.user, note=request.POST.get("note") or "")
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
    else:
        messages.success(request, "Group task marked complete. Everyone involved was notified.")
    return _division_redirect(request, task)


@login_required
def division_queue(request):
    from . import division as div_services

    import org

    queues = div_services.queue_for(request.user)
    if not queues and not (org.is_director(request.user) or services.is_override(request.user)):
        raise PermissionDenied("The division queue is for division heads and directors.")
    return render(request, "tasks/division_queue.html", {
        "queues": queues,
        "pending_total": sum(len(q["pending"]) for q in queues),
    })


# ===========================================================================
# Phase C (H17, I3): performance counts
# ===========================================================================
@login_required
def performance(request):
    import csv

    from staff.models import Department
    from work.visibility import can_see_all

    from . import performance as perf

    dept_id = request.GET.get("department") or ""
    data = perf.build(request.user, int(dept_id) if dept_id.isdigit() else None)
    if request.GET.get("format") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="task-performance.csv"'
        w = csv.writer(resp)
        w.writerow(["Person", "Division", "Open individual", "Open group", "Completed individual (month)",
                    "Completed group (month)", "Average days to complete"])
        for r in data["people"]:
            w.writerow([r["user"].get_full_name() or r["user"].email, getattr(r["division"], "name", ""),
                        r["open_individual"], r["open_group"], r["done_individual"], r["done_group"],
                        r["avg_days"] if r["avg_days"] is not None else ""])
        w.writerow([])
        w.writerow(["Division", "Open group tasks", "Capacity", "Waiting for head", "Completed (month)",
                    "Average days to complete"])
        for r in data["divisions"]:
            w.writerow([r["division"].name, r["open"], r["max"], r["pending"], r["done_month"],
                        r["avg_days"] if r["avg_days"] is not None else ""])
        return resp
    data["since"] = data["month_start"].date().isoformat()
    data["departments"] = Department.objects.order_by("name") if can_see_all(request.user, Task) else None
    return render(request, "tasks/performance.html", data)


# ===========================================================================
# Phase C (H19): the task record PDF
# ===========================================================================
@login_required
@require_GET
def task_report_pdf(request, pk):
    from .pdf import render_task_record

    task = _require_view(request, pk)
    pdf = render_task_record(task, request.user)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{task.reference}.pdf"'
    return resp


@login_required
@require_POST
def task_set_reviewer(request, pk):
    task = _require_view(request, pk)
    raw = request.POST.get("reviewer") or ""
    reviewer = staff_users().filter(pk=raw).first() if raw.isdigit() else None
    return _run(request, task, lambda: services.set_reviewer(task, request.user, reviewer), "Reviewer saved.")
