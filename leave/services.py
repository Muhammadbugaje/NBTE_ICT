from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import LeaveApplication, LeaveEntitlement, LeaveType

ANNUAL_LEAVE_TYPE_CODE = "annual-leave"  # the workflow RequestType (carries every leave type)
OVER_BALANCE_CONFIG_KEY = "leave.refuse_over_balance"
ROLE_ENTITLEMENT_CONFIG_KEY = "leave.entitlement_by_role"
APPROVED_REQUEST_STATUSES = (
    "in_progress",
    "awaiting_fulfillment",
    "ready_for_handover",
    "completed",
)
PENDING_REQUEST_STATUSES = (
    "awaiting_approval",
    "awaiting_receiving",
    "returned",
)


def parse_application_dates(form_data):
    values = form_data or {}
    parsed = {}
    for field in ("start_date", "end_date"):
        value = values.get(field)
        if isinstance(value, date):
            parsed[field] = value
        elif isinstance(value, str):
            parsed[field] = parse_date(value)
        else:
            parsed[field] = None
    if not parsed["start_date"] or not parsed["end_date"]:
        raise ValidationError("Enter both the first and last day of leave.")
    if parsed["end_date"] < parsed["start_date"]:
        raise ValidationError("The last day of leave must be on or after the first day.")
    return parsed["start_date"], parsed["end_date"]


def _holiday_covers(day, holiday):
    if not holiday.is_recurring:
        return holiday.date_start <= day <= holiday.date_end

    first = (holiday.date_start.month, holiday.date_start.day)
    last = (holiday.date_end.month, holiday.date_end.day)
    current = (day.month, day.day)
    if first <= last:
        return first <= current <= last
    return current >= first or current <= last


def _visible_holidays(user, start_date, end_date):
    from portal.services import live_holidays

    return list(live_holidays(
        user=user,
        from_date=start_date,
        to_date=end_date,
        include_recurring=True,
    ))


def _count_working_days(start_date, end_date, holidays):
    count = 0
    day = start_date
    while day <= end_date:
        if day.weekday() < 5 and not any(
            _holiday_covers(day, holiday) for holiday in holidays
        ):
            count += 1
        day += timedelta(days=1)
    return count


def working_days(start_date, end_date, *, user):
    if end_date < start_date:
        raise ValidationError("The last day of leave must be on or after the first day.")
    return _count_working_days(
        start_date,
        end_date,
        _visible_holidays(user, start_date, end_date),
    )


def resumption_date(end_date, *, user):
    """Return the first weekday after leave that is not a visible holiday."""
    candidate = end_date + timedelta(days=1)
    while working_days(candidate, candidate, user=user) == 0:
        candidate += timedelta(days=1)
    return candidate


def resolve_leave_type(value=None, *, default_application=None, active_only=True):
    """A LeaveType from a code, pk or instance; falls back to the type of the
    application being revised, then to annual leave (old form data)."""
    if isinstance(value, LeaveType):
        lt = value
    elif value not in (None, ""):
        qs = LeaveType.objects.all()
        lt = qs.filter(code=str(value)).first()
        if lt is None and str(value).isdigit():
            lt = qs.filter(pk=int(value)).first()
        if lt is None:
            raise ValidationError("Choose a valid leave type.")
    elif default_application is not None:
        lt = default_application.leave_type
    else:
        lt = LeaveType.annual()
    if active_only and not lt.is_active:
        raise ValidationError(f"{lt.name} is not open for applications.")
    return lt


def end_date_for(start_date, days, leave_type, *, user):
    """Days mode (F4): the last day of a ``days``-day leave starting on
    ``start_date``. Working-day types skip weekends and public holidays."""
    days = int(days)
    if days < 1:
        raise ValidationError("Enter at least one day.")
    if leave_type.counting == LeaveType.COUNT_CALENDAR:
        return start_date + timedelta(days=days - 1)
    horizon = start_date + timedelta(days=days * 3 + 31)
    holidays = _visible_holidays(user, start_date, horizon)
    day, counted = start_date, 0
    while True:
        if day.weekday() < 5 and not any(_holiday_covers(day, h) for h in holidays):
            counted += 1
            if counted == days:
                return day
        day += timedelta(days=1)
        if day > horizon:  # pragma: no cover - a holiday wall longer than the horizon
            horizon = day + timedelta(days=days * 3 + 31)
            holidays = _visible_holidays(user, day, horizon)


def counted_days(start_date, end_date, leave_type, *, user):
    """Days an application uses: working days or calendar days by type."""
    if leave_type.counting == LeaveType.COUNT_CALENDAR:
        return (end_date - start_date).days + 1
    return working_days(start_date, end_date, user=user)


