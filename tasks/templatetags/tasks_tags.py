"""Tasks template tags — badges, capacity, kind labels, filter chips."""
from django import template

from tasks.models import Task, TaskAssignee

register = template.Library()


# ---------------------------------------------------------------------------
# Task state chip
# ---------------------------------------------------------------------------
_STATE_CLASS = {
    Task.DRAFT:              "tk-chip-state-draft",
    Task.OPEN:               "tk-chip-state-open",
    Task.IN_PROGRESS:        "tk-chip-state-in_progress",
    Task.AWAITING_RESPONSE:  "tk-chip-state-awaiting_response",
    Task.RETURNED:           "tk-chip-state-returned",
    Task.AWAITING_REVIEW:    "tk-chip-state-awaiting_review",
    Task.COMPLETED:          "tk-chip-state-resolved",
    Task.CANCELLED:          "tk-chip-state-cancelled",
}


@register.filter
def state_chip_class(state):
    return _STATE_CLASS.get(state, "tk-chip-state-open")


_PRIORITY_CLASS = {
    Task.URGENT: "tk-chip-prio-urgent",
    Task.HIGH:   "tk-chip-prio-high",
    Task.NORMAL: "tk-chip-prio-normal",
    Task.LOW:    "tk-chip-prio-low",
}


@register.filter
def priority_chip_class(priority):
    return _PRIORITY_CLASS.get(priority, "tk-chip-prio-normal")


# ---------------------------------------------------------------------------
# Kind humaniser
# ---------------------------------------------------------------------------
_KIND_LABELS = {
    "created":              "Created",
    "created_from_template": "From template",
    "opened":               "Opened",
    "assignee_added":       "Assignee added",
    "assignee_removed":     "Assignee removed",
    "reassigned":           "Reassigned",
    "split":                "Split into subtasks",
    "accepted":             "Accepted",
    "in_progress":          "Work started",
    "question":             "Question asked",
    "resumed":              "Resumed",
    "returned":             "Returned",
    "resent":               "Resent",
    "assignee_done":        "Assignee done",
    "awaiting_review":      "Awaiting review",
    "review_rejected":      "Review rejected",
    "completed":            "Completed",
    "cancelled":            "Cancelled",
    "deleted":              "Deleted",
    "commented":            "Commented",
    "comment_pinned":       "Comment pinned",
    "comment_unpinned":     "Comment unpinned",
    "comment_deleted":      "Comment deleted",
    "attached":             "Attached",
    "attachment_deleted":   "Attachment deleted",
    "timer_started":        "Timer started",
    "timer_stopped":        "Timer stopped",
    "time_logged":          "Time logged",
    "dependency_added":     "Dependency added",
    "dependency_removed":   "Dependency removed",
    "priority_escalated":   "Priority auto-escalated",
    "priority_changed":     "Priority changed",
    "edited":               "Edited",
    "reopened":             "Reopened",
    "recurrence_spawned":   "Spawned from recurring",
}


@register.filter
def humanize_kind(value):
    if not value:
        return ""
    key = str(value).strip().lower()
    return _KIND_LABELS.get(key, key.replace("_", " ").title())


# ---------------------------------------------------------------------------
# Capacity badge
# ---------------------------------------------------------------------------
@register.simple_tag
def capacity_for(user):
    """Return the user's (used, max, remaining)."""
    try:
        from tasks.services import capacity_for_user
        return capacity_for_user(user)
    except Exception:
        return (0, 0, 0)


@register.simple_tag
def assigned_open_count(user):
    if not user or not user.is_authenticated:
        return 0
    return (
        TaskAssignee.objects
        .filter(user=user)
        .exclude(state__in=[TaskAssignee.DONE, TaskAssignee.DECLINED])
        .exclude(task__state__in=[Task.COMPLETED, Task.CANCELLED])
        .count()
    )


@register.simple_tag
def review_queue_count(user):
    if not user or not user.is_authenticated:
        return 0
    from django.db.models import Q
    return (
        Task.objects
        .filter(state=Task.AWAITING_REVIEW)
        .filter(Q(reviewer=user) | Q(reviewer__isnull=True, created_by=user))
        .count()
    )


