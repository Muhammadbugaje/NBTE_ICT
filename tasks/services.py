"""Tasks service layer — state machine, capacity, subtasks, dependencies,
time tracking, reminders, recurrence, escalation.

This is the ONLY place task state mutates. Views call these functions and
never touch models directly.

Public API groups:

    Reference & log
        generate_reference()
        log_activity(task, kind, actor, message, metadata)

    Rank & capacity
        rank_of(user) -> int
        capacity_for_user(user) -> (used, max, remaining)
        is_department_head(user, dept_id) -> bool

    Permission matrix
        is_task_creator(task, user)
        can_edit_task(task, user)
        can_cancel_task(task, user)
        can_delete_task(task, user)
        can_review(task, user)
        can_assign_to(assigner, assignee, category)

    Creation
        create_task(...)                        -> Task (DRAFT)
        create_from_template(template, user, **overrides) -> Task
        clone_task(task, user, reset_assignees=True) -> Task

    Opening & assignment
        open_task(task, user)
        add_assignee(task, user, assignee)
        assign_to_role(task, user, role_code, department=None)
        bulk_assign(task_ids, user, assignee)   -> (assigned, failed)
        remove_assignee(task, user, assignee)
        reassign(task, user, from_user, to_user)

    Container / subtasks
        break_into_subtasks(task, user, specs)  -> [Task]
        refresh_container_state(task)

    Leaf transitions
        accept_task(task, user)
        start_work(task, user)
        ask_question(task, user, note)
        resume_work(task, user, note="")
        return_to_assigner(task, user, reason)
        resend_task(task, user, note="")
        mark_assignee_done(task, user, note="")
        review_task(task, user, approve, notes="")
        cancel_task(task, user, reason="")
        delete_task(task, user, reason)

    Time tracking
        start_timer(task, user)
        stop_timer(task, user)
        log_time(task, user, started_at, ended_at, note="")

    Dependencies
        add_dependency(task, depends_on, note="")
        remove_dependency(task, depends_on)
        dependencies_met(task) -> bool

    Content
        add_comment(task, user, body, internal=False, pinned=False)
        pin_comment(task, user, comment)
        unpin_comment(task, user, comment)
        delete_comment(task, user, comment)
        add_attachment(task, user, file, description="")
        delete_attachment(task, user, attachment)

    Celery Beat
        reminder_sweep() -> dict
        spawn_recurring() -> int
        escalate_overdue_priorities() -> int
"""
import json
import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User

from notifications.models import Notification
from notifications.services import notify

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

logger = logging.getLogger(__name__)


# ===========================================================================
# Config helpers
# ===========================================================================
def _cfg_int(key, default):
    try:
        import config as _cfg
        return int(_cfg.get(key, default))
    except Exception:
        return default


def _cfg_json(key, default):
    try:
        import config as _cfg
        raw = _cfg.get(key, default)
    except Exception:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return default
    return default


