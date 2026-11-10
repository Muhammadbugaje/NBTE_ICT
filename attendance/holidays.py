"""Public holidays in attendance (v2.1 Phase D, F7, F8, T-CONS-5).

The table stays ``portal.Holiday``; HR manages it from Settings -> Holidays.
Whenever a holiday is created, edited, switched off or deleted the register
is reconciled for the days it covers (and covered before the edit):

  * an ABSENT record on a holiday the person gets becomes HOLIDAY
    (source "holiday"); present / late / leave / duty records are kept;
  * a HOLIDAY record written by this reconcile on a day that is no longer a
    holiday for that person goes back to ABSENT (past days only).
"""
from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from .models import AttendanceRecord


def occurrence_days(holiday, years=None):
    """The dates a holiday covers. A recurring one is expanded for the given
    years (default: last, this and next year)."""
    if holiday is None or not holiday.date_start:
        return []
    end = holiday.date_end or holiday.date_start
    if not holiday.is_recurring:
        days, d = [], holiday.date_start
        while d <= end:
            days.append(d)
            d += timedelta(days=1)
        return days
    today = timezone.localdate()
    years = years or (today.year - 1, today.year, today.year + 1)
    out = []
    for y in years:
        try:
            s = holiday.date_start.replace(year=y)
            e = end.replace(year=y if (end.month, end.day) >= (holiday.date_start.month, holiday.date_start.day)
                            else y + 1)
        except ValueError:  # 29 Feb
            continue
        d = s
        while d <= e:
            out.append(d)
            d += timedelta(days=1)
    return out


@transaction.atomic
def reconcile_days(days):
    """Make the register agree with the holidays on ``days`` (past and today)."""
    from .services import holidays_on, is_holiday_for

    today = timezone.localdate()
    changed = 0
    for day in sorted({d for d in days if d <= today}):
        hols = holidays_on(day)
        recs = list(AttendanceRecord.objects.filter(
            date=day, status__in=(AttendanceRecord.ABSENT, AttendanceRecord.HOLIDAY)).select_related("user"))
        for rec in recs:
            covered = bool(hols) and is_holiday_for(rec.user, day, hols)
            if covered and rec.status == AttendanceRecord.ABSENT:
                name = next((h.name for h in hols if is_holiday_for(rec.user, day, [h])), "Public holiday")
                rec.status = AttendanceRecord.HOLIDAY
                rec.source = AttendanceRecord.SOURCE_HOLIDAY
                rec.notes = f"Public holiday: {name}"
                rec.save(update_fields=["status", "source", "notes", "updated_at"])
                changed += 1
            elif (not covered and rec.status == AttendanceRecord.HOLIDAY
                  and rec.source == AttendanceRecord.SOURCE_HOLIDAY and day < today):
                rec.status = AttendanceRecord.ABSENT
                rec.source = AttendanceRecord.SOURCE_AUTO
                rec.notes = "Auto-marked absent (holiday removed)."
                rec.save(update_fields=["status", "source", "notes", "updated_at"])
                changed += 1
    return changed


def reconcile_holiday(holiday, previous_days=()):
    return reconcile_days(set(occurrence_days(holiday)) | set(previous_days))


def holiday_name_for(user, day, holidays=None):
    """Name of the holiday ``user`` has on ``day`` or "" (exports, F8)."""
    from .services import holidays_on, is_holiday_for

    hols = holidays_on(day) if holidays is None else holidays
    for h in hols:
        if is_holiday_for(user, day, [h]):
            return h.name
    return ""


def year_list(year):
    """Holidays of a year for the HR settings page (recurring expanded)."""
    from django.db.models import Q
    from portal.models import Holiday

    first, last = date(year, 1, 1), date(year, 12, 31)
    qs = (Holiday.objects.filter(Q(is_recurring=True) | Q(date_start__lte=last, date_end__gte=first))
          .select_related("department").order_by("date_start", "name"))
    return list(qs)
