"""SOP-03 — the internal QA case (Master Plan §5.4, §5.7, §8; Phase 4) — system code.

The 17 stages are the seeded ``qa_case`` flow (data). This module:

* starts a case's flow (automatically when the QA case is created);
* resolves who acts at each stage (``resolve_actors``) for the task bridge;
* checks the stage prerequisites and moves the case (``act``): every action
  stage has *approve* and *return* (a return needs a remark and goes back to
  the exact person who submitted);
* holds the stage data services: supervising staff, desk officer, cost sheet
  (department builds, Audit reviews and edits, ES approves), personnel list,
  visit details and fund release;
* validates a reassigned assignee (R-01 workload, R-02 reviewer ≠ visitor,
  R-20 grade range).

Who may act: the holder of the stage's open QA task (or a superuser). The
ICT monitor never acts (R-13).
"""
# scoped-by-caller
from __future__ import annotations

import contextlib
import contextvars
import datetime
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

import config
from qa_core import flow, task_bridge, timeline
from qa_core.departments import qa_department
from qa_core.seed_data_phase4 import OFFICER_STAGES, REVIEW_STAGES

from .models import (
    CostLine, CostSheet, FundRelease, PersonnelEntry, PersonnelList, QACase, RateTable, VisitDetail,
)

FLOW = "qa_case"
DATA_STAGES = {  # stage → which tab holds its work
    "case_opened": "overview", "assign_audit_officer": "overview", "assign_finance_officer": "overview",
    "assign_ict_officer": "overview", "generate_cost": "cost", "add_personnel": "personnel",
    "confirm_personnel": "personnel", "audit_personnel_cost": "cost", "confirm_audit_cost": "cost",
    "es_approve_cost": "cost", "add_visit_details": "visit", "confirm_visit_details": "visit",
    "es_approve_visit": "visit", "request_fund_release": "funds",
    # Phase 5
    "post_visit_report": "reports", "post_visit_recommendation": "reports",
    # Phase 8: decisions are recorded on the Decisions tab
    "cqac_recommendation": "decisions", "cqac_chair_approval": "decisions", "es_final_approval": "decisions",
}


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


def _json(key, default):
    value = config.get(key, default)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = default
    return list(value or [])


def _name(user):
    return (user.get_full_name() or user.email) if user else "—"


# ===========================================================================
# Starting a case and reading its state
# ===========================================================================
def instance_of(case):
    return flow.instance_for(case, FLOW)


def current_stages(case):
    inst = instance_of(case)
    return list(inst.current_stages) if inst else []


def stage_objects(case):
    inst = instance_of(case)
    if inst is None:
        return []
    by_code = {s.code: s for s in inst.flow_version.stages.all()}
    return [by_code[c] for c in inst.current_stages if c in by_code]


def ensure_records(case):
    PersonnelList.unscoped.get_or_create(case=case, defaults={"division": ""})
    VisitDetail.unscoped.get_or_create(case=case)
    if not CostSheet.unscoped.filter(case=case, is_current=True).exists():
        CostSheet.unscoped.create(case=case, version=1)


@transaction.atomic
def start_case(case, *, actor=None):
    """Start the SOP-03 flow for a case (idempotent)."""
    case = QACase.unscoped.select_for_update().get(pk=case.pk)
    if instance_of(case) is not None:
        return case
    threshold = _int("qa.assignment.second_ict_officer_threshold", 5)
    case.requires_second_ict_officer = case.programme_count >= threshold
    case.started_at = timezone.now()
    pins = _INITIAL_PINS.get()
    if pins:
        case.stage_actors = {**(case.stage_actors or {}), **{k: v.pk if hasattr(v, "pk") else v
                                                            for k, v in pins.items() if v is not None}}
    case.save(update_fields=["requires_second_ict_officer", "started_at", "stage_actors", "updated_at"])
    ensure_records(case)
    flow.start(FLOW, case, actor=actor)
    timeline.record(case.institution, "qa_case.started", subject=case, actor=actor, visibility="internal",
                    internal_text=f"SOP-03 started for {case.reference} ({case.programme_count} programme(s)).")
    case.refresh_from_db()
    return case


def ict_required(case):
    return 2 if case.programme_count >= _int("qa.assignment.second_ict_officer_threshold", 5) else 1


def variables(case):
    ict = case.tracking_ict_officers.count() if case.pk else 0
    ready = int(bool(case.supervising_audit_officer_id and case.supervising_finance_officer_id
                     and ict >= ict_required(case)))
    from inspection.services import report_variables

    return {"programme_count": case.programme_count, "audit_officers": int(bool(case.supervising_audit_officer_id)),
            "finance_officers": int(bool(case.supervising_finance_officer_id)), "ict_officers": ict,
            "supervisors_ready": ready, **report_variables(case)}


# ===========================================================================
# Actor resolution (used by the task bridge)
# ===========================================================================
def _role_users(role_code, department=None):
    from accounts.models import User

    qs = User.objects.filter(is_active=True, user_type="staff", role__code=role_code).order_by("pk")
    if department is not None:
        qs = qs.filter(department=department)
    return list(qs)


def _director_of(department):
    users = _role_users("director", department)
    return users[:1]


def stage_department(case, stage):
    scope = stage.actor_scope or ""
    if scope in ("", "owning"):
        return case.owning_department
    return qa_department(scope) or case.owning_department


# ---------------------------------------------------------------------------
# Pinned actors: once someone acts for a role on a case (the Director who
# opened it, the ES who approved the cost, a reassigned holder …), later
# stages for that role go to the same person (QACase.stage_actors).
# ---------------------------------------------------------------------------
PIN_KINDS = ("owning_department_director", "audit_director", "finance_director", "ict_director", "es", "cqac",
             "cqac_chair", "role_in_department")
_INITIAL_PINS = contextvars.ContextVar("qa_case_initial_pins", default=None)


@contextlib.contextmanager
def pinned_actors(mapping):
    """Cases started inside this block start with these pins (key → user),
    e.g. ``{"es": user}``; used by the demo seed."""
    token = _INITIAL_PINS.set(dict(mapping))
    try:
        yield
    finally:
        _INITIAL_PINS.reset(token)


