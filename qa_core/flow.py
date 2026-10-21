"""The dynamic state-machine engine (Master Plan §1, §5.1, §5.7).

Status names and transitions are FlowStage / FlowTransition rows, so renaming
a status or adding a step is a settings change. The engine provides:

Guards (Phase 1)
* ``required_permission`` — a permission label the actor must hold;
* ``required_role_setting`` — a setting naming the role the actor must hold
  (e.g. ``qa.payment.final_approver_role``); superusers get no exception;
* ``condition`` — a formula over the subject's variables that must be true;
* ``requires_remark`` — a non-empty remark (every ``return``, R-24);
* ``requires_confirmation`` — must go through a PendingAction instead.

Phase 2 additions
* **Parallel gates.** Entering a ``parallel_gate`` stage fans out at once to
  every branch the gate's transitions point at (the onboarding clearances:
  Legal, Finance, Academic Planning, Physical Planning run side by side). A
  branch that moves to the gate's ``join`` stage leaves the set; the join is
  reached only when *every* branch has arrived. A branch that moves anywhere
  else (a ``return`` to draft) closes all its sibling branches with it.
* **Automatic stages.** A ``join`` stage, and any stage whose only way out is
  a transition whose action code starts with ``auto``, is passed straight
  through by the system (recorded as its own FlowEvent).
* **Effects.** ``FlowTransition.effects`` is a list of ``"name"`` or
  ``"name:argument"`` strings. Each names a function registered with
  :func:`register_effect` by the app that owns the behaviour (for example
  ``create_payment_obligation:institutional_processing`` from ``payments``).
  Effects run inside the transition's transaction, so a failing effect rolls
  the whole move back.
* **Status mirroring.** A subject model that declares ``FLOW_STATUS_FIELD``
  gets that field set to the current stage code after every move (while
  branches run in parallel, to the gate's code).
"""
from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.dispatch import Signal
from django.utils import timezone

import config

from .formula import FormulaError, evaluate_value

logger = logging.getLogger(__name__)

# Sent after every committed move: sender=FlowInstance class, instance=…,
# transition=…, subject=…, actor=…, from_stages=[…], to_stages=[…].
stage_changed = Signal()

AUTO_PREFIX = "auto"
MAX_AUTO_STEPS = 25


class TransitionRefused(PermissionDenied):
    """The actor may not take this transition (message says why)."""


# ---------------------------------------------------------------------------
# Effects registry
# ---------------------------------------------------------------------------
_EFFECTS = {}


def register_effect(name):
    """Decorator: ``@register_effect("create_payment_obligation")``.

    The function receives one :class:`EffectContext`."""
    def deco(func):
        _EFFECTS[name] = func
        return func
    return deco


def registered_effects():
    return sorted(_EFFECTS)


# ---------------------------------------------------------------------------
# Stage listeners (Phase 4, §8 step 1 "enter_stage"): called synchronously,
# inside the transition's transaction, after every move. The task bridge uses
# it to open a Task for each entered stage with `creates_task` and to close
# the tasks of the stages that were left. A listener that raises rolls the
# whole transition back.
# ---------------------------------------------------------------------------
_STAGE_LISTENERS = []


def on_stages_changed(func):
    """Decorator/registration: ``func(instance, entered, left, transition,
    actor, remark, subject, automatic)``; entered/left are stage codes."""
    if func not in _STAGE_LISTENERS:
        _STAGE_LISTENERS.append(func)
    return func


def _notify_listeners(instance, before, transition, actor, remark, subject, automatic=False):
    after = list(instance.current_stages)
    entered = [c for c in after if c not in before]
    left = [c for c in before if c not in after]
    # A self-loop (e.g. "add programme") changes nothing.
    if not entered and not left:
        return
    for func in list(_STAGE_LISTENERS):
        func(instance, entered, left, transition, actor, remark, subject, automatic)


class EffectContext:
    def __init__(self, *, instance, subject, transition, actor, argument, remark, metadata):
        self.instance = instance
        self.subject = subject
        self.transition = transition
        self.actor = actor
        self.argument = argument
        self.remark = remark
        self.metadata = metadata or {}


def _run_effects(transition, instance, subject, actor, remark, metadata):
    for raw in transition.effects or []:
        name, _, arg = str(raw).partition(":")
        name = name.strip()
        func = _EFFECTS.get(name)
        if func is None:
            raise ValidationError(f"Workflow effect '{name}' is not available (transition "
                                  f"{transition.from_stage.code} → {transition.to_stage.code}).")
        func(EffectContext(instance=instance, subject=subject, transition=transition, actor=actor,
                           argument=arg.strip(), remark=remark, metadata=metadata))


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def current_version(code):
    from .models import FlowDefinition

    return (FlowDefinition.objects.filter(code=code, is_active=True, published_at__isnull=False)
            .order_by("-version").first())


