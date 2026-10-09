"""core/dates.py — small helpers to keep datetimes timezone-safe.

The problem this solves: `datetime.combine(date, time)` produces a **naive**
datetime, which raises `TypeError` when compared to a `timezone.now()` or
`timezone.localtime()` value (which are **aware**). Every time you see:

    TypeError: can't compare offset-naive and offset-aware datetimes

...it's because somewhere a naive `datetime.combine` result met an aware one.

Rule of thumb for the whole project:
    Never call `datetime.combine` directly.
    Use `at_local_date(...)` instead.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.utils import timezone


# ============================================================================
# Combining date + time → aware datetime
# ============================================================================
def at_local_date(
    local_date: date,
    t: time,
    *,
    tz=None,
) -> datetime:
    """Return an **aware** datetime for `t` on `local_date`.

    Use this everywhere a raw `datetime.combine(...)` used to appear.

    Examples:
        >>> from datetime import time, date
        >>> from django.utils import timezone
        >>> at_local_date(date(2026, 10, 5), time(9, 0))
        datetime(2026, 10, 5, 9, 0, tzinfo=ZoneInfo('Africa/Lagos'))

    `tz` defaults to the current active timezone (Django's TIME_ZONE).
    Pass an explicit tz when you already have an aware reference datetime
    you want to match — e.g. from `timezone.localtime()`.
    """
    tz = tz or timezone.get_current_timezone()
    naive = datetime.combine(local_date, t)
    return timezone.make_aware(naive, tz)


def end_of_local_date(local_date: date, *, tz=None) -> datetime:
    """23:59:59.999999 on `local_date`, aware. Handy for "up to and including
    that day" filters that would otherwise exclude the last day."""
    tz = tz or timezone.get_current_timezone()
    t = time(23, 59, 59, 999999)
    return timezone.make_aware(datetime.combine(local_date, t), tz)


def start_of_local_date(local_date: date, *, tz=None) -> datetime:
    """00:00:00 on `local_date`, aware."""
    return at_local_date(local_date, time(0, 0, 0), tz=tz)


# ============================================================================
# Coercion — make anything aware
# ============================================================================
def ensure_aware(dt: datetime, *, tz=None) -> datetime:
    """Return `dt` as aware, using `tz` (or the current timezone) if it's naive.

    Never raises. If `dt` is already aware, returns it unchanged.
    """
    if dt is None:
        return dt
    if timezone.is_aware(dt):
        return dt
    tz = tz or timezone.get_current_timezone()
    return timezone.make_aware(dt, tz)


def ensure_naive(dt: datetime, *, tz=None) -> datetime:
    """Strip tzinfo, converting to `tz` first so the wall-clock is preserved.

    Useful for legacy fields that still hold naive datetimes in the DB, or
    for third-party APIs that expect naive local time.
    """
    if dt is None:
        return dt
    tz = tz or timezone.get_current_timezone()
    if timezone.is_aware(dt):
        return timezone.localtime(dt, tz).replace(tzinfo=None)
    return dt


# ============================================================================
# Shift-cutoff convenience — the pattern used by attendance
# ============================================================================
def shift_cutoff(
    reference: datetime,
    start_time: time,
    *,
    grace_minutes: int = 0,
    tz=None,
) -> datetime:
    """Return the aware cutoff for a shift that starts at `start_time` on
    the same local date as `reference`, plus optional grace minutes.

    Both inputs are coerced to aware, so comparison is always safe.
    """
    reference = ensure_aware(reference, tz=tz)
    cutoff = at_local_date(reference.date(), start_time, tz=reference.tzinfo)
    if grace_minutes:
        cutoff = cutoff + timedelta(minutes=int(grace_minutes))
    return cutoff