def pin_key(stage):
    kind = getattr(stage, "actor_kind", "")
    if kind not in PIN_KINDS:
        return ""
    if kind == "role_in_department":
        return f"role:{stage.actor_role.code}" if stage.actor_role_id else ""
    return kind


def pinned_user(case, stage):
    from accounts.models import User

    key = pin_key(stage)
    uid = (case.stage_actors or {}).get(key) if key else None
    if not uid:
        return None
    return User.objects.filter(pk=uid, is_active=True).select_related("department", "role").first()


def pin(case, stage, user, *, replace=False):
    key = pin_key(stage)
    if not key or user is None:
        return
    current = dict(case.stage_actors or {})
    if current.get(key) and not replace:
        return
    current[key] = user.pk
    QACase.unscoped.filter(pk=case.pk).update(stage_actors=current)
    case.stage_actors = current


def resolve_actors(case, stage, prefer_user=None):
    """(users, department, title) for the bridge."""
    dept = stage_department(case, stage)
    title = f"{case.institution.code} · {case.reference}"
    if prefer_user is not None and prefer_user.is_active:
        return [prefer_user], prefer_user.department or dept, title
    pinned = pinned_user(case, stage)
    if pinned is not None:
        if stage.actor_kind in ("es", "cqac", "cqac_chair") and pinned.department_id:
            dept = pinned.department
        return [pinned], dept, title
    kind = stage.actor_kind
    users = []
    if kind == "owning_department_director":
        users = _director_of(case.owning_department)
    elif kind in ("audit_director", "finance_director", "ict_director"):
        users = _director_of(qa_department(kind.split("_")[0]))
    elif kind == "es":
        users = _role_users("executive-secretary")[:1]
        dept = users[0].department if users and users[0].department_id else dept
    elif kind == "specific_user":
        user = getattr(case, stage.form_key or "", None)
        users = [user] if user is not None else []
    elif kind == "role_in_department":
        code = stage.actor_role.code if stage.actor_role_id else ""
        users = _pick_least_loaded(_role_users(code, dept)) if code else []
    elif kind in ("cqac", "cqac_chair"):
        code = stage.actor_role.code if stage.actor_role_id else ("cqac-member" if kind == "cqac" else "cqac-chairman")
        users = _pick_least_loaded(_role_users(code))
        dept = users[0].department if users and users[0].department_id else dept
    return [u for u in users if u is not None], dept, title


def _pick_least_loaded(users):
    if not users:
        return []
    from tasks.services import capacity_for_user

    return [sorted(users, key=lambda u: (capacity_for_user(u)[0], u.pk))[0]]


def _resolver(case, stage, prefer_user):
    return resolve_actors(case, stage, prefer_user)


task_bridge.register_subject(QACase, _resolver)


# ===========================================================================
# Who may act; acting (approve / return)
# ===========================================================================
def open_link(case, stage_code):
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import QATaskLink

    return (QATaskLink.objects.filter(content_type=ContentType.objects.get_for_model(QACase), object_id=case.pk,
                                      stage_code=stage_code, closed_at__isnull=True)
            .select_related("task").first())


def holders(case, stage_code):
    link = open_link(case, stage_code)
    if link is None:
        return []
    return [r.user for r in link.task.assignee_rows.select_related("user").exclude(state="declined")]


def can_act(case, user, stage_code):
    """(ok, reason)."""
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return False, "Sign in as NBTE staff."
    if getattr(getattr(user, "role", None), "code", "") == "ict-qa-monitor":
        return False, "The ICT monitor has read-only access (R-13)."
    if stage_code not in current_stages(case):
        return False, "The case is not at this stage."
    if user.is_superuser:
        return True, ""
    if not user.has_perm("accreditation.act_on_qa_stage"):
        return False, "You do not hold accreditation.act_on_qa_stage."
    if user in holders(case, stage_code):
        return True, ""
    names = ", ".join(_name(u) for u in holders(case, stage_code)) or "nobody yet (department queue)"
    return False, f"This stage is with {names}."


def _require(case, user, stage_code):
    ok, why = can_act(case, user, stage_code)
    if not ok:
        raise PermissionDenied(why)


def prerequisites(case, stage_code):
    """Problems that block *approve* at this stage (plain language)."""
    problems = []
    sheet = current_sheet(case)
    plist = getattr(case, "personnel_list", None)
    visit = getattr(case, "visit", None)
    if stage_code == "case_opened" and not case.desk_officer_id:
        problems.append("Choose the desk officer who will build the cost sheet and personnel list.")
    elif stage_code == "assign_audit_officer" and not case.supervising_audit_officer_id:
        problems.append("Choose the supervising Audit officer.")
    elif stage_code == "assign_finance_officer" and not case.supervising_finance_officer_id:
        problems.append("Choose the supervising Finance officer.")
    elif stage_code == "assign_ict_officer" and case.tracking_ict_officers.count() < ict_required(case):
        problems.append(f"Choose {ict_required(case)} tracking ICT officer(s) — this case has "
                        f"{case.programme_count} programme(s) (R-04).")
    elif stage_code == "generate_cost" and (sheet is None or not CostLine.unscoped.filter(sheet=sheet).exists()):
        problems.append("Add at least one line to the cost sheet.")
    elif stage_code == "add_personnel" and (plist is None or not PersonnelEntry.unscoped.filter(personnel_list=plist).exclude(status="returned").exists()):
        problems.append("Add at least one person to the personnel list.")
    elif stage_code == "add_visit_details":
        if visit is None or not (visit.planned_start and visit.planned_end and visit.team_lead_id):
            problems.append("Enter the visit dates and choose the team lead.")
    elif stage_code == "request_fund_release":
        release = current_release(case)
        if release is None or not release.released_reference:
            problems.append("Enter the payment reference of the release.")
    elif stage_code == "post_visit_report":  # Phase 5: the report sub-flow
        from inspection.services import report_variables

        v = report_variables(case)
        if not v["reports_total"] or v["reports_open"]:
            problems.append("Every post-visit report must be endorsed by the Director first (Reports tab).")
    elif stage_code == "post_visit_recommendation":
        from inspection.services import recommendation_problems

        problems += recommendation_problems(case)
    elif stage_code in ("cqac_recommendation", "cqac_chair_approval", "es_final_approval"):  # Phase 8
        from . import decisions

        problems += decisions.problems(case, stage_code)
    return problems


