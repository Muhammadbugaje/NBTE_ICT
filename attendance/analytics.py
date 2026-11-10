"""Attendance analytics for HR and the ES (v2.1 Phase D, G5, G6, G8).

Scope (attendance/access.py, G5): the ES, HR's directors, super admins and
anyone granted ``attendance.view_all_attendance`` see every department; a
director (or holder of ``view_department_attendance``) sees their own.

Every figure is computed from AttendanceRecord rows in the period, on
working days up to today (the same rule as the reports: no record = not
counted):

    punctuality %   present / (present + late)
    attendance %    credited / counted (credited = every status but absent)
    early arrival   checked in at least ``attendance.early_minutes`` (15)
                    before the location's start time
    avg arrival     mean local check-in time of present + late days
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone

from . import access, services
from .models import AttendanceRecord


def early_minutes():
    return max(0, services._cfg_int("attendance.early_minutes", 15))


@dataclass
class PersonStats:
    user: object
    department: object = None
    present: int = 0
    late: int = 0
    absent: int = 0
    excused: int = 0
    holiday: int = 0
    on_leave: int = 0
    on_duty: int = 0
    missing_checkout: int = 0
    early: int = 0
    counted: int = 0
    arrivals: list = field(default_factory=list)   # minutes after midnight
    departures: list = field(default_factory=list)

    @property
    def credited(self):
        return self.counted - self.absent

    @property
    def attendance_pct(self):
        return services.pct_of(self.credited, self.counted)

    @property
    def punctuality_pct(self):
        attended = self.present + self.late
        return round(self.present * 100 / attended) if attended else None

    @property
    def avg_arrival(self):
        return round(sum(self.arrivals) / len(self.arrivals)) if self.arrivals else None

    @property
    def avg_arrival_label(self):
        return _hhmm(self.avg_arrival)

    @property
    def avg_departure_label(self):
        return _hhmm(round(sum(self.departures) / len(self.departures)) if self.departures else None)

    @property
    def name(self):
        return self.user.get_full_name() or self.user.email


def _hhmm(minutes):
    if minutes is None:
        return "—"
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _minutes(dt):
    local = timezone.localtime(dt)
    return local.hour * 60 + local.minute


def scope_departments(user):
    """(scope, departments queryset) the viewer may analyse."""
    from staff.models import Department

    scope = access.attendance_scope(user)
    if scope == "all":
        return scope, Department.objects.all().order_by("name")
    if scope == "department":
        return scope, Department.objects.filter(pk=user.department_id)
    return "none", Department.objects.none()


def users_in(departments, department_id=None):
    from accounts.models import User

    qs = User.objects.filter(is_active=True, user_type="staff", department__in=departments)
    if department_id:
        qs = qs.filter(department_id=department_id)
    return list(qs.select_related("department").order_by("first_name", "last_name", "email"))


def default_period(today=None):
    today = today or timezone.localdate()
    return today.replace(day=1), today


def compute(users, first_day, last_day, today=None):
    """{"people": [PersonStats], "totals": PersonStats-like dict, "weeks": [...]}"""
    today = today or timezone.localdate()
    stop = min(last_day, today)
    by_id = {u.pk: PersonStats(user=u, department=getattr(u, "department", None)) for u in users}
    recs = (AttendanceRecord.objects.filter(user_id__in=list(by_id), date__gte=first_day, date__lte=stop)
            .select_related("location"))
    early = early_minutes()
    weeks = defaultdict(lambda: defaultdict(int))
    for r in recs:
        if not services.is_working_day(r.date) or r.status not in services.TALLY_STATUSES:
            continue
        p = by_id[r.user_id]
        p.counted += 1
        setattr(p, r.status, getattr(p, r.status) + 1)
        monday = r.date - timedelta(days=r.date.weekday())
        weeks[monday][r.status] += 1
        weeks[monday]["counted"] += 1
        if r.missing_checkout:
            p.missing_checkout += 1
        if r.status in AttendanceRecord.STATUSES_PRESENT and r.check_in_at:
            m = _minutes(r.check_in_at)
            p.arrivals.append(m)
            start = getattr(r.location, "work_start_time", None) if r.location_id else None
            if start is not None and m <= start.hour * 60 + start.minute - early:
                p.early += 1
                weeks[monday]["early"] += 1
            if r.check_out_at:
                p.departures.append(_minutes(r.check_out_at))
    people = list(by_id.values())
    totals = defaultdict(int)
    for p in people:
        for k in services.TALLY_STATUSES + ("missing_checkout", "early", "counted"):
            totals[k] += getattr(p, k)
    totals["credited"] = totals["counted"] - totals["absent"]
    totals["attendance_pct"] = services.pct_of(totals["credited"], totals["counted"])
    attended = totals["present"] + totals["late"]
    totals["punctuality_pct"] = round(totals["present"] * 100 / attended) if attended else None
    all_arr = [m for p in people for m in p.arrivals]
    totals["avg_arrival_label"] = _hhmm(round(sum(all_arr) / len(all_arr)) if all_arr else None)
    week_rows = []
    for monday in sorted(weeks):
        w = weeks[monday]
        week_rows.append({"week": monday, "label": monday.strftime("%d %b"), **{k: w.get(k, 0) for k in (
            "present", "late", "absent", "on_leave", "on_duty", "holiday", "excused", "early", "counted")},
            "attendance_pct": services.pct_of(w["counted"] - w.get("absent", 0), w["counted"])})
    return {"people": people, "totals": dict(totals), "weeks": week_rows}


def leaderboards(people, size=8):
    with_arrivals = [p for p in people if p.arrivals]
    return {
        "earliest": sorted(with_arrivals, key=lambda p: (p.avg_arrival, -p.early))[:size],
        "late": sorted([p for p in people if p.late], key=lambda p: (-p.late, p.name))[:size],
        "absent": sorted([p for p in people if p.absent], key=lambda p: (-p.absent, p.name))[:size],
        "missing_checkout": sorted([p for p in people if p.missing_checkout],
                                   key=lambda p: (-p.missing_checkout, p.name))[:size],
    }


def department_rows(people):
    by = defaultdict(list)
    for p in people:
        by[getattr(p.department, "pk", None)].append(p)
    rows = []
    for _pk, ps in by.items():
        counted = sum(p.counted for p in ps)
        absent = sum(p.absent for p in ps)
        present = sum(p.present for p in ps)
        late = sum(p.late for p in ps)
        rows.append({"department": ps[0].department, "people": len(ps), "counted": counted,
                     "attendance_pct": services.pct_of(counted - absent, counted),
                     "punctuality_pct": round(present * 100 / (present + late)) if (present + late) else None,
                     "late": late, "absent": absent})
    return sorted(rows, key=lambda r: getattr(r["department"], "name", "") or "")


def can_view_person(viewer, person):
    if viewer.pk == person.pk:
        return True
    dept = getattr(person, "department", None)
    return bool(dept) and access.can_view_department(viewer, dept)


def person_days(person, first_day, last_day):
    return list(AttendanceRecord.objects.filter(user=person, date__gte=first_day, date__lte=last_day)
                .select_related("location", "leave_application__leave_type", "duty_inspection")
                .order_by("-date"))


def parse_period(get, today=None):
    from django.utils.dateparse import parse_date

    first, last = default_period(today)
    f = parse_date(get.get("from") or "") or first
    t = parse_date(get.get("to") or "") or last
    if t < f:
        f, t = t, f
    if (t - f).days > 400:
        f = t - timedelta(days=400)
    return f, t


__all__ = ["compute", "leaderboards", "department_rows", "scope_departments", "users_in", "parse_period",
           "person_days", "can_view_person", "date"]