# ---------------------------------------------------------------------------
# Assignee row colour
# ---------------------------------------------------------------------------
_ASSIGNEE_STATE_CLASS = {
    TaskAssignee.ASSIGNED:    "tk-assignee-assigned",
    TaskAssignee.ACCEPTED:    "tk-assignee-accepted",
    TaskAssignee.IN_PROGRESS: "tk-assignee-in_progress",
    TaskAssignee.DONE:        "tk-assignee-done",
    TaskAssignee.BLOCKED:     "tk-assignee-blocked",
    TaskAssignee.DECLINED:    "tk-assignee-declined",
}


@register.filter
def assignee_state_class(state):
    return _ASSIGNEE_STATE_CLASS.get(state, "tk-assignee-assigned")


# ---------------------------------------------------------------------------
# Container / leaf
# ---------------------------------------------------------------------------
@register.filter
def is_container(task):
    try:
        return task.is_container
    except Exception:
        return False


@register.filter
def initial_letters(user):
    if not user:
        return "?"
    return ((user.first_name[:1] or "") + (user.last_name[:1] or "")).upper() or "?"


@register.simple_tag
def task_column_key(state):
    """Map a task state to its kanban column key."""
    mapping = {
        "draft": "open",
        "open": "open",
        "in_progress": "active",
        "awaiting_response": "active",
        "returned": "review",
        "awaiting_review": "review",
        "completed": "done",
        "cancelled": "done",
    }
    return mapping.get(state, "open")

# ---------------------------------------------------------------------------
# Dashboard widget
# ---------------------------------------------------------------------------
@register.inclusion_tag("tasks/_dashboard_widget.html", takes_context=True)
def tasks_dashboard_widget(context):
    """Home dashboard widget — open tasks assigned to / created by the user."""
    request = context.get("request")
    user = request.user if request else None
    if not user or not user.is_authenticated:
        return {"visible": False}

    from tasks.models import Task, TaskAssignee
    from tasks.services import capacity_for_user

    assigned_open = (
        Task.objects
        .filter(assignee_rows__user=user)
        .exclude(state__in=Task.CLOSED_STATES)
        .select_related("category")
        .prefetch_related("assignee_rows__user")
        .distinct()
        .order_by("due_date", "-priority")[:5]
    )
    created_open = (
        Task.objects
        .filter(created_by=user)
        .exclude(state__in=Task.CLOSED_STATES)
        .select_related("category")
        .order_by("-created_at")[:5]
    )
    awaiting_review = (
        Task.objects
        .filter(state=Task.AWAITING_REVIEW)
        .filter(Q(reviewer=user) | Q(reviewer__isnull=True, created_by=user))
        .order_by("-updated_at")[:3]
    )

    assigned_count = (
        Task.objects
        .filter(assignee_rows__user=user)
        .exclude(state__in=Task.CLOSED_STATES)
        .distinct()
        .count()
    )
    created_count = (
        Task.objects
        .filter(created_by=user)
        .exclude(state__in=Task.CLOSED_STATES)
        .count()
    )
    review_count = (
        Task.objects
        .filter(state=Task.AWAITING_REVIEW)
        .filter(Q(reviewer=user) | Q(reviewer__isnull=True, created_by=user))
        .count()
    )

    used, cap, remaining = capacity_for_user(user)

    # Phase C (H16, H17): my completed count this month, group vs individual.
    from tasks.performance import month_start
    done = (Task.objects.filter(assignee_rows__user=user, state=Task.COMPLETED,
                                completed_at__gte=month_start()).distinct())
    completed_group = done.filter(division__isnull=False).count()
    completed_month = done.count()

    return {
        "completed_month": completed_month,
        "completed_group": completed_group,
        "completed_since": month_start().date().isoformat(),
        "visible": bool(assigned_count or created_count or review_count),
        "assigned_open": list(assigned_open),
        "created_open": list(created_open),
        "awaiting_review": list(awaiting_review),
        "assigned_count": assigned_count,
        "created_count": created_count,
        "review_count": review_count,
        "capacity_used": used,
        "capacity_max": cap,
        "capacity_remaining": remaining,
    }



# ---------------------------------------------------------------------------
# QA task links (Phase 4): "Open in QA" for tasks the QA system manages
# ---------------------------------------------------------------------------
@register.filter
def qa_open_url(task):
    """The QA screen for a task linked to a QA stage, or "" (not linked)."""
    try:
        link = task.qa_link
    except Exception:  # no link (RelatedObjectDoesNotExist) or the QA apps are absent
        return ""
    try:
        from qa_core.task_bridge import qa_url

        return qa_url(link)
    except Exception:
        return ""