def parse_application(form_data, leave_type, *, user):
    """(start, end, days_requested) from either mode's form data."""
    values = form_data or {}
    if leave_type.is_days_mode and values.get("days") not in (None, ""):
        start = values.get("start_date")
        start = start if isinstance(start, date) else parse_date(str(start or ""))
        if not start:
            raise ValidationError("Enter the first day of leave.")
        try:
            days = int(str(values.get("days")).strip())
        except (TypeError, ValueError):
            raise ValidationError("Enter the number of days as a whole number.")
        if days < 1:
            raise ValidationError("Enter at least one day.")
        if days > 366:
            raise ValidationError("A single application cannot be longer than a year.")
        return start, end_date_for(start, days, leave_type, user=user), days
    start, end = parse_application_dates(values)
    return start, end, None


@dataclass
class LeaveCheck:
    staff: object
    leave_type: LeaveType
    start_date: date
    end_date: date
    days: int
    days_requested: int | None
    balance: dict | None


def _staff_for(user):
    from staff.models import Staff

    staff = Staff.objects.filter(user=user, is_deleted=False).first()
    if staff is None:
        raise ValidationError(
            "Your account is not linked to an active staff record. Contact HR before applying."
        )
    return staff


def check_application(user, form_data, exclude_request_id=None, *, has_document=None, today=None):
    """Every rule for a leave application (F5), one clear message each:
    staff record, type open, gender, dates or days, notice, length, overlap,
    applications per year, balance and supporting document."""
    staff = _staff_for(user)
    form_data = form_data or {}
    existing = None
    if exclude_request_id is not None:
        existing = LeaveApplication.objects.filter(request_id=exclude_request_id).select_related("leave_type").first()
    leave_type = resolve_leave_type(form_data.get("leave_type"), default_application=existing)
    if leave_type.gender_restriction:
        if not staff.gender:
            raise ValidationError(
                f"{leave_type.name} is only for {leave_type.get_gender_restriction_display().lower()}, and your "
                "staff record has no gender. Ask HR to update your record.")
        if staff.gender != leave_type.gender_restriction:
            raise ValidationError(f"{leave_type.name} is only available to "
                                  f"{leave_type.get_gender_restriction_display().lower()}.")
    start_date, end_date, days_requested = parse_application(form_data, leave_type, user=user)
    days = counted_days(start_date, end_date, leave_type, user=user)
    if not days:
        raise ValidationError("The selected period contains no working days.")
    today = today or timezone.localdate()
    if leave_type.notice_days and existing is None:
        earliest = today + timedelta(days=leave_type.notice_days)
        if start_date < earliest:
            raise ValidationError(
                f"{leave_type.name} needs {leave_type.notice_days} days' notice: the earliest first day is "
                f"{earliest:%d %b %Y}.")
    if leave_type.max_days_per_application and days > leave_type.max_days_per_application:
        unit = "working days" if leave_type.counting == LeaveType.COUNT_WORKING else "days"
        raise ValidationError(f"{leave_type.name} is at most {leave_type.max_days_per_application} {unit} per "
                              f"application; this one is {days}.")
    # Review M16: block overlapping applications (pending or approved), any type.
    overlapping = LeaveApplication.objects.filter(
        staff=staff,
        start_date__lte=end_date,
        end_date__gte=start_date,
        request__is_deleted=False,
        request__status__in=APPROVED_REQUEST_STATUSES + PENDING_REQUEST_STATUSES,
    )
    if exclude_request_id is not None:
        overlapping = overlapping.exclude(request_id=exclude_request_id)
    clash = overlapping.select_related("request", "leave_type").first()
    if clash is not None:
        raise ValidationError(
            f"These dates overlap your {clash.leave_type.name.lower()} application {clash.request.reference} "
            f"({clash.start_date:%d %b %Y} to {clash.end_date:%d %b %Y})."
        )
    if leave_type.max_per_year:
        taken = LeaveApplication.objects.filter(
            staff=staff, leave_type=leave_type, start_date__year=start_date.year, request__is_deleted=False,
            request__status__in=APPROVED_REQUEST_STATUSES + PENDING_REQUEST_STATUSES)
        if exclude_request_id is not None:
            taken = taken.exclude(request_id=exclude_request_id)
        if taken.count() >= leave_type.max_per_year:
            raise ValidationError(f"You can apply for {leave_type.name.lower()} at most "
                                  f"{leave_type.max_per_year} time(s) a year.")
    balance = None
    first_year_end = min(end_date, date(start_date.year, 12, 31))
    in_year = counted_days(start_date, first_year_end, leave_type, user=user)
    balance = _balance_for(staff, start_date.year, leave_type=leave_type, exclude_request_id=exclude_request_id)
    if balance["is_set"] and refuse_over_balance():
        available = balance["remaining"] - balance["pending"]
        if Decimal(in_year) > available:
            raise ValidationError(
                f"Not enough {leave_type.name.lower()} left for {start_date.year}: you asked for {in_year} "
                f"day(s) and {max(available, Decimal('0')):g} are available"
                + (f" ({balance['pending']:g} pending)" if balance["pending"] else "") + ".")
    if leave_type.needs_document and has_document is False:
        raise ValidationError(f"{leave_type.name} needs a supporting document. Attach it to the application.")
    return LeaveCheck(staff, leave_type, start_date, end_date, days, days_requested, balance)


