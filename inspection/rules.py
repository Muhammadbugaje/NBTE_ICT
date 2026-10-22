"""Eligibility (§9 "Eligibility rules", v1.1) and the availability finder
(§9 "Available for the next visit"), with the booking guard.

Every rule is a fixed check with a configurable threshold (a setting). A
failure names the rule and the setting that blocked it. **There is no
override**: no permission, role or superuser path skips a rule — changing
the setting is the only lever (§9).

Two moments use the rules, always server-side:

* nomination / registration time — ``nomination_problems`` (resource
  persons) and ``staff_problems`` (QA personnel);
* assignment time — ``assignment_problems`` (a QAMapping being proposed or
  confirmed), which also runs every availability filter.

``book`` creates a mapping under the guard: it locks the person row
(``select_for_update`` — real on PostgreSQL) and bumps
``booking_version`` with a compare-and-swap UPDATE, which every database
honours. Of two officers booking the same person at once, the second is
refused. On PostgreSQL an ``ExclusionConstraint`` on (person, daterange) is
recommended as a further guard (not added: SQLite has no range types).
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

import config

from .models import (
    VISIT_DUTIES, QAMapping, QAPersonnel, ResourcePerson, ServiceRecord, SSQReviewAssignment,
)


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


def _list(key, default):
    value = config.get(key, default)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip().lower() for v in (value or [])]


def required_designations():
    return _list("qa.resource_person.required_designations",
                 ["director", "deputy-director", "chief-lecturer", "principal-lecturer"])


def min_rank_role():
    return str(config.get("qa.resource_person.min_rank", "director") or "director").strip()


def min_rank_value():
    from accounts.models import Role

    role = Role.objects.filter(code=min_rank_role()).first()
    return role.rank if role else 0


# ===========================================================================
# Problems
# ===========================================================================
@dataclass
class Problem:
    rule: str      # short code, e.g. "min_rank"
    setting: str   # the setting that sets the threshold ("" for fixed policy)
    message: str

    def __str__(self):
        return f"{self.message}" + (f" ({self.setting})" if self.setting else "")


def raise_if(problems, prefix=""):
    if problems:
        raise ValidationError([(prefix + str(p)) for p in problems])


RULES = [  # code, name, setting — the fixed list (for screens and docs)
    ("status", "Active status", ""),
    ("min_rank", "Minimum seniority", "qa.resource_person.min_rank"),
    ("designation", "Allowed posts / ranks", "qa.resource_person.required_designations"),
    ("discipline", "Discipline matches the programme", "qa.matching.allow_secondary_discipline"),
    ("double_booking", "Not double-booked", "qa.matching.travel_buffer_days"),
    ("one_programme", "One programme at a time (R-11) and concurrent load",
     "qa.resource_person.max_active_engagements"),
    ("conflict_of_interest", "Not their own institution", ""),
    ("cooldown", "Same-institution cooldown", "qa.matching.same_institution_cooldown_months"),
    ("annual_cap", "Annual visit cap", "qa.resource_person.max_visits_per_year"),
    ("open_ssq", "Open SSQ reviews (R-01)", "qa.resource_person.max_open_ssq_per_officer"),
    ("reviewer_not_visitor", "Reviewer is not a visitor (R-02)", "qa.workload.reviewer_cannot_verify"),
    ("ssq_year_cap", "SSQ reviews a year (per rank)", "qa.workload.limits"),
    ("open_tasks", "Open tasks (per rank)", "qa.workload.limits"),
    ("staff_first", "Resource persons only when no staff is free", "qa.workload.resource_persons_only_when_no_staff"),
]


# ---------------------------------------------------------------------------
# Rank and designation (staff and external)
# ---------------------------------------------------------------------------
def staff_problems(staff):
    """Rules 1–2 for an NBTE staff member (QA personnel or staff resource person)."""
    problems = []
    if staff is None:
        return [Problem("status", "", "No staff record.")]
    user = getattr(staff, "user", None)
    role = getattr(user, "role", None) if user else None
    need = min_rank_value()
    have = getattr(role, "rank", 0) or 0
    if have < need:
        problems.append(Problem(
            "min_rank", "qa.resource_person.min_rank",
            f"{staff} holds {getattr(role, 'name', 'no role')} (rank {have}); at least "
            f"{min_rank_role()} (rank {need}) is required"))
    codes = required_designations()
    designation = getattr(staff, "designation", None)
    if codes and (designation is None or (designation.code or "").lower() not in codes):
        problems.append(Problem(
            "designation", "qa.resource_person.required_designations",
            f"{staff}'s designation ({getattr(designation, 'name', 'none')}) is not one of: {', '.join(codes)}"))
    return problems


def nomination_problems(rp):
    """Rules checked when a resource person is nominated, vetted or activated."""
    if rp.is_staff_person:
        return staff_problems(rp.staff)
    codes = required_designations()
    if codes and (rp.rank or "").strip().lower() not in codes:
        return [Problem("designation", "qa.resource_person.required_designations",
                        f"{rp.full_name}'s rank '{rp.rank or 'none'}' is not one of: {', '.join(codes)}")]
    return []


def no_longer_eligible(person):
    """The §9 badge: a qualified person whose rank / designation changed."""
    if isinstance(person, ResourcePerson):
        return person.status == ResourcePerson.ACTIVE and bool(nomination_problems(person))
    if isinstance(person, QAPersonnel):
        return person.active and bool(staff_problems(person.staff))
    return False


# ===========================================================================
# Candidates (one shape for resource persons and QA personnel)
# ===========================================================================
@dataclass
class Candidate:
    obj: object
    kind: str                       # resource_person | qa_personnel
    name: str
    primary_disciplines: set = field(default_factory=set)
    secondary_disciplines: set = field(default_factory=set)
    home_institution_id: int | None = None
    user_id: int | None = None
    rating: float = 0.0
    visits_this_year: int = 0
    last_served: datetime.date | None = None
    load: dict = field(default_factory=dict)
    assignments_this_year: int = 0
    score: float = 0.0
    problems: list = field(default_factory=list)

    @property
    def pk(self):
        return self.obj.pk

    @property
    def key(self):
        return f"{self.kind}:{self.obj.pk}"

    @property
    def booking_version(self):
        return self.obj.booking_version

    @property
    def ok(self):
        return not self.problems


def candidate(person):
    if isinstance(person, ResourcePerson):
        prim = {d.discipline_id for d in person.disciplines.all() if d.is_primary}
        sec = {d.discipline_id for d in person.disciplines.all() if not d.is_primary}
        if not prim and sec:  # someone with only secondary rows: treat the first as primary
            prim, sec = sec, set()
        return Candidate(person, "resource_person", person.full_name, prim, sec, person.home_institution_id,
                         person.user_id, float(person.rating_avg or 0))
    return Candidate(person, "qa_personnel", str(person.staff), {d.pk for d in person.disciplines.all()}, set(),
                     None, person.staff.user_id, 0.0)


def _person_filter(c, prefix=""):
    return {f"{prefix}{c.kind}_id": c.obj.pk}


# ===========================================================================
# The assignment check (every rule) — used by the finder and by save paths
# ===========================================================================
@dataclass
class Need:
    """What a mapping is for."""
    case: object
    row: object                    # QARequestProgramme
    duty: str
    start: datetime.date
    end: datetime.date
    exclude_mapping_id: int | None = None

    @property
    def programme(self):
        return self.row.programme

    @property
    def institution_id(self):
        return self.case.institution_id

    @property
    def discipline_id(self):
        category = getattr(self.programme, "category", None)
        return getattr(category, "discipline_id", None)


def _live(c, need):
    qs = QAMapping.unscoped.filter(status__in=QAMapping.LIVE, **_person_filter(c))
    if need.exclude_mapping_id:
        qs = qs.exclude(pk=need.exclude_mapping_id)
    return qs


def _service(c):
    return ServiceRecord.unscoped.filter(person_type=c.kind, person_id=c.obj.pk)


def assignment_problems(person, need, *, stop_early=False):
    """All failed rules for putting ``person`` on ``need`` (empty = eligible)."""
    c = person if isinstance(person, Candidate) else candidate(person)
    p = []

    def add(problem):
        p.append(problem)
        return stop_early

    obj = c.obj
    # 1. status
    if c.kind == "resource_person":
        if obj.status != ResourcePerson.ACTIVE:
            if add(Problem("status", "", f"{c.name} is {obj.get_status_display().lower()}, not active")):
                return p
        for problem in nomination_problems(obj):  # rank / designation (no longer eligible → new work blocked)
            if add(problem):
                return p
    else:
        if not obj.active:
            if add(Problem("status", "", f"{c.name} is not active QA personnel")):
                return p
        for problem in staff_problems(obj.staff):
            if add(problem):
                return p
    # 2. discipline
    disc = need.discipline_id
    if disc is not None:
        allowed = c.primary_disciplines | (c.secondary_disciplines if _bool(
            "qa.matching.allow_secondary_discipline", False) else set())
        if disc not in allowed:
            label = getattr(getattr(need.programme.category, "discipline", None), "name", "the programme's")
            if add(Problem("discipline", "qa.matching.allow_secondary_discipline",
                           f"{c.name} does not cover the {label} discipline")):
                return p
    live = list(_live(c, need).select_related("qa_request_programme__programme"))
    # 3. double booking (with the travel buffer)
    buffer = datetime.timedelta(days=_int("qa.matching.travel_buffer_days", 2))
    lo, hi = need.start - buffer, need.end + buffer
    clash = [m for m in live if m.from_date <= hi and m.to_date >= lo]
    if clash:
        m = clash[0]
        if add(Problem("double_booking", "qa.matching.travel_buffer_days",
                       f"{c.name} is booked {m.from_date:%d %b}–{m.to_date:%d %b %Y} "
                       f"({m.qa_request_programme.programme}) within the {buffer.days}-day travel buffer")):
            return p
    # 4. one programme at a time (R-11) and the concurrent load
    others = {m.qa_request_programme.programme_id for m in live}
    if others and others != {need.programme.pk}:
        if add(Problem("one_programme", "", f"{c.name} already holds live work on another programme (R-11)")):
            return p
    cap = _int("qa.resource_person.max_active_engagements", 2)
    if len(live) >= cap:
        if add(Problem("one_programme", "qa.resource_person.max_active_engagements",
                       f"{c.name} already has {len(live)} live engagement(s); the limit is {cap}")):
            return p
    # 5. conflict of interest and cooldown
    if c.home_institution_id and c.home_institution_id == need.institution_id:
        if add(Problem("conflict_of_interest", "", f"{c.name} works at the institution being inspected")):
            return p
    # 5b–6 and R-01: the per-rank workload limits (qa.workload.limits), falling
    # back to the general settings (cooldown, annual cap, open SSQ reviews).
    from . import workload

    year = need.start.year
    c.visits_this_year = workload.visits_in(c, year)
    for rule, setting, message in workload.problems_for(c, duty=need.duty, institution_id=need.institution_id,
                                                        year=year, name=c.name,
                                                        exclude_mapping_id=need.exclude_mapping_id):
        if add(Problem(rule, setting, message)):
            return p
    # 7. R-02: the reviewer of the case is not a visitor
    if need.duty == "physical_verification" and _bool("qa.workload.reviewer_cannot_verify", True):
        if _is_reviewer_of(c, need.case):
            if add(Problem("reviewer_not_visitor", "qa.workload.reviewer_cannot_verify",
                           f"{c.name} reviewed this case's SSQs, so cannot verify it (R-02)")):
                return p
    return p


def _is_reviewer_of(c, case):
    mapped = QAMapping.unscoped.filter(case=case, duty="ssq_review", status__in=("proposed", "active", "released"),
                                       **_person_filter(c)).exists()
    if mapped:
        return True
    if c.user_id and case.qa_request_id:
        return SSQReviewAssignment.unscoped.filter(
            reviewer_id=c.user_id, ssq__qa_request_programme__qa_request_id=case.qa_request_id).exists()
    return False


def check_assignment(person, need):
    raise_if(assignment_problems(person, need) or resource_person_block(person, need))


# ===========================================================================
# The availability finder
# ===========================================================================
def _last_served(c):
    row = _service(c).filter(visit_count__gt=0).order_by("-served_to").values_list("served_to", flat=True).first()
    return row


def _score(c):
    from qa_core.formula import FormulaError, evaluate_setting

    days = (timezone.localdate() - c.last_served).days if c.last_served else 3650
    try:
        snap = evaluate_setting("qa.matching.rank_score", {"visits_this_year": c.visits_this_year,
                                                           "days_since_last": days, "rating": c.rating})
        return float(snap["result"])
    except (FormulaError, KeyError, TypeError, ValueError):
        return c.visits_this_year * 10 - days / 30 - c.rating


def pool(kind="all", *, search=""):
    rps = ResourcePerson.objects.prefetch_related("disciplines").select_related("home_institution", "staff__user")
    qps = QAPersonnel.objects.prefetch_related("disciplines").select_related("staff__user__role",
                                                                              "staff__designation")
    if search:
        rps = rps.filter(Q(full_name__icontains=search) | Q(email__icontains=search))
        qps = qps.filter(Q(staff__first_name__icontains=search) | Q(staff__last_name__icontains=search))
    out = []
    if kind in ("all", "resource_person"):
        out += list(rps)
    if kind in ("all", "qa_personnel"):
        out += list(qps)
    return out


def find(need, *, kind="all", search="", include_ineligible=False):
    """Candidates for ``need``: eligible ones ranked fairly — NBTE staff before
    resource persons (``qa.workload.staff_first``), then fewest assignments
    this year (visits + SSQ reviews), then longest since last served, then
    ``qa.matching.rank_score``. With ``include_ineligible`` the others come
    after, each with its reasons. Every candidate carries its current load."""
    from . import workload

    rows = workload.table()
    staff_first = workload.bool_setting("qa.workload.staff_first", True)
    only_when_no_staff = workload.bool_setting("qa.workload.resource_persons_only_when_no_staff", False)
    people = pool(kind, search=search)
    if only_when_no_staff and kind == "resource_person":
        people += pool("qa_personnel", search="")  # needed to know whether staff are free
    good, bad = [], []
    for person in people:
        c = candidate(person)
        c.problems = assignment_problems(c, need)
        c.load = workload.load(c, year=need.start.year, rows=rows)
        c.last_served = c.load["last_served"] or _last_served(c)
        c.assignments_this_year = c.load["assignments_year"]
        if c.ok:
            c.score = _score(c)
            good.append(c)
        elif include_ineligible:
            bad.append(c)
    if only_when_no_staff and any(c.kind == "qa_personnel" for c in good):
        for c in [c for c in good if c.kind == "resource_person" and not c.obj.is_staff_person]:
            c.problems = [Problem("staff_first", "qa.workload.resource_persons_only_when_no_staff",
                                  f"{c.name} is a resource person and NBTE staff are free for this work")]
            good.remove(c)
            if include_ineligible:
                bad.append(c)
    if kind == "resource_person":
        good = [c for c in good if c.kind == "resource_person"]
        bad = [c for c in bad if c.kind == "resource_person"]
    today = timezone.localdate()

    def fair(c):
        days = (today - c.last_served).days if c.last_served else 100000
        external = c.kind == "resource_person" and not c.obj.is_staff_person
        return (staff_first and external, c.assignments_this_year, -days, c.score, c.name)

    good.sort(key=fair)
    bad.sort(key=lambda c: c.name)
    return good + bad


def staff_free_for(need):
    """True when an active NBTE QA officer passes every rule for ``need``."""
    return any(not assignment_problems(qp, need, stop_early=True) for qp in pool("qa_personnel"))


def resource_person_block(person, need):
    """The "resource persons only when no staff" rule for a save path."""
    from . import workload

    if (isinstance(person, ResourcePerson) and not person.is_staff_person
            and workload.bool_setting("qa.workload.resource_persons_only_when_no_staff", False)
            and staff_free_for(need)):
        return [Problem("staff_first", "qa.workload.resource_persons_only_when_no_staff",
                        f"{person.full_name} is a resource person and NBTE staff are free for this work")]
    return []


# ===========================================================================
# Booking (the save path) — double-booking guard
# ===========================================================================
class BookingConflict(ValidationError):
    pass


def _model_of(person):
    return ResourcePerson if isinstance(person, ResourcePerson) else QAPersonnel


@transaction.atomic
def book(person, need, *, actor, expected_version=None, status="proposed", pending_action=None):
    """Create a QAMapping for ``person`` under the guard. Raises
    ValidationError naming the failed rules, or BookingConflict when someone
    else booked the person between the check and the save."""
    model = _model_of(person)
    # PostgreSQL: the second officer waits here until the first commits, then
    # re-checks against the committed booking. SQLite ignores the lock; the
    # compare-and-swap below still rejects the loser.
    locked = model.objects.select_for_update().get(pk=person.pk)
    version = locked.booking_version if expected_version is None else int(expected_version)
    check_assignment(locked, need)
    updated = model.objects.filter(pk=locked.pk, booking_version=version).update(booking_version=version + 1)
    if updated != 1:
        raise BookingConflict(f"{locked} was booked by someone else a moment ago. Refresh the list and try again.")
    kwargs = {"resource_person": locked} if model is ResourcePerson else {"qa_personnel": locked}
    return QAMapping.unscoped.create(case=need.case, qa_request_programme=need.row, duty=need.duty, status=status,
                                     from_date=need.start, to_date=need.end, created_by=actor,
                                     created_via=pending_action, **kwargs)


@transaction.atomic
def activate(mapping, *, actor=None):
    """Proposed → active (on confirmation), re-checking every rule except the
    mapping's own booking."""
    mapping = QAMapping.unscoped.select_for_update().get(pk=mapping.pk)
    if mapping.status != "proposed":
        raise ValidationError("Only a proposed mapping can be confirmed.")
    need = Need(mapping.case, mapping.qa_request_programme, mapping.duty, mapping.from_date, mapping.to_date,
                exclude_mapping_id=mapping.pk)
    check_assignment(mapping.person, need)
    mapping.status = "active"
    mapping.save(update_fields=["status", "updated_at"])
    return mapping


def release(mapping, *, reason="released"):
    QAMapping.unscoped.filter(pk=mapping.pk, status__in=QAMapping.LIVE).update(
        status="released", released_at=timezone.now(), updated_at=timezone.now())