@transaction.atomic
def act(case, *, user, stage_code, action, remark=""):
    """Approve or return the case at ``stage_code``."""
    case = QACase.unscoped.select_for_update().get(pk=case.pk)
    _require(case, user, stage_code)
    remark = (remark or "").strip()
    if action == "return":
        if len(remark) < 3:
            raise ValidationError("A return needs a remark (it is pinned on the returned task).")
        code = "return"
    elif action == "approve":
        problems = prerequisites(case, stage_code)
        if problems:
            raise ValidationError(problems)
        code = "approve"
        if stage_code == "case_opened" and variables(case)["supervisors_ready"]:
            code = "resubmit"
    else:
        raise ValidationError("Unknown action.")
    instance = instance_of(case)
    stage = instance.flow_version.stages.filter(code=stage_code).first()
    if stage is not None and not user.is_superuser:
        pin(case, stage, user)
    flow.transition(instance, code, user, remark=remark, variables=variables(case), from_stage=stage_code)
    if case.needs_reassignment:
        QACase.unscoped.filter(pk=case.pk).update(needs_reassignment=False)
    case.refresh_from_db()
    return case


# ===========================================================================
# Stage 0/1 — desk officer and supervising staff
# ===========================================================================
def _staff_user(user, dept=None, label="officer"):
    if user is None or not user.is_active or getattr(user, "user_type", "") != "staff":
        raise ValidationError(f"Choose an active NBTE {label}.")
    if dept is not None and user.department_id != dept.pk:
        raise ValidationError(f"{_name(user)} is not in {dept.name}.")
    return user


@transaction.atomic
def set_desk_officer(case, *, user, officer):
    _require(case, user, "case_opened")
    _staff_user(officer, case.owning_department, "officer of the owning department")
    _check_grade(officer)
    case.desk_officer = officer
    case.save(update_fields=["desk_officer", "updated_at"])
    return case


@transaction.atomic
def set_supervisor(case, *, user, kind, officers):
    """kind: audit | finance | ict (ict takes a list)."""
    stage = {"audit": "assign_audit_officer", "finance": "assign_finance_officer", "ict": "assign_ict_officer"}[kind]
    _require(case, user, stage)
    dept = qa_department(kind)
    officers = [o for o in officers if o is not None]
    if not officers:
        raise ValidationError("Choose an officer.")
    for officer in officers:
        _staff_user(officer, dept, f"{kind.upper() if kind == 'ict' else kind.title()} officer")
    if kind == "audit":
        case.supervising_audit_officer = officers[0]
        case.save(update_fields=["supervising_audit_officer", "updated_at"])
    elif kind == "finance":
        case.supervising_finance_officer = officers[0]
        case.save(update_fields=["supervising_finance_officer", "updated_at"])
    else:
        if len(set(officers)) < ict_required(case):
            raise ValidationError(f"This case needs {ict_required(case)} different ICT officers (R-04).")
        case.tracking_ict_officers.set(officers)
    return case


def eligible_officers(case, kind):
    from accounts.models import User

    dept = case.owning_department if kind == "desk" else qa_department(kind)
    if dept is None:
        return []
    return list(User.objects.filter(is_active=True, user_type="staff", department=dept)
                .exclude(role__code__in=("executive-secretary", "ict-qa-monitor")).select_related("role")
                .order_by("first_name", "last_name"))


# ===========================================================================
# Cost sheet (stages 2, 5–7)
# ===========================================================================
def current_sheet(case):
    return CostSheet.unscoped.filter(case=case, is_current=True).order_by("-version").first()


def rates_for(case):
    """Active rates: department overrides shadow the shared row with the same code."""
    out = {}
    for rate in RateTable.objects.filter(is_active=True, department__isnull=True):
        out[rate.code] = rate
    if case.owning_department_id:
        for rate in RateTable.objects.filter(is_active=True, department=case.owning_department):
            out[rate.code] = rate
    return sorted(out.values(), key=lambda r: (r.sort_order, r.code))


def _days(raw):
    try:
        value = Decimal(str(raw).strip() or "0")
    except (InvalidOperation, ValueError):
        raise ValidationError("Days must be a number.") from None
    if value <= 0 or value > 60:
        raise ValidationError("Days must be between 0.5 and 60.")
    return value


def line_amount(rate, days):
    amount = rate.amount * (days if rate.unit == RateTable.PER_DAY else 1)
    return amount.quantize(Decimal("0.01"))


def cost_editor(case, user):
    """'department' | 'audit' | None — who may edit the sheet now."""
    stages = current_stages(case)
    if "generate_cost" in stages and can_act(case, user, "generate_cost")[0] \
            and (user.is_superuser or user.has_perm("accreditation.edit_cost_sheet")):
        return "department"
    if "audit_personnel_cost" in stages and can_act(case, user, "audit_personnel_cost")[0] \
            and (user.is_superuser or user.has_perm("accreditation.audit_cost_sheet")):
        return "audit"
    return None


def _recompute(sheet):
    total = CostLine.unscoped.filter(sheet=sheet).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    CostSheet.unscoped.filter(pk=sheet.pk).update(total=total, updated_at=timezone.now())
    sheet.total = total
    return total


