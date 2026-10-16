from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from .models import LeaveApplication, LeaveEntitlement

ANNUAL_LEAVE_TYPE_CODE = "annual-leave"
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
        raise ValidationError("Enter both the first and last day of annual leave.")
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


def validate_application(user, form_data, exclude_request_id=None):
    from staff.models import Staff

    staff = Staff.objects.filter(user=user, is_deleted=False).first()
    if staff is None:
        raise ValidationError(
            "Your account is not linked to an active staff record. Contact HR before applying."
        )
    start_date, end_date = parse_application_dates(form_data)
    if not working_days(start_date, end_date, user=user):
        raise ValidationError("The selected period contains no working days.")
    # Review M16: block overlapping applications (pending or approved).
    overlapping = LeaveApplication.objects.filter(
        staff=staff,
        start_date__lte=end_date,
        end_date__gte=start_date,
        request__is_deleted=False,
        request__status__in=APPROVED_REQUEST_STATUSES + PENDING_REQUEST_STATUSES,
    )
    if exclude_request_id is not None:
        overlapping = overlapping.exclude(request_id=exclude_request_id)
    clash = overlapping.select_related("request").first()
    if clash is not None:
        raise ValidationError(
            f"These dates overlap your leave application {clash.request.reference} "
            f"({clash.start_date:%d %b %Y} to {clash.end_date:%d %b %Y})."
        )
    return staff, start_date, end_date


def sync_application(workflow_request, form_data):
    staff, start_date, end_date = validate_application(
        workflow_request.requester, form_data, exclude_request_id=workflow_request.pk,
    )
    LeaveApplication.objects.update_or_create(
        request=workflow_request,
        defaults={
            "staff": staff,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


def _leave_working_dates(application):
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
                    "notes": "Approved annual leave.",
                },
            )
            if not created and record.status in {
                AttendanceRecord.ABSENT,
                AttendanceRecord.ON_LEAVE,
            }:
                record.status = AttendanceRecord.ON_LEAVE
                record.source = AttendanceRecord.SOURCE_LEAVE
                record.leave_application = application
                record.notes = "Approved annual leave."
                record.save(update_fields=[
                    "status", "source", "leave_application", "notes", "updated_at",
                ])

    for day in removed_dates:
        _restore_absence(application.staff.user, day)


def _balance_for(staff, year, *, exclude_request_id=None):
    first_day = date(year, 1, 1)
    last_day = date(year, 12, 31)
    applications = LeaveApplication.objects.filter(
        staff=staff,
        start_date__lte=last_day,
        end_date__gte=first_day,
        request__is_deleted=False,  # review M16: deleted requests don't count
    )
    if exclude_request_id is not None:
        applications = applications.exclude(request_id=exclude_request_id)
    applications = list(applications.select_related("request", "staff__user"))
    holidays = _visible_holidays(staff.user, first_day, last_day)

    used = 0
    pending = 0
    for application in applications:
        start = max(application.start_date, first_day)
        end = min(application.end_date, last_day)
        days = _count_working_days(start, end, holidays)
        if application.request.status in APPROVED_REQUEST_STATUSES:
            used += days
        elif application.request.status in PENDING_REQUEST_STATUSES:
            pending += days

    entitlement = LeaveEntitlement.objects.filter(staff=staff, year=year).first()
    role_days = None if entitlement is not None else role_entitlement_for(staff)
    entitled_days = (
        entitlement.days if entitlement is not None
        else role_days if role_days is not None
        else Decimal("0")
    )
    return {
        "year": year,
        "entitlement": entitled_days,
        "used": Decimal(used),
        "pending": Decimal(pending),
        "remaining": entitled_days - Decimal(used),
        "is_set": entitlement is not None or role_days is not None,
        "entitlement_source": (
            "override" if entitlement is not None
            else "role" if role_days is not None
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


def balance_for(staff, year):
    return _balance_for(staff, year)


def request_summary(workflow_request):
    application = (
        LeaveApplication.objects
        .filter(request=workflow_request)
        .select_related("staff", "staff__user")
        .first()
    )
    if application is None:
        return None

    yearly_days = {}
    period_start = application.start_date
    while period_start <= application.end_date:
        period_end = min(
            application.end_date,
            date(period_start.year, 12, 31),
        )
        yearly_days[period_start.year] = working_days(
            period_start,
            period_end,
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
    current = (
        LeaveApplication.objects
        .filter(
            staff=staff,
            start_date__lte=today,
            end_date__gte=today,
            request__status__in=APPROVED_REQUEST_STATUSES,
            request__is_deleted=False,
        )
        .select_related("request")
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
        .select_related("request")
        .order_by("start_date", "pk")
        .first()
    )
    recent = list(
        LeaveApplication.objects
        .filter(staff=staff, request__is_deleted=False)
        .exclude(request__status__in=Request.CLOSED_STATUSES)
        .select_related("request")
        .order_by("-created_at")[:3]
    )
    request_type_available = RequestType.objects.filter(
        code=ANNUAL_LEAVE_TYPE_CODE,
        is_active=True,
    ).exists()
    return {
        "staff": staff,
        "balance": balance,
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
            reverse(
                "workflow:raise_type",
                kwargs={"type_code": ANNUAL_LEAVE_TYPE_CODE},
            )
            if request_type_available else None
        ),
    }
