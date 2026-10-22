"""Personal insights for "My workspace" (Phase 9).

Plain dicts/lists so the mobile-app API can return them unchanged:

    period_range(period, today=None) -> (start_date, end_date, buckets)
    arrival_stats(user, period, today=None) -> dict
    task_state_counts(user, period, today=None) -> dict
    ticket_trend(user, period, today=None) -> dict

Periods: "week" (last 7 days, one bucket per day), "month" (last 4 weeks plus
today, one bucket per week) and "quarter" (last 13 weeks, one per week).

`chart_context(user, period)` adds the SVG geometry the template draws; the
API should use the three functions above, not the geometry.
"""
from __future__ import annotations

import datetime as dt
import math

from django.db.models import Q
from django.utils import timezone

PERIODS = ("week", "month", "quarter")
DEFAULT_PERIOD = "month"
PERIOD_LABELS = {"week": "This week", "month": "Last 4 weeks", "quarter": "Last 13 weeks"}


def clean_period(value):
    return value if value in PERIODS else DEFAULT_PERIOD


def _today(today=None):
    return today or timezone.localdate()


def period_range(period, today=None):
    """(first day, last day, [bucket dicts]) - each bucket has start, end, label."""
    period = clean_period(period)
    today = _today(today)
    if period == "week":
        start = today - dt.timedelta(days=6)
        buckets = [{"start": start + dt.timedelta(days=i), "end": start + dt.timedelta(days=i),
                    "label": (start + dt.timedelta(days=i)).strftime("%a")} for i in range(7)]
        return start, today, buckets
    weeks = 4 if period == "month" else 13
    this_monday = today - dt.timedelta(days=today.weekday())
    first_monday = this_monday - dt.timedelta(weeks=weeks - 1)
    buckets = []
    for i in range(weeks):
        b_start = first_monday + dt.timedelta(weeks=i)
        b_end = min(b_start + dt.timedelta(days=6), today)
        buckets.append({"start": b_start, "end": b_end, "label": b_start.strftime("%-d %b")})
    return first_monday, today, buckets


def _bucket_index(buckets, day):
    for i, b in enumerate(buckets):
        if b["start"] <= day <= b["end"]:
            return i
    return None


def _hhmm(minutes):
    if minutes is None:
        return None
    minutes = int(round(minutes))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _iso(day):
    return day.isoformat()


# ---------------------------------------------------------------------------
# Arrival times (attendance.AttendanceRecord.check_in_at)
# ---------------------------------------------------------------------------
def arrival_stats(user, period, today=None):
    from attendance.models import AttendanceRecord

    start, end, buckets = period_range(period, today)
    rows = (AttendanceRecord.objects
            .filter(user=user, date__gte=start, date__lte=end, check_in_at__isnull=False,
                    status__in=AttendanceRecord.STATUSES_PRESENT)
            .values_list("date", "check_in_at", "status"))
    per_bucket = [[] for _ in buckets]
    all_minutes, late = [], 0
    for day, check_in, status in rows:
        local = timezone.localtime(check_in)
        minutes = local.hour * 60 + local.minute
        all_minutes.append(minutes)
        if status == AttendanceRecord.LATE:
            late += 1
        idx = _bucket_index(buckets, day)
        if idx is not None:
            per_bucket[idx].append(minutes)
    out_buckets = []
    for b, values in zip(buckets, per_bucket):
        avg = sum(values) / len(values) if values else None
        out_buckets.append({"label": b["label"], "start": _iso(b["start"]), "end": _iso(b["end"]),
                            "days": len(values), "average_minutes": round(avg) if avg is not None else None,
                            "average": _hhmm(avg)})
    average = sum(all_minutes) / len(all_minutes) if all_minutes else None
    return {
        "period": clean_period(period), "start": _iso(start), "end": _iso(end),
        "days_recorded": len(all_minutes), "late_days": late,
        "average_minutes": round(average) if average is not None else None,
        "average": _hhmm(average),
        "earliest": _hhmm(min(all_minutes)) if all_minutes else None,
        "latest": _hhmm(max(all_minutes)) if all_minutes else None,
        "buckets": out_buckets,
    }