@transaction.atomic
def add_cost_line(case, *, user, rate, days, role_on_exercise="", staff=None):
    mode = cost_editor(case, user)
    if mode is None:
        raise PermissionDenied("The cost sheet is not open for you to edit.")
    if rate is None or not rate.is_active:
        raise ValidationError("Choose a rate from the rate table.")
    sheet = current_sheet(case)
    days = _days(days) if rate.unit == RateTable.PER_DAY else Decimal("1")
    role_text = (role_on_exercise or "").strip() or rate.role
    order = (CostLine.unscoped.filter(sheet=sheet).count() + 1) * 10
    line = CostLine.unscoped.create(sheet=sheet, staff=staff, role_on_exercise=role_text[:120], rate=rate,
                                    kind=rate.allowance_type, days=days, unit_amount=rate.amount,
                                    amount=line_amount(rate, days), edited_by_audit=(mode == "audit"),
                                    audit_note="Added by Audit" if mode == "audit" else "", sort_order=order)
    if sheet.prepared_by_id is None and mode == "department":
        CostSheet.unscoped.filter(pk=sheet.pk).update(prepared_by=user)
    _recompute(sheet)
    return line


@transaction.atomic
def update_cost_line(case, line, *, user, days=None, rate=None, note=""):
    mode = cost_editor(case, user)
    if mode is None:
        raise PermissionDenied("The cost sheet is not open for you to edit.")
    if line.sheet.case_id != case.pk or not line.sheet.is_current:
        raise ValidationError("That line is not on this case's cost sheet.")
    if mode == "audit" and len((note or "").strip()) < 3:
        raise ValidationError("Audit changes need a note (it is shown to the department).")
    rate = rate or line.rate
    new_days = _days(days) if (days not in (None, "") and rate.unit == RateTable.PER_DAY) else (
        line.days if rate.unit == RateTable.PER_DAY else Decimal("1"))
    line.rate, line.kind, line.days, line.unit_amount = rate, rate.allowance_type, new_days, rate.amount
    line.amount = line_amount(rate, new_days)
    if mode == "audit":
        line.edited_by_audit = True
        line.audit_note = note.strip()[:255]
    line.save()
    _recompute(line.sheet)
    return line


@transaction.atomic
def remove_cost_line(case, line, *, user, note=""):
    mode = cost_editor(case, user)
    if mode is None:
        raise PermissionDenied("The cost sheet is not open for you to edit.")
    if line.sheet.case_id != case.pk:
        raise ValidationError("That line is not on this case's cost sheet.")
    if mode == "audit" and len((note or "").strip()) < 3:
        raise ValidationError("Audit removals need a note.")
    sheet = line.sheet
    line.delete()
    _recompute(sheet)
    if mode == "audit":
        timeline.record(case.institution, "qa_case.cost_line_removed", subject=case, actor=user,
                        visibility="internal", internal_text=f"Audit removed a cost line: {note}")


# ===========================================================================
# Personnel list (stages 3–4)
# ===========================================================================
@transaction.atomic
def add_personnel(case, *, user, staff, duty="member", remark=""):
    _require(case, user, "add_personnel")
    if staff is None:
        raise ValidationError("Choose a member of staff.")
    plist, _ = PersonnelList.unscoped.get_or_create(case=case)
    if PersonnelEntry.unscoped.filter(personnel_list=plist, staff=staff).exists():
        raise ValidationError(f"{staff} is already on the list.")
    duties = dict(PersonnelEntry.DUTY_CHOICES)
    if duty not in duties:
        raise ValidationError("Choose a duty.")
    if duty == "team_lead":
        _check_team_lead(staff)
    _check_visit_load(case, staff)
    return PersonnelEntry.unscoped.create(personnel_list=plist, staff=staff, duty=duty, remark=(remark or "")[:255],
                                          added_by=user)


def _check_visit_load(case, staff):
    """Fair workload (Phase 8): the visit team respects the per-rank limits —
    visits a year, the same institution within N months, open tasks."""
    from inspection import workload

    user = getattr(staff, "user", None)
    person = user if user is not None else staff
    problems = workload.problems_for(person, duty="physical_verification", institution_id=case.institution_id,
                                     name=str(staff))
    if problems:
        raise ValidationError([f"{message} ({setting})." for _rule, setting, message in problems])


@transaction.atomic
def remove_personnel(case, entry, *, user):
    _require(case, user, "add_personnel")
    if entry.personnel_list.case_id != case.pk:
        raise ValidationError("That person is not on this case's list.")
    entry.delete()


def team_lead_roles():
    return _json("qa.visit.team_lead_roles", ["director", "deputy-director", "zonal-director"])


def _check_team_lead(staff):
    user = getattr(staff, "user", None)
    role = getattr(getattr(user, "role", None), "code", "") if user else ""
    if role not in team_lead_roles():
        raise ValidationError(f"{staff} cannot lead the visit: team leads must hold one of these roles: "
                              f"{', '.join(team_lead_roles())} (qa.visit.team_lead_roles).")


def staff_choices(case):
    from staff.models import Staff

    return list(Staff.objects.filter(user__isnull=False, user__is_active=True).select_related(
        "user__role", "department").order_by("first_name", "last_name")[:500])


# ===========================================================================
# Visit details (stages 8–10)
# ===========================================================================
@transaction.atomic
def save_visit(case, *, user, planned_start, planned_end, team_lead, notes=""):
    _require(case, user, "add_visit_details")
    if not planned_start or not planned_end:
        raise ValidationError("Enter both visit dates.")
    if planned_end < planned_start:
        raise ValidationError("The visit cannot end before it starts.")
    if planned_start < timezone.localdate():
        raise ValidationError("The visit cannot start in the past.")
    if team_lead is None:
        raise ValidationError("Choose the team lead.")
    _check_team_lead(team_lead)
    visit, _ = VisitDetail.unscoped.get_or_create(case=case)
    visit.planned_start, visit.planned_end, visit.team_lead = planned_start, planned_end, team_lead
    visit.logistics_notes = (notes or "").strip()
    visit.save()
    return visit


# ===========================================================================
# Fund release (stage 11)
# ===========================================================================
def current_release(case):
    return FundRelease.unscoped.filter(case=case).exclude(status="returned").order_by("-created_at").first()


