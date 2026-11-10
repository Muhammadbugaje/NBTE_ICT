"""The QA ↔ Tasks bridge (Master Plan §8, Phase 4) — system code.

* A flow stage with ``creates_task`` gets a normal ``tasks.Task`` when it is
  entered (bell notification, *Assigned to me*), linked by ``QATaskLink``.
  The assignee comes from the stage's ``actor_kind`` resolver against the
  case; after a *return* the new task goes to the exact person who submitted
  from that stage, with the remark pinned as the first comment.
* Linked tasks are **externally managed**: the Tasks app cannot mark them
  done, cancel or delete them; they show *Open in QA*. When the QA action is
  taken the bridge closes the task itself (``close_reason`` approved /
  returned / superseded).
* ``task_state_changed`` receivers keep both sides consistent: a
  reassignment in Tasks is re-validated (who may reassign; R-01, R-02, R-20)
  and rolled back when it breaks a rule; "return to assigner" flags the case
  for reassignment.
* ``send_stage_reminders()`` (SLA reminders at ``qa.stage.reminder_at_percent``)
  and ``reconcile()`` (the nightly drift repair) are run by management
  commands.
"""
# scoped-by-caller
from __future__ import annotations

import contextlib
import contextvars
import datetime
import json
import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config

from . import flow, timeline

logger = logging.getLogger(__name__)

_BRIDGE_ACTIVE = contextvars.ContextVar("qa_task_bridge_active", default=False)
_SUBJECT_MODELS = {}   # model label → callable(subject) -> info dict (registered by accreditation)


# ===========================================================================
# Registration
# ===========================================================================
def register_subject(model, resolver):
    """``resolver(subject, stage, prefer_user)`` → (users, department, title_suffix)."""
    _SUBJECT_MODELS[model._meta.label_lower] = resolver


def _resolver_for(subject):
    if subject is None:
        return None
    return _SUBJECT_MODELS.get(subject._meta.label_lower)


@contextlib.contextmanager
def bridge_active():
    from tasks.hooks import external_close

    token = _BRIDGE_ACTIVE.set(True)
    try:
        with external_close():
            yield
    finally:
        _BRIDGE_ACTIVE.reset(token)


def is_bridge_active():
    return _BRIDGE_ACTIVE.get()


def is_linked(task):
    from .models import QATaskLink

    return QATaskLink.objects.filter(task_id=task.pk).exists()


def open_link_for(task):
    from .models import QATaskLink

    return QATaskLink.objects.filter(task_id=task.pk, closed_at__isnull=True).first()


def system_user():
    from accounts.models import User

    from .seed_data_phase4 import SYSTEM_USER

    email, first, last = SYSTEM_USER
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User(email=email, first_name=first, last_name=last, user_type="staff", is_active=True,
                    is_superuser=False, is_staff=False)
        user.set_unusable_password()
        user.save()
    return user


def qa_url(link):
    """Where *Open in QA* goes for a link."""
    model = link.content_type.model
    if model == "qacase":
        if link.stage_code == "capacity_assessment":
            return f"/qa/cases/{link.object_id}/?tab=capacity&stage=post_visit_recommendation"
        if link.stage_code.startswith("case_change_"):
            request_id = link.stage_code[len("case_change_"):].split("_", 1)[0]
            from accreditation.models import CaseChangeRequest

            request = CaseChangeRequest.unscoped.filter(pk=request_id).only("kind").first()
            tab = "visit" if request and request.kind == CaseChangeRequest.VISIT_DATE else "overview"
            return f"/qa/cases/{link.object_id}/?tab={tab}#change-request-{request_id}"
        return f"/qa/cases/{link.object_id}/?stage={link.stage_code}"
    if model == "postvisitreport":  # Phase 5: the report sub-flow
        return f"/qa/inspection/reports/{link.object_id}/"
    if model == "inspection":
        return f"/qa/inspection/inspections/{link.object_id}/"
    if model == "complaint":  # Phase 7: complaint stages and escalations
        return f"/qa/complaints/{link.object_id}/"
    return "/qa/"