def instance_for(subject, code=None):
    """The newest FlowInstance whose subject is `subject` (optionally of flow `code`)."""
    from .models import FlowInstance

    if subject is None or getattr(subject, "pk", None) is None:
        return None
    qs = FlowInstance.objects.filter(
        content_type=ContentType.objects.get_for_model(subject, for_concrete_model=True),
        object_id=subject.pk,
    ).select_related("flow_version")
    if code:
        qs = qs.filter(flow_version__code=code)
    return qs.order_by("-id").first()


def stage(instance, code):
    return instance.flow_version.stages.filter(code=code).first()


def stages_of(instance):
    """FlowStage rows for the instance's current stages, in flow order."""
    return list(instance.flow_version.stages.filter(code__in=instance.current_stages).order_by("sort_order"))


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
def start(code, subject, *, actor=None):
    from .models import FlowEvent, FlowInstance, FlowStage

    flow = current_version(code)
    if flow is None:
        raise ValidationError(f"No published flow '{code}'.")
    start_stage = flow.stages.filter(kind=FlowStage.START).order_by("sort_order").first()
    if start_stage is None:
        raise ValidationError(f"Flow '{code}' has no start stage.")
    with transaction.atomic():
        instance = FlowInstance.objects.create(
            flow_version=flow,
            content_type=ContentType.objects.get_for_model(subject, for_concrete_model=True),
            object_id=subject.pk,
            current_stages=[start_stage.code],
        )
        FlowEvent.objects.create(instance=instance, from_stage="", to_stage=start_stage.code,
                                 action_code="start", actor=actor if getattr(actor, "pk", None) else None)
        _sync_status(instance, subject)
        _notify_listeners(instance, [], None, actor, "", subject)
    return instance


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def required_role_for(transition):
    """Role code the actor must hold for `transition`, or ''."""
    if not transition.required_role_setting:
        return ""
    return str(config.get(transition.required_role_setting, "") or "").strip()


def check(transition, actor, *, variables=None, remark=""):
    """Raise TransitionRefused / ValidationError unless `actor` may take it."""
    if transition.required_permission:
        if actor is None or not actor.has_perm(transition.required_permission):
            raise TransitionRefused(
                f"'{transition.label}' needs the permission {transition.required_permission}.")
    if transition.required_role_setting:
        role_code = required_role_for(transition)
        actor_role = getattr(getattr(actor, "role", None), "code", "")
        if not role_code or actor_role != role_code:
            raise TransitionRefused(
                f"'{transition.label}' can only be taken by the holder of the role "
                f"'{role_code or '(not set)'}' ({transition.required_role_setting}).")
    if transition.requires_remark and len((remark or "").strip()) < 3:
        raise ValidationError(f"'{transition.label}' needs a remark.")
    if transition.condition.strip():
        try:
            ok = evaluate_value(transition.condition, variables or {},
                                declared=list((variables or {}).keys()), returns="bool")
        except FormulaError as exc:
            raise ValidationError(f"Condition could not be checked: {exc}") from None
        if not ok:
            raise TransitionRefused(f"'{transition.label}' is not available: its condition is not met.")


def available(instance, actor, *, variables=None, from_stage=None):
    """Transitions out of the current stage(s) that `actor` may take now."""
    out = []
    for t in _outgoing(instance):
        if from_stage and t.from_stage.code != from_stage:
            continue
        try:
            check(t, actor, variables=variables, remark="(preview)")
        except (PermissionDenied, ValidationError):
            continue
        out.append(t)
    return out


def can(instance, action_code, *, from_stage=None):
    """True if `action_code` leaves one of the current stages (no actor checks)."""
    if instance is None:
        return False
    return any(t.action_code == action_code and (not from_stage or t.from_stage.code == from_stage)
               for t in _outgoing(instance))


def _outgoing(instance, stage_codes=None):
    from .models import FlowTransition

    codes = stage_codes if stage_codes is not None else instance.current_stages
    return list(FlowTransition.objects.filter(
        flow=instance.flow_version, from_stage__code__in=codes,
    ).select_related("from_stage", "to_stage").order_by("sort_order", "id"))