@transaction.atomic
def record_release(case, *, user, reference, remark=""):
    _require(case, user, "request_fund_release")
    if not (user.is_superuser or user.has_perm("accreditation.release_funds")):
        raise PermissionDenied("Only the Pay Officer releases funds (accreditation.release_funds).")
    release = current_release(case)
    if release is None:
        raise ValidationError("No fund release has been requested for this case.")
    reference = (reference or "").strip()
    if len(reference) < 4:
        raise ValidationError("Enter the payment / voucher reference.")
    release.released_reference, release.remark, release.pay_officer = reference[:80], remark, user
    release.save(update_fields=["released_reference", "remark", "pay_officer", "updated_at"])
    return release


# ===========================================================================
# Flow effects (registered in accreditation.effects)
# ===========================================================================
def _sheet_status(case, status, **fields):
    sheet = current_sheet(case)
    if sheet is not None:
        CostSheet.unscoped.filter(pk=sheet.pk).update(status=status, updated_at=timezone.now(), **fields)


def on_cost_submitted(ctx):
    _sheet_status(ctx.subject, "submitted", prepared_by=ctx.actor)


def on_personnel_submitted(ctx):
    PersonnelList.unscoped.filter(case=ctx.subject).update(status="submitted", submitted_by=ctx.actor,
                                                           updated_at=timezone.now())


def on_personnel_confirmed(ctx):
    now = timezone.now()
    PersonnelList.unscoped.filter(case=ctx.subject).update(status="approved", decided_by=ctx.actor, decided_at=now,
                                                           updated_at=now)
    PersonnelEntry.unscoped.filter(personnel_list__case=ctx.subject, status="proposed").update(
        status="approved", decided_by=ctx.actor, updated_at=now)


def on_cost_audited(ctx):
    _sheet_status(ctx.subject, "submitted", audited_by=ctx.actor)


def on_cost_audit_confirmed(ctx):
    _sheet_status(ctx.subject, "audited")


def on_cost_es_approved(ctx):
    _sheet_status(ctx.subject, "es_approved", approved_by=ctx.actor, approved_at=timezone.now())


def on_visit_submitted(ctx):
    VisitDetail.unscoped.filter(case=ctx.subject).update(status="submitted", updated_at=timezone.now())


def on_visit_confirmed(ctx):
    VisitDetail.unscoped.filter(case=ctx.subject).update(status="confirmed", updated_at=timezone.now())


def on_visit_es_approved(ctx):
    case = ctx.subject
    now = timezone.now()
    VisitDetail.unscoped.filter(case=case).update(status="es_approved", approved_by=ctx.actor, approved_at=now,
                                                  updated_at=now)
    sheet = current_sheet(case)
    if sheet is not None and current_release(case) is None:
        FundRelease.unscoped.create(case=case, cost_sheet=sheet, amount=sheet.total, status="es_approved")
    # Phase 5: the approved visit becomes an Inspection (dates, team lead, team).
    from inspection.services import create_for_case

    create_for_case(case, actor=ctx.actor)


def on_funds_released(ctx):
    release = current_release(ctx.subject)
    if release is not None:
        FundRelease.unscoped.filter(pk=release.pk).update(status="released", released_at=timezone.now(),
                                                          pay_officer=ctx.actor, updated_at=timezone.now())


RETURN_RESETS = {  # target stage → what goes back to "being prepared"
    "generate_cost": ("sheet", "draft"), "audit_personnel_cost": ("sheet", "submitted"),
    "confirm_audit_cost": ("sheet", "submitted"), "add_personnel": ("plist", "draft"),
    "add_visit_details": ("visit", "draft"), "confirm_visit_details": ("visit", "submitted"),
}


def on_returned(ctx):
    case = ctx.subject
    target = ctx.transition.to_stage.code
    what = RETURN_RESETS.get(target)
    now = timezone.now()
    if what is not None:
        kind, status = what
        if kind == "sheet":
            _sheet_status(case, status)
        elif kind == "plist":
            PersonnelList.unscoped.filter(case=case).update(status="returned", updated_at=now)
        elif kind == "visit":
            VisitDetail.unscoped.filter(case=case).update(status="returned" if status == "draft" else status,
                                                          updated_at=now)
    if ctx.transition.from_stage.code in ("cqac_recommendation", "cqac_chair_approval", "es_final_approval"):
        # Phase 8: a returned decision stage is decided again when it comes back.
        from .models import Decision

        stage = {v: k for k, v in Decision.FLOW_STAGE.items()}[ctx.transition.from_stage.code]
        Decision.unscoped.filter(case=case, stage=stage, applied_at__isnull=True).delete()
    if ctx.transition.from_stage.code == "request_fund_release":
        FundRelease.unscoped.filter(case=case, status="es_approved").update(status="returned", remark=ctx.remark,
                                                                           updated_at=now)
    timeline.record(case.institution, "qa_case.returned", subject=case, actor=ctx.actor, visibility="internal",
                    internal_text=f"{ctx.transition.from_stage.label} returned to "
                                  f"{ctx.transition.to_stage.label}: {ctx.remark}")


def on_completed(ctx):
    """Stage 17 → closed: apply the ES decisions to the programmes, issue the
    outcome letters and notify the institution (Phase 8, decisions.apply_final)."""
    from . import decisions

    case = ctx.subject
    letters = decisions.apply_final(case, actor=ctx.actor)
    QACase.unscoped.filter(pk=case.pk).update(completed_at=timezone.now(), updated_at=timezone.now())
    timeline.record(case.institution, "qa_case.completed", subject=case, actor=ctx.actor, visibility="internal",
                    internal_text=f"SOP-03 complete for {case.reference}: stage 17 updated the programme record(s) "
                                  f"and issued {len(letters)} outcome letter(s).")


def on_case_opened(ctx):
    case = ctx.subject
    timeline.record(case.institution, "qa_case.opened", subject=case, actor=ctx.actor, visibility="internal",
                    internal_text=f"Desk officer: {_name(case.desk_officer)}.")


# ===========================================================================
# Reassignment validation (R-01, R-02, R-20) and follow-up
# ===========================================================================
def _grade(user):
    profile = getattr(user, "staff_profile", None)
    raw = getattr(profile, "grade_level", "") if profile else ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return int(digits) if digits else None