# ===========================================================================
# Reference allocation
# ===========================================================================
@transaction.atomic
def generate_reference():
    """Allocate TSK-YYYY-NNNNN. Retries safe under concurrency."""
    year = date.today().year
    prefix = f"TSK-{year}-"
    last = (
        Task.objects
        .select_for_update()
        .filter(reference__startswith=prefix)
        .order_by("-reference")
        .first()
    )
    if last:
        try:
            seq = int(last.reference.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


# ===========================================================================
# Activity log
# ===========================================================================
def log_activity(task, kind, actor=None, message="", metadata=None):
    return TaskActivity.objects.create(
        task=task,
        kind=kind,
        actor=actor,
        message=(message or "")[:500],
        metadata=metadata or {},
    )


# ===========================================================================
# Rank & capacity
# ===========================================================================
def rank_of(user):
    if not user or not user.is_authenticated:
        return 0
    if user.is_superuser:
        return 1000
    if user.role_id:
        return user.role.rank or 0
    return 0


def capacity_for_user(user):
    """(used, max, remaining) — based on non-terminal assignee rows on
    non-terminal tasks."""
    if not user or not user.is_authenticated:
        return (0, 0, 0)

    used = (
        TaskAssignee.objects
        .filter(user=user)
        .exclude(state__in=[TaskAssignee.DONE, TaskAssignee.DECLINED])
        .exclude(task__state__in=[Task.COMPLETED, Task.CANCELLED])
        .count()
    )

    max_open = None
    if user.role_id:
        max_open = user.role.max_open_tasks
    if not max_open:
        max_open = _cfg_int("tasks.default_max_open", 5)

    return (used, max_open, max(0, max_open - used))


def is_department_head(user, dept_id):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.department_id or user.department_id != dept_id:
        return False
    if not user.role_id:
        return False
    return user.role.code in (
        "executive-secretary",
        "director", "deputy-director",
        "office-head-admin", "office-head-ts", "office-head-dev",
        "division-head",
    )


# ===========================================================================
# Permission matrix
# ===========================================================================
def is_task_creator(task, user):
    return user.is_authenticated and task.created_by_id == user.pk


def can_edit_task(task, user):
    if user.is_superuser:
        return True
    if is_task_creator(task, user):
        return True
    return is_department_head(user, task.owner_department_id)


def can_cancel_task(task, user):
    """Creator, owning-dept head, or strictly higher rank than creator."""
    if user.is_superuser:
        return True
    if is_task_creator(task, user):
        return True
    if is_department_head(user, task.owner_department_id):
        return True
    return rank_of(user) > rank_of(task.created_by)


def can_delete_task(task, user):
    """Stricter than cancel — creator, superuser, or strictly higher rank."""
    if user.is_superuser:
        return True
    if is_task_creator(task, user):
        return True
    return rank_of(user) > rank_of(task.created_by)


def can_review(task, user):
    if user.is_superuser:
        return True
    if task.reviewer_id and task.reviewer_id == user.pk:
        return True
    if task.created_by_id == user.pk:
        return True
    return is_department_head(user, task.owner_department_id)

def assignable_users_for(assigner, *, department=None, category=None,
                         include_ids=None):
    """Queryset of users `assigner` may assign work to."""
    from accounts.models import User
    from django.db.models import Q

    if not assigner or not assigner.is_authenticated:
        return User.objects.none()

    qs = User.objects.filter(is_active=True).select_related("role", "department")

    if department is not None:
        qs = qs.filter(department=department)

    qs = qs.exclude(role__code="executive-secretary")

    if not assigner.is_superuser:
        my_rank = rank_of(assigner)
        if my_rank <= 0:
            qs = User.objects.none()
        else:
            qs = qs.filter(Q(role__rank__lt=my_rank) | Q(role__isnull=True))

    if category and category.allowed_role_codes:
        qs = qs.filter(role__code__in=category.allowed_role_codes)

    if include_ids:
        extra = User.objects.filter(
            is_active=True,
            pk__in=[int(x) for x in include_ids if str(x).isdigit()],
        ).select_related("role", "department")
        qs = (qs | extra).distinct()

    return qs.order_by("-role__rank", "first_name", "last_name", "email")

def can_assign_to(assigner, assignee, category):
    """Raise ValidationError if the assignment is not permitted."""
    if assignee is None or not assignee.is_active:
        raise ValidationError("Invalid assignee.")

    if assignee.role_id and assignee.role.code == "executive-secretary":
        raise ValidationError("The Executive Secretary cannot be assigned tasks.")

    if not assigner.is_superuser:
        my_rank = rank_of(assigner)
        their_rank = rank_of(assignee)
        if my_rank <= their_rank:
            raise ValidationError(
                f"You cannot assign tasks to "
                f"{assignee.get_full_name() or assignee.email} — "
                f"their rank is equal to or higher than yours."
            )

    if category and category.allowed_role_codes:
        if not assignee.role_id:
            raise ValidationError(
                f"{assignee.get_full_name() or assignee.email} has no role."
            )
        if assignee.role.code not in category.allowed_role_codes:
            raise ValidationError(
                f"{assignee.get_full_name() or assignee.email} has role "
                f"'{assignee.role.name}' which is not permitted for "
                f"'{category.name}'."
            )

    if category and category.min_rank_to_assign:
        if rank_of(assigner) < category.min_rank_to_assign:
            raise ValidationError(
                f"Your rank is too low to create tasks in '{category.name}'."
            )
    return True    

# ===========================================================================
# Creation
# ===========================================================================
@transaction.atomic
def create_task(*, created_by, title, description="", category=None,
                owner_department=None, priority=None, start_date=None,
                due_date=None, requires_review=None, reviewer=None,
                parent=None, tags=None, estimated_hours=None,
                recurrence_type=None, recurrence_rule="",
                assignees=None):
    """Create a task in DRAFT. `assignees` is an optional list of Users —
    attached immediately, notified when `open_task()` fires."""
    title = (title or "").strip()
    if len(title) < 3:
        raise ValidationError("Title must be at least 3 characters.")

    if category and category.min_rank_to_assign:
        if rank_of(created_by) < category.min_rank_to_assign:
            raise ValidationError(
                f"Your rank is too low to create tasks in '{category.name}'."
            )

    # Category defaults
    if priority is None:
        priority = category.default_priority if category else Task.NORMAL
    if requires_review is None:
        requires_review = category.requires_review if category else True
    if due_date is None and category and category.default_due_days:
        due_date = timezone.localdate() + timedelta(days=category.default_due_days)

    if recurrence_type is None:
        recurrence_type = Task.RECURRENCE_NONE

    # Allocate reference (retry on race)
    task = None
    last_err = None
    for _ in range(5):
        ref = generate_reference()
        try:
            with transaction.atomic():
                task = Task.objects.create(
                    reference=ref,
                    created_by=created_by,
                    title=title,
                    description=(description or "").strip(),
                    category=category,
                    owner_department=(
                        owner_department
                        or getattr(category, "handling_department", None)
                    ),
                    priority=priority,
                    start_date=start_date,
                    due_date=due_date,
                    requires_review=requires_review,
                    reviewer=reviewer,
                    parent=parent,
                    tags=list(tags or []),
                    estimated_hours=estimated_hours,
                    recurrence_type=recurrence_type,
                    recurrence_rule=(recurrence_rule or "").strip(),
                    is_recurring=(recurrence_type != Task.RECURRENCE_NONE),
                    state=Task.DRAFT,
                )
            break
        except IntegrityError as exc:
            last_err = exc
            continue

    if task is None:
        logger.exception("Could not allocate task reference: %s", last_err)
        raise ValidationError(
            "Could not allocate a task reference right now. Try again."
        )

    log_activity(
        task, "created", actor=created_by,
        message=f"Task created: {task.title}",
        metadata={
            "category": category.code if category else None,
            "priority": priority,
            "parent_pk": parent.pk if parent else None,
        },
    )

    if assignees:
        for a in assignees:
            try:
                _attach_assignee(task, created_by, a, silent=True)
            except (ValidationError, PermissionDenied) as exc:
                logger.warning("Could not attach assignee %s: %s", a, exc)

    return task


@transaction.atomic
def create_from_template(*, template_task, created_by, **overrides):
    """Clone a reusable task with state reset; apply field overrides."""
    new = clone_task(template_task, created_by, reset_assignees=False)
    changed = []
    for field in (
        "title", "description", "priority", "start_date", "due_date",
        "reviewer", "estimated_hours",
    ):
        if field in overrides and overrides[field] is not None:
            setattr(new, field, overrides[field])
            changed.append(field)
    if changed:
        new.save(update_fields=changed + ["updated_at"])

    log_activity(
        new, "created_from_template", actor=created_by,
        message=f"Created from template {template_task.reference}",
        metadata={"template_pk": template_task.pk},
    )
    return new


@transaction.atomic
def clone_task(task, user, reset_assignees=True):
    """Deep-copy structure, reset runtime state."""
    new = Task.objects.create(
        reference=generate_reference(),
        created_by=user,
        title=task.title,
        description=task.description,
        category=task.category,
        owner_department=task.owner_department,
        priority=task.priority,
        requires_review=task.requires_review,
        tags=list(task.tags or []),
        estimated_hours=task.estimated_hours,
        recurrence_type=task.recurrence_type,
        recurrence_rule=task.recurrence_rule,
        is_recurring=task.is_recurring,
        cloned_from=task,
        state=Task.DRAFT,
    )
    log_activity(new, "created", actor=user,
                 message=f"Cloned from {task.reference}.")

    if not reset_assignees:
        for row in task.assignee_rows.select_related("user"):
            try:
                _attach_assignee(new, user, row.user, silent=True)
            except (ValidationError, PermissionDenied) as exc:
                logger.warning("Clone: could not attach %s: %s", row.user, exc)

    # Copy subtask structure. Review M10: the parent link was set on the
    # clone but never saved, leaving subtask clones as top-level tasks.
    for child in task.subtasks.all():
        child_clone = clone_task(child, user, reset_assignees=reset_assignees)
        child_clone.parent = new
        child_clone.save(update_fields=["parent", "updated_at"])
    return new


# ===========================================================================
# Opening
# ===========================================================================
@transaction.atomic
def open_task(task, user):
    """DRAFT -> OPEN. Notifies + emails every assignee."""
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot open this task.")
    if task.state != Task.DRAFT:
        raise ValidationError("Only draft tasks can be opened.")
    if not task.assignee_rows.exists() and not task.subtasks.exists():
        raise ValidationError(
            "Add at least one assignee or subtask before opening."
        )

    task.state = Task.OPEN
    task.save(update_fields=["state", "updated_at"])
    schedule_recurrence(task)
    log_activity(task, "opened", actor=user, message="Task opened.")

    for row in task.assignee_rows.select_related("user"):
        _notify_assignee(task, row.user, user, event="assigned")
    if task.reviewer_id and task.reviewer_id != user.pk:
        _notify_reviewer(task, task.reviewer,
                         "A task you'll review has been opened.")

    _send_task_email(task, event="assigned")
    return task


# ===========================================================================
# Assignment
# ===========================================================================
def _attach_assignee(task, actor, assignee, silent=False):
    """Internal — add row without notifications."""
    if task.assignee_rows.filter(user=assignee).exists():
        return
    can_assign_to(actor, assignee, task.category)

    used, cap, remaining = capacity_for_user(assignee)
    if remaining <= 0:
        raise ValidationError(
            f"{assignee.get_full_name() or assignee.email} is at capacity "
            f"({used}/{cap} open tasks)."
        )

    TaskAssignee.objects.create(
        task=task,
        user=assignee,
        state=TaskAssignee.ASSIGNED,
        assigned_by=actor,
    )
    log_activity(
        task, "assignee_added", actor=actor,
        message=f"Added {assignee.get_full_name() or assignee.email}",
        metadata={"assignee_id": assignee.pk},
    )


@transaction.atomic
def add_assignee(task, user, assignee):
    """Public — adds and notifies."""
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot assign this task.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("Closed tasks cannot be reassigned.")

    _attach_assignee(task, user, assignee)
    _notify_assignee(task, assignee, user, event="assigned")
    _send_task_email(task, event="assigned", to_user=assignee)

    if task.state == Task.DRAFT and not task.subtasks.exists():
        task.state = Task.OPEN
        task.save(update_fields=["state", "updated_at"])
    return task


@transaction.atomic
def assign_to_role(task, user, role_code, department=None):
    """Auto-pick the eligible user with the most remaining capacity."""
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot assign this task.")
    from accounts.models import User

    qs = User.objects.filter(is_active=True, role__code=role_code)
    if department is not None:
        qs = qs.filter(department=department)

    candidates = []
    for u in qs:
        used, cap, remaining = capacity_for_user(u)
        if remaining > 0:
            # Category role filter
            if task.category and task.category.allowed_role_codes:
                if not u.role_id or u.role.code not in task.category.allowed_role_codes:
                    continue
            candidates.append((remaining, u))

    if not candidates:
        raise ValidationError(
            f"No user with role '{role_code}' has spare capacity right now."
        )
    candidates.sort(key=lambda t: (-t[0], t[1].pk))
    return add_assignee(task, user, candidates[0][1])


@transaction.atomic
def bulk_assign(task_ids, user, assignee):
    """Assign one person to many tasks. Returns (assigned_ids, failures)."""
    assigned, failed = [], []
    for tid in task_ids:
        task = Task.objects.filter(pk=tid).first()
        if task is None:
            failed.append((tid, "not found"))
            continue
        try:
            add_assignee(task, user, assignee)
            assigned.append(tid)
        except (ValidationError, PermissionDenied) as exc:
            failed.append((tid, str(exc)))
    return assigned, failed


@transaction.atomic
def remove_assignee(task, user, assignee):
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot modify assignees.")
    if task.state not in (Task.DRAFT, Task.OPEN):
        raise ValidationError(
            "Assignees can only be removed while the task is draft or open."
        )
    row = task.assignee_rows.filter(user=assignee).first()
    if row is None:
        raise ValidationError("That user is not assigned.")
    row.delete()
    log_activity(
        task, "assignee_removed", actor=user,
        message=f"Removed {assignee.get_full_name() or assignee.email}",
    )


@transaction.atomic
def reassign(task, user, from_user, to_user):
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot reassign this task.")
    if task.state not in (Task.DRAFT, Task.OPEN, Task.RETURNED):
        raise ValidationError(
            "Reassignment is only allowed before work starts or after "
            "a return."
        )
    old_row = task.assignee_rows.filter(user=from_user).first()
    if old_row is None:
        raise ValidationError("From-user is not assigned to this task.")

    old_row.delete()
    _attach_assignee(task, user, to_user, silent=True)
    log_activity(
        task, "reassigned", actor=user,
        message=f"Reassigned from {from_user} to {to_user}",
        metadata={"from_id": from_user.pk, "to_id": to_user.pk},
    )
    _notify_assignee(task, to_user, user, event="reassigned")
    _notify_assignee(task, from_user, user, event="unassigned")
    _send_task_email(task, event="assigned", to_user=to_user)
    return task


# ===========================================================================
# Subtasks / containers
# ===========================================================================
@transaction.atomic
def break_into_subtasks(task, user, specs):
    """Split a container.

    `specs` is a list of dicts:
        {"title": str, "assignee": User|None, "due_date": date|None,
         "priority": str|None, "description": str|None}
    """
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot split this task.")
    if not specs:
        raise ValidationError("At least one subtask spec is required.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("Closed tasks cannot be split.")

    created = []
    for spec in specs:
        child = create_task(
            created_by=user,
            title=spec["title"],
            description=spec.get("description") or "",
            category=task.category,
            owner_department=task.owner_department,
            priority=spec.get("priority") or task.priority,
            start_date=spec.get("start_date") or task.start_date,
            due_date=spec.get("due_date") or task.due_date,
            requires_review=task.requires_review,
            parent=task,
            tags=task.tags,
        )
        if spec.get("assignee"):
            try:
                _attach_assignee(child, user, spec["assignee"], silent=True)
            except (ValidationError, PermissionDenied) as exc:
                logger.warning(
                    "Could not attach %s to subtask %s: %s",
                    spec["assignee"], child.reference, exc,
                )
        created.append(child)

    log_activity(
        task, "split", actor=user,
        message=f"Split into {len(created)} subtask(s).",
        metadata={"subtask_ids": [c.pk for c in created]},
    )
    refresh_container_state(task)
    return created


def refresh_container_state(task):
    """Recompute a container's state from its children."""
    children = list(task.subtasks.all())
    if not children:
        return

    open_children = [c for c in children if not c.is_closed]
    done = [c for c in children if c.state == Task.COMPLETED]

    if not open_children:
        _maybe_finalise_container(task)
        return

    if task.state in (Task.DRAFT, Task.OPEN):
        task.state = Task.IN_PROGRESS
        task.save(update_fields=["state", "updated_at"])

    if any(c.state == Task.AWAITING_REVIEW for c in children):
        if task.state != Task.AWAITING_REVIEW:
            task.state = Task.AWAITING_REVIEW
            task.save(update_fields=["state", "updated_at"])


def _maybe_finalise_container(task):
    if task.state in (Task.COMPLETED, Task.CANCELLED):
        return
    if task.requires_review:
        task.state = Task.AWAITING_REVIEW
        task.save(update_fields=["state", "updated_at"])
        log_activity(task, "awaiting_review", actor=None,
                     message="All subtasks complete; awaiting review.")
        if task.reviewer_id:
            _notify_reviewer(task, task.reviewer,
                             "Container task is ready for your review.")
        _send_task_email(task, event="awaiting_review")
    else:
        task.state = Task.COMPLETED
        task.completed_at = timezone.now()
        task.save(update_fields=["state", "completed_at", "updated_at"])
        log_activity(task, "completed", actor=None,
                     message="All subtasks complete.")
        _on_task_completed(task)


# ===========================================================================
# Leaf transitions
# ===========================================================================
def _my_row(task, user):
    row = task.assignee_rows.filter(user=user).first()
    if row is None:
        raise PermissionDenied("You are not assigned to this task.")
    return row


@transaction.atomic
def accept_task(task, user):
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")
    row = _my_row(task, user)
    if row.state != TaskAssignee.ASSIGNED:
        return row
    row.state = TaskAssignee.ACCEPTED
    row.accepted_at = timezone.now()
    row.save(update_fields=["state", "accepted_at", "updated_at"])
    log_activity(task, "accepted", actor=user, message="Task accepted.")
    return row


@transaction.atomic
def start_work(task, user):
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")

    blocking = _blocking_dependencies(task)
    if blocking:
        names = ", ".join(b.reference for b in blocking[:3])
        raise ValidationError(
            f"Cannot start — waiting on: {names}. Complete dependencies first."
        )

    row = _my_row(task, user)
    now = timezone.now()
    if row.state != TaskAssignee.IN_PROGRESS:
        row.state = TaskAssignee.IN_PROGRESS
        row.started_at = row.started_at or now
        row.save(update_fields=["state", "started_at", "updated_at"])

    if task.state in (Task.OPEN, Task.DRAFT):
        task.state = Task.IN_PROGRESS
        task.save(update_fields=["state", "updated_at"])
        log_activity(task, "in_progress", actor=user, message="Work started.")
        _notify_creator(task, user,
                        f"{user.get_full_name() or user.email} started work.")
    return row


@transaction.atomic
def ask_question(task, user, note):
    note = (note or "").strip()
    if len(note) < 3:
        raise ValidationError("Please write your question.")
    row = _my_row(task, user)
    row.state = TaskAssignee.BLOCKED
    row.note = note
    row.save(update_fields=["state", "note", "updated_at"])
    task.state = Task.AWAITING_RESPONSE
    task.save(update_fields=["state", "updated_at"])
    log_activity(task, "question", actor=user,
                 message=f"Question: {note[:200]}")
    _notify_creator(task, user,
                    f"Question on {task.reference}: {note[:200]}")
    return task


@transaction.atomic
def resume_work(task, user, note=""):
    if task.state not in (Task.AWAITING_RESPONSE, Task.RETURNED):
        raise ValidationError("This task is not paused.")
    row = task.assignee_rows.filter(user=user).first()
    if row is not None:
        row.state = TaskAssignee.IN_PROGRESS
        row.note = (note or "")[:500]
        row.save(update_fields=["state", "note", "updated_at"])
    task.state = Task.IN_PROGRESS
    task.save(update_fields=["state", "updated_at"])
    log_activity(task, "resumed", actor=user,
                 message=note or "Work resumed.")
    return task


@transaction.atomic
def return_to_assigner(task, user, reason):
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please explain why you're returning this task.")
    row = _my_row(task, user)
    row.state = TaskAssignee.DECLINED
    row.note = reason
    row.save(update_fields=["state", "note", "updated_at"])

    task.state = Task.RETURNED
    task.save(update_fields=["state", "updated_at"])
    log_activity(task, "returned", actor=user,
                 message=f"Returned by assignee: {reason[:200]}")
    _notify_creator(task, user,
                    f"Task returned by {user.get_full_name() or user.email}.")
    _send_task_email(task, event="returned")
    return task


@transaction.atomic
def resend_task(task, user, note=""):
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot resend this task.")
    if task.state != Task.RETURNED:
        raise ValidationError("Only returned tasks can be resent.")

    for row in task.assignee_rows.filter(state=TaskAssignee.DECLINED):
        row.state = TaskAssignee.ASSIGNED
        row.save(update_fields=["state", "updated_at"])

    task.state = Task.OPEN
    task.save(update_fields=["state", "updated_at"])
    log_activity(task, "resent", actor=user, message=note or "Task resent.")

    for row in task.assignee_rows.select_related("user"):
        _notify_assignee(task, row.user, user, event="assigned")
    _send_task_email(task, event="assigned")
    return task


@transaction.atomic
def mark_assignee_done(task, user, note=""):
    row = _my_row(task, user)
    if row.state == TaskAssignee.DONE:
        return task

    # Stop any timer this user has running on this task — no point
    # counting time on work that's already finished.
    running = TaskTimeEntry.objects.filter(
        task=task, user=user, ended_at__isnull=True,
    ).first()
    if running is not None:
        stop_now = timezone.now()
        running.ended_at = stop_now
        running.duration_seconds = int((stop_now - running.started_at).total_seconds())
        running.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
        log_activity(
            task, "timer_stopped", actor=user,
            message=f"Timer auto-stopped on mark-done ({running.hours}h).",
        )

    now = timezone.now()
    row.state = TaskAssignee.DONE
    row.completed_at = now
    row.note = (note or "")[:500]
    row.save(update_fields=["state", "completed_at", "note", "updated_at"])
    log_activity(
        task, "assignee_done", actor=user,
        message=f"{user.get_full_name() or user.email} finished their part.",
    )

    remaining = task.assignee_rows.exclude(
        state__in=[TaskAssignee.DONE, TaskAssignee.DECLINED]
    ).count()
    if remaining == 0:
        if task.requires_review:
            task.state = Task.AWAITING_REVIEW
            task.save(update_fields=["state", "updated_at"])
            log_activity(task, "awaiting_review", actor=user,
                         message="All assignees done; awaiting review.")
            if task.reviewer_id:
                _notify_reviewer(task, task.reviewer,
                                 "Task ready for your review.")
            else:
                _notify_creator(task, user, "Task ready for review.")
            _send_task_email(task, event="awaiting_review")
        else:
            task.state = Task.COMPLETED
            task.completed_at = now
            task.save(update_fields=["state", "completed_at", "updated_at"])
            log_activity(task, "completed", actor=user, message="Task completed.")
            _on_task_completed(task)

    return task


@transaction.atomic
def review_task(task, user, approve, notes=""):
    if not can_review(task, user):
        raise PermissionDenied("You cannot review this task.")
    if task.state != Task.AWAITING_REVIEW:
        raise ValidationError("This task is not awaiting review.")

    task.reviewed_by = user
    task.reviewed_at = timezone.now()
    task.review_notes = (notes or "").strip()

    if approve:
        task.state = Task.COMPLETED
        task.completed_at = timezone.now()
        task.save(update_fields=[
            "reviewed_by", "reviewed_at", "review_notes",
            "state", "completed_at", "updated_at",
        ])
        log_activity(task, "completed", actor=user,
                     message="Task approved and completed.")
        _on_task_completed(task)
    else:
        task.state = Task.IN_PROGRESS
        task.save(update_fields=[
            "reviewed_by", "reviewed_at", "review_notes",
            "state", "updated_at",
        ])
        for row in task.assignee_rows.exclude(state=TaskAssignee.DECLINED):
            row.state = TaskAssignee.IN_PROGRESS
            row.completed_at = None
            row.save(update_fields=["state", "completed_at", "updated_at"])
        log_activity(task, "review_rejected", actor=user,
                     message=f"Rejected by reviewer: {notes[:200]}")
        for row in task.assignee_rows.select_related("user"):
            _notify_assignee(task, row.user, user, event="review_rejected")
        _send_task_email(task, event="review_rejected")

    return task


@transaction.atomic
def cancel_task(task, user, reason=""):
    if not can_cancel_task(task, user):
        raise PermissionDenied("You cannot cancel this task.")
    if task.state == Task.CANCELLED:
        return task

    task.state = Task.CANCELLED
    task.cancelled_at = timezone.now()
    task.save(update_fields=["state", "cancelled_at", "updated_at"])
    log_activity(task, "cancelled", actor=user,
                 message=reason or "Task cancelled.")
    for row in task.assignee_rows.select_related("user"):
        _notify_assignee(task, row.user, user, event="cancelled")
    _send_task_email(task, event="cancelled")
    return task


@transaction.atomic
def delete_task(task, user, reason=""):
    if not can_delete_task(task, user):
        raise PermissionDenied("You cannot delete this task.")
    if not reason or len(reason.strip()) < 5:
        raise ValidationError("A reason is required to delete a task.")
    log_activity(
        task, "deleted", actor=user,
        message=f"Deleted by {user.get_full_name() or user.email}.",
        metadata={"reason": reason},
    )
    task.delete()


@transaction.atomic
def reopen_completed_task(task, user, reason=""):
    """Reopen a COMPLETED task for inspection or further work.

    Who: the reviewer, the creator, a department head, or a superuser.
    What: state → IN_PROGRESS; done assignee rows reset; completed_at
          cleared; everyone re-notified.
    """
    if task.state != Task.COMPLETED:
        raise ValidationError("Only completed tasks can be reopened.")

    allowed = (
        user.is_superuser
        or task.created_by_id == user.pk
        or (task.reviewer_id and task.reviewer_id == user.pk)
        or is_department_head(user, task.owner_department_id)
    )
    if not allowed:
        raise PermissionDenied("You cannot reopen this task.")

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please explain why you're reopening this task.")

    now = timezone.now()
    task.state = Task.IN_PROGRESS
    task.completed_at = None
    task.save(update_fields=["state", "completed_at", "updated_at"])

    for row in task.assignee_rows.exclude(state=TaskAssignee.DECLINED):
        row.state = TaskAssignee.IN_PROGRESS
        row.completed_at = None
        row.save(update_fields=["state", "completed_at", "updated_at"])

    log_activity(
        task, "reopened", actor=user,
        message=f"Reopened: {reason[:200]}",
        metadata={"reason": reason},
    )
    for row in task.assignee_rows.select_related("user"):
        _notify_assignee(task, row.user, user, event="reopened")
    _notify_creator(
        task, user,
        f"Task reopened by {user.get_full_name() or user.email}.",
        title_override=f"Reopened — {task.reference}",
    )
    return task

# ===========================================================================
# Time tracking
# ===========================================================================
@transaction.atomic
def start_timer(task, user):
    if not task.assignee_rows.filter(user=user).exists():
        raise PermissionDenied("Only assignees can log time.")
    running = TaskTimeEntry.objects.filter(
        task=task, user=user, ended_at__isnull=True,
    ).first()
    if running:
        return running
    entry = TaskTimeEntry.objects.create(task=task, user=user)
    log_activity(task, "timer_started", actor=user, message="Timer started.")
    return entry


@transaction.atomic
def stop_timer(task, user):
    entry = TaskTimeEntry.objects.filter(
        task=task, user=user, ended_at__isnull=True,
    ).first()
    if not entry:
        raise ValidationError("No running timer.")
    now = timezone.now()
    entry.ended_at = now
    entry.duration_seconds = int((now - entry.started_at).total_seconds())
    entry.save(update_fields=["ended_at", "duration_seconds", "updated_at"])
    log_activity(task, "timer_stopped", actor=user,
                 message=f"Logged {entry.hours}h.")
    return entry


MAX_MANUAL_ENTRY_HOURS = 24


@transaction.atomic
def log_time(task, user, started_at, ended_at, note=""):
    # Review M10: same rule as the live timer - assignees only - plus basic
    # sanity limits on manually typed entries.
    if not task.assignee_rows.filter(user=user).exists():
        raise PermissionDenied("Only assignees can log time.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("Time cannot be logged on a closed task.")
    if ended_at <= started_at:
        raise ValidationError("End must be after start.")
    if ended_at > timezone.now() + timedelta(minutes=5):
        raise ValidationError("Time cannot be logged in the future.")
    seconds = int((ended_at - started_at).total_seconds())
    if seconds > MAX_MANUAL_ENTRY_HOURS * 3600:
        raise ValidationError(
            f"A single entry cannot exceed {MAX_MANUAL_ENTRY_HOURS} hours; split it by day."
        )
    entry = TaskTimeEntry.objects.create(
        task=task, user=user,
        started_at=started_at, ended_at=ended_at,
        duration_seconds=seconds,
        note=(note or "")[:500],
    )
    log_activity(task, "time_logged", actor=user,
                 message=f"Manually logged {entry.hours}h.")
    return entry


# ===========================================================================
# Dependencies
# ===========================================================================
@transaction.atomic
def add_dependency(task, depends_on, note=""):
    if task.pk == depends_on.pk:
        raise ValidationError("A task cannot depend on itself.")
    if TaskDependency.objects.filter(task=depends_on, depends_on=task).exists():
        raise ValidationError("That would create a circular dependency.")
    TaskDependency.objects.get_or_create(
        task=task, depends_on=depends_on,
        defaults={"note": note or ""},
    )
    log_activity(task, "dependency_added", actor=None,
                 message=f"Depends on {depends_on.reference}.")


@transaction.atomic
def remove_dependency(task, depends_on):
    TaskDependency.objects.filter(task=task, depends_on=depends_on).delete()
    log_activity(task, "dependency_removed", actor=None,
                 message=f"No longer depends on {depends_on.reference}.")


def dependencies_met(task):
    return not _blocking_dependencies(task)


def _blocking_dependencies(task):
    return [
        link.depends_on
        for link in task.dependency_links.select_related("depends_on")
        if link.depends_on.state != Task.COMPLETED
    ]


# ===========================================================================
# Comments
# ===========================================================================
@transaction.atomic
def add_comment(task, user, body, internal=False, pinned=False):
    if task.is_closed:
        raise ValidationError("This task is closed. Comments are read-only.")
    body = (body or "").strip()
    if not body:
        raise ValidationError("Comment cannot be empty.")
    comment = TaskComment.objects.create(
        task=task, author=user, body=body, is_internal=internal,
    )
    if pinned and can_edit_task(task, user):
        pin_comment(task, user, comment)

    log_activity(task, "commented", actor=user,
                 message=body[:200],
                 metadata={"comment_id": comment.pk, "internal": internal})

    if task.created_by_id != user.pk:
        _notify_creator(task, user, f"New comment on {task.reference}.")
    for row in task.assignee_rows.select_related("user"):
        if row.user_id != user.pk:
            _notify_assignee(task, row.user, user, event="commented")
    return comment


@transaction.atomic
def pin_comment(task, user, comment):
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot pin comments.")
    if comment.task_id != task.pk:
        raise ValidationError("Comment doesn't belong to this task.")
    comment.is_pinned = True
    comment.pinned_at = timezone.now()
    comment.pinned_by = user
    comment.save(update_fields=[
        "is_pinned", "pinned_at", "pinned_by", "updated_at",
    ])
    log_activity(task, "comment_pinned", actor=user,
                 message="Comment pinned.",
                 metadata={"comment_id": comment.pk})


@transaction.atomic
def unpin_comment(task, user, comment):
    if not can_edit_task(task, user):
        raise PermissionDenied("You cannot unpin comments.")
    comment.is_pinned = False
    comment.pinned_at = None
    comment.pinned_by = None
    comment.save(update_fields=[
        "is_pinned", "pinned_at", "pinned_by", "updated_at",
    ])
    log_activity(task, "comment_unpinned", actor=user,
                 message="Comment unpinned.",
                 metadata={"comment_id": comment.pk})


def _can_delete_content(actor, owner, task):
    if actor is None or owner is None:
        return False
    if actor.is_superuser:
        return True
    if actor.pk == owner.pk:
        return True
    return is_department_head(actor, task.owner_department_id)


@transaction.atomic
def delete_comment(task, user, comment):
    if not _can_delete_content(user, comment.author, task):
        raise PermissionDenied("You cannot delete this comment.")
    log_activity(task, "comment_deleted", actor=user,
                 message=f"Comment deleted by {user.get_full_name() or user.email}.",
                 metadata={"comment_id": comment.pk,
                           "author_id": comment.author_id})
    comment.delete()


# ===========================================================================
# Attachments
# ===========================================================================
@transaction.atomic
def add_attachment(task, user, file, description=""):
    if task.is_closed:
        raise ValidationError("This task is closed. No new attachments.")

    attachment = TaskAttachment.objects.create(
        task=task,
        uploaded_by=user,
        file=file,
        original_name=getattr(file, "name", "") or "",
        description=(description or "")[:255],
    )
    log_activity(task, "attached", actor=user,
                 message=f"Attached {attachment.original_name}",
                 metadata={"attachment_id": attachment.pk})
    return attachment


@transaction.atomic
def delete_attachment(task, user, attachment):
    if not _can_delete_content(user, attachment.uploaded_by, task):
        raise PermissionDenied("You cannot delete this attachment.")
    log_activity(task, "attachment_deleted", actor=user,
                 message=f"Deleted attachment {attachment.original_name}.",
                 metadata={"attachment_id": attachment.pk})
    try:
        attachment.file.delete(save=False)
    except Exception:
        logger.exception("Could not delete file for attachment %s", attachment.pk)
    attachment.delete()


# ===========================================================================
# Completion side-effects
# ===========================================================================
def on_child_changed(child):
    """Walk up the parent chain refreshing container states."""
    parent = child.parent
    while parent is not None:
        refresh_container_state(parent)
        parent = parent.parent


def _on_task_completed(task):
    """Fires when a task lands in COMPLETED."""
    _notify_creator(task, None,
                    f"Task {task.reference} is complete.",
                    title_override=f"Task completed — {task.reference}")
    for row in task.assignee_rows.select_related("user"):
        _notify_assignee(task, row.user, None, event="completed")
    _send_task_email(task, event="completed")

    if task.is_recurring and task.recurrence_type != Task.RECURRENCE_NONE:
        _schedule_next_recurrence(task)

    if task.parent_id:
        on_child_changed(task)


def _schedule_next_recurrence(task):
    days = {
        Task.RECURRENCE_DAILY: 1,
        Task.RECURRENCE_WEEKLY: 7,
        Task.RECURRENCE_MONTHLY: 30,
    }.get(task.recurrence_type, 0)
    if days:
        task.recurrence_next_at = timezone.now() + timedelta(days=days)
        task.save(update_fields=["recurrence_next_at", "updated_at"])


# ===========================================================================
# Notifications
# ===========================================================================
_TITLE_BY_EVENT = {
    "assigned":         "You've been assigned a task",
    "reassigned":       "A task was reassigned to you",
    "unassigned":       "You were unassigned from a task",
    "commented":        "New comment on your task",
    "completed":        "Task completed",
    "cancelled":        "Task cancelled",
    "review_rejected":  "Your task needs more work",
    "reopened":         "Task reopened",
}


def _notify_assignee(task, assignee, actor, event="assigned"):
    if not assignee or not assignee.is_active:
        return
    title = _TITLE_BY_EVENT.get(event, "Task update")
    try:
        notify(
            recipient=assignee,
            title=f"{title} — {task.reference}",
            body=task.title,
            url=f"/tasks/{task.pk}/",
            category=Notification.REQUEST,
            level=(
                Notification.WARNING
                if event in ("reassigned", "review_rejected")
                else Notification.INFO
            ),
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify assignee %s", assignee.pk)


def _notify_creator(task, actor, body, title_override=None):
    if not task.created_by_id:
        return
    try:
        notify(
            recipient=task.created_by,
            title=title_override or f"Task update — {task.reference}",
            body=body,
            url=f"/tasks/{task.pk}/",
            category=Notification.REQUEST,
            level=Notification.INFO,
            actor=actor,
        )
    except Exception:
        logger.exception("Could not notify creator %s", task.created_by_id)


def _notify_reviewer(task, reviewer, body):
    if not reviewer or not reviewer.is_active:
        return
    try:
        notify(
            recipient=reviewer,
            title=f"Review needed — {task.reference}",
            body=body,
            url=f"/tasks/{task.pk}/",
            category=Notification.REQUEST,
            level=Notification.WARNING,
            actor=None,
        )
    except Exception:
        logger.exception("Could not notify reviewer %s", reviewer.pk)


# ===========================================================================
# Email dispatch bridge
# ===========================================================================
def _send_task_email(task, event, to_user=None):
    """Bridge to tasks.emails — swallow all errors."""
    try:
        from . import emails as task_emails
    except Exception:
        logger.exception("Could not import tasks.emails")
        return

    try:
        if event == "assigned":
            if to_user is not None:
                task_emails.send_assigned_email(task, to_user)
            else:
                task_emails.send_assigned_bulk(task)
        elif event == "returned":
            task_emails.send_returned_email(task)
        elif event == "awaiting_review":
            task_emails.send_awaiting_review_email(task)
        elif event == "review_rejected":
            task_emails.send_review_rejected_email(task)
        elif event == "completed":
            task_emails.send_completed_email(task)
        elif event == "cancelled":
            task_emails.send_cancelled_email(task)
    except Exception:
        logger.exception(
            "Task email dispatch failed for %s / %s", task.reference, event,
        )


# ===========================================================================
# Reminder sweep (Celery Beat)
# ===========================================================================
def reminder_sweep():
    """Fire in-app (optionally email) reminders for approaching and
    overdue tasks. Returns counts."""
    if _cfg_int("tasks.reminders.enabled", 1) != 1:
        return {"skipped": "disabled"}

    days_list = _cfg_json("tasks.reminders.days_before_due", [3, 1])
    if not isinstance(days_list, list):
        days_list = [3, 1]
    days_list = sorted(
        {int(d) for d in days_list if str(d).isdigit()}, reverse=True,
    ) or [3, 1]

    email_enabled = _cfg_int("tasks.reminders.email_enabled", 0) == 1
    overdue_daily = _cfg_int("tasks.reminders.overdue_daily", 1) == 1

    today = timezone.localdate()
    now = timezone.now()
    fired_due = fired_overdue = 0

    for days in days_list:
        target = today + timedelta(days=days)
        for task in (
            Task.objects
            .filter(state__in=Task.ACTIVE_STATES, due_date=target)
            .prefetch_related("assignee_rows__user")
        ):
            for row in task.assignee_rows.all():
                _fire_reminder(task, row.user, days, email_enabled)
            task.last_reminder_at = now
            task.save(update_fields=["last_reminder_at", "updated_at"])
            fired_due += 1

    if overdue_daily:
        cutoff = now - timedelta(hours=20)
        overdue_qs = (
            Task.objects
            .filter(state__in=Task.ACTIVE_STATES, due_date__lt=today)
            .filter(Q(last_reminder_at__isnull=True) | Q(last_reminder_at__lt=cutoff))
            .prefetch_related("assignee_rows__user")
        )
        for task in overdue_qs:
            for row in task.assignee_rows.all():
                _fire_reminder(task, row.user, None, email_enabled)
            _notify_creator(task, None, f"Task {task.reference} is overdue.")
            task.last_reminder_at = now
            task.save(update_fields=["last_reminder_at", "updated_at"])
            fired_overdue += 1

    return {"due": fired_due, "overdue": fired_overdue}


def _fire_reminder(task, user, days_before, email_enabled=False):
    if not user or not user.is_active:
        return
    if days_before is None:
        body = "Overdue — please complete or update this task."
        level = Notification.ERROR
    elif days_before == 0:
        body = "Due today."
        level = Notification.WARNING
    else:
        body = f"Due in {days_before} day{'s' if days_before != 1 else ''}."
        level = Notification.INFO

    try:
        notify(
            recipient=user,
            title=f"Task reminder — {task.reference}",
            body=f"{task.title} · {body}",
            url=f"/tasks/{task.pk}/",
            category=Notification.REQUEST,
            level=level,
            actor=None,
        )
    except Exception:
        logger.exception("Reminder notify failed for %s", user.pk)

    if email_enabled:
        try:
            from . import emails as task_emails
            task_emails.send_reminder_email(task, user, body)
        except Exception:
            logger.exception("Reminder email failed for %s", user.pk)


# ===========================================================================
# Recurring spawn (Celery Beat)
# ===========================================================================
def spawn_recurring():
    """Spawn fresh clones for tasks whose recurrence timer is due."""
    now = timezone.now()
    spawned = 0
    qs = Task.objects.filter(
        is_recurring=True,
        recurrence_next_at__lte=now,
    ).exclude(recurrence_type=Task.RECURRENCE_NONE)

    for template in qs:
        try:
            with transaction.atomic():
                fresh = clone_task(template, template.created_by,
                                   reset_assignees=False)
                template.recurrence_next_at = None
                template.save(update_fields=["recurrence_next_at", "updated_at"])
                log_activity(fresh, "recurrence_spawned", actor=None,
                             message=f"Spawned from recurring {template.reference}.")
                _notify_creator(fresh, None,
                                f"Recurring task spawned: {fresh.reference}")
                spawned += 1
        except Exception:
            logger.exception("Recurring spawn failed for %s", template.reference)
    return spawned


# ===========================================================================
# Priority escalation (Celery Beat)
# ===========================================================================
def escalate_overdue_priorities():
    """Bump priority once at halfway-past-due, and again to urgent at fully
    past. Reads config toggles."""
    if _cfg_int("tasks.escalation.halfway_bump", 1) != 1 and \
       _cfg_int("tasks.escalation.overdue_bump", 1) != 1:
        return 0

    order = [Task.LOW, Task.NORMAL, Task.HIGH, Task.URGENT]
    today = timezone.localdate()
    updated = 0

    for task in Task.objects.filter(
        state__in=Task.ACTIVE_STATES, due_date__isnull=False,
    ).iterator():
        days_over = (today - task.due_date).days
        if days_over < 0:
            continue

        try:
            current_idx = order.index(task.priority)
        except ValueError:
            continue

        original_window = max(1, (task.due_date - task.created_at.date()).days or 1)
        half = max(1, original_window // 2)

        if _cfg_int("tasks.escalation.overdue_bump", 1) == 1 \
           and days_over >= original_window \
           and current_idx < order.index(Task.URGENT):
            task.priority = Task.URGENT
            task.save(update_fields=["priority", "updated_at"])
            log_activity(task, "priority_escalated", actor=None,
                         message="Auto-escalated to urgent (well past due).")
            _notify_creator(task, None,
                            "Task auto-escalated to urgent (past due).")
            updated += 1
        elif _cfg_int("tasks.escalation.halfway_bump", 1) == 1 \
             and days_over >= half \
             and current_idx < order.index(Task.URGENT) - 1:
            task.priority = order[current_idx + 1]
            task.save(update_fields=["priority", "updated_at"])
            log_activity(task, "priority_escalated", actor=None,
                         message=f"Auto-escalated to {task.priority}.")
            updated += 1
    return updated

# ===========================================================================
# Recurrence scheduling
# ===========================================================================
def schedule_recurrence(task):
    """Set `recurrence_next_at` for a recurring task.

    Called when a recurring task first opens (so the timer starts even if
    the task never completes) and after each spawn. Idempotent — safe to
    call repeatedly."""
    if not task.is_recurring or task.recurrence_type == Task.RECURRENCE_NONE:
        return
    if task.recurrence_next_at:
        return
    days = {
        Task.RECURRENCE_DAILY: 1,
        Task.RECURRENCE_WEEKLY: 7,
        Task.RECURRENCE_MONTHLY: 30,
    }.get(task.recurrence_type, 0)
    if days:
        task.recurrence_next_at = timezone.now() + timedelta(days=days)
        task.save(update_fields=["recurrence_next_at", "updated_at"])