def refuse_over_balance():
    try:
        from config.registry import get
        return bool(get(OVER_BALANCE_CONFIG_KEY, True))
    except Exception:
        return True


def validate_application(user, form_data, exclude_request_id=None, **kwargs):
    """Kept for callers of v2 (web raise, API preview): (staff, start, end)."""
    check = check_application(user, form_data, exclude_request_id, **kwargs)
    return check.staff, check.start_date, check.end_date


def sync_application(workflow_request, form_data):
    check = check_application(
        workflow_request.requester, form_data, exclude_request_id=workflow_request.pk,
    )
    LeaveApplication.objects.update_or_create(
        request=workflow_request,
        defaults={
            "staff": check.staff,
            "leave_type": check.leave_type,
            "start_date": check.start_date,
            "end_date": check.end_date,
            "days_requested": check.days_requested,
        },
    )


def _leave_working_dates(application):
    """Days the leave covers in attendance: working days only (a weekend or
    a holiday is never an attendance day, whatever the type counts)."""
    holidays = _visible_holidays(
        application.staff.user,
        application.start_date,
        application.end_date,
    )
    day = application.start_date
    while day <= application.end_date:
        if day.weekday() < 5 and not any(
            _holiday_covers(day, holiday) for holiday in holidays
        ):
            yield day
        day += timedelta(days=1)


def _restore_absence(user, day):
    from attendance.models import AttendanceRecord

    if day >= timezone.localdate():
        return
    AttendanceRecord.objects.get_or_create(
        user=user,
        date=day,
        defaults={
            "status": AttendanceRecord.ABSENT,
            "source": AttendanceRecord.SOURCE_AUTO,
            "notes": "Auto-marked absent after approved leave was withdrawn.",
        },
    )


def sync_attendance(application):
    """Reflect approved leave in attendance without overwriting real attendance."""
    from attendance.models import AttendanceRecord

    is_approved = (
        not application.request.is_deleted
        and application.request.status in APPROVED_REQUEST_STATUSES
    )
    day_set = set(_leave_working_dates(application)) if is_approved else set()
    linked_rows = AttendanceRecord.objects.filter(
        leave_application=application,
        status=AttendanceRecord.ON_LEAVE,
    )
    removed_dates = list(linked_rows.exclude(date__in=day_set).values_list("date", flat=True))
    linked_rows.exclude(date__in=day_set).delete()

    if is_approved:
        for day in day_set:
            record, created = AttendanceRecord.objects.get_or_create(
                user=application.staff.user,
                date=day,
                defaults={
                    "status": AttendanceRecord.ON_LEAVE,
                    "source": AttendanceRecord.SOURCE_LEAVE,
                    "leave_application": application,
                    "notes": f"Approved {application.leave_type.name.lower()}.",
                },
            )
            if not created and record.status in {
                AttendanceRecord.ABSENT,
                AttendanceRecord.ON_LEAVE,
            }:
                record.status = AttendanceRecord.ON_LEAVE
                record.source = AttendanceRecord.SOURCE_LEAVE
                record.leave_application = application
                record.notes = f"Approved {application.leave_type.name.lower()}."
                record.save(update_fields=[
                    "status", "source", "leave_application", "notes", "updated_at",
                ])

    for day in removed_dates:
        _restore_absence(application.staff.user, day)


