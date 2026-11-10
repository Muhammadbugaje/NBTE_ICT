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

from .hooks import external_close_active, is_externally_managed
from .signals import task_state_changed
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
# QA hook points (Master Plan §8)
# ===========================================================================
def _emit_state_changed(task, old, actor, action, **metadata):
    """Send `task_state_changed` synchronously (inside the caller's
    transaction, so a receiver that raises rolls the action back)."""
    task_state_changed.send(
        sender=Task, task=task, old=old, new=task.state,
        actor=actor, action=action, metadata=metadata,
    )


def _refuse_if_externally_managed(task, verb):
    if is_externally_managed(task):
        raise PermissionDenied(
            f"This task is managed by another module and cannot be {verb} here. "
            "Use its \"Open in QA\" link."
        )


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
    """(used, max, remaining) - non-terminal assignee rows on non-terminal
    tasks. Reads the ``open_load`` annotation when the user came from
    ``work.visibility.assignable_users`` (no query, T-PERF-1)."""
    if not user or not user.is_authenticated:
        return (0, 0, 0)
    from work.visibility import load_info
    info = load_info(user, default_max=_cfg_int("tasks.default_max_open", 5))
    return (info["open"], info["max"], info["remaining"])


# Roles that lead a department's task list (plus the director seat, org).
TASK_LEAD_ROLES = (
    "executive-secretary", "deputy-director",
    "office-head-admin", "office-head-ts", "office-head-dev", "division-head",
)


def is_department_head(user, dept_id):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.department_id or user.department_id != dept_id:
        return False
    import org
    return bool(org.has_role(user, *TASK_LEAD_ROLES) or org.is_department_lead(user, dept_id))


# ===========================================================================
# Permission matrix
# ===========================================================================
def is_task_creator(task, user):
    return user.is_authenticated and task.created_by_id == user.pk


def is_override(user):
    """ES, superuser or super admin: the audited override on another
    person's task (H7)."""
    from work.visibility import is_override as _ov
    return _ov(user)


def _log_override(task, user, verb):
    """Record that someone other than the creator used the override."""
    if task.created_by_id != getattr(user, "pk", None):
        log_activity(task, "override", actor=user,
                     message=f"{user.get_full_name() or user.email} used the override to {verb}.",
                     metadata={"verb": verb})


def can_edit_task(task, user):
    """H7: only the creator edits, adds or removes people and cancels; the
    ES and superusers keep an audited override."""
    if not getattr(user, "is_authenticated", False):
        return False
    return is_task_creator(task, user) or is_override(user)


def can_cancel_task(task, user):
    """H7: the creator (ES / superuser override)."""
    return can_edit_task(task, user)


def can_delete_task(task, user):
    """The creator (ES / superuser override)."""
    return can_edit_task(task, user)