# ===========================================================================
# Opening and closing stage tasks
# ===========================================================================
def _sla_hours(stage):
    if stage.sla_hours:
        return int(stage.sla_hours)
    try:
        return int(config.get("qa.stage.default_sla_hours", 72))
    except (TypeError, ValueError):
        return 72


def _category(stage):
    if stage.task_category_id:
        return stage.task_category
    from tasks.models import TaskCategory

    return TaskCategory.objects.filter(code="qa-sop03").first()


def submitter_of(instance, stage_code):
    """The user who last moved the instance *out of* ``stage_code`` (the
    'exact submitting node' a return goes back to)."""
    event = (instance.events.filter(from_stage=stage_code).exclude(action_code__in=("return", "revert"))
             .exclude(actor__isnull=True).order_by("-at", "-id").first())
    return event.actor if event else None


def open_for_stage(subject, instance, stage, *, actor=None, remark="", prefer_user=None, returned_from=""):
    """Create, assign and open the Task for ``stage`` of ``subject``."""
    from tasks import services as task_services
    from tasks.models import Task, TaskAssignee

    from .models import QATaskLink

    resolver = _resolver_for(subject)
    if resolver is None:
        return None
    ct = ContentType.objects.get_for_model(subject, for_concrete_model=True)
    existing = QATaskLink.objects.filter(content_type=ct, object_id=subject.pk, stage_code=stage.code,
                                         kind="stage_action", closed_at__isnull=True).first()
    if existing is not None:
        return existing
    users, department, title = resolver(subject, stage, prefer_user)
    sys_user = system_user()
    hours = _sla_hours(stage)
    due_at = timezone.now() + datetime.timedelta(hours=hours)
    no = f"Stage {stage.sop_stage_no} · " if stage.sop_stage_no else ""
    description = (f"{stage.label}.\n\nThis task belongs to the QA system: open it in QA to act "
                   f"(approve or return). It closes by itself when you do.")
    with bridge_active():
        task = task_services.create_task(
            created_by=sys_user, title=f"[QA] {no}{stage.label} — {title}"[:200], description=description,
            category=_category(stage), owner_department=department, due_date=timezone.localdate(due_at),
            requires_review=False, priority="high" if returned_from else None)
        for user in users:
            # QA decides who acts (the stage resolver); the Tasks capacity and
            # rank rules are not applied to the system's own stage tasks.
            if not TaskAssignee.objects.filter(task=task, user=user).exists():
                TaskAssignee.objects.create(task=task, user=user, state=TaskAssignee.ASSIGNED,
                                            assigned_by=sys_user)
                task_services.log_activity(task, "assignee_added", actor=sys_user,
                                           message=f"Assigned by QA to {user.get_full_name() or user.email}",
                                           metadata={"assignee_id": user.pk})
        link = QATaskLink.objects.create(task=task, content_type=ct, object_id=subject.pk, flow_instance=instance,
                                         stage_code=stage.code, kind="stage_action", due_at=due_at)
        if users:
            task_services.open_task(task, sys_user)
        if returned_from and remark:
            who = actor.get_full_name() if getattr(actor, "pk", None) else "NBTE"
            comment = task_services.add_comment(task, sys_user, f"Returned by {who} ({returned_from}): {remark}")
            task_services.pin_comment(task, sys_user, comment)
    if not users:
        _flag_unassigned(subject, task, stage, department)
    inst = getattr(subject, "institution", None)
    if inst is not None:
        names = ", ".join(u.get_full_name() or u.email for u in users) or "unassigned (department queue)"
        timeline.record(inst, "qa_task.opened", subject=subject, actor=actor, visibility="internal",
                        internal_text=f"{no}{stage.label}: task {task.reference} for {names}"
                                      + (f" (returned: {remark})" if returned_from else "") + ".")
    return link


