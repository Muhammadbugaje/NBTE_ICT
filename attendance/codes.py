"""Rotating check-in codes for office-based manual attendance.

Public API:
    get_current_code(location, now=None) -> LocationCheckInCode | None
    verify_check_in_code(user, raw_code, now=None) -> (location, code_row) | (None, None)
    location_is_open(location, now=None) -> bool
    seconds_until_next_rotation(now=None) -> int
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from .models import AttendanceLocation, LocationCheckInCode
from .services import is_working_day, non_working_reason

logger = logging.getLogger(__name__)

_DEFAULT_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/I/1/L


# ===========================================================================
# Config
# ===========================================================================
def _cfg(key, default):
    try:
        import config as _cfg
        return _cfg.get(key, default)
    except Exception:
        return default


def _cfg_int(key, default):
    try:
        return int(_cfg(key, default))
    except (TypeError, ValueError):
        return default


def manual_checkin_enabled():
    return bool(_cfg("attendance.manual_checkin_enabled", True))


def code_length():
    return max(4, min(12, _cfg_int("attendance.manual_code_length", 6)))


def rotation_minutes():
    return max(1, min(24 * 60, _cfg_int("attendance.manual_code_rotation_minutes", 30)))


def grace_before_open():
    return max(0, _cfg_int("attendance.manual_code_grace_minutes_before_open", 30))


def grace_after_close():
    return max(0, _cfg_int("attendance.manual_code_grace_minutes_after_close", 30))


def max_attempts_per_hour():
    return max(1, _cfg_int("attendance.manual_code_max_attempts_per_hour", 10))


# ===========================================================================
# Window computation
# ===========================================================================
def location_is_open(location, now=None):
    """True when `now` falls inside the location's code-issuing window."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    today = local.date()

    # No codes on weekends / off days / organisation-wide holidays.
    if non_working_reason(today):
        return False

    start_time = getattr(location, "work_start_time", None)
    end_time = getattr(location, "work_end_time", None)
    if not start_time or not end_time:
        return False

    tz = local.tzinfo
    start = timezone.make_aware(datetime.combine(today, start_time), tz)
    end = timezone.make_aware(datetime.combine(today, end_time), tz)

    # If the shift crosses midnight (unlikely here), handle the wrap.
    if end <= start:
        end += timedelta(days=1)

    start -= timedelta(minutes=grace_before_open())
    end += timedelta(minutes=grace_after_close())

    return start <= now <= end