def can_review(task, user):
    """Confirm or return work: the reviewer, the creator (ES / superuser)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if task.reviewer_id and task.reviewer_id == user.pk:
        return True
    return is_task_creator(task, user) or is_override(user)


def can_split_task(task, user):
    """H8: the creator and every assignee may split a task into subtasks,
    as often as needed, while it is open."""
    if task.state in Task.CLOSED_STATES or not getattr(user, "is_authenticated", False):
        return False
    if can_edit_task(task, user):
        return True
    return task.assignee_rows.filter(user=user).exclude(state=TaskAssignee.DECLINED).exists()


def assignable_users_for(assigner, *, department=None, category=None,
                         include_ids=None):
    """Users ``assigner`` may assign work to (C.1: ``work.visibility``): same
    department, lower rank (ES: anyone), annotated with ``open_load`` in one
    query. ``include_ids`` keeps already-chosen people in the list."""
    from accounts.selectors import staff_users
    from work.visibility import assignable_users, with_open_load
    import org

    if not assigner or not assigner.is_authenticated:
        return User.objects.none()
    qs = assignable_users(assigner, department, annotate_load=False)
    if category and category.allowed_role_codes:
        qs = qs.filter(org.role_q(*category.allowed_role_codes))
    if include_ids:
        extra = [int(x) for x in include_ids if str(x).isdigit()]
        if extra:
            qs = staff_users().filter(Q(pk__in=qs.values("pk")) | Q(pk__in=extra))
    qs = with_open_load(qs.select_related("role", "department"))
    return qs.order_by("-role__rank", "first_name", "last_name", "email")


def reviewer_choices(actor, include_ids=None):
    """People who may review ``actor``'s task (H20): staff of the actor's
    department of any rank, plus the ES; everyone for the ES / superuser.
    Annotated with ``open_load``."""
    from accounts.selectors import staff_users
    from work.visibility import department_id_of, with_open_load
    import org

    if not actor or not actor.is_authenticated:
        return User.objects.none()
    qs = staff_users()
    if not is_override(actor):
        qs = qs.filter(Q(department_id=department_id_of(actor)) | org.role_q(org.ES_ROLE))
    qs = qs.exclude(pk=actor.pk)
    if include_ids:
        extra = [int(x) for x in include_ids if str(x).isdigit()]
        qs = staff_users().filter(Q(pk__in=qs.values("pk")) | Q(pk__in=extra))
    return with_open_load(qs.select_related("role", "department")).order_by(
        "-role__rank", "first_name", "last_name", "email")


def can_assign_to(assigner, assignee, category, department=None):
    """Raise ValidationError if the assignment is not permitted (H4, H10)."""
    from work.visibility import assign_problem

    problem = assign_problem(assigner, assignee, department)
    if problem:
        raise ValidationError(problem)

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
    if reviewer is not None and getattr(reviewer, "user_type", "staff") != "staff":  # Phase 0 (H22)
        raise ValidationError("The reviewer must be an NBTE staff member.")

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

    old_state = task.state
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
    _emit_state_changed(task, old_state, user, "open_task")
    return task


# ===========================================================================
# Assignment
# ===========================================================================
def _attach_assignee(task, actor, assignee, silent=False):
    """Internal — add row without notifications."""
    if task.assignee_rows.filter(user=assignee).exists():
        return
    can_assign_to(actor, assignee, task.category, task.owner_department_id)

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

    import org
    qs = User.objects.filter(org.role_q(role_code), is_active=True)
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
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("Closed tasks cannot be changed.")
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

    old_state = task.state
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
    _emit_state_changed(task, old_state, user, "reassign",
                        from_user_id=from_user.pk, to_user_id=to_user.pk)
    return task


# ===========================================================================
# Subtasks / containers
# ===========================================================================
@transaction.atomic
def break_into_subtasks(task, user, specs):
    """Split a task into subtasks (H8: the creator or any assignee, as often
    as needed).

    `specs` is a list of dicts:
        {"title": str, "assignee": User|None, "due_date": date|None,
         "priority": str|None, "description": str|None,
         "department": Department|None}
    A spec with another ``department`` becomes a Request in that
    department's workflow (H11, ``work.bridge``).
    """
    if not can_split_task(task, user):
        raise PermissionDenied("Only the creator or the people on this task can split it.")
    if not specs:
        raise ValidationError("At least one subtask spec is required.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("Closed tasks cannot be split.")

    from work.visibility import department_id_of

    created = []
    for spec in specs:
        target = spec.get("department")
        if target is not None and target.pk == (task.owner_department_id or department_id_of(user)):
            target = None
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
        if target is not None:
            from work.bridge import escalate_to_request
            escalate_to_request(child, target, user)
        elif spec.get("assignee"):
            _attach_assignee(child, user, spec["assignee"], silent=True)
            if task.state not in (Task.DRAFT,):
                child.state = Task.OPEN
                child.save(update_fields=["state", "updated_at"])
                _notify_assignee(child, spec["assignee"], user, event="assigned")
        created.append(child)

    log_activity(
        task, "split", actor=user,
        message=f"{user.get_full_name() or user.email} added {len(created)} subtask(s).",
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
    if row.state == TaskAssignee.DONE:
        raise ValidationError("Your part is already marked done.")
    if row.state != TaskAssignee.IN_PROGRESS:
        if row.state == TaskAssignee.ASSIGNED:
            row.accepted_at = row.accepted_at or now  # H12: "Accept and start" in one step
        row.state = TaskAssignee.IN_PROGRESS
        row.started_at = row.started_at or now
        row.save(update_fields=["state", "started_at", "accepted_at", "updated_at"])

    if task.state in (Task.OPEN, Task.DRAFT):
        task.state = Task.IN_PROGRESS
        task.save(update_fields=["state", "updated_at"])
        log_activity(task, "in_progress", actor=user, message="Work started.")
        _notify_creator(task, user,
                        f"{user.get_full_name() or user.email} started work.")
    # H12: starting work starts this person's timer automatically.
    start_timer(task, user, auto=True)
    return row


def accept_and_start(task, user):
    """H12: the combined "Accept and start" action (accept + start + timer)."""
    return start_work(task, user)


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
    if row is not None:
        start_timer(task, user, auto=True)
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

    old_state = task.state
    task.state = Task.RETURNED
    task.save(update_fields=["state", "updated_at"])
    log_activity(task, "returned", actor=user,
                 message=f"Returned by assignee: {reason[:200]}")
    _notify_creator(task, user,
                    f"Task returned by {user.get_full_name() or user.email}.")
    _send_task_email(task, event="returned")
    _emit_state_changed(task, old_state, user, "return_to_assigner", reason=reason[:500])
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
    if not external_close_active():
        # QA-linked tasks (Phase 4) are completed from QA, never from Tasks.
        _refuse_if_externally_managed(task, "marked done")
    row = _my_row(task, user)
    if row.state == TaskAssignee.DONE:
        return task
    old_state = task.state

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
    row.review_state = TaskAssignee.REVIEW_PENDING
    row.save(update_fields=["state", "completed_at", "note", "review_state", "updated_at"])
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

    _emit_state_changed(task, old_state, user, "mark_assignee_done", assignee_id=user.pk)
    return task


@transaction.atomic
def review_task(task, user, approve, notes=""):
    if not can_review(task, user):
        raise PermissionDenied("You cannot review this task.")
    if task.state != Task.AWAITING_REVIEW:
        raise ValidationError("This task is not awaiting review.")

    old_state = task.state
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
        task.assignee_rows.filter(state=TaskAssignee.DONE).update(
            review_state=TaskAssignee.REVIEW_CONFIRMED, reviewed_at=task.reviewed_at)
        log_activity(task, "completed", actor=user,
                     message="Task approved and completed.")
        _on_task_completed(task, approver=user)
    else:
        task.state = Task.IN_PROGRESS
        task.save(update_fields=[
            "reviewed_by", "reviewed_at", "review_notes",
            "state", "updated_at",
        ])
        for row in task.assignee_rows.exclude(state=TaskAssignee.DECLINED):
            row.state = TaskAssignee.IN_PROGRESS
            row.completed_at = None
            row.review_state = TaskAssignee.REVIEW_RETURNED
            row.review_note = (notes or "")[:500]
            row.save(update_fields=["state", "completed_at", "review_state", "review_note", "updated_at"])
        log_activity(task, "review_rejected", actor=user,
                     message=f"Rejected by reviewer: {notes[:200]}")
        for row in task.assignee_rows.select_related("user"):
            _notify_assignee(task, row.user, user, event="review_rejected")
        _send_task_email(task, event="review_rejected")

    _emit_state_changed(task, old_state, user, "review_task", approved=bool(approve))
    return task


@transaction.atomic
def set_reviewer(task, user, reviewer):
    """H7 / H20: the creator (ES / superuser override) picks the reviewer
    (``None`` = the creator reviews)."""
    if not can_edit_task(task, user):
        raise PermissionDenied("Only the creator can change the reviewer.")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("Closed tasks cannot be changed.")
    if reviewer is not None and not reviewer_choices(user, include_ids=[task.reviewer_id] if task.reviewer_id
                                                     else None).filter(pk=reviewer.pk).exists():
        raise ValidationError("Choose a reviewer from your department (or the ES).")
    if (reviewer.pk if reviewer else None) == task.reviewer_id:
        return task
    task.reviewer = reviewer
    task.save(update_fields=["reviewer", "updated_at"])
    _log_override(task, user, "change the reviewer")
    log_activity(task, "edited", actor=user,
                 message=f"Reviewer set to {reviewer.get_full_name() or reviewer.email}." if reviewer
                 else "Reviewer cleared; the creator reviews.")
    if reviewer is not None and task.state != Task.DRAFT and reviewer.pk != user.pk:
        _notify_reviewer(task, reviewer, "You were chosen to review this task.")
    return task


# ===========================================================================
# Per-person confirm / return (H14, H15)
# ===========================================================================
def _complete_after_confirm(task, user):
    """Complete the task once every live part is confirmed."""
    live = task.assignee_rows.exclude(state=TaskAssignee.DECLINED)
    if live.exists() and not live.exclude(review_state=TaskAssignee.REVIEW_CONFIRMED).exists():
        old_state = task.state
        now = timezone.now()
        task.state = Task.COMPLETED
        task.completed_at = now
        task.reviewed_by = user
        task.reviewed_at = now
        task.save(update_fields=["state", "completed_at", "reviewed_by", "reviewed_at", "updated_at"])
        log_activity(task, "completed", actor=user, message="Every part confirmed; task completed.")
        _on_task_completed(task, approver=user)
        _emit_state_changed(task, old_state, user, "review_task", approved=True)
        return True
    return False


@transaction.atomic
def confirm_assignee_part(task, user, assignee):
    """H15: the creator (or reviewer) confirms one person's finished part.
    The task completes when every part is confirmed."""
    if not can_review(task, user):
        raise PermissionDenied("Only the creator or the reviewer can confirm work.")
    _refuse_if_externally_managed(task, "confirmed")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")
    row = task.assignee_rows.filter(user=assignee).first()
    if row is None:
        raise ValidationError("That person is not on this task.")
    if row.state != TaskAssignee.DONE:
        raise ValidationError("That person has not marked their part done yet.")
    if row.review_state == TaskAssignee.REVIEW_CONFIRMED:
        return task
    row.review_state = TaskAssignee.REVIEW_CONFIRMED
    row.reviewed_at = timezone.now()
    row.save(update_fields=["review_state", "reviewed_at", "updated_at"])
    name = assignee.get_full_name() or assignee.email
    _log_override(task, user, f"confirm {name}'s part")
    log_activity(task, "part_confirmed", actor=user, message=f"Confirmed {name}'s part.",
                 metadata={"assignee_id": assignee.pk})
    if not _complete_after_confirm(task, user):
        _notify_assignee(task, assignee, user, event="part_confirmed")
    return task


@transaction.atomic
def confirm_all_parts(task, user, notes=""):
    """H15: confirm everyone together (every part must be done)."""
    if not can_review(task, user):
        raise PermissionDenied("Only the creator or the reviewer can confirm work.")
    _refuse_if_externally_managed(task, "confirmed")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")
    live = task.assignee_rows.exclude(state=TaskAssignee.DECLINED)
    if live.exclude(state=TaskAssignee.DONE).exists():
        raise ValidationError("Not everyone has marked their part done yet.")
    if task.state == Task.AWAITING_REVIEW:
        return review_task(task, user, approve=True, notes=notes)
    now = timezone.now()
    live.update(review_state=TaskAssignee.REVIEW_CONFIRMED, reviewed_at=now)
    _log_override(task, user, "confirm every part")
    _complete_after_confirm(task, user)
    return task


@transaction.atomic
def return_assignee_part(task, user, assignee, reason):
    """H14: send one person's part back with a reason. Only that person is
    notified; their row goes back to in progress (the timer can restart) and
    everyone else's confirmed / done parts stay as they are."""
    if not can_review(task, user):
        raise PermissionDenied("Only the creator or the reviewer can return work.")
    _refuse_if_externally_managed(task, "returned")
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please say what needs to be done again.")
    row = task.assignee_rows.filter(user=assignee).first()
    if row is None:
        raise ValidationError("That person is not on this task.")
    if row.state != TaskAssignee.DONE:
        raise ValidationError("Only a part marked done can be returned.")
    old_state = task.state
    row.state = TaskAssignee.IN_PROGRESS
    row.completed_at = None
    row.review_state = TaskAssignee.REVIEW_RETURNED
    row.review_note = reason[:500]
    row.reviewed_at = timezone.now()
    row.save(update_fields=["state", "completed_at", "review_state", "review_note", "reviewed_at", "updated_at"])
    if task.state in (Task.AWAITING_REVIEW, Task.OPEN):
        task.state = Task.IN_PROGRESS
        task.save(update_fields=["state", "updated_at"])
    name = assignee.get_full_name() or assignee.email
    _log_override(task, user, f"return {name}'s part")
    log_activity(task, "part_returned", actor=user, message=f"Returned {name}'s part: {reason[:200]}",
                 metadata={"assignee_id": assignee.pk, "reason": reason[:500]})
    _notify_assignee(task, assignee, user, event="part_returned", body=f"{task.title}: {reason[:200]}")
    if old_state != task.state:
        _emit_state_changed(task, old_state, user, "return_assignee_part", assignee_id=assignee.pk)
    return task