# ---------------------------------------------------------------------------
# Tasks by state (tasks.Task, assigned to the user)
# ---------------------------------------------------------------------------
TASK_STATE_LABELS = (
    ("done", "Done"), ("in_progress", "In progress"), ("waiting", "Waiting"), ("overdue", "Overdue"),
)


def task_state_counts(user, period, today=None):
    """`done` counts tasks completed inside the period; the other three are
    the user's open tasks right now (a snapshot), with overdue taking
    precedence over in progress / waiting."""
    from tasks.models import Task

    start, end, _buckets = period_range(period, today)
    today = _today(today)
    mine = Task.objects.filter(assignee_rows__user=user).distinct()
    done = mine.filter(state=Task.COMPLETED, completed_at__date__gte=start, completed_at__date__lte=end).count()
    open_tasks = mine.filter(state__in=Task.OPEN_STATES)
    overdue = open_tasks.filter(due_date__lt=today).count()
    not_overdue = open_tasks.filter(Q(due_date__isnull=True) | Q(due_date__gte=today))
    in_progress = not_overdue.filter(state__in=[Task.OPEN, Task.IN_PROGRESS]).count()
    waiting = not_overdue.filter(state__in=[Task.AWAITING_RESPONSE, Task.AWAITING_REVIEW, Task.RETURNED]).count()
    counts = {"done": done, "in_progress": in_progress, "waiting": waiting, "overdue": overdue}
    return {"period": clean_period(period), "start": _iso(start), "end": _iso(end),
            **counts, "total": sum(counts.values()),
            "states": [{"key": k, "label": label, "count": counts[k]} for k, label in TASK_STATE_LABELS]}


# ---------------------------------------------------------------------------
# Tickets raised vs resolved (tickets.Ticket the user raised or handles)
# ---------------------------------------------------------------------------
def ticket_trend(user, period, today=None):
    from tickets.models import Ticket

    start, end, buckets = period_range(period, today)
    mine = Ticket.objects.filter(Q(raised_by=user) | Q(assigned_to=user))
    raised = [0] * len(buckets)
    resolved = [0] * len(buckets)
    for created in mine.filter(created_at__date__gte=start, created_at__date__lte=end).values_list(
            "created_at", flat=True):
        idx = _bucket_index(buckets, timezone.localtime(created).date())
        if idx is not None:
            raised[idx] += 1
    for done in mine.filter(resolved_at__isnull=False, resolved_at__date__gte=start,
                            resolved_at__date__lte=end).values_list("resolved_at", flat=True):
        idx = _bucket_index(buckets, timezone.localtime(done).date())
        if idx is not None:
            resolved[idx] += 1
    return {
        "period": clean_period(period), "start": _iso(start), "end": _iso(end),
        "raised_total": sum(raised), "resolved_total": sum(resolved),
        "buckets": [{"label": b["label"], "start": _iso(b["start"]), "end": _iso(b["end"]),
                     "raised": r, "resolved": s} for b, r, s in zip(buckets, raised, resolved)],
    }


# ---------------------------------------------------------------------------
# SVG geometry for the server-drawn charts (template only)
# ---------------------------------------------------------------------------
CHART_W, CHART_H, PAD_L, PAD_B, PAD_T = 320, 150, 34, 22, 10


