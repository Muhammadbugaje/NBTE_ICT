"""Complaints engine (Master Plan §11) — system code.

    raise_complaint · act (any transition of the complaint's flow) ·
    reply / add_message · escalate_manually · place_hold ·
    sla_sweep (reminders, breaches, escalation levels, auto-close) ·
    kb_search · visible_to / can_view / can_act

Nothing about a complaint's path is in code: the layer picks the flow, the
flow's stages and transitions are ``qa_core`` rows, routing rules pick who
handles each stage, escalation rules pick who is told after a breach. The
only action codes this module knows are the raiser's own (reply, confirm,
reopen, withdraw) and the system's (auto_close).

Views never query complaint models themselves: they call ``visible_to``,
which applies the complaint visibility rules (raiser, handling department,
watchers, oversight) — institution scoping alone would hide a payment
complaint from the Finance officer it is routed to.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging
import re

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

import config
from qa_core import flow, task_bridge, timeline
from qa_core.departments import qa_department

from . import sla
from .models import (
    Complaint,
    ComplaintAttachment,
    ComplaintLayer,
    ComplaintMessage,
    ComplaintStage,
    Escalation,
    EscalationRule,
    KnowledgeArticle,
    RoutingRule,
)

logger = logging.getLogger(__name__)

RAISER_ACTIONS = ("raiser_replied", "confirm", "reopen", "withdraw")
SYSTEM_ACTIONS = ("auto_close",)
HANDLE_PERM = "complaints.handle_complaint"
QUEUE_PERM = "complaints.view_complaint_queue"
DEFAULT_FLOW = "complaint"
RESOLVED_CODES = ("resolved",)


# ===========================================================================
# Settings
# ===========================================================================
def _int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool(key, default=False):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _json_list(key):
    import json

    raw = config.get(key, [])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            return []
    return [str(v).strip() for v in (raw or []) if str(v).strip()]


def reopen_window_days():
    return _int("qa.complaints.reopen_window_days", 14)


def auto_close_days():
    return _int("qa.complaints.auto_close_days", 5)


def show_officer_name():
    return _bool("qa.complaints.show_officer_name_to_raiser", False)


# ===========================================================================
# Who sees what
# ===========================================================================
def _user_type(user):
    return getattr(user, "user_type", "staff")


def sees_everything(user):
    """NBTE-wide complaint oversight: the ES and the ICT QA monitor (they hold
    the oversight board). Seeing every institution (Finance does, for bills)
    is not enough: a complaint is shown to the people it is routed to."""
    return bool(user.is_superuser or user.has_perm("qa_core.view_oversight_board"))


def is_handler(user):
    return (user.is_authenticated and _user_type(user) == "staff"
            and (user.is_superuser or user.has_perm(HANDLE_PERM) or user.has_perm(QUEUE_PERM)
                 or user.has_perm("complaints.view_complaint_reports") or sees_everything(user)))


def visible_to(user):
    """Every complaint ``user`` may open (a queryset; never raises)."""
    if user is None or not user.is_authenticated or not user.is_active:
        return Complaint.unscoped.none()
    kind = _user_type(user)
    if kind == "institution":
        return Complaint.objects.for_user(user)
    if kind == "resource_person":
        return Complaint.unscoped.filter(raised_by=user)
    if sees_everything(user):
        return Complaint.unscoped.all()
    involved = Q(raised_by=user) | Q(watchers=user) | Q(stages__assigned_to=user) | Q(referred_user=user)
    if user.department_id and (user.has_perm(HANDLE_PERM) or user.has_perm(QUEUE_PERM)):
        involved |= (Q(current_department_id=user.department_id) | Q(stages__assigned_department_id=user.department_id)
                     | Q(referred_department_id=user.department_id))
    ids = Complaint.unscoped.filter(involved).values("pk")
    return Complaint.unscoped.filter(pk__in=ids)


def get_visible(user, pk):
    return visible_to(user).select_related("layer", "institution", "raised_by", "current_department",
                                           "current_assignee").filter(pk=pk).first()


def can_view(user, complaint):
    return complaint is not None and visible_to(user).filter(pk=complaint.pk).exists()


def can_handle_view(user, complaint):
    """Staff who may read internal notes on this complaint."""
    return _user_type(user) == "staff" and can_view(user, complaint) and (
        user.is_superuser or user.has_perm("complaints.view_internal_messages") or user.has_perm(HANDLE_PERM)
        or sees_everything(user))


def is_raiser(user, complaint):
    if user is None or not user.is_authenticated:
        return False
    if complaint.raised_by_id == user.pk:
        return True
    # Any account of the same institution may follow the institution's complaint.
    return (_user_type(user) == "institution" and complaint.institution_id is not None
            and getattr(user, "institution_id", None) == complaint.institution_id
            and complaint.raiser_type == "institution")


def can_act(user, complaint):
    """(ok, reason) for the handler actions of the current stage."""
    if user is None or not user.is_authenticated or _user_type(user) != "staff":
        return False, "Only NBTE staff handle complaints."
    if not complaint.is_open:
        return False, "This complaint is closed."
    if user.is_superuser:
        return True, ""
    if not user.has_perm(HANDLE_PERM):
        return False, "You do not hold complaints.handle_complaint."
    stage = complaint.open_stage()
    if stage is None:
        return False, "No stage is open."
    if stage.assigned_to_id == user.pk:
        return True, ""
    if stage.assigned_department_id and stage.assigned_department_id == user.department_id:
        return True, ""
    if complaint.referred_user_id == user.pk:
        return True, ""
    if complaint.watchers.filter(pk=user.pk).exists():
        return True, ""  # an escalation target may act; escalation never reassigns
    return False, "This complaint is handled by another department."


# ===========================================================================
# Raising
# ===========================================================================
def layers_for(user):
    kind = _user_type(user)
    return [layer for layer in ComplaintLayer.objects.filter(is_active=True).select_related("parent")
            if layer.allows(kind)]


def related_choices():
    """Record types a complaint may point at (Requirement 13)."""
    return {
        "payment": ("payments", "paymentobligation"),
        "qa_request": ("accreditation", "qarequest"),
        "ssq": ("accreditation", "ssq"),
        "programme": ("institutions", "programme"),
        "inspection": ("inspection", "inspection"),
        "case": ("accreditation", "qacase"),
        "timeline": ("qa_core", "timelineevent"),
    }


SUGGESTED_LAYERS = {"payment": "payment-issues", "ssq": "ssq-submission", "qa_request": "workflow-delays",
                    "programme": "workflow-delays", "inspection": "visit-scheduling", "case": "workflow-delays",
                    "timeline": "others"}


def resolve_related(user, kind, pk):
    """The record a complaint points at, if ``user`` may see it; else None."""
    pair = related_choices().get(kind or "")
    if not pair or not str(pk or "").isdigit():
        return None
    try:
        model = ContentType.objects.get(app_label=pair[0], model=pair[1]).model_class()
    except ContentType.DoesNotExist:
        return None
    manager = model._default_manager
    if hasattr(manager, "for_user"):
        return manager.for_user(user).filter(pk=int(pk)).first()
    return None


def related_label(obj):
    if obj is None:
        return ""
    for attr in ("reference", "name", "title", "public_text"):
        value = getattr(obj, attr, "")
        if value:
            return f"{obj._meta.verbose_name.capitalize()} {value}"[:200]
    return f"{obj._meta.verbose_name.capitalize()} #{obj.pk}"


def validate_answers(layer, answers):
    answers = dict(answers or {})
    errors = {}
    clean = {}
    for field in layer.custom_fields or []:
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        value = str(answers.get(key, "") or "").strip()
        if field.get("required") and not value:
            errors[key] = f"{field.get('label') or key} is required."
        options = field.get("options") or []
        if value and field.get("type") == "choice" and options and value not in [str(o) for o in options]:
            errors[key] = f"Choose one of the options for {field.get('label') or key}."
        if value:
            clean[key] = value[:2000]
    if errors:
        raise ValidationError(errors)
    return clean


@transaction.atomic
def raise_complaint(user, *, layer, subject, body, priority="", related=None, answers=None, files=()):
    from qa_core import references

    kind = _user_type(user)
    if not layer.is_active or not layer.allows(kind):
        raise ValidationError("This kind of complaint cannot be raised from your account.")
    subject, body = (subject or "").strip(), (body or "").strip()
    if len(subject) < 5:
        raise ValidationError({"subject": "Give the complaint a short title (at least 5 characters)."})
    if len(body) < 10:
        raise ValidationError({"body": "Describe the problem (at least 10 characters)."})
    answers = validate_answers(layer, answers)
    institution = None
    if kind == "institution":
        institution = getattr(user, "institution", None)
    elif related is not None:
        institution = getattr(related, "institution", None)
    priority = priority if priority in dict(Complaint._meta.get_field("priority").choices) else layer.default_priority
    inst_code = getattr(institution, "code", "") or ""
    complaint = Complaint.unscoped.create(
        reference=references.issue("complaint", inst=inst_code), layer=layer, raised_by=user, raiser_type=kind,
        institution=institution, subject=subject[:200], body=body, priority=priority, answers=answers,
        flow_code=layer.flow_code,
        related_type=ContentType.objects.get_for_model(related, for_concrete_model=True) if related else None,
        related_id=str(related.pk) if related is not None else "", related_label=related_label(related))
    message = ComplaintMessage.objects.create(complaint=complaint, author=user, body=body,
                                              visibility=ComplaintMessage.PUBLIC)
    _attach(complaint, message, user, files)
    timeline.record(institution, "complaint.raised", subject=complaint, actor=user, visibility="public",
                    public_text=f"Complaint {complaint.reference} raised: {complaint.subject}",
                    internal_text=f"Complaint {complaint.reference} ({layer.name}, {complaint.get_priority_display()}) "
                                  f"raised by {user.get_full_name() or user.email}.")
    flow.start(complaint.flow_code, complaint, actor=user)
    complaint.refresh_from_db()
    _notify([user], "complaint.received", _ctx(complaint), url=portal_path(complaint))
    return complaint


def _attach(complaint, message, user, files):
    from qa_core.files import validate_upload

    out = []
    for upload in files or ():
        if not upload:
            continue
        validate_upload(upload, extensions=["pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"],
                        max_mb=_int("qa.documents.max_upload_mb", 10), label="attachment")
        out.append(ComplaintAttachment.objects.create(complaint=complaint, message=message, uploaded_by=user,
                                                      file=upload))
    return out


# ===========================================================================
# Routing (evaluated on entry to each stage that creates a task)
# ===========================================================================
class Route:
    def __init__(self, department=None, users=(), rule=None, why=""):
        self.department = department
        self.users = [u for u in users if u is not None]
        self.rule = rule
        self.why = why


def routing_variables(complaint, stage=None):
    inst = complaint.institution
    return {
        "priority": complaint.priority,
        "layer": complaint.layer.code,
        "parent_layer": complaint.layer.parent.code if complaint.layer.parent_id else "",
        "raiser_type": complaint.raiser_type,
        "stage": getattr(stage, "code", "") or "",
        "institution_code": getattr(inst, "code", "") or "",
        "institution_zone": getattr(inst, "zone", "") or "",
        "institution_state": getattr(getattr(inst, "state", None), "name", "") or "",
        "handling_department": getattr(getattr(inst, "handling_department", None), "code", "") or "",
        "reopened": int(complaint.reopened_count or 0),
    }


def _least_loaded(users):
    users = [u for u in users if u is not None and u.is_active]
    if not users:
        return []
    try:
        from tasks.services import capacity_for_user

        return [sorted(users, key=lambda u: (capacity_for_user(u)[0], u.pk))[0]]
    except Exception:  # capacity is advisory
        return users[:1]


def _staff(department=None, roles=None, perm=None):
    from accounts.models import User

    qs = User.objects.filter(is_active=True, user_type="staff").select_related("role").order_by("pk")
    if department is not None:
        qs = qs.filter(department=department)
    if roles:
        qs = qs.filter(role__code__in=roles)
    users = list(qs[:300])
    if perm:
        users = [u for u in users if u.has_perm(perm)]
    return users


def department_handlers(department):
    """Who works a department queue: handlers first, then the heads."""
    users = [u for u in _staff(department, perm=HANDLE_PERM) if not u.is_superuser]
    if not users:
        users = _staff(department, roles=["division-head", "deputy-director", "director", "qa-finance-director"])
    return users


def _target(rule, complaint):
    kind = rule.target_kind
    inst = complaint.institution
    if kind == "department":
        dept = rule.target_department
        return (dept, _least_loaded(department_handlers(dept))) if dept else (None, [])
    if kind == "role":
        dept = rule.target_department or getattr(inst, "handling_department", None)
        code = rule.target_role.code if rule.target_role_id else ""
        if not code:
            return None, []
        users = _staff(dept, roles=[code]) if dept else _staff(roles=[code])
        return dept or (users[0].department if users else None), _least_loaded(users)
    if kind == "user":
        user = rule.target_user
        if user is None or not user.is_active:
            return None, []
        return user.department, [user]
    if kind == "institution_handling_department":
        dept = getattr(inst, "handling_department", None)
        return (dept, _least_loaded(department_handlers(dept))) if dept else (None, [])
    if kind == "desk_officer":
        if inst is None:
            return None, []
        from accreditation.models import QACase

        case = (QACase.unscoped.filter(institution=inst, desk_officer__isnull=False, desk_officer__is_active=True)
                .select_related("desk_officer").order_by("-created_at", "-id").first())
        if case is None:
            return None, []
        return case.desk_officer.department or getattr(inst, "handling_department", None), [case.desk_officer]
    return None, []


def rules_for(complaint, stage_code):
    layers = [complaint.layer_id] + ([complaint.layer.parent_id] if complaint.layer.parent_id else [])
    return list(RoutingRule.objects.filter(is_active=True)
                .filter(Q(layer_id__in=layers) | Q(layer__isnull=True))
                .filter(Q(stage_code="") | Q(stage_code=stage_code))
                .select_related("target_department", "target_role", "target_user", "layer")
                .order_by("order", "id"))


def route(complaint, stage):
    """The first rule whose condition holds wins; else the fallback queue."""
    from qa_core.formula import FormulaError, evaluate_value

    code = getattr(stage, "code", stage)
    if code == "referred" and (complaint.referred_user_id or complaint.referred_department_id):
        user = complaint.referred_user
        dept = complaint.referred_department or getattr(user, "department", None)
        users = [user] if user is not None else _least_loaded(department_handlers(dept))
        return Route(dept, users, why="Referred internally")
    variables = routing_variables(complaint, stage if not isinstance(stage, str) else None)
    for rule in rules_for(complaint, code):
        condition = (rule.condition or "").strip()
        if condition:
            try:
                if not evaluate_value(condition, variables, declared=list(variables), returns="bool"):
                    continue
            except (FormulaError, Exception):  # a broken rule never blocks a complaint
                logger.warning("complaint routing rule %s could not be evaluated", rule.pk, exc_info=True)
                continue
        dept, users = _target(rule, complaint)
        if dept is None and not users:
            continue
        return Route(dept, users, rule=rule, why=f"Rule #{rule.order}: {rule.note or rule}")
    fallback = qa_department(str(config.get("qa.complaints.fallback_department", "ict") or "ict"))
    return Route(fallback, _least_loaded(department_handlers(fallback)) if fallback else [],
                 why="No rule matched: the general triage queue")


def _resolver(complaint, stage, prefer_user):
    routes = getattr(complaint, "_qa_routes", None)
    if routes is None:
        routes = complaint._qa_routes = {}
    r = route(complaint, stage)
    if prefer_user is not None and prefer_user.is_active and _user_type(prefer_user) == "staff":
        r = Route(prefer_user.department or r.department, [prefer_user], r.rule, "Back to the officer who sent it on")
    routes[stage.code] = r
    return r.users, r.department, f"{complaint.reference} · {complaint.subject[:70]}"


task_bridge.register_subject(Complaint, _resolver)


# ===========================================================================
# The stage listener: SLA clocks, assignment, timeline
# ===========================================================================
def _sla_seconds(stage):
    if stage.sla_hours:
        return int(stage.sla_hours) * 3600
    if stage.creates_task:
        return _int("qa.stage.default_sla_hours", 72) * 3600
    return None


def _is_terminal(stage):
    return stage.kind == "end" or stage.is_terminal_success or stage.is_terminal_failure


@flow.on_stages_changed
def _on_stages_changed(instance, entered, left, transition, actor, remark, subject, automatic):
    if not isinstance(subject, Complaint):
        return
    now = timezone.now()
    cal = sla.Calendar()
    stages = {s.code: s for s in instance.flow_version.stages.filter(code__in=list(entered) + list(left))}
    entering_pause = any(getattr(stages.get(c), "pauses_sla", False) for c in entered)
    for code in left:
        for cs in subject.stages.filter(stage_code=code, left_at__isnull=True):
            cs.left_at = now
            fields = ["left_at"]
            if entering_pause and cs.due_at:
                cs.remaining_seconds = int(max(0.0, sla.elapsed(now, cs.due_at, business=cs.business_hours,
                                                                calendar=cal))) if cs.due_at > now else 0
                fields.append("remaining_seconds")
            cs.save(update_fields=fields)
    routes = getattr(subject, "_qa_routes", {}) or {}
    current_dept, current_user = subject.current_department, subject.current_assignee
    public_labels = []
    for code in entered:
        stage = stages.get(code)
        if stage is None:
            continue
        previous = subject.stages.order_by("-entered_at", "-id").first()
        cs = ComplaintStage(complaint=subject, stage_code=code, stage_label=stage.label, entered_at=now,
                            business_hours=stage.sla_business_hours, pauses_sla=stage.pauses_sla)
        r = routes.get(code)
        if r is None and stage.creates_task:
            r = route(subject, stage)
        if r is not None:
            cs.assigned_department = r.department
            cs.assigned_to = r.users[0] if r.users else None
            cs.routed_by = r.rule
        elif stage.pauses_sla or _is_terminal(stage) or code in RESOLVED_CODES:
            cs.assigned_department = current_dept
        seconds = None if (stage.pauses_sla or _is_terminal(stage)) else _sla_seconds(stage)
        if seconds is not None:
            # Back from a pause (awaiting the raiser): the clock resumes where it stopped.
            if previous is not None and previous.pauses_sla:
                before = (subject.stages.exclude(pk=previous.pk).filter(entered_at__lte=previous.entered_at)
                          .order_by("-entered_at", "-id").first())
                if before is not None and before.stage_code == code and before.remaining_seconds is not None:
                    seconds = before.remaining_seconds
                    cs.breached_at = before.breached_at
                    cs.escalation_level = before.escalation_level
                    cs.reminded_at = before.reminded_at
            cs.sla_seconds = int(seconds)
            cs.due_at = sla.deadline(now, seconds, business=stage.sla_business_hours, calendar=cal)
        cs.save()
        if stage.creates_task:
            link = _stage_link(subject, code)
            if link is not None:
                cs.task = link.task
                cs.save(update_fields=["task"])
                if cs.due_at:
                    link.due_at = cs.due_at
                    link.save(update_fields=["due_at", "updated_at"])
                    type(link.task).objects.filter(pk=link.task_id).update(due_date=timezone.localdate(cs.due_at))
        if r is not None:
            current_dept = cs.assigned_department or current_dept
            current_user = cs.assigned_to
        public_labels.append(stage.public_label or stage.label)
    Complaint.unscoped.filter(pk=subject.pk).update(current_department=current_dept, current_assignee=current_user)
    subject.current_department, subject.current_assignee = current_dept, current_user
    if public_labels and transition is not None:
        label = ", ".join(public_labels)
        who = "" if automatic else (getattr(actor, "get_full_name", lambda: "")() or "")
        timeline.record(subject.institution, "complaint.stage", subject=subject, actor=actor, visibility="public",
                        public_text=f"Complaint {subject.reference}: {label}",
                        internal_text=f"Complaint {subject.reference} → {label}"
                                      + (f" ({transition.label} by {who})" if who else "")
                                      + (f": {remark}" if remark else ""))
        if actor is None or getattr(actor, "pk", None) != subject.raised_by_id:
            _notify([subject.raised_by], "complaint.updated", dict(_ctx(subject), status=label),
                    url=portal_path(subject))


def _stage_link(subject, code):
    from qa_core.models import QATaskLink

    ct = ContentType.objects.get_for_model(Complaint)
    return (QATaskLink.objects.filter(content_type=ct, object_id=subject.pk, stage_code=code, kind="stage_action",
                                      closed_at__isnull=True).select_related("task").first())


# ===========================================================================
# Actions
# ===========================================================================
def current_stage_rows(complaint):
    inst = flow.instance_for(complaint)
    return (flow.stages_of(inst) if inst else []), inst


def handler_actions(user, complaint):
    """Transitions of the current stage this handler may take (no raiser /
    system actions)."""
    inst = flow.instance_for(complaint)
    if inst is None or inst.completed_at is not None or not can_act(user, complaint)[0]:
        return []
    return [t for t in flow.available(inst, user)
            if t.action_code not in RAISER_ACTIONS + SYSTEM_ACTIONS]


def raiser_actions(user, complaint):
    inst = flow.instance_for(complaint)
    if inst is None or inst.completed_at is not None or not is_raiser(user, complaint):
        return []
    out = []
    for t in flow.available(inst, user):
        if t.action_code not in RAISER_ACTIONS or t.action_code == "raiser_replied":
            continue
        if t.action_code == "reopen" and not within_reopen_window(complaint):
            continue
        out.append(t)
    return out


def within_reopen_window(complaint, now=None):
    if complaint.resolved_at is None:
        return False
    now = now or timezone.now()
    return now <= complaint.resolved_at + datetime.timedelta(days=reopen_window_days())


MESSAGE_VISIBILITY = {"request_info": "public", "resolve": "public", "close": "public", "refer": "internal",
                      "refer_back": "internal", "acknowledge": "public", "withdraw": "public",
                      "reopen": "public", "confirm": "public"}


@transaction.atomic
def act(complaint, action_code, actor, *, remark="", refer_department=None, refer_user=None, system=False):
    """Take ``action_code`` on the complaint's flow, with the checks this
    module owns (who may act, the reopen window, referral targets)."""
    complaint = Complaint.unscoped.select_for_update().get(pk=complaint.pk)
    remark = (remark or "").strip()
    if action_code in SYSTEM_ACTIONS and not system:
        raise PermissionDenied("This step is taken by the system.")
    if action_code in RAISER_ACTIONS:
        if not system and not is_raiser(actor, complaint):
            raise PermissionDenied("Only the person who raised the complaint can do this.")
    elif not system:
        ok, why = can_act(actor, complaint)
        if not ok:
            raise PermissionDenied(why)
    if action_code == "reopen" and not within_reopen_window(complaint):
        raise ValidationError(f"A resolved complaint can be reopened within {reopen_window_days()} days "
                              f"of its resolution.")
    if action_code == "refer":
        if refer_department is None and refer_user is None:
            raise ValidationError("Choose the department or person to refer the complaint to.")
        if not (actor.is_superuser or actor.has_perm("complaints.assign_complaint") or actor.has_perm(HANDLE_PERM)):
            raise PermissionDenied("You cannot refer complaints.")
        complaint.referred_department = refer_department or getattr(refer_user, "department", None)
        complaint.referred_user = refer_user
        Complaint.unscoped.filter(pk=complaint.pk).update(referred_department=complaint.referred_department,
                                                          referred_user=refer_user)
    inst = flow.instance_for(complaint)
    if inst is None:
        raise ValidationError("This complaint has no workflow.")
    now = timezone.now()
    flow.transition(inst, action_code, actor, remark=remark)
    complaint.refresh_from_db()
    updates = {}
    stage_now = complaint.status
    if stage_now in RESOLVED_CODES or action_code == "resolve":
        updates.update(resolved_at=now, resolution=remark or complaint.resolution)
    if action_code == "reopen":
        updates.update(reopened_count=complaint.reopened_count + 1, resolved_at=None, closed_at=None)
    inst.refresh_from_db()
    if inst.completed_at is not None and complaint.closed_at is None:
        updates["closed_at"] = now
    if action_code == "refer_back":
        updates.update(referred_department=None, referred_user=None)
    if updates:
        Complaint.unscoped.filter(pk=complaint.pk).update(**updates)
        complaint.refresh_from_db()
    if remark:
        visibility = MESSAGE_VISIBILITY.get(action_code, "internal" if _user_type(actor) == "staff" else "public")
        ComplaintMessage.objects.create(complaint=complaint, author=None if system else actor, body=remark,
                                        visibility=visibility, is_system=system)
    if action_code in ("resolve",) or stage_now in RESOLVED_CODES:
        _notify([complaint.raised_by], "complaint.resolved", dict(_ctx(complaint), resolution=remark),
                url=portal_path(complaint))
    return complaint


@transaction.atomic
def add_message(complaint, author, body, *, visibility="public", files=(), mentions=()):
    body = (body or "").strip()
    if len(body) < 2:
        raise ValidationError({"body": "Write a message."})
    staff = _user_type(author) == "staff"
    if not staff:
        visibility = ComplaintMessage.PUBLIC  # the raiser never writes internal notes
        if not is_raiser(author, complaint):
            raise PermissionDenied("You cannot write on this complaint.")
    elif not can_handle_view(author, complaint):
        raise PermissionDenied("You cannot write on this complaint.")
    if visibility not in (ComplaintMessage.PUBLIC, ComplaintMessage.INTERNAL):
        visibility = ComplaintMessage.INTERNAL
    message = ComplaintMessage.objects.create(complaint=complaint, author=author, body=body, visibility=visibility)
    if mentions:
        message.mentions.set([m for m in mentions if _user_type(m) == "staff"])
    _attach(complaint, message, author, files)
    ctx = dict(_ctx(complaint), who=author.get_full_name() or author.email, excerpt=body[:160])
    if staff and visibility == ComplaintMessage.PUBLIC:
        # The raiser sees the department, not the officer, unless the setting says otherwise.
        public_ctx = dict(ctx, who=ctx["who"] if show_officer_name() else "NBTE")
        _notify([complaint.raised_by], "complaint.message", public_ctx, url=portal_path(complaint))
    elif not staff:
        _notify(_handlers_of(complaint), "complaint.message", ctx, url=internal_path(complaint))
    if staff and mentions:
        _notify(list(mentions), "complaint.message", ctx, url=internal_path(complaint))
    return message


def reply(complaint, user, body, files=()):
    """The raiser answers: a public message, and the clock restarts when the
    complaint was waiting for them."""
    message = add_message(complaint, user, body, files=files)
    inst = flow.instance_for(complaint)
    if inst is not None and flow.can(inst, "raiser_replied"):
        act(complaint, "raiser_replied", user)
    return message


def _handlers_of(complaint):
    users = []
    stage = complaint.open_stage()
    if stage is not None and stage.assigned_to_id:
        users.append(stage.assigned_to)
    users += list(complaint.watchers.all())
    if not users and complaint.current_department_id:
        users = department_handlers(complaint.current_department)[:3]
    seen, out = set(), []
    for u in users:
        if u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    return out


# ===========================================================================
# Escalation (§11, TR-06/TR-07): adds a watcher and a task; never reassigns
# ===========================================================================
def _heads(department, roles):
    return _staff(department, roles=roles) if department is not None else []


def escalation_targets(rule, complaint, stage):
    dept = stage.assigned_department or complaint.current_department
    kind = rule.target_kind
    if kind == "department_director":
        return _heads(dept, ["director"])[:1] or _heads(dept, ["qa-finance-director"])[:1]
    if kind == "deputy_director":
        return _heads(dept, ["deputy-director"])[:1] or _heads(dept, ["division-head"])[:1]
    if kind == "division_head":
        return _heads(dept, ["division-head"])[:1]
    if kind == "es":
        return _staff(roles=["executive-secretary"])[:1]
    if kind == "role" and rule.target_role_id:
        users = _staff(dept, roles=[rule.target_role.code]) or _staff(roles=[rule.target_role.code])
        return users[:1]
    if kind == "user" and rule.target_user_id and rule.target_user.is_active:
        return [rule.target_user]
    return []


def _repeat_breach(complaint, stage):
    return complaint.stages.exclude(pk=stage.pk).filter(breached_at__isnull=False).exists()


def rule_applies(rule, complaint, stage):
    if rule.only_if == "critical_or_repeat":
        return complaint.priority == "critical" or _repeat_breach(complaint, stage)
    return True


def escalation_rules(complaint, stage):
    code = complaint.flow_code
    return list(EscalationRule.objects.filter(is_active=True)
                .filter(Q(flow__isnull=True) | Q(flow__code=code))
                .filter(Q(stage_code="") | Q(stage_code=stage.stage_code))
                .select_related("target_role", "target_user").order_by("level", "after_breach_minutes", "id"))


def _escalation_task(complaint, stage, user, level, reason):
    from tasks import services as task_services
    from tasks.models import TaskAssignee, TaskCategory

    from qa_core.models import QATaskLink

    sys_user = task_bridge.system_user()
    inst = flow.instance_for(complaint)
    category = TaskCategory.objects.filter(code="qa-complaint").first() or TaskCategory.objects.filter(
        code="qa-sop03").first()
    with task_bridge.bridge_active():
        task = task_services.create_task(
            created_by=sys_user, title=f"[QA] Escalated (level {level}) — {complaint.reference} · "
                                       f"{complaint.subject[:80]}"[:200],
            description=(f"This complaint breached its deadline at '{stage.stage_label or stage.stage_code}'. "
                         f"{reason}\n\nYou are now watching it. Open it in QA to follow up or act; the "
                         f"handler keeps it — escalation never reassigns."),
            category=category, owner_department=user.department or stage.assigned_department,
            due_date=timezone.localdate(), requires_review=False, priority="high")
        TaskAssignee.objects.create(task=task, user=user, state=TaskAssignee.ASSIGNED, assigned_by=sys_user)
        QATaskLink.objects.create(task=task, content_type=ContentType.objects.get_for_model(Complaint),
                                  object_id=complaint.pk, flow_instance=inst, stage_code=stage.stage_code,
                                  kind="assignment", due_at=None)
        task_services.open_task(task, sys_user)
    return task


def escalate(complaint, stage, level, users, *, rule=None, by=None, reason=""):
    out = []
    for user in users:
        complaint.watchers.add(user)
        task = None
        try:
            task = _escalation_task(complaint, stage, user, level, reason)
        except Exception:  # a task failure never stops the escalation record
            logger.exception("escalation task for %s failed", complaint.reference)
        out.append(Escalation.objects.create(complaint_stage=stage, rule=rule, level=level, escalated_to=user,
                                             by=by, reason=reason[:255], task=task))
        event = (rule.notify_template if rule is not None and rule.notify_template else "complaint.escalated")
        _notify([user], event, dict(_ctx(complaint), level=level, reason=reason,
                                    stage=stage.stage_label or stage.stage_code), url=internal_path(complaint))
    names = ", ".join(u.get_full_name() or u.email for u in users) or "nobody (no one holds the target role)"
    ComplaintMessage.objects.create(complaint=complaint, author=by, is_system=by is None,
                                    visibility=ComplaintMessage.INTERNAL,
                                    body=f"Escalated to level {level}: {names}. {reason}".strip())
    timeline.record(complaint.institution, "complaint.escalated", subject=complaint, actor=by,
                    visibility="internal",
                    internal_text=f"Complaint {complaint.reference} escalated (level {level}) to {names}.")
    stage.escalation_level = max(stage.escalation_level, level)
    stage.save(update_fields=["escalation_level"])
    return out


@transaction.atomic
def escalate_manually(complaint, actor, *, reason, to_user=None):
    if not (actor.is_superuser or actor.has_perm("complaints.escalate_complaint")):
        raise PermissionDenied("You do not hold complaints.escalate_complaint.")
    if not complaint.is_open:
        raise ValidationError("This complaint is closed.")
    if len((reason or "").strip()) < 3:
        raise ValidationError({"reason": "Say why you are escalating."})
    stage = complaint.open_stage()
    if stage is None:
        raise ValidationError("No stage is open.")
    level = stage.escalation_level + 1
    users = [to_user] if to_user is not None else []
    rule = None
    if not users:
        for candidate in escalation_rules(complaint, stage):
            if candidate.level >= level and rule_applies(candidate, complaint, stage):
                users = escalation_targets(candidate, complaint, stage)
                if users:
                    rule, level = candidate, candidate.level
                    break
    if not users:
        raise ValidationError("No one is configured to receive the next escalation level. Choose a person.")
    return escalate(complaint, stage, level, users, rule=rule, by=actor, reason=reason.strip())


# ===========================================================================
# Holds (Requirements 15–17): built, off until qa.suspension.enabled_for
# ===========================================================================
def hold_allowed(complaint):
    """True only when ``qa.suspension.enabled_for`` names the related record's
    type, optionally per layer ("payments.paymentobligation" or
    "payment-issues:payments.paymentobligation")."""
    if not complaint.related_type_id:
        return False
    label = f"{complaint.related_type.app_label}.{complaint.related_type.model}"
    enabled = _json_list("qa.suspension.enabled_for")
    return label in enabled or f"{complaint.layer.code}:{label}" in enabled


@transaction.atomic
def place_hold(complaint, actor, reason):
    from qa_core.models import Suspension

    if not hold_allowed(complaint):
        raise PermissionDenied("Holds are switched off for this kind of record (qa.suspension.enabled_for).")
    if not (actor.is_superuser or actor.has_perm("qa_core.place_suspension")):
        raise PermissionDenied("You do not hold qa_core.place_suspension.")
    if len((reason or "").strip()) < 3:
        raise ValidationError({"reason": "Give the reason for the hold."})
    target = complaint.related_record
    if target is None:
        raise ValidationError("The related record no longer exists.")
    hold = Suspension.objects.create(content_type=complaint.related_type, object_id=complaint.related_id,
                                     reason=reason.strip(), linked_complaint_reference=complaint.reference,
                                     placed_by=actor)
    target_flow = flow.instance_for(target)
    if target_flow is not None and not target_flow.paused:
        type(target_flow).objects.filter(pk=target_flow.pk).update(paused=True)
    ComplaintMessage.objects.create(complaint=complaint, author=actor, visibility=ComplaintMessage.INTERNAL,
                                    body=f"Hold placed on {complaint.related_label}: {reason.strip()}")
    return hold


# ===========================================================================
# The sweep (Celery: every qa.complaints.sla_poll_minutes)
# ===========================================================================
def sla_sweep(now=None):
    """Reminders at 50%, breaches, escalation levels and auto-close. Safe to
    run as often as wanted: every step happens once."""
    now = now or timezone.now()
    cal = sla.Calendar()
    report = {"reminded": 0, "breached": 0, "escalated": 0, "auto_closed": 0}
    open_stages = (ComplaintStage.objects.filter(left_at__isnull=True, due_at__isnull=False, pauses_sla=False)
                   .filter(complaint__closed_at__isnull=True)
                   .select_related("complaint", "complaint__layer", "complaint__institution", "assigned_to",
                                   "assigned_department", "complaint__raised_by"))
    for cs in open_stages:
        complaint = cs.complaint
        try:
            with transaction.atomic():
                total = cs.sla_seconds or 0
                if total and cs.reminded_at is None and cs.breached_at is None:
                    spent = sla.elapsed(cs.entered_at, now, business=cs.business_hours, calendar=cal)
                    if spent * 2 >= total:
                        cs.reminded_at = now
                        cs.save(update_fields=["reminded_at"])
                        holders = [cs.assigned_to] if cs.assigned_to_id else department_handlers(
                            cs.assigned_department)[:3]
                        _notify(holders, "complaint.reminder", dict(_ctx(complaint), due=_fmt(cs.due_at),
                                                                    stage=cs.stage_label), url=internal_path(complaint))
                        report["reminded"] += 1
                if cs.breached_at is None and now >= cs.due_at:
                    cs.breached_at = now
                    cs.save(update_fields=["breached_at"])
                    holders = [cs.assigned_to] if cs.assigned_to_id else department_handlers(
                        cs.assigned_department)[:3]
                    _notify(holders, "complaint.breached", dict(_ctx(complaint), due=_fmt(cs.due_at),
                                                                stage=cs.stage_label), url=internal_path(complaint))
                    timeline.record(complaint.institution, "complaint.breached", subject=complaint,
                                    visibility="internal",
                                    internal_text=f"Complaint {complaint.reference}: '{cs.stage_label}' passed its "
                                                  f"deadline ({_fmt(cs.due_at)}).")
                    report["breached"] += 1
                if cs.breached_at is not None:
                    minutes = (now - cs.breached_at).total_seconds() / 60
                    for rule in escalation_rules(complaint, cs):
                        if rule.level <= cs.escalation_level or minutes < rule.after_breach_minutes:
                            continue
                        if not rule_applies(rule, complaint, cs):
                            continue
                        users = escalation_targets(rule, complaint, cs)
                        reason = (f"'{cs.stage_label}' was due {_fmt(cs.due_at)}"
                                  + (" (critical priority)" if complaint.priority == "critical" else "")
                                  + (" (repeated breach)" if _repeat_breach(complaint, cs) else "") + ".")
                        escalate(complaint, cs, rule.level, users, rule=rule, reason=reason)
                        report["escalated"] += 1
        except Exception:  # one complaint never stops the sweep
            logger.exception("complaint SLA sweep failed for %s", complaint.reference)
    report["auto_closed"] = auto_close(now)
    return report


def auto_close(now=None):
    now = now or timezone.now()
    cutoff = now - datetime.timedelta(days=auto_close_days())
    closed = 0
    for complaint in Complaint.unscoped.filter(status__in=RESOLVED_CODES, resolved_at__lte=cutoff):
        inst = flow.instance_for(complaint)
        if inst is None or not flow.can(inst, "auto_close"):
            continue
        try:
            act(complaint, "auto_close", task_bridge.system_user(), system=True,
                remark=f"Closed automatically {auto_close_days()} days after it was resolved.")
            closed += 1
        except Exception:
            logger.exception("auto-close failed for %s", complaint.reference)
    return closed


# ===========================================================================
# Knowledge base (TR-17)
# ===========================================================================
WORD = re.compile(r"[a-z0-9]{3,}")
STOP = {"the", "and", "for", "with", "not", "can", "cannot", "our", "has", "have", "was", "are", "you", "your",
        "this", "that", "from", "but", "why", "how", "what", "when", "will", "been", "please", "any"}


def kb_search(text, *, audience="", layer=None, limit=5):
    words = {w for w in WORD.findall((text or "").lower()) if w not in STOP}
    if not words:
        return []
    qs = KnowledgeArticle.objects.filter(is_published=True)
    if audience:
        qs = qs.filter(audience__in=("all", audience))
    scored = []
    for article in qs.select_related("layer"):
        title = article.title.lower()
        extra = f"{article.keywords} {article.summary}".lower()
        body = article.body.lower()
        score = 0
        for w in words:
            score += 5 * (w in title) + 3 * (w in extra) + (w in body)
        if layer is not None and article.layer_id == getattr(layer, "pk", None):
            score += 2
        if score:
            scored.append((score, article.helpful - article.not_helpful, article))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2].title))
    return [a for _s, _h, a in scored[:limit]]


# ===========================================================================
# Small helpers
# ===========================================================================
def _fmt(dt):
    return timezone.localtime(dt).strftime("%d %b %Y %H:%M") if dt else ""


def _ctx(complaint):
    return {"reference": complaint.reference, "subject": complaint.subject, "layer": complaint.layer.name,
            "priority": complaint.get_priority_display(), "status": complaint.status}


def portal_path(complaint):
    return f"/complaints/{complaint.pk}/"


def internal_path(complaint):
    return f"/qa/complaints/{complaint.pk}/"


def _notify(users, event, context, url=""):
    users = [u for u in users if u is not None]
    if not users:
        return
    try:
        from qa_core import notify

        notify.dispatch(event, context, {"role": users}, url=url)
    except Exception:  # notifications never break the complaint
        logger.exception("complaint notification %s failed", event)


def sla_state(cs, now=None, calendar=None):
    """For templates: {'state': on_track|near|breached|paused|none, 'pct', 'left', 'due'}."""
    now = now or timezone.now()
    if cs is None:
        return {"state": "none", "pct": 0, "left": "", "due": None}
    if cs.pauses_sla:
        return {"state": "paused", "pct": 0, "left": "Waiting for the raiser", "due": None}
    if not cs.due_at:
        return {"state": "none", "pct": 0, "left": "", "due": None}
    total = cs.sla_seconds or 1
    spent = sla.elapsed(cs.entered_at, now, business=cs.business_hours, calendar=calendar)
    pct = min(100, int(100 * spent / total)) if total else 100
    if now >= cs.due_at or cs.breached_at:
        late = sla.elapsed(cs.due_at, now, business=cs.business_hours, calendar=calendar) if now > cs.due_at else 0
        return {"state": "breached", "pct": 100, "left": (f"{sla.humanise(late)} overdue" + (" (working time)" if cs.business_hours else ""))
                if late else "Overdue",
                "due": cs.due_at}
    left = max(0, total - spent)
    return {"state": "near" if pct >= 50 else "on_track", "pct": pct,
            "left": f"{sla.humanise(left)} left" + (" (working time)" if cs.business_hours else ""),
            "due": cs.due_at}