@transaction.atomic
def cancel_task(task, user, reason=""):
    if not can_cancel_task(task, user):
        raise PermissionDenied("You cannot cancel this task.")
    _refuse_if_externally_managed(task, "cancelled")
    if task.state == Task.CANCELLED:
        return task

    old_state = task.state
    task.state = Task.CANCELLED
    task.cancelled_at = timezone.now()
    task.save(update_fields=["state", "cancelled_at", "updated_at"])
    log_activity(task, "cancelled", actor=user,
                 message=reason or "Task cancelled.")
    for row in task.assignee_rows.select_related("user"):
        _notify_assignee(task, row.user, user, event="cancelled")
    _send_task_email(task, event="cancelled")
    _emit_state_changed(task, old_state, user, "cancel_task", reason=(reason or "")[:500])
    return task


@transaction.atomic
def delete_task(task, user, reason=""):
    if not can_delete_task(task, user):
        raise PermissionDenied("You cannot delete this task.")
    _refuse_if_externally_managed(task, "deleted")
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
        or is_override(user)
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
def start_timer(task, user, auto=False):
    if task.state in Task.CLOSED_STATES:
        raise ValidationError("This task is closed.")
    if not task.assignee_rows.filter(user=user).exists():
        raise PermissionDenied("Only assignees can log time.")
    running = TaskTimeEntry.objects.filter(
        task=task, user=user, ended_at__isnull=True,
    ).first()
    if running:
        return running
    entry = TaskTimeEntry.objects.create(task=task, user=user)
    log_activity(task, "timer_started", actor=user,
                 message="Timer started automatically." if auto else "Timer started.",
                 metadata={"auto": bool(auto)})
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
# Internal notes (Phase 0 / H18, T-SEC-6)
# ===========================================================================
# Roles that always see internal notes. Phase B replaces this with org.*.
_INTERNAL_NOTE_ALWAYS_ROLES = ("executive-secretary",)