def _check_grade(user):
    lo, hi = _int("qa.staff.ssq_grade_min", 7), _int("qa.staff.ssq_grade_max", 12)
    grade = _grade(user)
    if grade is None:
        raise ValidationError(f"{_name(user)} has no grade level on record (R-20 needs GL {lo}–{hi}).")
    if not lo <= grade <= hi:
        raise ValidationError(f"{_name(user)} is on GL {grade}; this work needs GL {lo}–{hi} (R-20).")


def open_case_load(user):
    """How many cases this user holds an open officer-level QA task on (R-01)."""
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import QATaskLink

    ct = ContentType.objects.get_for_model(QACase)
    return (QATaskLink.objects.filter(content_type=ct, closed_at__isnull=True, stage_code__in=OFFICER_STAGES,
                                      task__assignee_rows__user=user)
            .values("object_id").distinct().count())


def validate_assignee(case, stage, user):
    if user is None:
        raise ValidationError("Choose a person.")
    if getattr(getattr(user, "role", None), "code", "") == "ict-qa-monitor":
        raise ValidationError("The ICT monitor cannot hold QA work (R-13).")
    code = getattr(stage, "code", "")
    from inspection import workload

    if code in OFFICER_STAGES:
        lim = workload.limit(user, "max_open_ssq")  # per rank (qa.workload.limits), else the general R-01 cap
        cap = lim.value if lim.value is not None else _int("qa.resource_person.max_open_ssq_per_officer", 3)
        setting = lim.setting if lim.row_label else "qa.resource_person.max_open_ssq_per_officer"
        load = open_case_load(user)
        if load >= cap:
            raise ValidationError(f"{_name(user)} already holds QA work on {load} open case(s); the limit is {cap} "
                                  f"(R-01, {setting}).")
        _check_grade(user)
    tasks_cap = workload.limit(user, "max_open_tasks")
    if tasks_cap:
        held = workload.open_tasks(user)
        if held >= tasks_cap.value:
            raise ValidationError(f"{_name(user)} holds {held} open task(s); the limit is {tasks_cap.value} "
                                  f"({tasks_cap.setting}).")
    if code in REVIEW_STAGES and _bool("qa.workload.reviewer_cannot_verify", True):
        on_team = PersonnelEntry.unscoped.filter(personnel_list__case=case, staff__user=user).exclude(
            status="returned").exists()
        if on_team:
            raise ValidationError(f"{_name(user)} is on this case's visit team, so cannot review it (R-02).")


FIELD_FOR_STAGE = {"generate_cost": "desk_officer", "add_personnel": "desk_officer",
                   "add_visit_details": "desk_officer", "post_visit_report": "desk_officer",
                   "audit_personnel_cost": "supervising_audit_officer"}


def on_reassigned(case, stage, user, actor):
    pin(case, stage, user, replace=True)
    field = FIELD_FOR_STAGE.get(getattr(stage, "code", ""))
    if field:
        QACase.unscoped.filter(pk=case.pk).update(**{f"{field}_id": user.pk, "needs_reassignment": False,
                                                     "updated_at": timezone.now()})
    else:
        QACase.unscoped.filter(pk=case.pk).update(needs_reassignment=False, updated_at=timezone.now())


# Hooks the bridge calls on the subject.
QACase.qa_validate_assignee = lambda self, stage, user: validate_assignee(self, stage, user)
QACase.qa_on_reassigned = lambda self, stage, user, actor: on_reassigned(self, stage, user, actor)


# ===========================================================================
# Send Back To Audit (§6, two-step action)
# ===========================================================================
SEND_BACK_FROM = ("es_approve_cost", "add_visit_details", "confirm_visit_details", "es_approve_visit",
                  "request_fund_release", "post_visit_report", "post_visit_recommendation", "cqac_recommendation",
                  "cqac_chair_approval", "es_final_approval")


def can_send_back_to_audit(case):
    stages = current_stages(case)
    return bool(stages) and stages[0] in SEND_BACK_FROM


@transaction.atomic
def send_back_to_audit(case, *, actor, remark):
    if not can_send_back_to_audit(case):
        raise ValidationError("Send Back To Audit is possible only from stage 7 onwards.")
    instance = instance_of(case)
    flow.force_stage(instance, "audit_personnel_cost", actor, remark=remark, metadata={"action": "send_back_to_audit"})
    _sheet_status(case, "submitted")
    FundRelease.unscoped.filter(case=case, status__in=("requested", "es_approved")).update(
        status="returned", remark=remark, updated_at=timezone.now())
    timeline.record(case.institution, "qa_case.sent_back_to_audit", subject=case, actor=actor,
                    visibility="internal", internal_text=f"Sent back to Audit: {remark}")
    return case


# ===========================================================================
# Board helpers
# ===========================================================================
STAGE_GROUPS = [
    ("setup", "Opening & supervising staff (1)", ("case_opened", "assign_supervising_staff")),
    ("cost", "Cost (2–7)", ("generate_cost", "add_personnel", "confirm_personnel", "audit_personnel_cost",
                           "confirm_audit_cost", "es_approve_cost")),
    ("visit", "Visit & funds (8–11)", ("add_visit_details", "confirm_visit_details", "es_approve_visit",
                                       "request_fund_release")),
    ("decision", "Post-visit & decision (12–17)", ("post_visit_report", "post_visit_recommendation",
                                                   "cqac_recommendation", "cqac_chair_approval", "es_final_approval",
                                                   "programme_auto_update")),
    ("closed", "Complete", ("closed",)),
]


# ===========================================================================
# Reading cases for the screens (the views call these; this module is
# ``scoped-by-caller`` — the visibility rule lives here, in one place)
# ===========================================================================
CROSS_DEPARTMENT_ROLES = ("executive-secretary", "pay-officer", "cqac-member", "cqac-chairman", "ict-qa-monitor",
                          "qa-finance-director", "qa-finance-officer")
SUPPORT_DEPARTMENTS = ("audit", "finance", "ict")


def _support_department_ids():
    return [d.pk for d in (qa_department(code) for code in SUPPORT_DEPARTMENTS) if d is not None]