def _balance_for(staff, year, *, leave_type=None, exclude_request_id=None):
    """Entitlement, used (approved), pending and remaining days of one leave
    type in a calendar year. Entitlement: the person's override for that
    type and year, else (annual only) the role policy, else the type's
    yearly days; a type without days has no balance (``is_set`` False)."""
    leave_type = leave_type or LeaveType.annual()
    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    applications = LeaveApplication.objects.filter(
        staff=staff,
        leave_type=leave_type,
        start_date__lte=last_day,
        end_date__gte=first_day,
        request__is_deleted=False,  # review M16: deleted requests don't count
    )
    if exclude_request_id is not None:
        applications = applications.exclude(request_id=exclude_request_id)
    applications = list(applications.select_related("request", "staff__user"))
    holidays = _visible_holidays(staff.user, first_day, last_day) if applications else []

    used = 0
    pending = 0
    for application in applications:
        start = max(application.start_date, first_day)
        end = min(application.end_date, last_day)
        if leave_type.counting == LeaveType.COUNT_CALENDAR:
            days = (end - start).days + 1
        else:
            days = _count_working_days(start, end, holidays)
        if application.request.status in APPROVED_REQUEST_STATUSES:
            used += days
        elif application.request.status in PENDING_REQUEST_STATUSES:
            pending += days

    entitlement = LeaveEntitlement.objects.filter(staff=staff, leave_type=leave_type, year=year).first()
    role_days = None
    if entitlement is None and leave_type.code == LeaveType.ANNUAL:
        role_days = role_entitlement_for(staff)
    type_days = (Decimal(leave_type.days) if entitlement is None and role_days is None
                 and leave_type.days is not None else None)
    entitled_days = (
        entitlement.days if entitlement is not None
        else role_days if role_days is not None
        else type_days if type_days is not None
        else Decimal("0")
    )
    return {
        "year": year,
        "leave_type": leave_type,
        "entitlement": entitled_days,
        "used": Decimal(used),
        "pending": Decimal(pending),
        "remaining": entitled_days - Decimal(used),
        "is_set": entitlement is not None or role_days is not None or type_days is not None,
        "entitlement_source": (
            "override" if entitlement is not None
            else "role" if role_days is not None
            else "type" if type_days is not None
            else None
        ),
    }


def role_entitlement_for(staff):
    """Return the configured annual allowance for a staff member's role."""
    if not staff.user_id or not staff.user.role_id:
        return None

    from config.registry import get

    entitlements = get(ROLE_ENTITLEMENT_CONFIG_KEY, {})
    if not isinstance(entitlements, dict):
        raise ValidationError(
            f"{ROLE_ENTITLEMENT_CONFIG_KEY} must be a role-to-days mapping."
        )
    value = entitlements.get(staff.user.role.code)
    if value is None or value == "":
        return None

    try:
        days = Decimal(str(value))
    except (ArithmeticError, ValueError):
        raise ValidationError(
            f"Invalid annual leave entitlement configured for role "
            f"'{staff.user.role.name}'."
        )
    if not days.is_finite() or days < 0:
        raise ValidationError(
            f"Invalid annual leave entitlement configured for role "
            f"'{staff.user.role.name}'."
        )
    return days


def balance_for(staff, year, leave_type=None):
    return _balance_for(staff, year, leave_type=leave_type)


def balances_for(staff, year):
    """One balance per active leave type the person may take (gender rule)."""
    out = []
    for lt in LeaveType.objects.filter(is_active=True):
        if lt.gender_restriction and staff.gender and staff.gender != lt.gender_restriction:
            continue
        out.append(_balance_for(staff, year, leave_type=lt))
    return out


def request_summary(workflow_request):
    application = (
        LeaveApplication.objects
        .filter(request=workflow_request)
        .select_related("staff", "staff__user", "leave_type")
        .first()
    )
    if application is None:
        return None
    leave_type = application.leave_type

    yearly_days = {}
    period_start = application.start_date
    while period_start <= application.end_date:
        period_end = min(
            application.end_date,
            date(period_start.year, 12, 31),
        )
        yearly_days[period_start.year] = counted_days(
            period_start,
            period_end,
            leave_type,
            user=application.staff.user,
        )
        if period_end == application.end_date:
            break
        period_start = period_end + timedelta(days=1)

    years = []
    for year, requested_days in yearly_days.items():
        balance = _balance_for(
            application.staff,
            year,
            leave_type=leave_type,
            exclude_request_id=workflow_request.pk,
        )
        requested = Decimal(requested_days)
        is_set = balance["is_set"]
        excess = (
            max(Decimal("0"), requested - balance["remaining"])
            if is_set else Decimal("0")
        )
        years.append({
            "year": year,
            "requested_days": requested,
            "remaining": balance["remaining"],
            "is_set": is_set,
            "over_balance_days": excess,
            "is_over_balance": excess > 0,
        })

    return {
        "application": application,
        "leave_type": leave_type,
        "day_unit": "working days" if leave_type.counting == LeaveType.COUNT_WORKING else "days",
        "years": years,
        "requested_days": sum(
            (period["requested_days"] for period in years),
            Decimal("0"),
        ),
        "has_over_balance": any(period["is_over_balance"] for period in years),
    }


