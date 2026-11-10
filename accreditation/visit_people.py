"""SOP-03 — who should go on (or be costed for) a visit: the people rules engine.

``rank(case, staff, rate=..., purpose=...)`` labels every NBTE staff candidate
for this case with one status and the reasons behind it, so the person picker
can group and explain them:

    recommended   right department / grade / role for the line, light workload
    available     nothing against them
    busy          over the open-task cap, on another visit whose dates overlap
                  (or within ``qa.people.visit_gap_days`` of this one), or at the
                  ``qa.people.max_visits_per_period`` cap — a warning only
    away          approved leave over the visit dates (or the next 14 days) —
                  a warning only
    locked        a rule says no: R-02 (reviewed this request's SSQ, or audits
                  this case's cost), already on the sheet / list, grade out of
                  the line's range (R-20 style band), team-lead role rule
                  (``qa.visit.team_lead_roles``), a second team lead, an
                  external-only line, a declared conflict of interest with the
                  institution, not an NBTE staff account, the ICT monitor (R-13)

Everything is read from data the system already holds: Staff (department,
grade_level, user role), the rate table, other cases' VisitDetail and
PersonnelEntry rows, approved leave (leave app), SSQ reviews and work
assignments, VisitConflictDeclaration, open tasks.

Per-rate expectations live in ``qa.cost.person_rules`` (JSON keyed by rate
code — see :data:`DEFAULT_RATE_RULES`). ``check(...)`` returns the locking
reasons for one person; ``case_services.add_cost_line`` refuses a locked
person with that message (server side), and the cost view shows Busy / On
leave as a warning toast (there is no override mechanism for cost lines).
"""
from __future__ import annotations

import datetime
import json

from django.utils import timezone

import config

RECOMMENDED, AVAILABLE, BUSY, AWAY, LOCKED = "recommended", "available", "busy", "away", "locked"
STATUS_ORDER = (RECOMMENDED, AVAILABLE, BUSY, AWAY, LOCKED)
STATUS_LABELS = {RECOMMENDED: "Recommended", AVAILABLE: "Available", BUSY: "Busy", AWAY: "On leave",
                 LOCKED: "Not allowed"}

# Rate code → what the line expects of the person. Keys:
#   lead: true            team-lead line: role must be in qa.visit.team_lead_roles; one lead per sheet
#   department            "owning" (the case's handling department), "audit", "finance", "ict" or a
#                         department code — a preference (recommended) unless department_strict
#   department_strict     true: anyone outside that department is locked
#   grade_min / grade_max grade band; a known grade outside it locks the person
#   external_only         true: only external resource persons — NBTE staff are locked
#   visit                 false: not a visiting line (R-02 and visit clashes do not apply)
DEFAULT_RATE_RULES = {
    "dta-director": {"lead": True},
    "acad-allow-lead": {"lead": True},
    "dta-officer": {"department": "owning", "grade_min": 7, "grade_max": 16},
    "acad-allow-member": {"department": "owning", "grade_min": 7, "grade_max": 16},
    "dta-support": {"grade_max": 8},
    "honorarium-rp": {"external_only": True},
    "audit-supervision": {"department": "audit", "department_strict": True, "visit": False},
}
FALLBACK_RULE = {"department": "owning"}


def _int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def settings():
    return {
        "gap_days": _int("qa.people.visit_gap_days", 7),
        "max_visits": _int("qa.people.max_visits_per_period", 3),
        "period_days": _int("qa.people.visits_period_days", 90),
        "busy_tasks": _int("qa.people.busy_open_tasks", 8),
        "light_tasks": _int("qa.people.light_open_tasks", 3),
        "leave_days": _int("qa.people.leave_lookahead_days", 14),
    }