def _flag_unassigned(subject, task, stage, department):
    if hasattr(subject, "needs_reassignment"):
        type(subject).unscoped.filter(pk=subject.pk).update(needs_reassignment=True)
        subject.needs_reassignment = True
    heads = _department_heads(department)
    _notify(heads, "qa_stage.needs_reassignment",
            {"title": task.title, "who": "The QA system", "reason": "Nobody could be resolved for this stage."},
            url=f"/tasks/{task.pk}/")


def _department_heads(department):
    from institutions.services import staff_in_department

    return staff_in_department(department, roles=["director", "deputy-director", "division-head"])


def _notify(users, event, context, url=""):
    if not users:
        return
    from institutions.services import notify_role

    try:
        notify_role(event, context, users, url=url)
    except Exception:  # notifications never break the workflow
        logger.exception("QA notification %s failed", event)


def close_for_stage(subject, stage_code, *, reason, actor=None):
    """Close every open task of ``stage_code`` on ``subject`` (from QA)."""
    from tasks import services as task_services
    from tasks.models import Task, TaskAssignee

    from .models import QATaskLink

    ct = ContentType.objects.get_for_model(subject, for_concrete_model=True)
    closed = []
    for link in QATaskLink.objects.filter(content_type=ct, object_id=subject.pk, stage_code=stage_code,
                                          closed_at__isnull=True).select_related("task"):
        _close_link(link, reason=reason, actor=actor)
        closed.append(link)
    return closed


def _close_link(link, *, reason, actor=None):
    from tasks import services as task_services
    from tasks.models import Task, TaskAssignee

    task = link.task
    with bridge_active():
        if task.state not in Task.CLOSED_STATES:
            rows = list(task.assignee_rows.exclude(state__in=[TaskAssignee.DONE, TaskAssignee.DECLINED])
                        .select_related("user"))
            note = f"Closed by QA ({reason})" + (f" — {actor.get_full_name() or actor.email}"
                                                  if getattr(actor, "pk", None) else "")
            for row in rows:
                task_services.mark_assignee_done(task, row.user, note=note)
            task.refresh_from_db()
            if task.state not in Task.CLOSED_STATES:
                task.state = Task.COMPLETED
                task.completed_at = timezone.now()
                task.save(update_fields=["state", "completed_at", "updated_at"])
                task_services.log_activity(task, "completed", actor=system_user(), message=note)
    link.closed_at = timezone.now()
    link.close_reason = reason[:40]
    link.save(update_fields=["closed_at", "close_reason", "updated_at"])


# ===========================================================================
# The flow listener (stage entered / left)
# ===========================================================================
@flow.on_stages_changed
def _on_stages_changed(instance, entered, left, transition, actor, remark, subject, automatic):
    if _resolver_for(subject) is None:
        return
    action = getattr(transition, "action_code", "") or "revert"
    reason = "returned" if action == "return" else "approved"
    source = transition.from_stage.code if transition is not None else None
    for code in left:
        # The stage acted on closes as approved/returned; siblings closed by a
        # return out of a parallel region, and forced moves, are "superseded".
        close_for_stage(subject, code, reason=reason if code == source else ("reverted" if transition is None
                                                                                else "superseded"),
                        actor=actor)
    stages = {s.code: s for s in instance.flow_version.stages.filter(code__in=entered)}
    for code in entered:
        stage = stages.get(code)
        if stage is None or not stage.creates_task:
            continue
        prefer = submitter_of(instance, code) if action in ("return", "revert") else None
        open_for_stage(subject, instance, stage, actor=actor, remark=remark, prefer_user=prefer,
                       returned_from=(getattr(getattr(transition, "from_stage", None), "label", "") or "revert")
                       if action in ("return", "revert") else "")


# ===========================================================================
# Tasks → QA (signal receivers)
# ===========================================================================
def _stage_of(link):
    from .models import FlowStage

    if link.flow_instance_id is None:
        return None
    return FlowStage.objects.filter(flow=link.flow_instance.flow_version, code=link.stage_code).first()


