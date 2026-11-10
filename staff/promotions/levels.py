"""Grade-level adapter for the promotion engine (B.9, D2/D4/D8).

The promotion engine never reads grade fields directly; it asks this module.
Today a "level" is the existing ladder: `Staff.designation` (ordered by
`Designation.rank`, with `next_designation` as the normal step) plus the free
text `Staff.grade_level` (CONTEDISS number), auto-filled from the next
designation's `contediss_min`.

MERGE NOTE (pb-org, B.6): when the `GradeLevel` model and `Staff.grade` FK
land, switch the bodies below to it — the engine and the views keep calling
the same functions:

    level_of(staff)        -> staff.grade (GradeLevel)
    next_level(staff)      -> next active GradeLevel by number (+ the designation
                              whose grade range covers it, else staff.designation)
    level_rank(step)       -> GradeLevel.number
    min_years_for(staff)   -> staff.grade.min_years_in_grade (fallback: setting)
    same_level_filter(s)   -> Q(grade=s.grade)

Nothing outside this file should need to change for that switch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Q

import config

MIN_YEARS_KEY = "hr.promotion.min_years_in_grade"
GRACE_DAYS_KEY = "hr.promotion.overdue_grace_days"
DEFAULT_MIN_YEARS = 3
DEFAULT_GRACE_DAYS = 90


@dataclass(frozen=True)
class Step:
    """One rung of the ladder: where a promotion lands."""
    designation: object            # staff.Designation
    grade: str                     # CONTEDISS text ("" = keep the current grade)
    grade_autofilled: bool = False

    @property
    def rank(self):
        return level_rank(self)

    @property
    def label(self):
        g = f" · GL {self.grade}" if self.grade else ""
        return f"{self.designation.name}{g}"


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def grade_number(text) -> int | None:
    """'CONTEDISS 08' / '8' / 'GL8' -> 8; '' -> None."""
    m = re.search(r"\d+", str(text or ""))
    return int(m.group()) if m else None


def level_of(staff):
    """The staff member's current level (today: the designation)."""
    return staff.designation if staff.designation_id else None


def level_rank(level) -> int:
    """Seniority of a level, a Step or a Designation (higher = more senior)."""
    if level is None:
        return -1
    if isinstance(level, Step):
        level = level.designation
    return int(getattr(level, "rank", 0) or 0)


def step_to(designation, current_grade="") -> Step:
    """Landing on `designation`: its lowest CONTEDISS level, but never below
    the person's current grade (no demotion by grade either)."""
    if designation.contediss_min:
        new, cur = grade_number(designation.contediss_min), grade_number(current_grade)
        if new is not None and cur is not None and new < cur:
            return Step(designation, current_grade, False)
        return Step(designation, designation.contediss_min, True)
    return Step(designation, current_grade or "", False)


def next_level(staff) -> Step | None:
    """The only promotion normally allowed: one step up (D2)."""
    from . import next_step
    d = next_step(level_of(staff))
    return step_to(d, staff.grade_level) if d is not None else None


def is_next_level(staff, designation) -> bool:
    step = next_level(staff)
    return bool(step and designation and step.designation.pk == designation.pk)


def top_level_designation_ids() -> set:
    """Designations with no step above them (nobody there is 'eligible')."""
    from staff.models import Designation

    from . import next_step
    return {d.pk for d in Designation.objects.filter(is_active=True) if next_step(d) is None}


def min_years_for(staff=None) -> int:
    """Years in the present grade before promotion is due (D4)."""
    return max(0, _int(config.get(MIN_YEARS_KEY, DEFAULT_MIN_YEARS), DEFAULT_MIN_YEARS))


def overdue_grace_days() -> int:
    return max(0, _int(config.get(GRACE_DAYS_KEY, DEFAULT_GRACE_DAYS), DEFAULT_GRACE_DAYS))


def due_date(staff) -> date | None:
    start = staff.date_present_appointment
    if not start:
        return None
    years = min_years_for(staff)
    try:
        return start.replace(year=start.year + years)
    except ValueError:                       # 29 Feb
        return start.replace(year=start.year + years, day=28)


def _years_ago(on: date, years: int) -> date:
    try:
        return on.replace(year=on.year - years)
    except ValueError:
        return on.replace(year=on.year - years, day=28)


def eligible_filter(on: date, *, grace_days: int = 0) -> Q:
    """Q for staff whose time in grade is met on `on` (minus `grace_days`).
    With GradeLevel this becomes an OR of one cut-off per level."""
    cutoff = _years_ago(on, min_years_for()) - timedelta(days=grace_days)
    return Q(date_present_appointment__isnull=False, date_present_appointment__lte=cutoff)


def same_level_filter(staff) -> Q:
    """Everyone on the same grade level as `staff`, any department (D8)."""
    grade = (staff.grade_level or "").strip()
    if not grade:
        return Q(pk__in=[])
    n = grade_number(grade)
    variants = {grade}
    if n is not None:
        variants |= {str(n), f"{n:02d}"}
    return Q(grade_level__in=sorted(variants))