def dashboard_data(user):
    from staff.models import Staff
    from workflow.models import Request, RequestType

    staff = (
        Staff.objects
        .filter(user=user, is_deleted=False)
        .select_related("department", "state")
        .first()
    )
    if staff is None:
        return None

    today = timezone.localdate()
    balance = balance_for(staff, today.year)
    balances = balances_for(staff, today.year)
    current = (
        LeaveApplication.objects
        .filter(
            staff=staff,
            start_date__lte=today,
            end_date__gte=today,
            request__status__in=APPROVED_REQUEST_STATUSES,
            request__is_deleted=False,
        )
        .select_related("request", "leave_type")
        .order_by("start_date", "pk")
        .first()
    )
    upcoming = (
        LeaveApplication.objects
        .filter(
            staff=staff,
            end_date__gte=today,
            request__status__in=APPROVED_REQUEST_STATUSES,
            request__is_deleted=False,
        )
        .select_related("request", "leave_type")
        .order_by("start_date", "pk")
        .first()
    )
    recent = list(
        LeaveApplication.objects
        .filter(staff=staff, request__is_deleted=False)
        .exclude(request__status__in=Request.CLOSED_STATUSES)
        .select_related("request", "leave_type")
        .order_by("-created_at")[:3]
    )
    request_type_available = RequestType.objects.filter(
        code=ANNUAL_LEAVE_TYPE_CODE,
        is_active=True,
    ).exists()
    return {
        "staff": staff,
        "balance": balance,
        "balances": balances,
        "upcoming": upcoming,
        "current": current,
        "resumption_date": (
            resumption_date(
                current.end_date if current else upcoming.end_date,
                user=user,
            )
            if current or upcoming else None
        ),
        "days_until_upcoming": (
            (upcoming.start_date - today).days if upcoming else None
        ),
        "recent": recent,
        "request_url": (
            reverse("leave:apply")
            if request_type_available else None
        ),
    }


# ===========================================================================
# v2.1 Phase D: apply for any leave type (web page; the API reuses it)
# ===========================================================================
def leave_request_type():
    from workflow.models import RequestType

    return RequestType.objects.filter(code=ANNUAL_LEAVE_TYPE_CODE, is_active=True).first()


def preview(user, form_data, *, has_document=None):
    """What the apply page shows while typing: computed end date, counted
    days, resumption date and the balance after - or the refusal."""
    check = check_application(user, form_data, has_document=has_document)
    balance = check.balance
    in_year = counted_days(check.start_date, min(check.end_date, date(check.start_date.year, 12, 31)),
                           check.leave_type, user=user)
    return {
        "check": check,
        "resumption_date": resumption_date(check.end_date, user=user),
        "balance_after": (balance["remaining"] - balance["pending"] - in_year) if balance and balance["is_set"]
        else None,
        "day_unit": "working days" if check.leave_type.counting == LeaveType.COUNT_WORKING else "calendar days",
    }


def apply_for_leave(user, form_data, *, reason="", document=None, require_document=True):
    """Raise the workflow request for a leave application of any type and
    attach the supporting document. Raises ValidationError with the reason."""
    from workflow import services as wf

    request_type = leave_request_type()
    if request_type is None:
        raise ValidationError("Leave applications are closed. Contact HR.")
    check = check_application(user, form_data, has_document=bool(document) if require_document else None)
    lt = check.leave_type
    period = (f"{check.start_date:%d %b %Y}" if check.start_date == check.end_date
              else f"{check.start_date:%d %b %Y} to {check.end_date:%d %b %Y}")
    unit = "working day" if lt.counting == LeaveType.COUNT_WORKING else "day"
    subject = f"{lt.name}: {period}"
    body = f"{lt.name} from {period} ({check.days} {unit}{'s' if check.days != 1 else ''})."
    if reason.strip():
        body += "\n\n" + reason.strip()
    with transaction.atomic():
        req = wf.raise_request_checked(requester=user, request_type=request_type, subject=subject, body=body,
                                       priority="normal", form_data=form_data)
        if document:
            wf.add_attachment(req, user, file=document, description=f"Supporting document for {lt.name.lower()}")
    return req