def may_reassign(actor, task):
    """(ok, reason): who may reassign a QA task (§8 step 7)."""
    if actor is None:
        return False, "No user."
    if actor.is_superuser:
        return True, ""
    role = getattr(getattr(actor, "role", None), "code", "")
    if role == "ict-qa-monitor":
        return False, "The ICT monitor cannot reassign QA tasks (R-13)."
    if task.assignee_rows.filter(user=actor).exists():
        return False, "The assignee cannot reassign a QA task; use “Return to assigner” instead."
    if role == "executive-secretary":
        return True, ""
    if not actor.has_perm("qa_core.reassign_qa_task"):
        return False, "You do not hold qa_core.reassign_qa_task."
    if actor.has_perm("qa_core.confirm_any_department"):
        return True, ""
    if task.owner_department_id and actor.department_id != task.owner_department_id:
        return False, "This QA task belongs to another department."
    return True, ""


def validate_assignee(subject, stage, user):
    """Raise ValidationError when ``user`` may not hold ``stage`` of ``subject``
    (R-01 workload, R-02 reviewer ≠ visitor, R-20 grade range)."""
    validator = getattr(subject, "qa_validate_assignee", None)
    if validator is not None:
        validator(stage, user)


def _on_task_state_changed(sender, task, old, new, actor, action, metadata, **kwargs):
    link = open_link_for(task)
    if link is None:
        return
    if (link.kind == "assignment" and link.stage_code.startswith("case_change_")
            and action in ("reassign", "return_to_assigner")):
        raise PermissionDenied("This QA assignment is managed from its case workspace.")
    if action == "reassign":
        if is_bridge_active():
            return
        ok, why = may_reassign(actor, task)
        if not ok:
            raise PermissionDenied(f"Reassignment refused: {why}")
        from accounts.models import User

        to_user = User.objects.filter(pk=metadata.get("to_user_id")).first()
        subject = link.subject
        stage = _stage_of(link)
        try:
            validate_assignee(subject, stage, to_user)
        except ValidationError as exc:
            raise ValidationError(["Reassignment refused: " + " ".join(exc.messages)]) from None
        _after_reassign(link, task, subject, stage, to_user, actor)
    elif action == "return_to_assigner":
        subject = link.subject
        if hasattr(subject, "needs_reassignment"):
            type(subject).unscoped.filter(pk=subject.pk).update(needs_reassignment=True)
        _notify(_department_heads(task.owner_department), "qa_stage.needs_reassignment",
                {"title": task.title, "who": actor.get_full_name() or actor.email,
                 "reason": (metadata or {}).get("reason", "")}, url=f"/tasks/{task.pk}/")
        inst = getattr(subject, "institution", None)
        if inst is not None:
            timeline.record(inst, "qa_task.returned_by_assignee", subject=subject, actor=actor,
                            visibility="internal",
                            internal_text=f"{task.reference} returned by the assignee: "
                                          f"{(metadata or {}).get('reason', '')}")
    elif action in ("mark_assignee_done", "cancel_task", "review_task") and not is_bridge_active():
        raise PermissionDenied("This task is managed by the QA system. Use “Open in QA”.")


def _after_reassign(link, task, subject, stage, to_user, actor):
    on_reassigned = getattr(subject, "qa_on_reassigned", None)
    if on_reassigned is not None:
        on_reassigned(stage, to_user, actor)
    if link.flow_instance_id:
        from .models import FlowEvent

        FlowEvent.objects.create(instance=link.flow_instance, from_stage=link.stage_code,
                                 to_stage=link.stage_code, action_code="reassigned", actor=actor,
                                 actor_role_label=getattr(getattr(actor, "role", None), "name", "")[:100],
                                 remark=f"Task {task.reference} reassigned to "
                                        f"{to_user.get_full_name() or to_user.email}",
                                 metadata={"task": task.reference, "to_user": to_user.pk})
    inst = getattr(subject, "institution", None)
    if inst is not None:
        timeline.record(inst, "qa_task.reassigned", subject=subject, actor=actor, visibility="internal",
                        internal_text=f"{task.reference} reassigned to {to_user.get_full_name() or to_user.email}.")


