"""Attendance service layer — the only place attendance state changes.

Public API:

    Day helpers
        is_working_day(date) -> bool
        working_days_in_month(year, month) -> [date]

    Check-in (GPS)
        check_in_user(user, lat, lng, now=None) -> CheckInResult
        find_matching_location(user, lat, lng)   -> (location, distance)

    Direct record writes
        mark_present(user, date, source, actor, ...)
        mark_status(user, date, status, source, actor, notes="")
        ensure_absent(user, date, actor=None)

    Excel
        parse_matrix(file_obj, month, year, department=None) -> ParseResult
        apply_matrix(parsed, actor, overwrite=True)          -> ApplyResult

    Stats & reports
        attendance_stats(user, days=30) -> dict
        department_summary(department, start, end) -> dict
        to_matrix(records, month, year) -> list[list[str]]

    Celery-facing
        auto_mark_absent(for_date=None, days_back=0) -> int
        send_weekly_summary(for_date=None) -> int

    Logging
        log(user, date, message, kind="info", actor=None)
"""
from __future__ import annotations

import calendar
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from notifications.services import notify

from .models import AttendanceLocation, AttendanceRecord

logger = logging.getLogger(__name__)


# ===========================================================================
# Type-safe parsing helpers
# ===========================================================================
def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _valid_month(value, default):
    m = _as_int(value, default)
    return m if m is not None and 1 <= m <= 12 else default


def _valid_year(value, default):
    y = _as_int(value, default)
    return y if y is not None and 1900 <= y <= 2200 else default


# ===========================================================================
# Config helpers
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


def _cfg_json(key, default):
    raw = _cfg(key, default)
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return default
    return default


# ===========================================================================
# Day helpers
# ===========================================================================
def work_week():
    """Return a set of weekday indexes (0=Mon..6=Sun) considered working days."""
    raw = _cfg_json("attendance.work_week", [0, 1, 2, 3, 4])
    try:
        return {int(x) for x in raw}
    except (TypeError, ValueError):
        return {0, 1, 2, 3, 4}


def is_working_day(d):
    return d.weekday() in work_week()


def holidays_on(day):
    """Active portal holidays that cover `day` (recurring ones included).
    Review M12: automatic absent-marking must not run on public holidays."""
    from django.db.models import Q
    from portal.models import Holiday
    from leave.services import _holiday_covers

    qs = (
        Holiday.objects.filter(is_active=True)
        .filter(Q(is_recurring=True) | Q(date_start__lte=day, date_end__gte=day))
        .prefetch_related("roles")
    )
    return [h for h in qs if _holiday_covers(day, h)]


def is_holiday_for(user, day, holidays=None):
    """True if a holiday visible to `user` covers `day`."""
    from portal.models import Scope

    holidays = holidays_on(day) if holidays is None else holidays
    for h in holidays:
        if h.visibility in (Scope.PUBLIC, Scope.STAFF):
            return True
        if h.visibility == Scope.DEPARTMENT and h.department_id \
                and h.department_id == getattr(user, "department_id", None):
            return True
        if h.visibility == Scope.ROLE and getattr(user, "role_id", None) \
                and any(r.pk == user.role_id for r in h.roles.all()):
            return True
    return False


def working_days_in_month(year, month):
    days = []
    for d in range(1, calendar.monthrange(year, month)[1] + 1):
        day = date_cls(year, month, d)
        if is_working_day(day):
            days.append(day)
    return days


def global_holidays_on(day):
    """Holidays that apply to the whole organisation (public / all-staff).
    Department- and role-scoped holidays are checked per user instead."""
    from portal.models import Scope
    return [h for h in holidays_on(day)
            if h.visibility in (Scope.PUBLIC, Scope.STAFF)]


def non_working_reason(day, user=None):
    """Return a user-facing message when attendance must NOT be recorded on
    `day`, or "" when it is a normal working day.

    One rule shared by GPS check-in, manual (code) check-in and the rotating
    code generator, so a weekend / off day / holiday is refused everywhere.
    Pass `user` to also honour department- or role-scoped holidays.
    """
    if not is_working_day(day):
        return ("Today is not a working day, so attendance can't be "
                "recorded. See you on the next working day.")
    holidays = holidays_on(day)
    if holidays:
        if user is None:
            applicable = [h for h in holidays if h in global_holidays_on(day)]
        else:
            applicable = [h for h in holidays if is_holiday_for(user, day, [h])]
        if applicable:
            name = getattr(applicable[0], "name", "") or "a holiday"
            return (f"Today is a holiday ({name}), so attendance can't be "
                    f"recorded. Enjoy the day off.")
    return ""


# ===========================================================================
# Logging
# ===========================================================================
def log(user, d, message, kind="info", actor=None):
    logger.info(
        "[attendance] %s · user=%s actor=%s date=%s — %s",
        kind, getattr(user, "pk", user), getattr(actor, "pk", actor), d, message,
    )