def rate_rules():
    raw = config.get("qa.cost.person_rules", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            raw = None
    return raw if isinstance(raw, dict) and raw else DEFAULT_RATE_RULES


def rule_for(rate):
    if rate is None:
        return dict(FALLBACK_RULE)
    rule = rate_rules().get(getattr(rate, "code", ""), None)
    if rule is None:
        rule = dict(FALLBACK_RULE)
        if getattr(rate, "allowance_type", "") == "fee" and "resource person" in (rate.role or "").lower():
            rule["external_only"] = True
    return dict(rule)


def grade_of(staff):
    raw = getattr(staff, "grade_level", "") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits:
        return int(digits)
    grade = getattr(staff, "grade", None)
    return getattr(grade, "number", None) if grade is not None else None


def _dept_for(case, spec):
    if not spec:
        return None
    if spec == "owning":
        return case.owning_department
    from qa_core.departments import qa_department

    if spec in ("audit", "finance", "ict"):
        return qa_department(spec)
    from staff.models import Department

    return Department.objects.filter(code=spec).first()


def _fmt(d):
    return f"{d.day} {d.strftime('%b')}"


def _span(a, b):
    if not a:
        return ""
    if not b or a == b:
        return _fmt(a)
    return f"{a.day}–{_fmt(b)}" if a.month == b.month else f"{_fmt(a)}–{_fmt(b)}"


# ---------------------------------------------------------------------------
# Batch facts (a handful of queries for the whole list)
# ---------------------------------------------------------------------------
class Facts:
    """Everything the rules need about the case and the candidates, loaded once."""

    def __init__(self, case, staff_list, *, purpose="cost", rate=None):
        self.case, self.purpose, self.rate = case, purpose, rate
        self.cfg = settings()
        self.today = timezone.localdate()
        staff_ids = [s.pk for s in staff_list]
        user_ids = [s.user_id for s in staff_list if s.user_id]
        visit = getattr(case, "_visit_cache", None)
        if visit is None:
            from . import case_services as cs
            from .models import VisitDetail

            visit = cs._memo(("visit", case.pk),
                             lambda: VisitDetail.unscoped.filter(case=case).first())
        self.visit = visit
        self.start = getattr(visit, "planned_start", None)
        self.end = getattr(visit, "planned_end", None) or self.start
        self.reviewers = self._ssq_reviewers(user_ids)
        self.audit_officer_id = case.supervising_audit_officer_id
        self.on_list = self._on_list()
        self.sheet_lines = self._sheet_lines()
        self.other_visits = self._other_visits(staff_ids)
        self.leave = self._leave(staff_ids)
        self.conflicts = self._conflicts(staff_ids)
        from . import case_ui

        self.tasks = case_ui._open_tasks_by_user(user_ids) if user_ids else {}
        self.lead_roles = set(self._lead_roles())
        self.workload = (batch_workload(case, staff_list, self.tasks)
                         if purpose == "personnel" and len(staff_list) > 1 else None)

    @staticmethod
    def _lead_roles():
        from . import case_services as cs

        return cs.team_lead_roles()

    def _ssq_reviewers(self, user_ids):
        """R-02: users who reviewed (or were given) this request's SSQs."""
        if not user_ids:
            return set()
        from django.contrib.contenttypes.models import ContentType
        from django.db.models import Q

        from assignments.models import WorkAssignment
        from inspection.models import SSQReviewAssignment

        from .models import SSQ

        def build():
            req = self.case.qa_request
            out = set(SSQ.unscoped.filter(qa_request_programme__qa_request=req, reviewed_by_id__in=user_ids)
                      .values_list("reviewed_by_id", flat=True))
            out |= set(SSQReviewAssignment.unscoped.filter(ssq__qa_request_programme__qa_request_id=req.pk,
                                                           reviewer_id__in=user_ids).values_list("reviewer_id", flat=True))
            ssq_ids = [str(pk) for pk in SSQ.unscoped.filter(qa_request_programme__qa_request=req)
                       .values_list("pk", flat=True)]
            ssq_ct = ContentType.objects.get_for_model(SSQ, for_concrete_model=False)
            inst_ct = ContentType.objects.get_for_model(self.case.institution, for_concrete_model=False)
            out |= set(WorkAssignment.objects.filter(assignee_id__in=user_ids, kind=WorkAssignment.SSQ_REVIEW)
                       .exclude(status__in=(WorkAssignment.DECLINED, WorkAssignment.CANCELLED))
                       .filter(Q(subject_ct=ssq_ct, subject_id__in=ssq_ids)
                               | Q(subject_ct=inst_ct, subject_id=str(self.case.institution_id),
                                   assigned_at__gte=req.created_at))
                       .values_list("assignee_id", flat=True))
            return out

        from . import case_services as cs

        # The personnel and visit pickers build Facts for overlapping staff
        # lists; the reviewer sets only change on SSQ/assignment writes.
        return cs._memo(("ssq_reviewers", self.case.pk, tuple(sorted(set(user_ids)))), build)

    def _on_list(self):
        from .models import PersonnelEntry

        return {e.staff_id: e.duty for e in PersonnelEntry.unscoped.filter(personnel_list__case=self.case)
                .exclude(status="returned").only("staff_id", "duty")}

    def _sheet_lines(self):
        from . import case_services as cs
        from .models import CostLine

        sheet = cs.current_sheet(self.case)
        if sheet is None:
            return []
        return list(CostLine.unscoped.filter(sheet=sheet).select_related("rate", "staff"))

    def _other_visits(self, staff_ids):
        """{staff_id: [(start, end, institution name)]} — planned visits on other cases."""
        from django.db.models import Q

        from .models import PersonnelEntry, VisitDetail

        if not staff_ids:
            return {}
        lo = self.today - datetime.timedelta(days=self.cfg["period_days"])
        hi = self.today + datetime.timedelta(days=max(self.cfg["period_days"], 400))
        visits = {v.case_id: v for v in VisitDetail.unscoped.filter(planned_start__isnull=False,
                                                                     planned_start__gte=lo, planned_start__lte=hi)
                  .exclude(case=self.case).select_related("case__institution")}
        out = {}
        if not visits:
            return out
        rows = (PersonnelEntry.unscoped.filter(personnel_list__case_id__in=list(visits), staff_id__in=staff_ids)
                .exclude(status="returned").values_list("staff_id", "personnel_list__case_id"))
        pairs = set(rows)
        for v in visits.values():
            if v.team_lead_id in staff_ids:
                pairs.add((v.team_lead_id, v.case_id))
        for staff_id, case_id in pairs:
            v = visits[case_id]
            out.setdefault(staff_id, []).append((v.planned_start, v.planned_end or v.planned_start,
                                                 v.case.institution.name))
        for lst in out.values():
            lst.sort()
        return out

    def _leave(self, staff_ids):
        try:
            from leave.models import LeaveApplication
            from leave.services import APPROVED_REQUEST_STATUSES
        except Exception:  # noqa: BLE001 — the leave module is optional
            return {}
        if not staff_ids:
            return {}
        lo = self.start or self.today
        hi = self.end or (self.today + datetime.timedelta(days=self.cfg["leave_days"]))
        out = {}
        try:
            for sid, a, b in (LeaveApplication.objects.filter(staff_id__in=staff_ids,
                                                              request__status__in=APPROVED_REQUEST_STATUSES,
                                                              start_date__lte=hi, end_date__gte=lo)
                              .values_list("staff_id", "start_date", "end_date").order_by("start_date")):
                out.setdefault(sid, (a, b))
        except Exception:  # noqa: BLE001 — never break a form over the leave flag
            return {}
        return out

    def _conflicts(self, staff_ids):
        from .models import VisitConflictDeclaration

        if not staff_ids:
            return set()
        return set(VisitConflictDeclaration.unscoped.filter(
            staff_id__in=staff_ids, has_conflict=True, inspection__case__institution_id=self.case.institution_id)
            .values_list("staff_id", flat=True))


# ---------------------------------------------------------------------------
# The rules for one person
# ---------------------------------------------------------------------------
def evaluate(staff, facts, rule=None):
    """{status, reasons: [(tone, text)], locks: [text], warnings: [text]} for one Staff."""
    rule = rule if rule is not None else rule_for(facts.rate)
    case, cfg, purpose = facts.case, facts.cfg, facts.purpose
    user = staff.user
    good, warns, locks = [], [], []
    is_visit = rule.get("visit", True)
    role_code = getattr(getattr(user, "role", None), "code", "") if user is not None else ""

    # --- locks -------------------------------------------------------------
    if user is None or getattr(user, "user_type", "staff") != "staff":
        if not rule.get("external_only"):
            locks.append("Not an NBTE staff account")
    elif rule.get("external_only"):
        locks.append("This line is for external resource persons only")
    if role_code == "ict-qa-monitor":
        locks.append("ICT monitor is read-only (R-13)")
    if is_visit and user is not None and user.pk in facts.reviewers:
        locks.append("Reviewed this request's SSQ (R-02)")
    if is_visit and purpose in ("cost", "personnel") and user is not None and user.pk == facts.audit_officer_id \
            and _reviewer_cannot_verify():
        locks.append("Audits this case's cost — cannot be on the visit (R-02)")
    if is_visit and staff.pk in facts.conflicts:
        locks.append("Declared a conflict of interest with this institution")
    if purpose == "personnel" and staff.pk in facts.on_list:
        locks.append("Already on the visit team")
    if purpose == "cost" and facts.rate is not None:
        if any(line.staff_id == staff.pk and line.rate_id == facts.rate.pk for line in facts.sheet_lines):
            locks.append("Already has this line on the sheet")
    grade = grade_of(staff)
    lo, hi = rule.get("grade_min"), rule.get("grade_max")
    if grade is not None and ((lo is not None and grade < lo) or (hi is not None and grade > hi)):
        band = f"GL {lo or 1}–{hi}" if hi is not None else f"GL {lo}+"
        locks.append(f"GL {grade} is outside this line's {band}")
    if rule.get("lead") or purpose == "lead":
        if role_code not in facts.lead_roles:
            locks.append("Role cannot lead a visit (qa.visit.team_lead_roles)")
        elif purpose == "cost":
            lead_codes = {code for code, r in rate_rules().items() if r.get("lead")}
            other = next((ln for ln in facts.sheet_lines if ln.staff_id and ln.staff_id != staff.pk
                          and ln.rate is not None and ln.rate.code in lead_codes), None)
            if other is not None:
                locks.append(f"{other.staff.first_name} {other.staff.last_name} is already costed as team lead")
    dept = _dept_for(case, rule.get("department"))
    in_dept = dept is not None and staff.department_id == dept.pk
    if dept is not None and rule.get("department_strict") and not in_dept:
        locks.append(f"Only {dept.name} staff")

    # --- busy / away -------------------------------------------------------
    tasks = facts.tasks.get(getattr(user, "pk", None), 0)
    cap = cfg["busy_tasks"]
    if cap and tasks >= cap:
        warns.append(("busy", f"{tasks} open tasks (cap {cap})"))
    visits = facts.other_visits.get(staff.pk, []) if is_visit else []
    gap = datetime.timedelta(days=cfg["gap_days"])
    if facts.start:
        win_lo, win_hi = facts.start - gap, facts.end + gap
    else:
        win_lo, win_hi = facts.today, facts.today + gap
    for a, b, inst in visits:
        if b >= win_lo and a <= win_hi:
            overlap = bool(facts.start and b >= facts.start and a <= facts.end)
            warns.append(("busy", f"{'Same dates as' if overlap else 'Close to'} the visit to {inst} ({_span(a, b)})"))
            break
    period = datetime.timedelta(days=cfg["period_days"])
    ref = facts.start or facts.today
    n_period = sum(1 for a, _b, _i in visits if ref - period <= a <= ref + period)
    if is_visit and cfg["max_visits"] and n_period >= cfg["max_visits"]:
        warns.append(("busy", f"{n_period} visits within {cfg['period_days']} days (max {cfg['max_visits']})"))
    if purpose == "personnel" and is_visit:
        locks.extend(facts.workload.get(staff.pk, []) if facts.workload is not None
                     else _workload_problems(staff, case))
    lv = facts.leave.get(staff.pk)
    if lv:
        a, b = lv
        warns.append(("away", f"On leave {_span(a, b)}"))

    # --- good signs --------------------------------------------------------
    if staff.pk in facts.on_list:
        good.append("On this visit team" if purpose != "personnel" else "")
    if in_dept:
        good.append("Handling department" if rule.get("department") == "owning" else dept.name)
    if grade is not None and (lo is not None or hi is not None) and not any("outside" in x for x in locks):
        good.append(f"GL {grade} fits")
    if rule.get("lead") and role_code in facts.lead_roles:
        good.append("Can lead")
    good = [g for g in good if g]

    if locks:
        status = LOCKED
    elif any(k == "away" for k, _t in warns):
        status = AWAY
    elif warns:
        status = BUSY
    else:
        fits_dept = in_dept or dept is None or staff.pk in facts.on_list
        fits_grade = (lo is None and hi is None) or grade is not None
        status = RECOMMENDED if (fits_dept and fits_grade and tasks < cfg["light_tasks"]) else AVAILABLE
    reasons = ([("bad", t) for t in locks] + [("warn", t) for _k, t in warns]
               + [("good", t) for t in good])
    return {"status": status, "reasons": reasons, "locks": locks, "warnings": [t for _k, t in warns],
            "tasks": tasks}


def _reviewer_cannot_verify():
    from . import case_services as cs

    return cs._bool("qa.workload.reviewer_cannot_verify", True)


def batch_workload(case, staff_list, tasks_by_user=None):
    """{staff_id: [message]} — the per-rank limits add_personnel enforces
    (inspection.workload.problems_for: visits a year, same institution within
    N months, open tasks), computed for the whole list in a few queries
    instead of ~10 per person."""
    from django.db.models import Q

    from inspection import workload
    from inspection.models import VISIT_DUTIES, QAPersonnel, ResourcePerson, ServiceRecord

    staff_list = [s for s in staff_list if s is not None]
    if not staff_list:
        return {}
    rows = workload.table()
    by_key = {r["key"]: r for r in rows}
    default = by_key.get("default")
    staff_ids = [s.pk for s in staff_list]
    user_ids = [s.user_id for s in staff_list if s.user_id]
    qp = dict(QAPersonnel.objects.filter(staff_id__in=staff_ids).values_list("staff_id", "pk"))
    rp = dict(ResourcePerson.objects.filter(user_id__in=user_ids).values_list("user_id", "pk")) if user_ids else {}
    rp_staff = dict(ResourcePerson.objects.filter(staff_id__in=staff_ids).values_list("pk", "staff_id"))
    # (person_type, person_id) → staff id
    owner = {("staff", sid): sid for sid in staff_ids}
    owner.update({("qa_personnel", qid): sid for sid, qid in qp.items()})
    uid_staff = {s.user_id: s.pk for s in staff_list if s.user_id}
    owner.update({("resource_person", rid): uid_staff[uid] for uid, rid in rp.items()})
    owner.update({("resource_person", rid): sid for rid, sid in rp_staff.items()})
    q = (Q(person_type="staff", person_id__in=staff_ids) | Q(person_type="qa_personnel", person_id__in=list(qp.values()))
         | Q(person_type="resource_person", person_id__in=[k[1] for k in owner if k[0] == "resource_person"]))
    today = timezone.localdate()
    year = today.year
    visits_year, at_inst = {}, {}
    max_months = max([r.get("same_institution_months") or 0 for r in rows]
                     + [workload._general("qa.matching.same_institution_cooldown_months", 24)])
    since_max = today - datetime.timedelta(days=int(max_months * 30.44))
    for ptype, pid, served_to, n, inst_id in (ServiceRecord.unscoped.filter(q, duty__in=VISIT_DUTIES)
                                              .filter(Q(served_to__year=year) | Q(institution_id=case.institution_id,
                                                                                    served_to__gte=since_max))
                                              .values_list("person_type", "person_id", "served_to", "visit_count",
                                                           "institution_id")):
        sid = owner.get((ptype, pid))
        if sid is None:
            continue
        if served_to and served_to.year == year:
            visits_year[sid] = visits_year.get(sid, 0) + (n or 0)
        if inst_id == case.institution_id and served_to:
            at_inst.setdefault(sid, []).append((served_to, n or 0))
    if tasks_by_user is None:
        from . import case_ui

        tasks_by_user = case_ui._open_tasks_by_user(user_ids) if user_ids else {}

    def lim(row, field):
        spec = next(f for f in workload.FIELDS if f[0] == field)
        for r in (row, default):
            if r is not None and r.get(field) is not None:
                return r[field]
        return workload._general(spec[3], spec[4])

    out = {}
    for st in staff_list:
        user = st.user
        keys = []
        code = getattr(getattr(st, "designation", None), "code", "") or ""
        if code:
            keys.append(code.lower())
        role = getattr(getattr(user, "role", None), "code", "") if user is not None else ""
        if role:
            keys.append(role.lower())
        if st.pk in qp:
            keys.append("qa_personnel")
        keys.append("staff")
        row = next((by_key[k] for k in keys if k in by_key and k != "default"), default)
        msgs = []
        cap = lim(row, "max_visits_per_year")
        if cap is not None and visits_year.get(st.pk, 0) >= cap:
            msgs.append(f"{visits_year.get(st.pk, 0)} visit(s) in {year}; the cap is {cap}")
        n_cap, months = lim(row, "max_same_institution"), lim(row, "same_institution_months")
        if n_cap and months:
            since = today - datetime.timedelta(days=int(months * 30.44))
            served = sum(n for d, n in at_inst.get(st.pk, []) if d >= since)
            if served >= n_cap:
                msgs.append(f"Served this institution within the last {months} months")
        t_cap = lim(row, "max_open_tasks")
        if t_cap is not None and user is not None and tasks_by_user.get(user.pk, 0) >= t_cap:
            msgs.append(f"{tasks_by_user.get(user.pk, 0)} open task(s); the limit is {t_cap}")
        if msgs:
            out[st.pk] = msgs
    return out


def _workload_problems(staff, case):
    """The per-rank limits add_personnel enforces (annual visits, same institution, open tasks)."""
    try:
        from inspection import workload

        person = staff.user if staff.user_id else staff
        return [msg for _rule, _setting, msg in workload.problems_for(
            person, duty="physical_verification", institution_id=case.institution_id, name="")]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def rank(case, staff_list, *, rate=None, purpose="cost"):
    """Picker options (case_ui.people rows) with status, reasons, sorted
    Recommended → Available → Busy → On leave → Not allowed."""
    from . import case_ui

    staff_list = [s for s in staff_list if s is not None]
    facts = Facts(case, staff_list, purpose=purpose, rate=rate)
    rule = rule_for(rate) if purpose == "cost" else ({"lead": True} if purpose == "lead" else {"department": "owning"})
    verdicts = {s.pk: evaluate(s, facts, rule) for s in staff_list}
    rows = case_ui.people(staff_list, staff_mode=True, suggest=False)
    for row in rows:
        v = verdicts[row["pk"]]
        row["status"] = v["status"]
        row["status_label"] = STATUS_LABELS[v["status"]]
        row["reasons"] = v["reasons"]
        row["blocked"] = "; ".join(v["locks"])
        row["suggested"] = v["status"] == RECOMMENDED
        row["tasks"] = v["tasks"]
    order = {s: i for i, s in enumerate(STATUS_ORDER)}
    rows.sort(key=lambda r: (order[r["status"]], r["tasks"], r["name"].lower()))
    return rows


def check(case, staff, *, rate=None, purpose="cost"):
    """The full verdict for one person (used by the save paths)."""
    facts = Facts(case, [staff], purpose=purpose, rate=rate)
    rule = rule_for(rate) if purpose == "cost" else ({"lead": True} if purpose == "lead" else {"department": "owning"})
    return evaluate(staff, facts, rule)


def describe(case, rate):
    """Plain words for what the chosen line expects (shown under the picker)."""
    if rate is None:
        return "Choose the rate first — the list is then ranked for that line."
    rule = rule_for(rate)
    bits = []
    if rule.get("external_only"):
        return ("This line is for an external resource person: leave Person empty (NBTE staff cannot be costed "
                "on it). Resource persons join the visit team on the Personnel tab.")
    if rule.get("lead"):
        from . import case_services as cs

        bits.append("team lead: " + ", ".join(r.replace("-", " ") for r in cs.team_lead_roles()) + " only, one per sheet")
    dept = _dept_for(case, rule.get("department"))
    if dept is not None:
        bits.append(f"{'only' if rule.get('department_strict') else 'preferably'} {dept.name} staff")
    lo, hi = rule.get("grade_min"), rule.get("grade_max")
    if lo is not None or hi is not None:
        bits.append(f"GL {lo or 1}–{hi}" if hi is not None else f"GL {lo} and above")
    if not bits:
        return f"“{rate.role}”: anyone on the visit."
    return f"“{rate.role}”: " + "; ".join(bits) + "."


def summary(rows):
    """{status: count} for the picker header."""
    out = {s: 0 for s in STATUS_ORDER}
    for r in rows:
        out[r.get("status", AVAILABLE)] += 1
    return out


# ===========================================================================
# External participants on the visit team (personnel tab only)
# ===========================================================================
# Every other SOP-03 picker is NBTE staff only. The visit team may also take
# (1) resource persons from the inspection register whose discipline matches a
# case programme, and (2) representatives of the professional bodies mapped to
# the case's joint-QA programmes. They are stored as VisitExternalMember, never
# mixed into the staff list.
def case_programmes(case):
    from .models import QARequestProgramme

    return [rp.programme for rp in QARequestProgramme.unscoped.filter(
        qa_request=case.qa_request, status=QARequestProgramme.ACTIVE).select_related(
        "programme__category__discipline", "programme__master__discipline", "programme__professional_body")]


def case_disciplines(case):
    """{discipline_pk: name} across the case's programmes (per-request memo:
    the personnel and visit tabs ask, and each external candidate row asks)."""
    def build():
        return _case_disciplines_uncached(case)

    from . import case_services as cs

    return cs._memo(("disciplines", case.pk), build)


def _case_disciplines_uncached(case):
    """{discipline_id: name} of the case's programmes (category, else master list)."""
    out = {}
    for prog in case_programmes(case):
        disc = getattr(getattr(prog, "category", None), "discipline", None)
        if disc is None and getattr(prog, "master_id", None):
            disc = getattr(prog.master, "discipline", None)
        if disc is not None:
            out[disc.pk] = disc.name
    return out


def case_bodies(case):
    """{body: [programme names]} for the case's joint-QA programmes."""
    from institutions.professional_bodies import bodies_for_programme

    out = {}
    for prog in case_programmes(case):
        try:
            bodies = bodies_for_programme(prog)
        except Exception:  # noqa: BLE001
            bodies = []
        for body in bodies:
            out.setdefault(body, []).append(prog.name)
    return out


def external_value(kind, pk):
    return f"{'rp' if kind == 'resource_person' else 'pb'}:{pk}"


def external_candidates(case):
    """Picker rows (same shape as case_ui.people rows) for the external picker.

    The whole read is remembered for one request: the personnel tab asks once,
    the visit tab asks again, and each candidate row's data only changes on
    visit-team writes (which bump the request memo)."""
    def build():
        return _external_candidates_uncached(case)

    from . import case_services as cs

    return cs._memo(("external_candidates", case.pk), build)


def _external_candidates_uncached(case):
    """Picker rows (same shape as case_ui.people rows) for the external picker."""
    from django.db.models import Sum

    from inspection.models import VISIT_DUTIES, QAMapping, ResourcePerson, ServiceRecord

    from .models import VisitConflictDeclaration, VisitExternalMember

    try:
        on_team = list(VisitExternalMember.unscoped.filter(case=case).exclude(status="returned")
                       .values_list("resource_person_id", "user_id"))
    except Exception:  # noqa: BLE001 — table not migrated yet: no external picker
        return []
    rp_on = {a for a, _b in on_team if a}
    user_on = {b for _a, b in on_team if b}
    discs = case_disciplines(case)
    try:
        from . import case_services as cs

        facts_visit = cs.current_visit(case)
    except Exception:  # noqa: BLE001
        facts_visit = None
    today = timezone.localdate()
    start = getattr(facts_visit, "planned_start", None) or today
    end = getattr(facts_visit, "planned_end", None) or (start + datetime.timedelta(days=_int("qa.people.visit_gap_days", 7)))
    rows = []
    if discs:
        people = list(ResourcePerson.objects.filter(status=ResourcePerson.ACTIVE, disciplines__discipline_id__in=list(discs))
                      .distinct().prefetch_related("disciplines__discipline").select_related("home_institution"))
        ids = [p.pk for p in people]
        busy = {}
        for rp_id, a, b, inst in (QAMapping.unscoped.filter(resource_person_id__in=ids, status__in=QAMapping.LIVE,
                                                            from_date__lte=end, to_date__gte=start)
                                  .exclude(case=case).values_list("resource_person_id", "from_date", "to_date",
                                                                   "case__institution__name")):
            busy.setdefault(rp_id, (a, b, inst))
        past = dict(ServiceRecord.unscoped.filter(person_type="resource_person", person_id__in=ids,
                                                  duty__in=VISIT_DUTIES)
                    .values("person_id").annotate(n=Sum("visit_count")).values_list("person_id", "n"))
        conflicts = set(VisitConflictDeclaration.unscoped.filter(
            resource_person_id__in=ids, has_conflict=True, inspection__case__institution_id=case.institution_id)
            .values_list("resource_person_id", flat=True))
        for p in people:
            mine = [d for d in p.disciplines.all() if d.discipline_id in discs]
            primary = next((d for d in mine if d.is_primary), None)
            match = primary or (mine[0] if mine else None)
            reasons, locks = [], []
            if p.pk in rp_on:
                locks.append("Already on the visit team")
            if p.home_institution_id and p.home_institution_id == case.institution_id:
                locks.append("Works at this institution (conflict of interest)")
            if p.pk in conflicts:
                locks.append("Declared a conflict of interest with this institution")
            warn = []
            if p.pk in busy:
                a, b, inst = busy[p.pk]
                warn.append(f"On the visit to {inst} ({_span(a, b)})")
            n_past = past.get(p.pk) or 0
            good = [f"{'Primary' if primary else 'Secondary'} discipline: {match.discipline.name}"] if match else []
            if n_past:
                good.append(f"{n_past} past visit{'s' if n_past != 1 else ''}")
            status = LOCKED if locks else BUSY if warn else (RECOMMENDED if primary else AVAILABLE)
            reasons = [("bad", t) for t in locks] + [("warn", t) for t in warn] + [("good", t) for t in good]
            name = p.full_name
            parts = [w for w in name.split() if w]
            rows.append({"pk": external_value("resource_person", p.pk), "name": name,
                         "initials": "".join(w[0] for w in parts[:2]).upper() or "?",
                         "rank": "External resource person",
                         "grade": "", "dept": match.discipline.name if match else "External",
                         "photo": "", "tasks": None, "cases": 0, "away": "", "note": p.rank or "",
                         "blocked": "; ".join(locks), "suggested": status == RECOMMENDED, "status": status,
                         "status_label": STATUS_LABELS[status], "reasons": reasons, "staff_no": "",
                         "_order": (0 if primary else 1, n_past, name.lower())})
    for body, progs in case_bodies(case).items():
        from institutions.pb_portal import body_users

        for u in body_users(body):
            locks = ["Already on the visit team"] if u.pk in user_on else []
            name = (u.get_full_name() or "").strip() or u.email
            parts = [w for w in name.split() if w]
            status = LOCKED if locks else RECOMMENDED
            rows.append({"pk": external_value("professional_body", u.pk), "name": name,
                         "initials": "".join(w[0] for w in parts[:2]).upper() or "?",
                         "rank": "Professional body representative", "grade": "", "dept": body.name, "photo": "",
                         "tasks": None, "cases": 0, "away": "", "note": "",
                         "blocked": "; ".join(locks), "suggested": status == RECOMMENDED, "status": status,
                         "status_label": STATUS_LABELS[status],
                         "reasons": [("bad", t) for t in locks] + [("good", "Joint QA: " + ", ".join(progs))],
                         "staff_no": "", "_order": (2, 0, name.lower())})
    order = {s: i for i, s in enumerate(STATUS_ORDER)}
    rows.sort(key=lambda r: (order[r["status"]], r["_order"]))
    return rows


def add_external(case, *, user, value, remark=""):
    """Put an external participant on the visit team (server-side checks
    mirror the picker: discipline match / body mapping, conflicts, duplicates)."""
    from django.core.exceptions import ValidationError

    from accounts.models import User
    from inspection.models import ResourcePerson

    from . import case_services as cs
    from .models import VisitConflictDeclaration, VisitExternalMember

    cs._require(case, user, "add_personnel")
    kind, _sep, raw = (value or "").partition(":")
    try:
        pk = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("Choose an external participant.") from None
    if kind == "rp":
        rp = ResourcePerson.objects.filter(pk=pk).prefetch_related("disciplines").first()
        if rp is None or rp.status != ResourcePerson.ACTIVE:
            raise ValidationError("Only active resource persons from the register can join a visit.")
        discs = case_disciplines(case)
        match = sorted((d for d in rp.disciplines.all() if d.discipline_id in discs), key=lambda d: not d.is_primary)
        if not match:
            raise ValidationError(f"{rp.full_name} does not cover the discipline of this case's programmes "
                                  f"({', '.join(discs.values()) or 'none on record'}).")
        if rp.home_institution_id and rp.home_institution_id == case.institution_id:
            raise ValidationError(f"{rp.full_name} works at this institution (conflict of interest).")
        if VisitConflictDeclaration.unscoped.filter(resource_person=rp, has_conflict=True,
                                                    inspection__case__institution_id=case.institution_id).exists():
            raise ValidationError(f"{rp.full_name} declared a conflict of interest with this institution.")
        if VisitExternalMember.unscoped.filter(case=case, resource_person=rp).exclude(status="returned").exists():
            raise ValidationError(f"{rp.full_name} is already on the visit team.")
        return VisitExternalMember.unscoped.create(case=case, kind=VisitExternalMember.RESOURCE_PERSON,
                                                   resource_person=rp, discipline_id=match[0].discipline_id,
                                                   remark=(remark or "")[:255], added_by=user)
    if kind == "pb":
        rep = User.objects.filter(pk=pk, is_active=True, user_type="professional_body").select_related(
            "professional_body").first()
        bodies = case_bodies(case)
        if rep is None or rep.professional_body_id not in {b.pk for b in bodies}:
            raise ValidationError("Choose a representative of a professional body mapped to one of this case's "
                                  "programmes.")
        if VisitExternalMember.unscoped.filter(case=case, user=rep).exclude(status="returned").exists():
            raise ValidationError("That representative is already on the visit team.")
        return VisitExternalMember.unscoped.create(case=case, kind=VisitExternalMember.PROFESSIONAL_BODY,
                                                   user=rep, body=rep.professional_body, remark=(remark or "")[:255],
                                                   added_by=user)
    raise ValidationError("Choose an external participant.")


def remove_external(case, member, *, user):
    from django.core.exceptions import ValidationError

    from . import case_services as cs

    cs._require(case, user, "add_personnel")
    if member.case_id != case.pk:
        raise ValidationError("That person is not on this case's visit team.")
    member.delete()


def external_members(case):
    from .models import VisitExternalMember

    try:
        return list(VisitExternalMember.unscoped.filter(case=case).select_related(
            "resource_person", "discipline", "body", "user"))
    except Exception:  # noqa: BLE001 — not migrated yet
        return []