def can_see_internal(task, user):
    """May `user` read the task's internal notes?

    Yes for the people involved in the task - the creator, every assignee and
    the reviewer - and for people of higher rank than the creator in the
    task's department (its owning department, else the creator's); the
    Executive Secretary and superusers always. Everyone else who can open the
    task sees only the ordinary comments.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    import org
    if org.has_role(user, *_INTERNAL_NOTE_ALWAYS_ROLES):
        return True
    if task.created_by_id == user.pk or (task.reviewer_id and task.reviewer_id == user.pk):
        return True
    if task.assignee_rows.filter(user_id=user.pk).exists():
        return True
    dept_id = task.owner_department_id or getattr(task.created_by, "department_id", None)
    if dept_id and user.department_id == dept_id:
        return rank_of(user) > rank_of(task.created_by)
    return False


def visible_comments(task, user):
    """The task's comments `user` may read (internal notes filtered)."""
    from django.db.models import Q

    qs = task.comments.all()
    if can_see_internal(task, user):
        return qs
    return qs.filter(Q(is_internal=False) | Q(author_id=user.pk))


def visible_activities(task, user, queryset=None):
    """The task's timeline for `user`: the "commented" rows of internal
    notes carry the note text, so they are hidden with the notes."""
    qs = task.activities.all() if queryset is None else queryset
    if can_see_internal(task, user):
        return qs
    return qs.exclude(kind="commented", metadata__internal=True)