# ===========================================================================
# Location resolution
# ===========================================================================
def find_matching_location(user, lat, lng):
    """Return (location, distance_meters) for the first active location
    that contains the point and applies to the user's department.
    Returns (None, None) if nothing matches."""
    qs = AttendanceLocation.objects.filter(is_active=True).prefetch_related("departments")
    for loc in qs:
        if not loc.applies_to_department(user.department_id):
            continue
        inside, dist = loc.contains(lat, lng)
        if inside:
            return loc, dist
    return None, None


from core.dates import at_local_date, ensure_aware, shift_cutoff


def _status_for_check_in(location, moment):
    """Return AttendanceRecord.PRESENT or .LATE for a check-in at `moment`."""
    moment = ensure_aware(moment)
    start_time = getattr(location, "work_start_time", None)
    if not start_time:
        return AttendanceRecord.PRESENT

    grace = (
        getattr(location, "grace_minutes", None)
        or getattr(location, "grace_period_minutes", None)
        or 0
    )
    cutoff = shift_cutoff(
        moment, start_time,
        grace_minutes=int(grace),
        tz=moment.tzinfo,
    )

    return AttendanceRecord.PRESENT if moment <= cutoff else AttendanceRecord.LATE


# ===========================================================================
# Result shapes
# ===========================================================================
@dataclass
class CheckInResult:
    ok: bool
    status: str = ""
    record: AttendanceRecord | None = None
    message: str = ""
    distance_meters: int | None = None
    location: AttendanceLocation | None = None


# ===========================================================================
# Phase 9: shared by the website's check_in_api and the mobile API
# ===========================================================================
def validate_gps_reading(lat, lng, accuracy):
    """Return (lat, lng, accuracy) as floats or raise ValidationError with
    the user-facing message.

    Review M12: server-side sanity checks. A browser can still fake its
    position; these only reject impossible or imprecise readings.
    """
    import math

    if lat is None or lng is None:
        raise ValidationError("Missing coordinates.")
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        raise ValidationError("Invalid coordinates.")
    if (not (math.isfinite(lat) and math.isfinite(lng))
            or not (-90 <= lat <= 90 and -180 <= lng <= 180)
            or (lat == 0 and lng == 0)):
        raise ValidationError("Invalid coordinates.")
    max_accuracy = _cfg_int("attendance.max_gps_accuracy_m", 200)
    try:
        accuracy = float(accuracy)
    except (TypeError, ValueError):
        accuracy = None
    if accuracy is None or not math.isfinite(accuracy) or accuracy < 0:
        raise ValidationError("Your device did not report location accuracy. "
                              "Reload the page and try again.")
    if accuracy > max_accuracy:
        raise ValidationError(f"Your location is only accurate to about "
                              f"{round(accuracy)} m (limit {max_accuracy} m). "
                              f"Move to an open area and try again.")
    return lat, lng, accuracy


def check_in_block_reason(user, day=None):
    """The refusals of check_in_user that do not depend on the GPS reading,
    in the same order, or "" when the user may try to check in."""
    day = day or timezone.localdate()
    blocked = non_working_reason(day, user)
    if blocked:
        return blocked
    existing = AttendanceRecord.objects.filter(user=user, date=day).first()
    if existing is not None and existing.status != AttendanceRecord.ON_LEAVE:
        return f"You already checked in today ({existing.get_status_display()})."
    if not user.department_id:
        return "You are not assigned to a department, so we can't match a location."
    return ""


