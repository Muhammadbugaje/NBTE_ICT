"""The rotation engine (v2.1 X27–X29, spec_v21 G6.d).

    s = rotation.suggest("document_analysis:dpap", subject=doc, institution=inst)
    s.top(3)          # ranked eligible candidates, each with score and reasons
    s.blocked         # the others, each with the rules that blocked them
    rotation.record_decision(s.decision, by=director, chosen=user, override_reason="")

How the next person is chosen (lower score first):

    score = w.load     * open / cap                      (busy people wait)
          + w.fairness * (recent / weight) / pool average (weighted round robin)
          - w.recency  * min(idle days, 365) / 365        (the longest idle first)

then the anti-streak rule: the person chosen last time is never proposed
first while someone else is eligible (``qa.rotation.no_consecutive``).

Hard constraints are pluggable (``CONSTRAINTS``; each can be switched off per
pool or globally under ``qa.rotation.rules``): pause, department, cap,
availability (leave / holiday / duty from ``attendance.availability`` plus
visit bookings within the travel buffer), the SSQ reviewer is not on the
visit (R-02) and the reverse, declared conflict of interest (and optionally
state of origin), same-institution cooldown, discipline, and — for visit and
SSQ pools given a ``need`` — every Phase 5/8 rule of ``inspection.rules``.

The Phase 5/8 availability finder (``inspection.rules.find``) sorts with
``fair_sort`` below, so there is one fairness implementation.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

import config

from . import rotation_load as load
from .models_rotation import (
    DOCUMENT_ANALYSIS, OVERSIGHT_ICT, PHYSICAL_VERIFICATION, ROTATION_KINDS, SSQ_REVIEW, VISIT_KINDS, VISIT_PANEL,
    ConflictDeclaration, RotationDecision, RotationMember, RotationPool,
)

EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

# ===========================================================================
# Settings (qa.rotation.*) merged with the pool's own overrides
# ===========================================================================
DEFAULT_CAPS = {DOCUMENT_ANALYSIS: 5, SSQ_REVIEW: 3, OVERSIGHT_ICT: 15, VISIT_PANEL: 2, PHYSICAL_VERIFICATION: 2}
DEFAULT_WEIGHTS = {"load": 0.45, "fairness": 0.35, "recency": 0.20}
DEFAULTS = {
    "weights": DEFAULT_WEIGHTS,
    "fairness_window_days": 90,
    "never_assigned_days": 365,
    "no_consecutive": True,
    "idle_alert_days": 30,
    "reviewer_not_on_panel": True,
    "panel_not_reviewer": True,
    "same_institution_cooldown_months": 24,
    "cooldown_kinds": [VISIT_PANEL, PHYSICAL_VERIFICATION],
    "travel_buffer_days": 2,
    "coi.exclude_state_of_origin": False,
    "availability_days": 2,
    "rules": {},
}
# Constraint codes and whether they are on by default (``qa.rotation.rules``).
RULE_DEFAULTS = {"paused": True, "department": True, "cap": True, "availability": True,
                 "reviewer_not_on_panel": True, "panel_not_reviewer": True, "conflict_of_interest": True,
                 "cooldown": True, "discipline": True, "inspection_rules": True}


def _coerce(value, default):
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, (dict, list)):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return default
        return value if isinstance(value, type(default)) else default
    return value


def _setting(name, default):
    return _coerce(config.get(f"qa.rotation.{name}", default), default)


@dataclass
class Cfg:
    kind: str
    cap: int | None
    weights: dict
    fairness_window_days: int
    never_assigned_days: int
    no_consecutive: bool
    idle_alert_days: int
    reviewer_not_on_panel: bool
    panel_not_reviewer: bool
    same_institution_cooldown_months: int
    cooldown_kinds: list
    travel_buffer_days: int
    exclude_state_of_origin: bool
    availability_days: int
    rules: dict

    def rule_on(self, code):
        return bool(self.rules.get(code, RULE_DEFAULTS.get(code, True)))

    def snapshot(self):
        return {"kind": self.kind, "cap": self.cap, "weights": self.weights,
                "fairness_window_days": self.fairness_window_days, "no_consecutive": self.no_consecutive,
                "reviewer_not_on_panel": self.reviewer_not_on_panel, "panel_not_reviewer": self.panel_not_reviewer,
                "same_institution_cooldown_months": self.same_institution_cooldown_months,
                "travel_buffer_days": self.travel_buffer_days, "rules": self.rules}


def settings_for(pool_or_kind) -> Cfg:
    """The effective rotation settings of a pool (or a kind with no pool)."""
    pool = pool_or_kind if isinstance(pool_or_kind, RotationPool) else None
    kind = pool.kind if pool else str(pool_or_kind)
    over = dict(pool.rules or {}) if pool else {}
    vals = {k: _setting(k, d) for k, d in DEFAULTS.items()}
    cap_default = DEFAULT_CAPS.get(kind)
    cap = config.get(f"qa.rotation.{kind}.cap", cap_default)
    if kind == SSQ_REVIEW:  # v2 key is an alias (G6.h)
        cap = config.get("qa.ssq.max_open_per_officer", cap)
    for k, v in over.items():
        if k == "cap":
            cap = v
        elif k == "rules" and isinstance(v, dict):
            vals["rules"] = {**(vals["rules"] or {}), **v}
        elif k == "weights" and isinstance(v, dict):
            vals["weights"] = {**vals["weights"], **v}
        elif k in DEFAULTS:
            vals[k] = _coerce(v, DEFAULTS[k])
    try:
        cap = int(cap) if cap not in (None, "") else None
    except (TypeError, ValueError):
        cap = cap_default
    weights = {k: float(vals["weights"].get(k, d)) for k, d in DEFAULT_WEIGHTS.items()}
    return Cfg(kind=kind, cap=cap, weights=weights, fairness_window_days=vals["fairness_window_days"],
               never_assigned_days=vals["never_assigned_days"], no_consecutive=vals["no_consecutive"],
               idle_alert_days=vals["idle_alert_days"], reviewer_not_on_panel=vals["reviewer_not_on_panel"],
               panel_not_reviewer=vals["panel_not_reviewer"],
               same_institution_cooldown_months=vals["same_institution_cooldown_months"],
               cooldown_kinds=list(vals["cooldown_kinds"] or []), travel_buffer_days=vals["travel_buffer_days"],
               exclude_state_of_origin=vals["coi.exclude_state_of_origin"],
               availability_days=vals["availability_days"], rules=dict(vals["rules"] or {}))


# ===========================================================================
# Pools
# ===========================================================================
def pool_code(kind, department=None):
    if kind == OVERSIGHT_ICT or department is None:
        return kind
    code = department if isinstance(department, str) else department.code
    return f"{kind}:{code}"


def get_pool(pool_or_kind, department=None):
    """A RotationPool from an instance, a pool code, or a kind + department.
    Inside a page batch the lookup is remembered (the board asks for the same
    pools once per waiting job)."""
    if isinstance(pool_or_kind, RotationPool):
        return pool_or_kind
    b = load.active()
    code = str(pool_or_kind)
    if b is not None:
        return b.cached_pool(code, department, lambda: _get_pool_uncached(code, department))
    return _get_pool_uncached(code, department)


def _get_pool_uncached(code, department):
    pool = RotationPool.objects.filter(code=code).select_related("department").first()
    if pool is None and code in ROTATION_KINDS:
        pool = RotationPool.objects.filter(code=pool_code(code, department)).select_related("department").first()
        if pool is None and department is not None:
            dept_id = department if isinstance(department, int) else getattr(department, "pk", None)
            pool = RotationPool.objects.filter(kind=code, department_id=dept_id).first()
    if pool is None:
        raise RotationPool.DoesNotExist(f"No rotation pool '{code}'.")
    return pool


# ===========================================================================
# Candidates
# ===========================================================================
@dataclass
class Cand:
    user: object
    member: object
    eligible: bool = True
    score: float = 0.0
    open: int = 0
    cap: int | None = None
    recent: int = 0
    last: datetime.datetime | None = None
    idle_days: float = 0.0
    reasons: list = field(default_factory=list)      # hard: why blocked
    blocks: list = field(default_factory=list)       # constraint codes that blocked
    notes: list = field(default_factory=list)        # soft: why suggested / warnings
    warnings: list = field(default_factory=list)
    breakdown: dict = field(default_factory=dict)

    @property
    def name(self):
        return self.user.get_full_name() or self.user.email

    @property
    def initials(self):
        parts = [p for p in self.name.replace(".", " ").split() if p]
        return ("".join(p[0] for p in parts[:2]) or "?").upper()

    @property
    def summary(self):
        """'2 open of 5 · 4 in 90 days · last 12 days ago'."""
        cap = f" of {self.cap}" if self.cap is not None else ""
        last = "never assigned" if self.last is None else (
            "last today" if int(self.idle_days) == 0 else f"last {int(self.idle_days)} days ago")
        return f"{self.open} open{cap} · {self.recent} in {self.breakdown.get('window', 90)} days · {last}"

    def as_json(self):
        return {"user_id": self.user.pk, "name": self.name, "eligible": self.eligible, "score": self.score,
                "open": self.open, "cap": self.cap, "recent": self.recent,
                "last_assigned": self.last.isoformat() if self.last else None,
                "idle_days": round(self.idle_days, 1), "reasons": self.reasons, "blocks": self.blocks,
                "notes": self.notes, "warnings": self.warnings, "weight": float(self.member.weight)}


@dataclass
class Context:
    pool: RotationPool
    cfg: Cfg
    subject: object = None
    institution: object = None
    window: tuple | None = None
    need: object = None
    today: datetime.date = None
    members: tuple = ()            # the pool's (active) member rows, for bulk reads


# ---------------------------------------------------------------------------
# Constraints: fn(ctx, cand) -> reason text or None. Register more with
# register_constraint(code, fn, kinds=None).
# ---------------------------------------------------------------------------
CONSTRAINTS = []


def register_constraint(code, fn, kinds=None, label=""):
    CONSTRAINTS[:] = [c for c in CONSTRAINTS if c[0] != code]
    CONSTRAINTS.append((code, fn, tuple(kinds) if kinds else None, label or code))
    return fn


def _c_paused(ctx, c):
    m = c.member
    if m.paused_until and m.paused_until >= ctx.today:
        why = f": {m.pause_reason}" if m.pause_reason else ""
        return f"Paused until {m.paused_until:%d %b}{why}"
    return None


def _c_department(ctx, c):
    pool = ctx.pool
    if pool.department_id and c.user.department_id != pool.department_id:
        return "Not in this department any more"
    if pool.division_id:
        import org
        div = org.division_of(c.user)
        if div is None or div.pk != pool.division_id:
            return "Not in this division"
    return None


def _c_cap(ctx, c):
    if c.cap is not None and c.open >= c.cap:
        return f"At cap ({c.open} of {c.cap} open)"
    return None


def _window(ctx):
    if ctx.window:
        return ctx.window
    return ctx.today, ctx.today + datetime.timedelta(days=max(ctx.cfg.availability_days, 0))


def _c_availability(ctx, c):
    import attendance
    start, end = _window(ctx)
    try:
        b = load.active()
        if b is not None:  # one window, everyone: grouped leave + duty queries
            av = b.availability_map(start, end).get(c.user.pk) or attendance.availability(c.user, start, end)
        else:
            av = attendance.availability(c.user, start, end)
    except Exception:  # availability must never break a suggestion
        av = {}
    leave = av.get("leave") or []
    if leave:
        back = max(leave) + datetime.timedelta(days=1)
        return f"On leave until {back:%d %b}"
    if ctx.window and av.get("holiday") and not av.get("free_working_days"):
        return "Public holiday on every day of the window"
    if av.get("duty") and ctx.window and not av.get("free_working_days"):
        return "On duty elsewhere in the window"
    if ctx.pool.kind in VISIT_KINDS or ctx.window:
        buf = datetime.timedelta(days=ctx.cfg.travel_buffer_days)
        clash = load.visits_in_window(c.user, start - buf, end + buf)
        if clash:
            a, b = clash[0]
            return f"On a visit {a:%d %b}–{b:%d %b} (travel buffer {ctx.cfg.travel_buffer_days} days)"
    return None


def _c_reviewer_not_on_panel(ctx, c):
    if ctx.cfg.reviewer_not_on_panel and load.reviewed_ssq_of(c.user, ctx.institution, ctx.subject):
        return "Reviewed this SSQ — can't join the visit (R-02)"
    return None


def _c_panel_not_reviewer(ctx, c):
    if ctx.cfg.panel_not_reviewer and load.on_visit_panel_of(c.user, ctx.institution, ctx.subject):
        return "On the visit panel for this institution — can't review its SSQ"
    return None


def _c_conflict(ctx, c):
    inst = ctx.institution
    if inst is None:
        return None
    b = load.active()
    if b is not None:
        row = next((r for r in b.conflict_rows(inst) if r.user_id == c.user.pk), None)
        staff = b.staff_profile_of(c.user) if b.covers(c.user) else getattr(c.user, "staff_profile", None)
    else:
        row = ConflictDeclaration.objects.live().filter(user=c.user, institution=inst).first()
        staff = getattr(c.user, "staff_profile", None)
    if row:
        return f"Conflict of interest declared ({row.reason[:60]})"
    if ctx.cfg.exclude_state_of_origin and staff is not None and getattr(inst, "state_id", None) and \
            getattr(staff, "state_of_origin_id", None) == inst.state_id:
        return "Same state of origin as the institution"
    return None


def _c_cooldown(ctx, c):
    months = ctx.cfg.same_institution_cooldown_months
    if not months or ctx.pool.kind not in ctx.cfg.cooldown_kinds or ctx.institution is None:
        return None
    since = timezone.now() - datetime.timedelta(days=30 * months)
    if load.served_institution_since(c.user, ctx.institution, since, kinds=ctx.cfg.cooldown_kinds):
        return f"Served this institution in the last {months} months"
    return None


def _c_discipline(ctx, c):
    need = ctx.need
    disc = getattr(need, "discipline_id", None) if need is not None else None
    if disc is None:
        return None
    b = load.active()
    if b is not None:
        ids = set(b.member_disciplines(c.member, [m.pk for m in ctx.members]))
    else:
        ids = set(c.member.disciplines.values_list("pk", flat=True))
    if ids and disc not in ids:
        return "Does not cover the programme's discipline"
    return None


def _qa_personnel_of(user, members=()):
    from inspection.models import QAPersonnel
    b = load.active()
    if b is not None:  # same map and row order as QAPersonnel…first()
        return b.qa_personnel_of(user, [m.user_id for m in members])
    return QAPersonnel.objects.filter(staff__user=user).select_related("staff__user__role",
                                                                        "staff__designation").first()


def _c_inspection_rules(ctx, c):
    if ctx.need is None or ctx.pool.kind not in (SSQ_REVIEW,) + VISIT_KINDS:
        return None
    from inspection import rules
    person = _qa_personnel_of(c.user, ctx.members)
    if person is None:
        return "Not registered as QA personnel"
    problems = rules.assignment_problems(person, ctx.need)
    return "; ".join(p.message for p in problems) or None


register_constraint("paused", _c_paused, label="Paused")
register_constraint("department", _c_department, label="In the department")
register_constraint("cap", _c_cap, label="Cap per person")
register_constraint("availability", _c_availability, label="Leave, holiday, duty and visits")
register_constraint("reviewer_not_on_panel", _c_reviewer_not_on_panel, kinds=VISIT_KINDS,
                    label="SSQ reviewer can't join that visit")
register_constraint("panel_not_reviewer", _c_panel_not_reviewer, kinds=(SSQ_REVIEW,),
                    label="Visit panel member can't review that SSQ")
register_constraint("conflict_of_interest", _c_conflict, label="Conflict-of-interest exclusions")
register_constraint("cooldown", _c_cooldown, label="Same-institution cooldown")
register_constraint("discipline", _c_discipline, label="Discipline")
register_constraint("inspection_rules", _c_inspection_rules, kinds=(SSQ_REVIEW,) + VISIT_KINDS,
                    label="Phase 5/8 eligibility rules")


def constraint_labels():
    return [(code, label) for code, _fn, _k, label in CONSTRAINTS]


# ===========================================================================
# Fair ordering (shared with inspection.rules.find)
# ===========================================================================
def fair_sort(cands, *, staff_first=True, today=None):
    """The Phase 5/8 ordering, generalised: staff before external people (when
    ``staff_first``), then fewest assignments this year, then longest since
    last served, then the rank score, then the name. Sorts in place and
    returns the list. ``cands`` need ``kind``, ``obj``, ``assignments_this_year``,
    ``last_served`` (date or None), ``score`` and ``name``."""
    today = today or timezone.localdate()

    def key(c):
        days = (today - c.last_served).days if c.last_served else 100000
        external = c.kind == "resource_person" and not c.obj.is_staff_person
        return (staff_first and external, c.assignments_this_year, -days, c.score, c.name)

    cands.sort(key=key)
    return cands


def _score(cfg, c, avg):
    w = cfg.weights
    load_part = (c.open / c.cap) if c.cap else (c.open / 5.0)
    weight = float(c.member.weight or Decimal("1")) or 1.0
    fair_part = (c.recent / weight) / (avg or 1.0)
    idle = min(c.idle_days, 365.0)
    score = w["load"] * load_part + w["fairness"] * fair_part - w["recency"] * idle / 365.0
    c.breakdown = {"load": round(w["load"] * load_part, 4), "fairness": round(w["fairness"] * fair_part, 4),
                   "recency": round(-w["recency"] * idle / 365.0, 4), "window": cfg.fairness_window_days}
    return round(score, 6)


# ===========================================================================
# suggest / record_decision
# ===========================================================================
@dataclass
class Suggestion:
    pool: RotationPool
    cfg: Cfg
    eligible: list
    blocked: list
    decision: RotationDecision | None = None

    @property
    def suggested(self):
        return self.eligible[0] if self.eligible else None

    def top(self, n=3):
        return self.eligible[:n]

    @property
    def candidates(self):
        return self.eligible + self.blocked


def _subject_bits(subject):
    if subject is None or getattr(subject, "pk", None) is None:
        return None, "", ""
    ct = ContentType.objects.get_for_model(subject, for_concrete_model=False)
    label = getattr(subject, "reference", None) or str(subject)
    return ct, str(subject.pk), str(label)[:200]


def suggest(pool_or_kind, subject=None, n=3, *, institution=None, department=None, window=None, need=None,
            record=True):
    """Rank the pool's members for one piece of work. Returns a ``Suggestion``
    (eligible ranked, blocked with reasons) and, with ``record``, writes the
    ``RotationDecision`` (undecided) holding the candidate snapshot."""
    pool = get_pool(pool_or_kind, department)
    cfg = settings_for(pool)
    if institution is None and subject is not None:
        institution = subject if subject.__class__.__name__ == "Institution" else getattr(subject, "institution",
                                                                                            None)
    if need is not None and window is None:
        window = (need.start, need.end)
    ctx = Context(pool=pool, cfg=cfg, subject=subject, institution=institution, window=window, need=need,
                  today=timezone.localdate())
    now = timezone.now()
    members = list(pool.active_members())
    ctx.members = members
    b = load.active()
    if b is not None and need is not None and members:
        b.member_disciplines(members[0], [m.pk for m in members])  # prime the shared map
    cands = []
    for m in members:
        u = m.user
        cap = m.max_concurrent if m.max_concurrent is not None else cfg.cap
        c = Cand(user=u, member=m, cap=cap, open=load.open_load(u, pool.kind),
                 recent=load.recent_count(u, pool.kind, cfg.fairness_window_days),
                 last=load.last_assigned_at(u, pool.kind, m))
        c.idle_days = ((now - c.last).total_seconds() / 86400.0) if c.last else float(cfg.never_assigned_days)
        for code, fn, kinds, _label in CONSTRAINTS:
            if kinds and pool.kind not in kinds:
                continue
            # X4: the suggestion only ever offers the department's own staff —
            # the department rule cannot be switched off.
            if code != "department" and not cfg.rule_on(code):
                continue
            reason = fn(ctx, c)
            if reason:
                c.reasons.append(reason)
                c.blocks.append(code)
        c.eligible = not c.reasons
        if c.cap is not None and c.eligible and c.open == c.cap - 1 and c.cap > 1:
            c.warnings.append(f"Near cap: {c.open} of {c.cap} open — one more and the rotation skips "
                              f"{c.user.first_name or c.name}")
        cands.append(c)
    eligible = [c for c in cands if c.eligible]
    avg = (sum(c.recent / (float(c.member.weight) or 1.0) for c in eligible) / len(eligible)) if eligible else 1.0
    for c in cands:
        c.score = _score(cfg, c, avg)
    eligible.sort(key=lambda c: (c.score, c.last or EPOCH, c.user.pk))
    last_id = load.last_chosen(pool)
    if cfg.no_consecutive and len(eligible) > 1 and eligible[0].user.pk == last_id:
        eligible.append(eligible.pop(0))
        eligible[-1].notes.append("Chosen last time — moved to the back (no two in a row)")
    for i, c in enumerate(eligible):
        if c.last is None:
            c.notes.insert(0, "Never assigned in this rotation")
        elif c.idle_days >= cfg.idle_alert_days:
            c.notes.insert(0, f"Idle {int(c.idle_days)} days")
        if i == 0 and c.open == min(x.open for x in eligible):
            c.notes.append("Lowest load in the pool")
    blocked = sorted([c for c in cands if not c.eligible], key=lambda c: c.name)
    s = Suggestion(pool=pool, cfg=cfg, eligible=eligible, blocked=blocked)
    if record:
        ct, sid, label = _subject_bits(subject)
        s.decision = RotationDecision.objects.create(
            pool=pool, kind=pool.kind, department=pool.department, subject_ct=ct, subject_id=sid,
            subject_label=label, institution=institution if getattr(institution, "pk", None) else None,
            window_start=window[0] if window else None, window_end=window[1] if window else None,
            candidates=[c.as_json() for c in eligible[:20]] + [c.as_json() for c in blocked[:20]],
            suggested_user=eligible[0].user if eligible else None, parameters=cfg.snapshot())
    return s


# ---------------------------------------------------------------------------
# Who may confirm / override / manage
# ---------------------------------------------------------------------------
def _is_top(user):
    import org
    return bool(user and user.is_authenticated and (org.is_super_admin(user) or org.is_es(user)))


def can_assign(user, pool):
    """Director of the pool's department, a delegated lead holding
    ``assignments.assign_work`` in that department, the Director of ICT for
    ICT pools, ES and super admin."""
    import org
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if _is_top(user):
        return True
    dept = pool.department
    if pool.kind == OVERSIGHT_ICT and dept is None:
        dept = org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
    if dept is None:
        return False
    from . import work
    return work.can_assign(user, dept)


def can_manage_pool(user, pool):
    """Edit members, weights, caps and pause: the director (ICT pools: the
    Director of ICT), ES, super admin, or ``assignments.manage_rotation_pools``."""
    import org
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if _is_top(user) or user.has_perm("assignments.manage_rotation_pools"):
        return True
    dept = pool.department or (org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
                               if pool.kind == OVERSIGHT_ICT else None)
    return bool(dept is not None and org.is_director(user, dept))


def can_override_constraints(user):
    return bool(_is_top(user) or (getattr(user, "is_authenticated", False)
                                  and user.has_perm("assignments.override_rotation_constraints")))


def visible_pools(user):
    """Pools whose board ``user`` may see: everything for ES / super admin /
    ``view_rotation_board``; otherwise the pools of the user's own department
    and of the department they direct (staff see their own row only)."""
    import org
    qs = RotationPool.objects.active().select_related("department")
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "staff") != "staff":
        return qs.none()
    if _is_top(user) or user.has_perm("assignments.view_rotation_board"):
        return qs
    ids = {i for i in (getattr(user, "department_id", None),
                       getattr(org.directed_department(user), "pk", None)) if i}
    return qs.filter(department_id__in=ids) if ids else qs.none()


@transaction.atomic
def record_decision(decision, *, by, chosen, override_reason="", work_assignment=None, system=False):
    """Confirm the suggestion or choose someone else. Choosing anyone other
    than the suggested person needs a reason (5+ characters); choosing a
    person a rule blocked also needs ``assignments.override_rotation_constraints``
    (ES / super admin). Writes the outcome once (the row is then frozen),
    stamps the member's ``last_assigned_at`` and writes the audit log.

    ``system=True`` (X27b auto-assign) skips the director's permission gate:
    the rotation itself is the assignment and ``by`` stays None — the audit
    row records no actor instead of raising."""
    decision = RotationDecision.objects.select_for_update().get(pk=decision.pk)
    if decision.decided_at is not None:
        raise ValidationError("This rotation decision was already made.")
    pool = decision.pool
    if not system and not can_assign(by, pool):
        raise PermissionDenied("Only the department's director (or a delegated lead) can assign this work.")
    chosen_id = getattr(chosen, "pk", chosen)
    row = next((c for c in decision.candidates or [] if c.get("user_id") == chosen_id), None)
    if row is None:
        raise ValidationError("That person is not in this rotation.")
    from accounts.models import User
    from . import work
    dept = pool.department
    if pool.kind == OVERSIGHT_ICT and dept is None:
        import org
        dept = org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
    if not system:
        work.check_assignee(by, dept, User.objects.filter(pk=chosen_id).first())
    reason = (override_reason or "").strip()
    constraint_overridden = not row.get("eligible", False)
    if constraint_overridden:
        if not can_override_constraints(by):
            raise ValidationError(f"{row['name']} can't be assigned: " + "; ".join(row.get("reasons") or []))
        if len(reason) < 5:
            raise ValidationError("Say why the rule is overridden.")
    overridden = chosen_id != decision.suggested_user_id
    if overridden and not system and len(reason) < 5:
        raise ValidationError("Say why you chose someone else.")
    now = timezone.now()
    decision.chosen_user_id = chosen_id
    decision.overridden = overridden
    decision.constraint_overridden = constraint_overridden
    decision.override_reason = reason if (overridden or constraint_overridden) else ""
    decision.decided_by = by
    decision.decided_at = now
    decision.work_assignment = work_assignment
    decision.save()
    RotationMember.objects.filter(pool=pool, user_id=chosen_id).update(last_assigned_at=now)
    from core import audit
    audit.log("rotation_overridden" if overridden else "rotation_confirmed", decision, actor=by,
              changes={"suggested": decision.suggested_user_id, "chosen": chosen_id,
                       "constraint_overridden": constraint_overridden},
              note=(decision.override_reason[:255] or ("auto by rotation" if system else "")))
    return decision


def link_work(decision, work_assignment):
    """Attach the WorkAssignment created from a decided suggestion."""
    decision.work_assignment = work_assignment
    decision.save(update_fields=["work_assignment"])
    return decision


def confirm(decision, *, by, chosen, override_reason=""):
    """Spec name (G6.d) for ``record_decision``."""
    return record_decision(decision, by=by, chosen=chosen, override_reason=override_reason)


# ===========================================================================
# P.8: the ICT supervisor of an institution
# ===========================================================================
def suggest_ict_supervisor(institution, *, record=True):
    """Suggest the ICT officer to supervise ``institution`` (X19), from the
    ``oversight_ict`` pool (the ICT department's pool)."""
    return suggest(OVERSIGHT_ICT, subject=institution, institution=institution, record=record)


# ===========================================================================
# Pool membership (seed + sync)
# ===========================================================================
def eligible_users(pool):
    """Active staff of the pool's department (and division) holding an
    eligible role and rank."""
    from accounts.models import User

    import org
    qs = User.objects.filter(is_active=True, user_type="staff").select_related("role")
    if pool.department_id:
        qs = qs.filter(department_id=pool.department_id)
        seat = org.director_of(pool.department_id)
        if seat is not None:  # the director assigns; the seat does not take turns
            qs = qs.exclude(pk=seat.pk)
    qs = qs.exclude(role__code__in=(org.ES_ROLE, "super-admin"))
    roles = [r for r in (pool.eligible_roles or []) if r]
    if roles:
        qs = qs.filter(role__code__in=roles)
    if pool.min_rank:
        qs = qs.filter(role__rank__gte=pool.min_rank)
    if pool.division_id:
        qs = [u for u in qs if getattr(org.division_of(u), "pk", None) == pool.division_id]
    return list(qs)


def sync_members(pool):
    """Add eligible people who are missing (never removes; people who left
    are skipped by the department rule). Returns the number added."""
    have = set(pool.members.values_list("user_id", flat=True))
    added = 0
    for u in eligible_users(pool):
        if u.pk not in have:
            RotationMember.objects.create(pool=pool, user=u)
            added += 1
    return added