def _nice_max(value):
    if value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def _arrival_geometry(stats):
    values = [b["average_minutes"] for b in stats["buckets"] if b["average_minutes"] is not None]
    if not values:
        return None
    lo = (min(values) // 30) * 30 - 30
    hi = (max(values) // 30) * 30 + 60
    lo, hi = max(lo, 0), min(hi, 24 * 60)
    plot_w, plot_h = CHART_W - PAD_L - 4, CHART_H - PAD_B - PAD_T
    n = len(stats["buckets"])
    slot = plot_w / n
    bar_w = min(26, slot * 0.6)
    bars = []
    for i, b in enumerate(stats["buckets"]):
        x = PAD_L + i * slot + (slot - bar_w) / 2
        if b["average_minutes"] is None:
            bars.append({"x": round(x, 1), "w": round(bar_w, 1), "y": CHART_H - PAD_B - 2, "h": 2,
                         "empty": True, "label": b["label"], "lx": round(x + bar_w / 2, 1),
                         "title": f"{b['label']}: no check-in"})
            continue
        # Earlier arrival = taller bar: height measures time before `hi`.
        frac = (hi - b["average_minutes"]) / (hi - lo)
        h = max(3, frac * plot_h)
        bars.append({"x": round(x, 1), "w": round(bar_w, 1), "y": round(CHART_H - PAD_B - h, 1),
                     "h": round(h, 1), "empty": False, "label": b["label"], "lx": round(x + bar_w / 2, 1),
                     "title": f"{b['label']}: average {b['average']} over {b['days']} day(s)"})
    ticks = []
    for m in range(int(lo), int(hi) + 1, 60 if hi - lo > 150 else 30):
        frac = (hi - m) / (hi - lo)
        ticks.append({"y": round(CHART_H - PAD_B - frac * plot_h, 1), "label": _hhmm(m)})
    return {"bars": bars, "ticks": ticks, "w": CHART_W, "h": CHART_H, "base": CHART_H - PAD_B,
            "label_y": CHART_H - 6, "pad_l": PAD_L, "tick_x": PAD_L - 4, "every": 1 if n <= 7 else 2}


def _donut_geometry(counts):
    total = counts["total"]
    radius, circ = 52, 2 * math.pi * 52
    if not total:
        return None
    segs, offset = [], 0.0
    for state in counts["states"]:
        if not state["count"]:
            continue
        length = circ * state["count"] / total
        segs.append({"key": state["key"], "dash": f"{length:.2f} {circ - length:.2f}",
                     "offset": f"{-offset:.2f}", "label": state["label"], "count": state["count"]})
        offset += length
    return {"r": radius, "segments": segs, "total": total}


def _trend_geometry(trend):
    n = len(trend["buckets"])
    top = _nice_max(max([b["raised"] for b in trend["buckets"]] + [b["resolved"] for b in trend["buckets"]] + [0]))
    plot_w, plot_h = CHART_W - PAD_L - 10, CHART_H - PAD_B - PAD_T
    step = plot_w / max(n - 1, 1)

    def pts(key):
        out = []
        for i, b in enumerate(trend["buckets"]):
            x = PAD_L + 4 + i * step
            y = CHART_H - PAD_B - (b[key] / top) * plot_h
            out.append((round(x, 1), round(y, 1), b[key], b["label"]))
        return out

    raised, resolved = pts("raised"), pts("resolved")
    ticks = []
    for i in range(3):
        v = top * i / 2
        ticks.append({"y": round(CHART_H - PAD_B - (v / top) * plot_h, 1),
                      "label": f"{v:g}"})
    return {
        "w": CHART_W, "h": CHART_H, "pad_l": PAD_L, "tick_x": PAD_L - 4, "label_y": CHART_H - 6, "ticks": ticks,
        "raised_points": " ".join(f"{x},{y}" for x, y, *_ in raised),
        "resolved_points": " ".join(f"{x},{y}" for x, y, *_ in resolved),
        "raised": [{"x": x, "y": y, "v": v, "label": lbl} for x, y, v, lbl in raised],
        "resolved": [{"x": x, "y": y, "sx": round(x - 3, 1), "sy": round(y - 3, 1), "v": v, "label": lbl}
                     for x, y, v, lbl in resolved],
        "labels": [{"x": x, "label": lbl, "show": (i % (1 if n <= 7 else 3) == 0) or i == n - 1}
                   for i, (x, _y, _v, lbl) in enumerate(raised)],
    }


def chart_context(user, period, today=None):
    period = clean_period(period)
    arrivals = arrival_stats(user, period, today)
    tasks = task_state_counts(user, period, today)
    tickets = ticket_trend(user, period, today)
    return {
        "period": period, "period_label": PERIOD_LABELS[period],
        "periods": [{"key": p, "label": {"week": "Week", "month": "Month", "quarter": "Quarter"}[p]}
                    for p in PERIODS],
        "arrivals": arrivals, "arrival_chart": _arrival_geometry(arrivals),
        "tasks": tasks, "task_chart": _donut_geometry(tasks),
        "tickets": tickets,
        "ticket_chart": _trend_geometry(tickets) if (tickets["raised_total"] or tickets["resolved_total"]) else None,
    }