def _current_window(now=None):
    """Return (window_start, window_end) aligned to the rotation cadence."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    rotation = rotation_minutes()

    minutes_today = local.hour * 60 + local.minute
    window_index = minutes_today // rotation
    window_start_minutes = window_index * rotation

    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = day_start + timedelta(minutes=window_start_minutes)
    window_end = window_start + timedelta(minutes=rotation)
    return window_start, window_end


def seconds_until_next_rotation(now=None):
    now = now or timezone.now()
    _, window_end = _current_window(now)
    return max(0, int((window_end - now).total_seconds()))


# ===========================================================================
# Code generation
# ===========================================================================
def _generate_code(length):
    from secrets import choice
    alphabet = _cfg("attendance.manual_code_alphabet", _DEFAULT_ALPHABET) or _DEFAULT_ALPHABET
    return "".join(choice(str(alphabet)) for _ in range(length))


def get_current_code(location, now=None):
    """Return the live code row for a location, creating it if the
    window has rolled over. Returns None outside working hours."""
    now = now or timezone.now()
    if not location_is_open(location, now):
        return None

    window_start, window_end = _current_window(now)

    existing = LocationCheckInCode.objects.filter(
        location=location, window_start=window_start,
    ).first()
    if existing is not None:
        return existing

    try:
        obj, _created = LocationCheckInCode.objects.get_or_create(
            location=location,
            window_start=window_start,
            defaults={
                "code": _generate_code(code_length()),
                "window_end": window_end,
            },
        )
        return obj
    except IntegrityError:
        # Another worker created it in the same instant.
        return LocationCheckInCode.objects.filter(
            location=location, window_start=window_start,
        ).first()


# ===========================================================================
# Verification
# ===========================================================================
def _attempt_cache_key(user_id):
    return f"attendance:manual_code_attempts:{user_id}"


def attempts_remaining(user):
    used = cache.get(_attempt_cache_key(user.pk), 0)
    return max(0, max_attempts_per_hour() - used)


def _bump_attempts(user):
    key = _attempt_cache_key(user.pk)
    try:
        current = cache.get(key, 0)
        cache.set(key, current + 1, 3600)
    except Exception:
        logger.exception("cache unavailable while bumping code attempts for %s", user.pk)


def verify_check_in_code(user, raw_code, now=None):
    """Return (location, code_row) if the code matches a live window for a
    location serving the user's department. Returns (None, None) otherwise.

    Raises ValidationError when the user has exceeded the attempt limit.
    """
    now = now or timezone.now()

    if not manual_checkin_enabled():
        raise ValidationError(
            "Manual check-in is currently disabled. Ask an administrator to enable it."
        )

    # A code issued earlier can still be "live" (long rotation windows), so
    # refuse on weekends / off days / holidays here as well, before the code
    # is even looked at and without costing the user an attempt.
    blocked = non_working_reason(timezone.localtime(now).date(), user)
    if blocked:
        raise ValidationError(blocked)

    if attempts_remaining(user) <= 0:
        raise ValidationError(
            "Too many incorrect codes. Try again in an hour, or ask the "
            "office contact for the current code."
        )

    code = (raw_code or "").strip().upper().replace(" ", "")
    if not code:
        raise ValidationError("Enter the current code from your office contact.")

    candidates = (
        LocationCheckInCode.objects
        .filter(code=code, window_end__gt=now, location__is_active=True)
        .select_related("location")
    )

    for row in candidates:
        if row.location.applies_to_department(user.department_id):
            return row.location, row

    _bump_attempts(user)
    return None, None

# ===========================================================================
# Check-in with a code (Phase 9: shared by the website's check_in_manual view
# and the mobile API's attendance/check-in/code/)
# ===========================================================================
INVALID_CODE_MESSAGE = (
    "That code isn't valid right now. Ask the office contact "
    "for the current code and try again."
)
ON_LEAVE_MESSAGE = "You can't sign attendance because you're on approved leave today."
DISABLED_MESSAGE = "Manual check-in is currently disabled. Please use GPS check-in."
ALREADY_MESSAGE = "You already have an attendance record for today."


def code_check_in_block_reason(user, day=None):
    """The refusals of the code check-in page that do not depend on the code,
    in the page's order, or "" when the user may enter a code."""
    from .models import AttendanceRecord

    day = day or timezone.localdate()
    blocked = non_working_reason(day, user)
    if blocked:
        return blocked
    if not manual_checkin_enabled():
        return DISABLED_MESSAGE
    existing = AttendanceRecord.objects.filter(user=user, date=day).first()
    if existing is not None and existing.status != AttendanceRecord.ON_LEAVE:
        return ALREADY_MESSAGE
    if existing is not None:
        return ON_LEAVE_MESSAGE
    return ""


def check_in_with_code(user, raw_code, now=None):
    """The whole office-code check-in: block reasons, the attempt limit and
    code match (verify_check_in_code), lateness (_status_for_check_in) and the
    record + attendance log (mark_status, source "manual").

    Returns a services.CheckInResult with an extra ``error``:
    "" (ok), "refused" (cannot check in today) or "code" (the code itself:
    empty, wrong, or too many wrong attempts).
    """
    from . import services
    from .models import AttendanceRecord

    now = now or timezone.now()
    today = timezone.localtime(now).date()

    def fail(message, error):
        result = services.CheckInResult(ok=False, message=message)
        result.error = error
        return result

    reason = code_check_in_block_reason(user, today)
    if reason:
        return fail(reason, "refused")
    try:
        location, _row = verify_check_in_code(user, raw_code, now=now)
    except ValidationError as exc:
        return fail("; ".join(exc.messages), "code")
    if location is None:
        return fail(INVALID_CODE_MESSAGE, "code")

    status = services._status_for_check_in(location, timezone.localtime(now))
    try:
        record = services.mark_status(
            user, today, status,
            source=AttendanceRecord.SOURCE_MANUAL,
            actor=user,
            location=location,
            check_in_at=now,
            notes=f"Manual check-in via location code · {location.name}",
        )
    except ValidationError as exc:
        joined = " ".join(getattr(exc, "messages", None) or [str(exc)])
        return fail(ON_LEAVE_MESSAGE if "approved leave" in joined.lower() else joined, "refused")

    result = services.CheckInResult(
        ok=True, status=status, record=record, location=location,
        message=f"Checked in via {location.name}. "
                + ("You're on time." if status == AttendanceRecord.PRESENT else "Recorded as late."),
    )
    result.error = ""
    return result
