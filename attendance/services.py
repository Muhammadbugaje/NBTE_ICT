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
# Check-in
# ===========================================================================
@transaction.atomic
def check_in_user(user, lat, lng, now=None):
    """The full GPS check-in flow."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()

    if not is_working_day(today):
        return CheckInResult(ok=False, message="Today is not a working day.")

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

        parsed_rows.append(ParsedRow(
            row_number=r_idx,
            user=user,
            email=email_cell,
            statuses=statuses,
            errors=errors,
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

    log(actor, date_cls(parsed.year, parsed.month, 1),
        f"Matrix applied: +{result.created} ~{result.updated} skip{result.skipped}",
        kind="matrix")
    return result


# ===========================================================================
# Stats
# ===========================================================================
def attendance_stats(user, days=30):
    """Return summary numbers for a user over the last `days` calendar days."""
    end = timezone.localdate()
    start = end - timedelta(days=days - 1)

    records = AttendanceRecord.objects.filter(
        user=user, date__gte=start, date__lte=end,
    ).order_by("date")

    working_days = [
        d for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
        if is_working_day(d)
    ]
    total_expected = len(working_days)

    by_date = {r.date: r for r in records}

    present = late = absent = excused = holiday = on_leave = 0
    for d in working_days:
        r = by_date.get(d)
        if r is None:
            continue
        if r.status == AttendanceRecord.PRESENT:
            present += 1
        elif r.status == AttendanceRecord.LATE:
            late += 1
        elif r.status == AttendanceRecord.ABSENT:
            absent += 1
        elif r.status == AttendanceRecord.EXCUSED:
            excused += 1
        elif r.status == AttendanceRecord.HOLIDAY:
            holiday += 1
        elif r.status == AttendanceRecord.ON_LEAVE:
            on_leave += 1

    streak = 0
    cursor = end
    for _ in range(days):
        if not is_working_day(cursor):
            cursor -= timedelta(days=1)
            continue
        r = by_date.get(cursor)
        if r is not None and r.is_present:
            streak += 1
            cursor -= timedelta(days=1)
        else:
            break

    denom = total_expected - holiday - on_leave or 1
    pct = round(((present + late) / denom) * 100) if total_expected else 0

    return {
        "window_days": days,
        "start": start,
        "end": end,
        "total_expected": total_expected,
        "present": present,
        "late": late,
        "absent": absent,
        "excused": excused,
        "holiday": holiday,
        "on_leave": on_leave,
        "present_pct": pct,
        "streak": streak,
        "records": list(records),
    }


def department_summary(department, start, end):
    """Aggregate stats for every active user in a department for [start, end]."""
    from accounts.models import User

    users = (
        User.objects.filter(is_active=True, department=department)
        .order_by("first_name", "last_name", "email")
    )
    days = [
        start + timedelta(days=i)
        for i in range((end - start).days + 1)
        if is_working_day(start + timedelta(days=i))
    ]
    total_working = len(days)

    out = []
    for u in users:
        by_date = {
            r.date: r
            for r in AttendanceRecord.objects.filter(
                user=u, date__gte=start, date__lte=end,
            )
        }
        present = late = absent = 0
        for d in days:
            r = by_date.get(d)
            if r is None:
                continue
            if r.status in AttendanceRecord.STATUSES_PRESENT:
                present += 1
                if r.status == AttendanceRecord.LATE:
                    late += 1
            elif r.status == AttendanceRecord.ABSENT:
                absent += 1
        pct = round((present / total_working) * 100) if total_working else 0
        out.append({
            "user": u,
            "present": present,
            "late": late,
            "absent": absent,
            "pct": pct,
        })

    return {
        "department": department,
        "start": start,
        "end": end,
        "working_days": total_working,
        "users": out,
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
        .filter(is_deleted=False, user__isnull=False)
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
                AttendanceRecord.objects.bulk_create(to_create, ignore_conflicts=True)
                total_created += len(to_create)
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
        "present": 0, "late": 0, "absent": 0, "excused": 0, "total": 0,
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
        pct = (
            round((stats["present"] + stats["late"]) / stats["total"] * 100)
            if stats["total"] else 0
        )

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
    
    
    