# ===========================================================================
# Comments
# ===========================================================================
@transaction.atomic
def add_comment(task, user, body, internal=False, pinned=False):
    if task.is_closed:
        raise ValidationError("This task is closed. Comments are read-only.")
    if internal and not can_see_internal(task, user):
        raise PermissionDenied("Only the people involved in this task can add internal notes.")
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


def _on_task_completed(task, approver=None):
    """Fires when a task lands in COMPLETED. H16: everyone involved (creator,
    assignees, reviewer, division head) hears that it was approved."""
    who = (approver.get_full_name() or approver.email) if approver is not None else ""
    body = (f"{who} approved the work; {task.reference} is complete." if who
            else f"Task {task.reference} is complete.")
    if not approver or approver.pk != task.created_by_id:
        _notify_creator(task, approver, body,
                        title_override=f"Task completed — {task.reference}")
    notified = {task.created_by_id}
    for row in task.assignee_rows.select_related("user"):
        notified.add(row.user_id)
        _notify_assignee(task, row.user, approver, event="completed", body=body)
    for extra in (task.reviewer, getattr(task.division, "head", None) if task.division_id else None):
        if extra is not None and extra.pk not in notified and (approver is None or extra.pk != approver.pk):
            notified.add(extra.pk)
            _notify_assignee(task, extra, approver, event="completed", body=body)
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
    "part_returned":    "Your part was returned",
    "part_confirmed":   "Your part was confirmed",
    "division_routed":  "A group task was routed to your division",
    "division_member":  "You were added to a division task",
}