# ---------------------------------------------------------------------------
# Parallel regions
# ---------------------------------------------------------------------------
def _gate_of(flow, branch_code):
    """The parallel gate whose transitions lead into `branch_code`, or None."""
    from .models import FlowStage, FlowTransition

    t = (FlowTransition.objects.filter(flow=flow, to_stage__code=branch_code,
                                       from_stage__kind=FlowStage.PARALLEL_GATE)
         .select_related("from_stage").first())
    return t.from_stage if t else None


def _branches_of(gate):
    return [t.to_stage.code for t in gate.outgoing.select_related("to_stage").order_by("sort_order", "id")]


def branch_states(instance):
    """For an instance inside a parallel region: {branch_code: 'active'|'done'}."""
    from .models import FlowStage

    current = list(instance.current_stages)
    for code in current:
        gate = _gate_of(instance.flow_version, code)
        if gate is not None and gate.kind == FlowStage.PARALLEL_GATE:
            return {b: ("active" if b in current else "done") for b in _branches_of(gate)}
    return {}


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------
def transition(instance, action_code, actor, *, remark="", variables=None, metadata=None, from_stage=None):
    """Take `action_code` from the instance's current stage (or `from_stage`
    when several are current). Returns the updated instance. Raises
    TransitionRefused / ValidationError."""
    from .models import FlowInstance

    with transaction.atomic():
        instance = FlowInstance.objects.select_for_update().get(pk=instance.pk)
        if instance.paused:
            raise ValidationError("This record is on hold.")
        candidates = [t for t in _outgoing(instance) if t.action_code == action_code
                      and (not from_stage or t.from_stage.code == from_stage)]
        if not candidates:
            raise ValidationError(f"'{action_code}' is not possible from "
                                  f"{', '.join(instance.current_stages) or 'nowhere'}.")
        chosen = candidates[0]
        check(chosen, actor, variables=variables, remark=remark)
        if chosen.requires_confirmation:
            raise ValidationError(f"'{chosen.label}' needs a second officer (pending confirmation).")
        subject = instance.subject
        before = list(instance.current_stages)
        _move(instance, chosen, actor, remark=remark, metadata=metadata, subject=subject)
        _auto_follow(instance, actor, subject)
        _sync_status(instance, subject)
        after = list(instance.current_stages)
        transaction.on_commit(lambda: stage_changed.send(
            sender=FlowInstance, instance=instance, transition=chosen, subject=subject, actor=actor,
            from_stages=before, to_stages=after))
    return instance


def apply_confirmed(instance, action_code, actor, *, remark="", metadata=None, from_stage=None):
    """Take a ``requires_confirmation`` transition once its PendingAction has
    been confirmed (called only from an ActionHandler.apply)."""
    from .models import FlowInstance

    with transaction.atomic():
        instance = FlowInstance.objects.select_for_update().get(pk=instance.pk)
        candidates = [t for t in _outgoing(instance) if t.action_code == action_code
                      and (not from_stage or t.from_stage.code == from_stage)]
        if not candidates:
            raise ValidationError(f"'{action_code}' is not possible from "
                                  f"{', '.join(instance.current_stages) or 'nowhere'}.")
        chosen = candidates[0]
        if chosen.requires_remark and len((remark or "").strip()) < 3:
            raise ValidationError(f"'{chosen.label}' needs a remark.")
        subject = instance.subject
        _move(instance, chosen, actor, remark=remark, metadata=metadata, subject=subject)
        _auto_follow(instance, actor, subject)
        _sync_status(instance, subject)
    return instance


def force_stage(instance, stage_code, actor, *, remark, metadata=None):
    """Move an instance to an earlier stage with a compensating event
    ("Revert QA History", §6). Never deletes events."""
    from .models import FlowEvent, FlowInstance

    with transaction.atomic():
        instance = FlowInstance.objects.select_for_update().get(pk=instance.pk)
        target = instance.flow_version.stages.filter(code=stage_code).first()
        if target is None:
            raise ValidationError(f"Stage '{stage_code}' is not part of this workflow.")
        before = list(instance.current_stages)
        instance.current_stages = [stage_code]
        instance.completed_at = None
        instance.outcome = ""
        instance.save(update_fields=["current_stages", "completed_at", "outcome", "updated_at"])
        FlowEvent.objects.create(
            instance=instance, from_stage=",".join(before), to_stage=stage_code, action_code="revert",
            actor=actor if getattr(actor, "pk", None) else None,
            actor_role_label=getattr(getattr(actor, "role", None), "name", "")[:100],
            remark=(remark or "").strip(), metadata={"compensating": True, **(metadata or {})})
        _sync_status(instance, instance.subject)
        _notify_listeners(instance, before, None, actor, remark, instance.subject)
    return instance