# ===========================================================================
# Check-in
# ===========================================================================
@transaction.atomic
def check_in_user(user, lat, lng, now=None):
    """The full GPS check-in flow."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()

    blocked = non_working_reason(today, user)
    if blocked:
        return CheckInResult(ok=False, message=blocked)

    existing = (
        AttendanceRecord.objects.select_for_update()
        .filter(user=user, date=today)
        .first()
    )
    if existing is not None and existing.status != AttendanceRecord.ON_LEAVE:
        return CheckInResult(
            ok=False,
            status=existing.status,
            record=existing,
            message=f"You already checked in today ({existing.get_status_display()}).",
        )

    if not user.department_id:
        return CheckInResult(
            ok=False,
            message="You are not assigned to a department, so we can't match a location.",
        )

    location, distance = find_matching_location(user, lat, lng)
    if location is None:
        return CheckInResult(
            ok=False,
            message=(
                "You're outside every approved attendance location for your "
                "department. Move closer to the office and try again."
            ),
        )

    status = _status_for_check_in(location, timezone.localtime(now))

    if existing is not None:
        rec = existing
        rec.status = status
        rec.check_in_at = now
        rec.latitude = lat
        rec.longitude = lng
        rec.distance_meters = distance
        rec.location = location
        rec.source = AttendanceRecord.SOURCE_GPS
        rec.leave_application = None
        rec.notes = "Checked in during approved annual leave."
        rec.save(update_fields=[
            "status", "check_in_at", "latitude", "longitude",
            "distance_meters", "location", "source", "leave_application",
            "notes", "updated_at",
        ])
    else:
        try:
            rec = AttendanceRecord.objects.create(
                user=user,
                date=today,
                status=status,
                check_in_at=now,
                latitude=lat,
                longitude=lng,
                distance_meters=distance,
                location=location,
                source=AttendanceRecord.SOURCE_GPS,
            )
        except IntegrityError:
            rec = AttendanceRecord.objects.get(user=user, date=today)
            return CheckInResult(
                ok=False,
                status=rec.status,
                record=rec,
                message="You already checked in today.",
            )

    log(user, today, f"Check-in via {location.name} at {distance}m", kind="check_in")

    if status == AttendanceRecord.LATE:
        try:
            from . import emails
            emails.send_late_notice(rec)
        except Exception:
            logger.exception("Late notice failed for %s", user.pk)

    return CheckInResult(
        ok=True,
        status=status,
        record=rec,
        message=(
            "Checked in — you're on time." if status == AttendanceRecord.PRESENT
            else "Checked in — you're late. This has been logged."
        ),
        distance_meters=distance,
        location=location,
    )


# ===========================================================================
# Direct writes
# ===========================================================================
@transaction.atomic
def mark_status(user, d, status, *, source=AttendanceRecord.SOURCE_MANUAL,
                actor=None, notes="", check_in_at=None,
                lat=None, lng=None, location=None, distance=None):
    """Upsert a record for (user, date). Used by manual edit + Excel upload."""
    if status not in dict(AttendanceRecord.STATUS_CHOICES):
        raise ValidationError(f"Unknown status: {status}")
    if status == AttendanceRecord.ON_LEAVE and source != AttendanceRecord.SOURCE_LEAVE:
        raise ValidationError("Approved leave attendance is managed from leave requests.")
    existing = AttendanceRecord.objects.filter(user=user, date=d).first()
    if (
        existing is not None
        and existing.status == AttendanceRecord.ON_LEAVE
        and source != AttendanceRecord.SOURCE_GPS
    ):
        raise ValidationError(
            "Attendance for an approved leave day cannot be changed manually."
        )

    rec, created = AttendanceRecord.objects.update_or_create(
        user=user,
        date=d,
        defaults={
            "status": status,
            "source": source,
            "recorded_by": actor,
            "notes": notes or "",
            "check_in_at": check_in_at,
            "latitude": lat,
            "longitude": lng,
            "location": location,
            "distance_meters": distance,
        },
    )
    log(user, d, f"{'Created' if created else 'Updated'} → {status} via {source}",
        kind="mark", actor=actor)
    return rec


def mark_present(user, d, *, source, actor=None, **kwargs):
    return mark_status(user, d, AttendanceRecord.PRESENT,
                       source=source, actor=actor, **kwargs)


def ensure_absent(user, d, actor=None):
    """Create an ABSENT record if none exists yet. Safe to call repeatedly."""
    if not is_working_day(d):
        return None
    rec, created = AttendanceRecord.objects.get_or_create(
        user=user,
        date=d,
        defaults={
            "status": AttendanceRecord.ABSENT,
            "source": AttendanceRecord.SOURCE_MANUAL,
            "recorded_by": actor,
        },
    )
    if created:
        log(user, d, "Auto-marked absent", kind="auto_absent", actor=actor)
    return rec


# ===========================================================================
# Excel — parse
# ===========================================================================
@dataclass
class ParsedRow:
    row_number: int
    user: User | None
    email: str
    statuses: dict
    errors: list[str] = field(default_factory=list)
    # Per-status day counts this row would write (working days only), and
    # how many of them are credited. Filled by parse_matrix for the preview.
    counts: dict = field(default_factory=dict)

    @property
    def credited(self):
        return sum(v for k, v in self.counts.items() if k in CREDITED_STATUSES)

    @property
    def counted(self):
        return sum(self.counts.values())


@dataclass
class ParseResult:
    month: int
    year: int
    days: list[int]
    working_days: list[date_cls]
    rows: list[ParsedRow]
    emails_ok: int
    emails_missing: int
    columns_detected: list[int]
    # Aggregate of every valid row: {status: days}. See ParsedRow.counts.
    totals: dict = field(default_factory=dict)

    @property
    def total_credited(self):
        return sum(v for k, v in self.totals.items() if k in CREDITED_STATUSES)

    @property
    def total_counted(self):
        return sum(self.totals.values())


def _count_cells(statuses, year, month):
    """Count the days an upload row would record, per status.

    Mirrors apply_matrix: blank / unrecognised cells, approved-leave cells
    (system-managed) and non-working days are not counted.
    """
    counts = {}
    for day_num, raw in statuses.items():
        status = _CELL_TO_STATUS.get(raw or "")
        if status is None or status == AttendanceRecord.ON_LEAVE:
            continue
        try:
            d = date_cls(year, month, day_num)
        except ValueError:
            continue
        if not is_working_day(d):
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _sum_counts(count_dicts):
    total = {}
    for c in count_dicts:
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
    return total


def parse_matrix(file_obj, month, year, department=None):
    """Parse a matrix Excel. See module docstring for the expected layout."""
    from django.utils import timezone
    _today = timezone.localdate()
    year = _valid_year(year, _today.year)
    month = _valid_month(month, _today.month)

    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise ValidationError("openpyxl is required for Excel uploads.") from exc

    try:
        wb = load_workbook(file_obj, data_only=True)
    except Exception as exc:
        raise ValidationError(f"Could not read the file: {exc}") from exc

    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValidationError("The file is empty.")

    header = None
    header_idx = 0
    for idx, row in enumerate(rows[:5]):
        cells = [str(c or "").strip().lower() for c in row]
        if "email" in cells:
            header = row
            header_idx = idx
            break
    if header is None:
        raise ValidationError(
            "Couldn't find a header row. The first cell of the header must "
            "be 'Email'."
        )

    day_columns = []
    for col_idx, cell in enumerate(header):
        if col_idx == 0:
            continue
        try:
            n = int(str(cell).strip())
            if 1 <= n <= 31:
                day_columns.append((col_idx, n))
        except (TypeError, ValueError):
            continue
    if not day_columns:
        raise ValidationError(
            "No day columns found. Put '1', '2', '3'… in the header row "
            "starting at column B."
        )

    parsed_rows: list[ParsedRow] = []
    emails_ok = emails_missing = 0

    for r_idx, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        email_cell = str(row[0] or "").strip().lower() if row else ""
        if not email_cell:
            continue

        user = User.objects.filter(email__iexact=email_cell, is_active=True).first()
        statuses = {}
        for col_idx, day_num in day_columns:
            val = row[col_idx] if col_idx < len(row) else None
            if val is None:
                statuses[day_num] = ""
                continue
            s = str(val).strip().upper()
            statuses[day_num] = s

        errors = []
        if user is None:
            emails_missing += 1
            errors.append("No active user with this email.")
        elif department is not None and user.department_id != getattr(department, "pk", department):
            # Review H4: an uploader limited to one department may not
            # write attendance for anyone outside it.
            errors.append("This person is not in your department; row ignored.")
        else:
            emails_ok += 1

        counts = {}
        if user is not None and not errors:
            counts = _count_cells(statuses, year, month)

        parsed_rows.append(ParsedRow(
            row_number=r_idx,
            user=user,
            email=email_cell,
            statuses=statuses,
            errors=errors,
            counts=counts,
        ))

    days_in_month = calendar.monthrange(year, month)[1]

    all_days = [d for _, d in day_columns]
    working_days = [
        date_cls(year, month, d)
        for d in all_days
        if d <= days_in_month
        and is_working_day(date_cls(year, month, d))
    ]

    return ParseResult(
        month=month,
        year=year,
        days=all_days,
        working_days=working_days,
        rows=parsed_rows,
        emails_ok=emails_ok,
        emails_missing=emails_missing,
        columns_detected=[d for _, d in day_columns],
        totals=_sum_counts(r.counts for r in parsed_rows),
    )


# ===========================================================================
# Excel — apply
# ===========================================================================
@dataclass
class ApplyResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    # Per-status count of the days written by this upload, so the screen can
    # show the aggregate straight away (present / late / absent / excused /
    # holiday) together with how many of them are credited.
    counts: dict = field(default_factory=dict)

    @property
    def credited(self):
        return sum(v for k, v in self.counts.items()
                   if k in CREDITED_STATUSES)


_CELL_TO_STATUS = {
    "1": AttendanceRecord.PRESENT,
    "P": AttendanceRecord.PRESENT,
    "PRESENT": AttendanceRecord.PRESENT,
    "L": AttendanceRecord.LATE,
    "LATE": AttendanceRecord.LATE,
    "0": AttendanceRecord.ABSENT,
    "A": AttendanceRecord.ABSENT,
    "ABSENT": AttendanceRecord.ABSENT,
    "E": AttendanceRecord.EXCUSED,
    "EXCUSED": AttendanceRecord.EXCUSED,
    "H": AttendanceRecord.HOLIDAY,
    "HOLIDAY": AttendanceRecord.HOLIDAY,
    "LV": AttendanceRecord.ON_LEAVE,
}


@transaction.atomic
def apply_matrix(parsed, actor, overwrite=True):
    """Write parsed rows into AttendanceRecord."""
    result = ApplyResult()
    for row in parsed.rows:
        if row.user is None:
            result.skipped += 1
            result.errors.append(
                f"Row {row.row_number} ({row.email}): no matching user."
            )
            continue
        if row.errors:
            result.skipped += 1
            result.errors.append(
                f"Row {row.row_number} ({row.email}): {'; '.join(row.errors)}"
            )
            continue

        for day_num, raw in row.statuses.items():
            if not raw:
                continue
            status = _CELL_TO_STATUS.get(raw)
            if status is None:
                result.errors.append(
                    f"Row {row.row_number} day {day_num}: unrecognised value '{raw}'."
                )
                continue
            if status == AttendanceRecord.ON_LEAVE:
                result.skipped += 1
                result.errors.append(
                    f"Row {row.row_number} day {day_num}: approved leave status is system-managed."
                )
                continue
            try:
                d = date_cls(parsed.year, parsed.month, day_num)
            except ValueError:
                continue
            if not is_working_day(d):
                continue

            existing = AttendanceRecord.objects.filter(user=row.user, date=d).first()
            if existing is not None and existing.status == AttendanceRecord.ON_LEAVE:
                result.skipped += 1
                result.errors.append(
                    f"Row {row.row_number} day {day_num}: approved leave attendance was not changed."
                )
                continue
            if existing is not None and not overwrite:
                result.skipped += 1
                continue

            mark_status(
                row.user, d, status,
                source=AttendanceRecord.SOURCE_EXCEL,
                actor=actor,
                notes=f"Excel upload row {row.row_number}",
            )
            if existing is None:
                result.created += 1
            else:
                result.updated += 1
            result.counts[status] = result.counts.get(status, 0) + 1

    log(actor, date_cls(parsed.year, parsed.month, 1),
        f"Matrix applied: +{result.created} ~{result.updated} skip{result.skipped}",
        kind="matrix")
    return result


# ===========================================================================
# Stats
# ===========================================================================
# ---------------------------------------------------------------------------
# Aggregation rules (one place, used by every screen and every export)
#
#   Present + Late                         -> physically attended
#   Excused + Holiday + On approved leave  -> NOT attended, but credited: they
#                                             count towards the total exactly
#                                             like a present day and are never
#                                             held against the person.
#   Absent                                 -> the only status that is not credited.
#
#   Attendance %  = credited days / counted working days (capped at 100)
#
# ONLY RECORDED DAYS ARE COUNTED. A working day with no attendance record at
# all (for example before the system started being used) is not counted as
# present, absent or anything else: it is simply not part of the calculation.
# Only working days up to today are looked at.
# ---------------------------------------------------------------------------
CREDITED_STATUSES = frozenset({
    AttendanceRecord.PRESENT, AttendanceRecord.LATE,
    AttendanceRecord.EXCUSED, AttendanceRecord.HOLIDAY,
    AttendanceRecord.ON_LEAVE,
})
NEUTRAL_STATUSES = frozenset({
    AttendanceRecord.EXCUSED, AttendanceRecord.HOLIDAY,
    AttendanceRecord.ON_LEAVE,
})


def pct_of(credited, expected):
    if not expected:
        return 0
    return min(100, round((credited / expected) * 100))


def period_records(users, first_day, last_day):
    """Records for `users` in [first_day, last_day], with what the reports
    need to say *who approved* a leave day (no per-row queries)."""
    return list(
        AttendanceRecord.objects
        .filter(user__in=list(users), date__gte=first_day, date__lte=last_day)
        .select_related("user", "recorded_by", "location", "leave_application__request")
        .prefetch_related("leave_application__request__approvals__actor")
    )


def leave_approver(record):
    """Name of the person who approved the leave behind an ON_LEAVE record,
    or "" when unknown."""
    app = getattr(record, "leave_application", None)
    if app is None:
        return ""
    try:
        approvals = [a for a in app.request.approvals.all() if a.decision == "approve"]
    except Exception:
        return ""
    if not approvals:
        return ""
    last = max(approvals, key=lambda a: (a.decided_at, a.pk or 0))
    actor = getattr(last, "actor", None)
    if actor is None:
        return ""
    return actor.get_full_name() or actor.email


def credit_authority(record):
    """Who is behind a credited-but-not-present day: the leave approver for
    approved leave, the person who recorded it for an excused day."""
    if record.status == AttendanceRecord.ON_LEAVE:
        return leave_approver(record)
    if record.status == AttendanceRecord.EXCUSED and record.recorded_by_id:
        return record.recorded_by.get_full_name() or record.recorded_by.email
    return ""


def record_detail(record):
    """Short human description for reports: why a non-present day is credited."""
    st = record.status
    if st == AttendanceRecord.ON_LEAVE:
        who = leave_approver(record)
        return "On approved leave" + (f" — approved by {who}" if who else "")
    if st == AttendanceRecord.EXCUSED:
        who = ""
        if record.recorded_by_id:
            who = record.recorded_by.get_full_name() or record.recorded_by.email
        note = (record.notes or "").strip()
        bits = ["Excused"]
        if who:
            bits.append(f"by {who}")
        text = " ".join(bits)
        return text + (f" — {note}" if note else "")
    if st == AttendanceRecord.HOLIDAY:
        return "Holiday"
    return record.get_status_display()


def format_date_range(start, end):
    """"Wed 14 Oct 2026" or "Wed 14 Oct – Fri 16 Oct 2026"."""
    if start == end:
        return start.strftime("%a %d %b %Y")
    if start.year == end.year:
        return f"{start.strftime('%a %d %b')} – {end.strftime('%a %d %b %Y')}"
    return f"{start.strftime('%a %d %b %Y')} – {end.strftime('%a %d %b %Y')}"


def _contiguous(prev_day, next_day, user_id, by_user_date):
    """True when nothing but non-working days (or the person's own holiday
    records) lies between the two dates, i.e. they are back-to-back working
    days. Friday -> Monday counts as consecutive."""
    d = prev_day + timedelta(days=1)
    while d < next_day:
        if is_working_day(d):
            gap = by_user_date.get((user_id, d))
            if gap is None or gap.status != AttendanceRecord.HOLIDAY:
                return False
        d += timedelta(days=1)
    return True


def group_credited_days(records):
    """Collapse excused / holiday / approved-leave records into one row per
    unbroken stretch: same person, same status, same approver (or recorder).

    14, 15 and 16 Oct approved by the same person become ONE row
    "Wed 14 Oct – Fri 16 Oct 2026" (3 days). A different approver, a different
    status or a real gap starts a new row.
    """
    recs = sorted(
        records,
        key=lambda r: ((r.user.get_full_name() or r.user.email).lower(), r.user_id, r.date),
    )
    by_user_date = {(r.user_id, r.date): r for r in recs}
    groups = []
    for r in recs:
        who = credit_authority(r)
        last = groups[-1] if groups else None
        if (
            last is not None
            and last["user"].pk == r.user_id
            and last["status"] == r.status
            and last["authority"] == who
            and _contiguous(last["end"], r.date, r.user_id, by_user_date)
        ):
            last["end"] = r.date
            last["days"] += 1
            note = (r.notes or "").strip()
            if note and note not in last["notes"]:
                last["notes"].append(note)
            continue
        note = (r.notes or "").strip()
        groups.append({
            "user": r.user,
            "status": r.status,
            "status_label": r.get_status_display(),
            "authority": who,
            "start": r.date,
            "end": r.date,
            "days": 1,
            "notes": [note] if note else [],
        })
    for g in groups:
        g["dates"] = format_date_range(g["start"], g["end"])
        g["notes_text"] = "; ".join(g["notes"])
    return groups


def _holidays_overlapping(first_day, last_day):
    from django.db.models import Q
    from portal.models import Holiday
    return list(
        Holiday.objects.filter(is_active=True)
        .filter(Q(is_recurring=True) | Q(date_start__lte=last_day, date_end__gte=first_day))
        .prefetch_related("roles")
    )


def _blank_tally():
    return {"present": 0, "late": 0, "absent": 0, "excused": 0,
            "holiday": 0, "on_leave": 0, "credited": 0, "expected": 0,
            "pct": 0, "holiday_dates": set()}


def summarize_period(users, first_day, last_day, records=None, today=None):
    """Per-user tallies for [first_day, last_day] -> {user_id: tally}.

    Counts ONLY days that have an attendance record (on working days up to
    today). No record = not counted at all.

    tally keys: present, late, absent, excused, holiday, on_leave,
    credited (everything except absent), expected (recorded days counted),
    pct, holiday_dates (always empty: unrecorded holidays are not counted).
    """
    today = today or timezone.localdate()
    users = list(users)
    if records is None:
        records = period_records(users, first_day, last_day)

    stop = min(last_day, today)
    out = {u.pk: _blank_tally() for u in users}
    for r in records:
        t = out.get(r.user_id)
        if t is None:
            continue
        if r.date < first_day or r.date > stop or not is_working_day(r.date):
            continue
        if r.status not in ("present", "late", "absent", "excused", "holiday", "on_leave"):
            continue
        t["expected"] += 1
        t[r.status] += 1
    for t in out.values():
        t["credited"] = t["expected"] - t["absent"]
        t["pct"] = pct_of(t["credited"], t["expected"])
    return out


def combine_tallies(tallies):
    """Sum several tallies (department / directorate aggregate)."""
    agg = _blank_tally()
    agg.pop("holiday_dates")
    for t in tallies:
        for k in ("present", "late", "absent", "excused", "holiday",
                  "on_leave", "credited", "expected"):
            agg[k] += t[k]
    agg["pct"] = pct_of(agg["credited"], agg["expected"])
    return agg


def attendance_stats(user, days=30):
    """Return summary numbers for a user over the last `days` calendar days."""
    end = timezone.localdate()
    start = end - timedelta(days=days - 1)

    records = period_records([user], start, end)
    records.sort(key=lambda r: r.date)
    tally = summarize_period([user], start, end, records=records, today=end)[user.pk]

    working_days = [
        d for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
        if is_working_day(d)
    ]
    by_date = {r.date: r for r in records}

    # Streak: consecutive working days attended. Excused / holiday / approved
    # leave days neither add to the streak nor break it.
    streak = 0
    cursor = end
    for _ in range(days):
        if not is_working_day(cursor):
            cursor -= timedelta(days=1)
            continue
        r = by_date.get(cursor)
        if r is not None and r.is_present:
            streak += 1
        elif r is not None and r.status in NEUTRAL_STATUSES:
            pass
        elif cursor == end and r is None:
            pass  # today, not checked in yet
        else:
            break
        cursor -= timedelta(days=1)

    return {
        "window_days": days,
        "start": start,
        "end": end,
        "total_expected": tally["expected"],
        "working_days": len(working_days),
        "present": tally["present"],
        "late": tally["late"],
        "absent": tally["absent"],
        "excused": tally["excused"],
        "holiday": tally["holiday"],
        "on_leave": tally["on_leave"],
        "credited": tally["credited"],
        "present_pct": tally["pct"],
        "streak": streak,
        "records": records,
    }


def department_summary(department, start, end):
    """Aggregate stats for every active user in a department for [start, end]."""
    from accounts.models import User

    users = list(
        User.objects.filter(is_active=True, department=department)
        .order_by("first_name", "last_name", "email")
    )
    total_working = sum(
        1 for i in range((end - start).days + 1)
        if is_working_day(start + timedelta(days=i))
    )
    records = period_records(users, start, end)
    tallies = summarize_period(users, start, end, records=records)

    out = []
    for u in users:
        t = tallies[u.pk]
        out.append({
            "user": u,
            "present": t["present"], "late": t["late"], "absent": t["absent"],
            "excused": t["excused"], "holiday": t["holiday"],
            "on_leave": t["on_leave"], "credited": t["credited"],
            "expected": t["expected"], "pct": t["pct"],
        })

    detail = [r for r in records if r.status in NEUTRAL_STATUSES
              and is_working_day(r.date) and r.date <= timezone.localdate()]
    detail.sort(key=lambda r: (r.user.get_full_name() or r.user.email, r.date))

    return {
        "department": department,
        "start": start,
        "end": end,
        "working_days": total_working,
        "users": out,
        "total": combine_tallies([tallies[u.pk] for u in users]),
        "detail_records": detail,
        "detail_groups": group_credited_days(detail),
    }


def to_matrix(records, month, year):
    """Return a 2D list representation (email column + one per day)."""
    from accounts.models import User

    days_in_month = calendar.monthrange(year, month)[1]
    by_user = defaultdict(dict)
    for r in records:
        by_user[r.user_id][r.date.day] = r

    users = list(
        User.objects.filter(pk__in=by_user.keys())
        .order_by("first_name", "last_name", "email")
    )

    header = ["Email"] + [str(d) for d in range(1, days_in_month + 1)]
    matrix = [header]
    for u in users:
        row = [u.email]
        for d in range(1, days_in_month + 1):
            r = by_user[u.pk].get(d)
            if r is None:
                row.append("")
            elif r.status == AttendanceRecord.PRESENT:
                row.append("1")
            elif r.status == AttendanceRecord.LATE:
                row.append("L")
            elif r.status == AttendanceRecord.ABSENT:
                row.append("0")
            elif r.status == AttendanceRecord.EXCUSED:
                row.append("E")
            elif r.status == AttendanceRecord.HOLIDAY:
                row.append("H")
            elif r.status == AttendanceRecord.ON_LEAVE:
                row.append("LV")
        matrix.append(row)
    return matrix


# ============================================================================
# Auto-mark absent (Celery Beat)
# ============================================================================
def auto_mark_absent(for_date=None, days_back=0):
    """Create absent records for staff with no record for the given day(s).

    Called at end of the workday by Celery Beat. Idempotent.
    """
    from datetime import date as _date, timedelta as _td

    from attendance.models import AttendanceRecord
    from staff.models import Staff

    today = for_date or timezone.localdate()

    if days_back > 0:
        days = []
        cursor = today
        while len(days) < days_back:
            cursor = cursor - _td(days=1)
            try:
                if is_working_day(cursor):
                    days.append(cursor)
            except Exception:
                days.append(cursor)
    else:
        days = [today]

    if days_back == 0:
        try:
            if not is_working_day(today):
                return 0
        except Exception:
            pass

    staff = list(
        Staff.objects
        # Deactivated accounts (people who left) must not collect "absent"
        # records: they would drag every department percentage down.
        .filter(is_deleted=False, user__isnull=False, user__is_active=True)
        .select_related("user", "state")
    )
    if not staff:
        return 0

    ABSENT = getattr(AttendanceRecord, "ABSENT", "absent")
    SOURCE_AUTO = getattr(AttendanceRecord, "SOURCE_AUTO", "auto")

    total_created = 0

    for day in days:
        existing = set(
            AttendanceRecord.objects
            .filter(date=day)
            .values_list("user_id", flat=True)
        )
        day_holidays = holidays_on(day)

        to_create = []
        for s in staff:
            if s.user_id in existing:
                continue
            if day_holidays and is_holiday_for(s.user, day, day_holidays):
                continue  # review M12: public/department holiday

            state = getattr(s, "state", None)
            if state and getattr(state, "counts_as_absent", False):
                continue

            kwargs = dict(
                user=s.user,
                date=day,
                status=ABSENT,
                source=SOURCE_AUTO,
            )
            try:
                AttendanceRecord._meta.get_field("notes")
                kwargs["notes"] = "Auto-marked absent (no check-in)."
            except Exception:
                pass

            to_create.append(AttendanceRecord(**kwargs))

        if to_create:
            try:
                before = AttendanceRecord.objects.filter(date=day).count()
                AttendanceRecord.objects.bulk_create(to_create, ignore_conflicts=True)
                # With ignore_conflicts the list length over-counts rows that
                # already existed (a concurrent check-in), so count what landed.
                total_created += (
                    AttendanceRecord.objects.filter(date=day).count() - before
                )
            except Exception:
                logger.exception("auto_mark_absent: bulk_create failed for %s", day)
                for obj in to_create:
                    try:
                        obj.save()
                        total_created += 1
                    except Exception:
                        logger.exception(
                            "auto_mark_absent: row failed for user %s on %s",
                            obj.user_id, day,
                        )

    return total_created


# ============================================================================
# Weekly summary email (Celery Beat)
# ============================================================================
def send_weekly_summary(for_date=None):
    """Email each department head a summary of last week's attendance."""
    from datetime import date as _date, timedelta as _td
    from collections import defaultdict

    from django.conf import settings

    from attendance.models import AttendanceRecord
    from staff.models import Department, Staff

    today = for_date or timezone.localdate()

    weekday = today.weekday()
    last_monday = today - _td(days=weekday + 7)
    last_sunday = last_monday + _td(days=6)

    records = (
        AttendanceRecord.objects
        .filter(date__gte=last_monday, date__lte=last_sunday)
        .select_related("user")
    )

    by_dept = defaultdict(lambda: {
        "present": 0, "late": 0, "absent": 0, "excused": 0,
        "holiday": 0, "on_leave": 0, "total": 0,
    })

    user_ids = {r.user_id for r in records}
    staff_by_user = {
        s.user_id: s
        for s in Staff.objects.filter(user_id__in=user_ids, is_deleted=False)
        .select_related("department")
    }

    for r in records:
        s = staff_by_user.get(r.user_id)
        if not s or not s.department_id:
            continue
        d = s.department_id
        bucket = by_dept[d]
        bucket["total"] += 1
        status = (r.status or "").lower()
        if status in bucket:
            bucket[status] += 1

    if not by_dept:
        return 0

    sent = 0
    departments = Department.objects.filter(pk__in=list(by_dept.keys()))

    from accounts.models import User as _User

    for dept in departments:
        # Review M12: Department had no head/director attribute, so this
        # never sent. It now uses the new Department.head field, falling
        # back to the department's active Director.
        head = dept.head if dept.head_id and dept.head.is_active else None
        if head is None:
            head = (
                _User.objects.filter(is_active=True, department=dept, role__code="director")
                .order_by("pk").first()
            )
        if not head or not getattr(head, "email", None):
            continue

        stats = by_dept[dept.pk]
        # Excused, holiday and approved-leave days are credited like present.
        credited = (stats["present"] + stats["late"] + stats["excused"]
                    + stats["holiday"] + stats["on_leave"])
        pct = pct_of(credited, stats["total"])

        try:
            from core.email_utils import send_templated_email
            send_templated_email(
                subject=f"Weekly attendance summary — {dept.name}",
                recipients=[head.email],
                template_name="emails/attendance_weekly.html",
                context={
                    "head": head,
                    "department": dept,
                    "week_start": last_monday,
                    "week_end": last_sunday,
                    "stats": stats,
                    "attendance_pct": pct,
                },
                category="general",
            )
            sent += 1
        except Exception:
            logger.exception(
                "send_weekly_summary: email failed for %s", dept.name,
            )

    return sent  
    
    
    