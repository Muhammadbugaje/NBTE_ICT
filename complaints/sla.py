"""Business-hour clocks for complaint SLAs (Master Plan §11).

One calendar for the whole of NBTE:

* working days — the existing ``attendance.work_week`` setting;
* opening hours — ``qa.calendar.business_hours`` (``["08:00", "16:00"]``);
* holidays — the existing ``portal.Holiday`` table (public and religious
  holidays; departmental holidays do not stop an NBTE-wide clock).

``add_business_seconds(start, seconds)`` gives the deadline; ``business_seconds
_between(a, b)`` measures elapsed working time. Calendar-hour stages use plain
time arithmetic (``deadline`` picks one or the other).
"""
from __future__ import annotations

import datetime
import json

from django.utils import timezone

import config

MAX_DAYS = 3660  # a safety stop: ten years of calendar days


def business_hours():
    raw = config.get("qa.calendar.business_hours", ["08:00", "16:00"])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = ["08:00", "16:00"]
    try:
        start, end = (datetime.time.fromisoformat(str(v).strip()) for v in list(raw)[:2])
    except (TypeError, ValueError):
        start, end = datetime.time(8), datetime.time(16)
    if end <= start:
        start, end = datetime.time(8), datetime.time(16)
    return start, end


class Calendar:
    """Working days, opening hours and holidays, cached for one computation."""

    def __init__(self):
        from attendance.services import work_week

        self.week = work_week()
        self.opens, self.closes = business_hours()
        self._holidays = {}

    def is_holiday(self, day):
        if day not in self._holidays:
            from attendance.services import holidays_on

            try:
                found = [h for h in holidays_on(day)
                         if getattr(h, "kind", "public") != "departmental" and not getattr(h, "department_id", None)]
            except Exception:  # the holiday table is optional infrastructure
                found = []
            self._holidays[day] = bool(found)
        return self._holidays[day]

    def is_working_day(self, day):
        return day.weekday() in self.week and not self.is_holiday(day)

    def window(self, day):
        """(open, close) aware datetimes of ``day``, or None on a non-working day."""
        if not self.is_working_day(day):
            return None
        tz = timezone.get_current_timezone()
        return (timezone.make_aware(datetime.datetime.combine(day, self.opens), tz),
                timezone.make_aware(datetime.datetime.combine(day, self.closes), tz))

    @property
    def seconds_per_day(self):
        return (datetime.datetime.combine(datetime.date.today(), self.closes)
                - datetime.datetime.combine(datetime.date.today(), self.opens)).total_seconds()


def add_business_seconds(start, seconds, calendar=None):
    """The moment ``seconds`` of working time after ``start``."""
    cal = calendar or Calendar()
    remaining = max(0.0, float(seconds))
    current = timezone.localtime(start)
    day = current.date()
    for _ in range(MAX_DAYS):
        win = cal.window(day)
        if win is not None:
            opens, closes = win
            begin = max(current, opens)
            if begin < closes:
                available = (closes - begin).total_seconds()
                if remaining <= available:
                    return begin + datetime.timedelta(seconds=remaining)
                remaining -= available
        day += datetime.timedelta(days=1)
        current = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min),
                                      timezone.get_current_timezone())
    return start + datetime.timedelta(seconds=seconds)  # pragma: no cover - unreachable in practice


def business_seconds_between(a, b, calendar=None):
    """Working seconds from ``a`` to ``b`` (0 when b <= a)."""
    if b <= a:
        return 0.0
    cal = calendar or Calendar()
    a, b = timezone.localtime(a), timezone.localtime(b)
    total = 0.0
    day = a.date()
    for _ in range(MAX_DAYS):
        if day > b.date():
            break
        win = cal.window(day)
        if win is not None:
            lo, hi = max(a, win[0]), min(b, win[1])
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += datetime.timedelta(days=1)
    return total


def deadline(start, seconds, *, business, calendar=None):
    if business:
        return add_business_seconds(start, seconds, calendar)
    return start + datetime.timedelta(seconds=seconds)


def elapsed(start, end, *, business, calendar=None):
    if business:
        return business_seconds_between(start, end, calendar)
    return max(0.0, (end - start).total_seconds())


def humanise(seconds):
    """'3h 20m', '2d 4h' (working time is shown the same way)."""
    seconds = int(abs(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"