def visible_cases(user):
    """Cases ``user`` may open: the institution scope (owning department),
    plus — because SOP-03 crosses departments — every case for the ES, the
    Pay Officer, CQAC, the ICT monitor and the Audit / Finance / ICT
    departments, plus any case the user is named on or holds a task for."""
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    from qa_core.models import QATaskLink

    base = QACase.objects.for_user(user)
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return base.none()
    role = getattr(getattr(user, "role", None), "code", "")
    if user.is_superuser or role in CROSS_DEPARTMENT_ROLES or (
            user.department_id and user.department_id in _support_department_ids()):
        qs = QACase.unscoped.all()
    else:
        ct = ContentType.objects.get_for_model(QACase)
        task_case_ids = QATaskLink.objects.filter(content_type=ct, task__assignee_rows__user=user).values("object_id")
        qs = QACase.unscoped.filter(Q(pk__in=base.values("pk")) | Q(desk_officer=user)
                                    | Q(supervising_audit_officer=user) | Q(supervising_finance_officer=user)
                                    | Q(tracking_ict_officers=user) | Q(pk__in=task_case_ids)).distinct()
    qs._mark_scoped()
    return qs


def can_view_cases(user):
    return bool(user is not None and user.is_authenticated and getattr(user, "user_type", "staff") == "staff"
                and (user.is_superuser or user.has_perm("accreditation.view_qa_case")
                     or user.has_perm("accreditation.act_on_qa_stage")))


def is_monitor(user):
    return getattr(getattr(user, "role", None), "code", "") == "ict-qa-monitor"


def case_links(case, *, open_only=False):
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import QATaskLink

    qs = (QATaskLink.objects.filter(content_type=ContentType.objects.get_for_model(QACase), object_id=case.pk)
          .select_related("task").order_by("-opened_at", "-pk"))
    if open_only:
        qs = qs.filter(closed_at__isnull=True)
    links = list(qs)
    for link in links:
        link.holders = [r.user for r in link.task.assignee_rows.select_related("user").exclude(state="declined")]
    return links


def case_bundle(case):
    """Everything the case screens show, read once."""
    sheet = current_sheet(case)
    lines = list(CostLine.unscoped.filter(sheet=sheet).select_related("rate", "staff").order_by("sort_order", "pk")) \
        if sheet else []
    plist = PersonnelList.unscoped.filter(case=case).first()
    entries = list(PersonnelEntry.unscoped.filter(personnel_list=plist).select_related(
        "staff__user__role", "staff__department").order_by("duty", "pk")) if plist else []
    sheets = list(CostSheet.unscoped.filter(case=case).order_by("-version"))
    return {
        "sheet": sheet, "lines": lines, "sheets": sheets, "plist": plist, "entries": entries,
        "visit": VisitDetail.unscoped.filter(case=case).select_related("team_lead").first(),
        "releases": list(FundRelease.unscoped.filter(case=case).select_related("pay_officer").order_by("-created_at")),
        "release": current_release(case),
        "tracking_ict": list(case.tracking_ict_officers.order_by("first_name")),
    }


def cost_line_of(case, pk):
    sheet = current_sheet(case)
    return CostLine.unscoped.filter(pk=pk, sheet=sheet).select_related("sheet", "rate").first() if sheet else None


def personnel_entry_of(case, pk):
    return PersonnelEntry.unscoped.filter(pk=pk, personnel_list__case=case).select_related("personnel_list").first()


def rate_by_pk(case, pk):
    return next((r for r in rates_for(case) if str(r.pk) == str(pk)), None)


def staff_by_pk(pk):
    from staff.models import Staff

    try:
        return Staff.objects.filter(pk=int(pk), user__isnull=False).select_related("user__role").first()
    except (TypeError, ValueError):
        return None


def my_stage_links(user, *, kind=""):
    """Open QA stage tasks ``user`` holds (the ES / Pay Officer queues)."""
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import QATaskLink

    groups = {"cost": ("generate_cost", "add_personnel", "confirm_personnel", "audit_personnel_cost",
                       "confirm_audit_cost", "es_approve_cost"),
              "visit": ("add_visit_details", "confirm_visit_details", "es_approve_visit"),
              "funds": ("request_fund_release",)}
    qs = (QATaskLink.objects.filter(content_type=ContentType.objects.get_for_model(QACase), closed_at__isnull=True,
                                    task__assignee_rows__user=user).select_related("task").order_by("due_at", "pk")
          .distinct())
    if kind in groups:
        qs = qs.filter(stage_code__in=groups[kind])
    links = list(qs)
    cases = {c.pk: c for c in QACase.unscoped.filter(pk__in=[l.object_id for l in links]).select_related("institution")}
    out = []
    for link in links:
        case = cases.get(link.object_id)
        if case is not None:
            link.case = case
            out.append(link)
    return out


def _published_flow():
    from qa_core.models import FlowDefinition

    return (FlowDefinition.objects.filter(code=FLOW, published_at__isnull=False).order_by("-version").first()
            or FlowDefinition.objects.filter(code=FLOW).order_by("-version").first())


def flow_stages(case=None):
    """The qa_case stages in order (the case's own version when it has one)."""
    inst = instance_of(case) if case is not None else None
    flow_def = inst.flow_version if inst is not None else _published_flow()
    return list(flow_def.stages.order_by("sort_order", "pk")) if flow_def else []


def stage_label(code):
    flow_def = _published_flow()
    stage = flow_def.stages.filter(code=code).first() if flow_def else None
    return stage.label if stage else code.replace("_", " ").capitalize()