@transaction.atomic
def reassign_stage_task(link, *, actor, from_user, to_user):
    """Reassign a QA stage task from the QA screens (§8 step 7): the same
    checks as a reassignment in Tasks (who may reassign; R-01, R-02, R-20),
    without the Tasks rank rules that would block e.g. the ES."""
    from tasks.models import Task, TaskAssignee
    from tasks.services import log_activity

    task = Task.objects.select_for_update().get(pk=link.task_id)
    if link.closed_at is not None or task.state in Task.CLOSED_STATES:
        raise ValidationError("This stage task is already closed.")
    ok, why = may_reassign(actor, task)
    if not ok:
        raise PermissionDenied(f"Reassignment refused: {why}")
    if to_user is None or not to_user.is_active:
        raise ValidationError("Choose an active member of staff.")
    row = task.assignee_rows.filter(user=from_user).first() if from_user is not None else None
    if from_user is not None and row is None:
        raise ValidationError("That person does not hold this task.")
    if to_user == from_user or task.assignee_rows.filter(user=to_user).exists():
        raise ValidationError(f"{to_user.get_full_name() or to_user.email} already holds this task.")
    subject, stage = link.subject, _stage_of(link)
    try:
        validate_assignee(subject, stage, to_user)
    except ValidationError as exc:
        raise ValidationError(["Reassignment refused: " + " ".join(exc.messages)]) from None
    if row is not None:
        row.delete()
    TaskAssignee.objects.create(task=task, user=to_user, state=TaskAssignee.ASSIGNED, assigned_by=actor)
    if task.state == Task.RETURNED:
        Task.objects.filter(pk=task.pk).update(state=Task.OPEN)
    log_activity(task, "reassigned", actor=actor,
                 message=f"Reassigned from {from_user or 'the department queue'} to {to_user} (QA)",
                 metadata={"from_id": getattr(from_user, "pk", None), "to_id": to_user.pk})
    _after_reassign(link, task, subject, stage, to_user, actor)
    try:
        from tasks.services import _notify_assignee

        _notify_assignee(task, to_user, actor, event="reassigned")
    except Exception:  # notification failure never blocks the reassignment
        logger.exception("could not notify the new assignee of %s", task.reference)
    return task


def connect():
    from tasks.hooks import register_external_manager
    from tasks.signals import task_state_changed

    register_external_manager(is_linked)
    task_state_changed.connect(_on_task_state_changed, dispatch_uid="qa-task-bridge", weak=False)


# ===========================================================================
# SLA reminders (qa.stage.reminder_at_percent) and breach notices
# ===========================================================================
def _percents():
    raw = config.get("qa.stage.reminder_at_percent", [50, 90])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [50, 90]
    out = []
    for p in raw or []:
        try:
            out.append(int(p))
        except (TypeError, ValueError):
            continue
    return sorted(set(p for p in out if 0 < p < 100))


def send_stage_reminders(now=None):
    """Remind the holders of open QA stage tasks at each configured
    percentage of the stage SLA (once each), and notify on breach. Returns a
    list of (link id, percent or 'breach')."""
    from .models import QATaskLink

    now = now or timezone.now()
    sent = []
    percents = _percents()
    # Phase 7: complaints keep their own business-hour clocks and escalation
    # chain (complaints.services.sla_sweep), so their links are not reminded here.
    for link in (QATaskLink.objects.filter(closed_at__isnull=True, due_at__isnull=False, kind="stage_action")
                 .exclude(content_type__app_label="complaints").select_related("task")):
        total = (link.due_at - link.opened_at).total_seconds()
        if total <= 0:
            continue
        elapsed = (now - link.opened_at).total_seconds()
        pct_now = elapsed * 100.0 / total
        holders = [r.user for r in link.task.assignee_rows.select_related("user")
                   if r.state not in ("done", "declined")]
        done = set(link.reminders_sent or [])
        new = [p for p in percents if pct_now >= p and p not in done]
        due_text = timezone.localtime(link.due_at).strftime("%d %b %Y %H:%M")
        if new:
            p = max(new)
            _notify(holders, "qa_stage.reminder", {"title": link.task.title, "percent": p, "due": due_text},
                    url=f"/tasks/{link.task_id}/")
            link.reminders_sent = sorted(done | set(new))
            link.save(update_fields=["reminders_sent", "updated_at"])
            sent.append((link.pk, p))
        if pct_now >= 100 and link.breach_notified_at is None:
            _notify(holders + _department_heads(link.task.owner_department), "qa_stage.breached",
                    {"title": link.task.title, "due": due_text}, url=f"/tasks/{link.task_id}/")
            link.breach_notified_at = now
            link.save(update_fields=["breach_notified_at", "updated_at"])
            sent.append((link.pk, "breach"))
    return sent