def _move(instance, chosen, actor, *, remark, metadata, subject, automatic=False):
    """Record one move and run its effects (inside the caller's atomic block)."""
    from .models import FlowEvent, FlowStage

    last = instance.events.order_by("-at", "-id").first()
    now = timezone.now()
    flow_def = instance.flow_version
    src, dst = chosen.from_stage, chosen.to_stage
    before_move = list(instance.current_stages)
    stages = [s for s in instance.current_stages if s != src.code]

    gate = _gate_of(flow_def, src.code) if src.code in instance.current_stages else None
    if gate is not None:
        siblings = set(_branches_of(gate)) - {src.code}
        if dst.kind == FlowStage.JOIN:
            # Arrive at the join only when no sibling branch is still running.
            if not any(s in siblings for s in stages):
                stages.append(dst.code)
        else:
            # Leaving the region (e.g. a return to draft) closes every sibling.
            stages = [s for s in stages if s not in siblings]
            stages.append(dst.code)
    elif dst.kind == FlowStage.PARALLEL_GATE:
        stages.extend(b for b in _branches_of(dst) if b not in stages)
    else:
        stages.append(dst.code)

    instance.current_stages = stages
    terminal = dst.kind == FlowStage.END or dst.is_terminal_success or dst.is_terminal_failure
    if terminal and stages == [dst.code]:
        instance.completed_at = now
        instance.outcome = dst.code
    instance.save(update_fields=["current_stages", "completed_at", "outcome", "updated_at"])
    meta = dict(metadata or {})
    if automatic:
        meta["automatic"] = True
    if dst.kind == FlowStage.PARALLEL_GATE:
        meta["branches"] = _branches_of(dst)
    FlowEvent.objects.create(
        instance=instance, from_stage=src.code, to_stage=dst.code,
        action_code=chosen.action_code, actor=actor if getattr(actor, "pk", None) else None,
        actor_role_label=("System" if automatic else getattr(getattr(actor, "role", None), "name", ""))[:100],
        remark=(remark or "").strip(), metadata=meta, at=now,
        duration_seconds=int((now - last.at).total_seconds()) if last else None,
    )
    _run_effects(chosen, instance, subject, actor, remark, metadata)
    _notify_listeners(instance, before_move, chosen, actor, remark, subject, automatic)


def _auto_follow(instance, actor, subject):
    """Pass through join stages and `auto*` system stages."""
    from .models import FlowStage

    for _ in range(MAX_AUTO_STEPS):
        moved = False
        for code in list(instance.current_stages):
            st = instance.flow_version.stages.filter(code=code).first()
            if st is None:
                continue
            outs = _outgoing(instance, [code])
            autos = [t for t in outs if t.action_code.startswith(AUTO_PREFIX)]
            if st.kind == FlowStage.JOIN and len(outs) == 1:
                autos = outs
            if len(autos) == 1 and (st.kind in (FlowStage.JOIN, FlowStage.SYSTEM, FlowStage.START)
                                    or len(outs) == 1):
                _move(instance, autos[0], actor, remark="", metadata=None, subject=subject, automatic=True)
                moved = True
                break
        if not moved:
            return


def _sync_status(instance, subject):
    field = getattr(type(subject), "FLOW_STATUS_FIELD", None) if subject is not None else None
    if not field:
        return
    stages = list(instance.current_stages)
    value = ""
    if stages:
        # A stage inside a parallel region reports the gate's code, even when
        # only one branch is still running.
        gate = _gate_of(instance.flow_version, stages[0])
        value = gate.code if gate is not None else stages[0]
    if getattr(subject, field) != value:
        setattr(subject, field, value)
        model = type(subject)
        manager = getattr(model, "unscoped", None) or model._base_manager
        updates = {field: value}
        if hasattr(subject, "updated_at"):
            updates["updated_at"] = timezone.now()
            subject.updated_at = updates["updated_at"]
        manager.filter(pk=subject.pk).update(**updates)


def routes_into(flow, stage_code):
    """Every transition of `flow` whose target is `stage_code`."""
    return list(flow.transitions.filter(to_stage__code=stage_code).select_related("from_stage", "to_stage"))


def public_label(instance):
    """The plain-language status for institutions (Requirement 18)."""
    if instance is None:
        return ""
    stages = stages_of(instance)
    if stages:
        gate = _gate_of(instance.flow_version, stages[0].code)
        if gate is not None:
            return gate.public_label or gate.label
        return stages[0].public_label or stages[0].label
    return ""


def history(instance):
    if instance is None:
        return []
    return list(instance.events.select_related("actor").order_by("at", "id"))