def _notify_assignee(task, assignee, actor, event="assigned", body=None):
    if not assignee or not assignee.is_active:
        return
    title = _TITLE_BY_EVENT.get(event, "Task update")
    try:
        notify(
            recipient=assignee,
            title=f"{title} — {task.reference}",
            body=body or task.title,
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
def reminder_sweep(now=None):
    """Fire in-app (optionally email) reminders for approaching and
    overdue tasks. Returns counts.

    `now` lets tests / `manage.py fire_reminders --tasks` pretend it is another
    moment. Each task is reminded at most once per local day: the sweep runs
    hourly from Celery Beat and used to repeat the "due in N days" reminder on
    every run (24 identical notifications a day).
    """
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

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    fired_due = fired_overdue = 0

    def _already_reminded_today(task):
        last = task.last_reminder_at
        return bool(last and timezone.localtime(last).date() == today)

    for days in days_list:
        target = today + timedelta(days=days)
        for task in (
            Task.objects
            .filter(state__in=Task.ACTIVE_STATES, due_date=target)
            .prefetch_related("assignee_rows__user")
        ):
            if _already_reminded_today(task):
                continue
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

    result = {"due": fired_due, "overdue": fired_overdue}
    try:  # v2.6: record-the-visit tasks spawn on their own on a visit's last day
        from inspection.services import visit_recording_sweep

        result["visit_recording"] = visit_recording_sweep(now=now)
    except Exception:
        logger.exception("the visit-recording sweep failed")
    return result


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



# ===========================================================================
# What can this user do on the detail page? (Phase 9: shared by the website's
# task_detail view and the mobile API, so the two can never drift apart.)
# ===========================================================================
def available_actions(task, user, my_row=None, running_timer=None):
    """Return the ``can_*`` flags the task detail page uses.

    ``my_row`` / ``running_timer`` may be passed when the caller already
    fetched them; otherwise they are looked up.
    """
    if my_row is None:
        my_row = task.assignee_rows.filter(user=user).first()
    if running_timer is None:
        running_timer = TaskTimeEntry.objects.filter(
            task=task, user=user, ended_at__isnull=True,
        ).first()

    is_creator = task.created_by_id == user.pk
    is_assignee = my_row is not None
    closed = task.state in Task.CLOSED_STATES

    can_edit = can_edit_task(task, user)
    flags = {
        "is_creator": is_creator,
        "is_assignee": is_assignee,
        "can_edit": can_edit,
        "can_cancel": can_cancel_task(task, user),
        "can_delete": can_delete_task(task, user),
        "can_review": can_review(task, user) and task.state == Task.AWAITING_REVIEW,
        "can_assign": can_edit and task.state in (Task.DRAFT, Task.OPEN, Task.RETURNED),
        # Accept is only for someone who hasn't accepted yet.
        "can_accept": bool(is_assignee and my_row.state == TaskAssignee.ASSIGNED and not closed),
        # Start work only until the row is IN_PROGRESS.
        "can_start": bool(
            is_assignee
            and my_row.state in (TaskAssignee.ASSIGNED, TaskAssignee.ACCEPTED)
            and task.state in Task.ACTIVE_STATES
        ),
        "can_reopen": bool(
            task.state == Task.COMPLETED
            and (
                user.is_superuser
                or is_creator
                or (task.reviewer_id and task.reviewer_id == user.pk)
                or is_override(user)
            )
        ),
        # Timer controls — visible only while this user's row is IN_PROGRESS.
        "can_start_timer": bool(
            is_assignee and my_row.state == TaskAssignee.IN_PROGRESS
            and running_timer is None and not closed
        ),
        "can_stop_timer": bool(is_assignee and running_timer is not None and not closed),
        "can_ask": bool(
            is_assignee and task.state in (Task.OPEN, Task.IN_PROGRESS)
            and my_row.state != TaskAssignee.DONE
        ),
        "can_resume": bool(is_assignee and task.state in (Task.AWAITING_RESPONSE, Task.RETURNED)),
        "can_return": bool(
            is_assignee
            and task.state in (Task.OPEN, Task.IN_PROGRESS, Task.AWAITING_RESPONSE)
            and my_row.state != TaskAssignee.DONE
        ),
        "can_mark_done": bool(
            is_assignee
            and my_row.state in (TaskAssignee.ACCEPTED, TaskAssignee.IN_PROGRESS, TaskAssignee.BLOCKED)
            and task.state in Task.ACTIVE_STATES
        ),
        "can_resend": can_edit and task.state == Task.RETURNED,
        # H8: creator and assignees split, as often as needed.
        "can_split": can_split_task(task, user) and not task.linked_request_id,
        # H19 / T-CONS-6: closed tasks are read-only (the services refuse too).
        "can_comment": not task.is_closed,
        "can_attach": not task.is_closed,
        "can_download_report": task.is_closed,
        "qa_link_url": "",
        "qa_link_open": False,
    }
    # H14 / H15: per-person confirm / return by the creator (or reviewer).
    live_rows = [r for r in task.assignee_rows.all() if r.state != TaskAssignee.DECLINED]
    reviewer_ok = can_review(task, user) and not closed
    flags["can_confirm_parts"] = bool(reviewer_ok and live_rows)
    flags["can_confirm_all"] = bool(
        reviewer_ok and live_rows and all(r.state == TaskAssignee.DONE for r in live_rows)
        and any(r.review_state != TaskAssignee.REVIEW_CONFIRMED for r in live_rows))
    # H12: one button, "Accept and start" (accept + start + timer).
    flags["can_accept_start"] = flags["can_start"]
    # H23 / H24: division routing.
    from . import division as _division
    flags["can_route_division"] = bool(_division.can_route(task, user) and not task.linked_request_id)
    head_ok = _division.can_act_as_head(task, user) and not closed
    flags["can_division_accept"] = bool(head_ok and task.division_state == Task.DIV_PENDING)
    flags["can_division_complete"] = bool(head_ok and task.division_state == Task.DIV_ACCEPTED)
    if task.linked_request_id:
        for key in ("can_mark_done", "can_cancel", "can_delete", "can_review", "can_assign", "can_reopen",
                    "can_resend", "can_confirm_parts", "can_confirm_all", "can_route_division"):
            flags[key] = False
    # Phase 4: a task linked to a QA stage is driven from the QA screens —
    # it closes when the stage is approved or returned there, never here.
    try:
        from qa_core.task_bridge import is_linked, open_link_for, qa_url

        if is_linked(task):
            link = open_link_for(task) or task.qa_link
            flags["qa_link_url"], flags["qa_link_open"] = qa_url(link), link.closed_at is None
            for key in ("can_mark_done", "can_cancel", "can_delete", "can_review", "can_assign",
                        "can_reopen", "can_split", "can_resend", "can_confirm_parts", "can_confirm_all",
                        "can_route_division", "can_division_complete"):
                flags[key] = False
    except ImportError:  # the QA apps are not installed
        pass
    return flags