def case_timeline(case, user):
    """The case's timeline: flow events (with reassignments) and the timeline
    rows written for the case, newest first."""
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import TimelineEvent

    rows = []
    instance = instance_of(case)
    for e in (flow.history(instance) if instance else []):
        rows.append({"at": e.at, "kind": "flow", "title": e.action_code.replace("_", " ").capitalize(),
                     "detail": f"{e.from_stage or '—'} → {e.to_stage}", "remark": e.remark, "actor": e.actor})
    if user.is_superuser or user.has_perm("qa_core.view_timeline_internal") or can_view_cases(user):
        for t in TimelineEvent.unscoped.filter(content_type=ContentType.objects.get_for_model(QACase),
                                               object_id=str(case.pk)).select_related("actor"):
            rows.append({"at": t.at, "kind": "timeline", "title": t.event_code.split(".")[-1].replace("_", " ")
                         .capitalize(), "detail": "", "remark": t.internal_text or t.public_text, "actor": t.actor})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


# ===========================================================================
# Screen helpers shared by the website and the mobile API (Phase 9: moved
# here from case_views so both read the same rules)
# ===========================================================================
APPROVE_LABELS = {
    "case_opened": "Send to supervising departments", "assign_audit_officer": "Assign Audit officer",
    "assign_finance_officer": "Assign Finance officer", "assign_ict_officer": "Assign ICT officer(s)",
    "generate_cost": "Submit cost sheet", "add_personnel": "Submit personnel list",
    "confirm_personnel": "Confirm personnel", "audit_personnel_cost": "Submit audited cost",
    "confirm_audit_cost": "Confirm audited cost", "es_approve_cost": "Approve cost (ES)",
    "add_visit_details": "Submit visit details", "confirm_visit_details": "Confirm visit details",
    "es_approve_visit": "Approve visit (ES)", "request_fund_release": "Confirm funds released",
    "post_visit_report": "Submit post-visit report", "post_visit_recommendation": "Confirm recommendation",
    "cqac_recommendation": "Send CQAC recommendation", "cqac_chair_approval": "Approve (CQAC Chairman)",
    "es_final_approval": "Final approval (ES)",
}
NO_RETURN_STAGES = ("case_opened", "programme_auto_update")
NON_ACTION_KINDS = ("end", "join", "parallel_gate")


def stage_actions(case, user, current=None):
    """One row per current action stage: may ``user`` act, why not, who
    holds it, what blocks *approve*, its open task link."""
    current = stage_objects(case) if current is None else current
    rows = []
    for stage in current:
        if stage.kind in NON_ACTION_KINDS:
            continue
        ok, why = can_act(case, user, stage.code)
        rows.append({"stage": stage, "can": ok, "why": why, "holders": holders(case, stage.code),
                     "problems": prerequisites(case, stage.code) if ok else [],
                     "link": open_link(case, stage.code),
                     "approve_label": APPROVE_LABELS.get(stage.code, "Approve"),
                     "returns": stage.code not in NO_RETURN_STAGES})
    return rows


def stepper(case, current):
    """The 17-stage progress rows ({no, label, state, code}) of the case page."""
    rows, seen_current = [], False
    current_nos = [s.sop_stage_no for s in current if s.sop_stage_no is not None]
    low = min(current_nos) if current_nos else None
    closed = case.status == "closed"
    for stage in flow_stages(case):
        if stage.kind in ("parallel_gate", "join", "start", "end") and stage.code != "case_opened":
            continue
        if stage.code in ("assign_finance_officer", "assign_ict_officer"):
            continue
        no = stage.sop_stage_no
        label = "Assign supervising staff" if stage.code == "assign_audit_officer" else stage.label
        if closed:
            state = "done"
        elif stage.code in [s.code for s in current] or (
                stage.code == "assign_audit_officer" and any(s.code.startswith("assign_") for s in current)):
            state, seen_current = "current", True
        elif low is not None and no is not None and no < low:
            state = "done"
        elif not seen_current and low is None and current and current[0].code == "case_opened":
            state = "todo"
        else:
            state = "todo" if (low is None or no is None or no > low) else "done"
        rows.append({"no": no if no is not None else 0, "label": label, "state": state, "code": stage.code})
    return rows


def open_links_by_case(case_ids):
    """{case_id: [open QATaskLink with prefetched assignees]} (one query)."""
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import QATaskLink

    out = {}
    links = (QATaskLink.objects.filter(content_type=ContentType.objects.get_for_model(QACase), closed_at__isnull=True,
                                       object_id__in=list(case_ids))
             .prefetch_related("task__assignee_rows__user"))
    for link in links:
        out.setdefault(link.object_id, []).append(link)
    return out


def decorate_cases(cases, now=None):
    """Board card data on each case: ``holders``, ``due`` (earliest open
    stage deadline), ``overdue``, ``stage_label``, ``stage_no``."""
    now = now or timezone.now()
    by_case = open_links_by_case([c.pk for c in cases])
    labels = {s.code: s for s in flow_stages()}
    for case in cases:
        case_links_ = by_case.get(case.pk, [])
        case.holders = [r.user for link in case_links_ for r in link.task.assignee_rows.all() if r.state != "declined"]
        case.due = min((link.due_at for link in case_links_ if link.due_at), default=None)
        case.overdue = bool(case.due and case.due < now)
        stage = labels.get(case.status)
        case.stage_label = stage.label if stage else ("Not started" if case.status in ("", "awaiting_sop03")
                                                     else case.status.replace("_", " ").capitalize())
        case.stage_no = stage.sop_stage_no if stage else None
    return cases


def decorate_stage_links(links, now=None):
    """``stage_label``, ``stage_no`` and ``overdue`` on my-stage links."""
    now = now or timezone.now()
    labels = {s.code: s for s in flow_stages()}
    for link in links:
        stage = labels.get(link.stage_code)
        link.stage_label = stage.label if stage else link.stage_code
        link.stage_no = stage.sop_stage_no if stage else None
        link.overdue = bool(link.due_at and link.due_at < now)
    return links


def filter_cases(qs, *, q="", department="", flag=""):
    """The Case Board filters (search, owning department, needs reassignment)."""
    from django.db.models import Q

    q = (q or "").strip()
    qs = qs.select_related("institution", "owning_department").order_by("-updated_at")
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(institution__name__icontains=q) | Q(institution__code__iexact=q))
    if department:
        qs = qs.filter(owning_department__code=department)
    if flag == "reassign":
        qs = qs.filter(needs_reassignment=True)
    return qs