# ===========================================================================
# Nightly reconciliation (drift repair) — §8 step 5
# ===========================================================================
_RECONCILE_CHECKS = []


def register_reconcile_check(fn):
    """Extra report-only checks for the nightly run (Phase 5: people who are
    no longer eligible, §9). ``fn()`` returns a list of detail dicts."""
    if fn not in _RECONCILE_CHECKS:
        _RECONCILE_CHECKS.append(fn)


def reconcile(*, dry_run=False):
    """Reconcile stage tasks with their flow; subsystem-owned assignments keep
    their own lifecycle. Writes a TaskLinkDriftReport for the ICT monitor."""
    from tasks.models import Task

    from .models import FlowInstance, FlowStage, QATaskLink, TaskLinkDriftReport

    details, opened, closed, checked = [], 0, 0, 0
    with transaction.atomic():
        for link in QATaskLink.objects.filter(closed_at__isnull=True).select_related("task", "flow_instance"):
            checked += 1
            inst = link.flow_instance
            problem = ""
            if inst is None or inst.completed_at is not None:
                problem = "the workflow has ended"
            elif link.kind != "assignment" and link.stage_code not in (inst.current_stages or []):
                problem = f"the case is no longer at {link.stage_code}"
            elif link.task.state in (Task.CANCELLED, Task.COMPLETED):
                problem = f"the task was {link.task.state} outside QA"
            if problem:
                details.append({"kind": "orphaned", "task": link.task.reference, "stage": link.stage_code,
                                "subject": f"{link.content_type.model}#{link.object_id}", "why": problem})
                if not dry_run:
                    _close_link(link, reason="reconciled")
                closed += 1
        models_with_bridge = [label for label in _SUBJECT_MODELS]
        for inst in FlowInstance.objects.filter(completed_at__isnull=True).select_related("flow_version",
                                                                                           "content_type"):
            label = f"{inst.content_type.app_label}.{inst.content_type.model}"
            if label not in models_with_bridge:
                continue
            subject = inst.subject
            if subject is None:
                continue
            stages = {s.code: s for s in FlowStage.objects.filter(flow=inst.flow_version,
                                                                  code__in=inst.current_stages or [])}
            for code in inst.current_stages or []:
                stage = stages.get(code)
                if stage is None or not stage.creates_task:
                    continue
                if QATaskLink.objects.filter(flow_instance=inst, stage_code=code, kind="stage_action",
                                             closed_at__isnull=True).exists():
                    continue
                details.append({"kind": "missing", "stage": code, "subject": f"{inst.content_type.model}#"
                                f"{inst.object_id}", "why": "no open task for the current stage"})
                if not dry_run:
                    open_for_stage(subject, inst, stage, prefer_user=None)
                opened += 1
        for check in _RECONCILE_CHECKS:  # report only: nothing is repaired
            details.extend(check())
        # A dry run changes nothing but still keeps its report (marked "dry run").
        report = TaskLinkDriftReport.objects.create(checked=checked, opened=opened, closed=closed, details=details,
                                                    dry_run=dry_run)
    return report
