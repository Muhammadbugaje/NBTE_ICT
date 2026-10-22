"""attendance/ - GPS check-in and my attendance.

Reuses: attendance.services.check_in_block_reason (the refusals of
check_in_user that do not need a reading), validate_gps_reading (the reading
checks of the website's check_in_api), check_in_user (the whole check-in),
non_working_reason, summarize_period, attendance_stats, record_detail,
NEUTRAL_STATUSES; attendance.views._backfill_absent_range (the website's
absent backfill before showing history) and attendance.codes
.manual_checkin_enabled.

Mock locations: the website (a browser) cannot detect a mocked position, so
it has no policy. The app reports ``is_mocked``; when true the check-in is
refused (400 ``mock_location``) and the attempt is written to the attendance
log (attendance.services.log, kind "mock_location") - nothing is recorded.
"""
import calendar
from datetime import date, timedelta

from django.utils import timezone

from .work import iso


def location_item(loc):
    return {"id": loc.pk, "name": loc.name, "latitude": float(loc.latitude), "longitude": float(loc.longitude),
            "radius_meters": loc.effective_radius_meters,
            "work_start_time": loc.work_start_time.strftime("%H:%M") if loc.work_start_time else None,
            "grace_minutes": loc.grace_minutes}


def record_item(rec):
    from attendance import services

    return {"date": rec.date, "status": rec.status, "status_display": rec.get_status_display(),
            "check_in_at": iso(rec.check_in_at), "location": rec.location.name if rec.location_id else None,
            "distance_meters": rec.distance_meters, "source": rec.source,
            "source_display": rec.get_source_display(),
            "detail": services.record_detail(rec) if rec.status in services.NEUTRAL_STATUSES else ""}


def my_locations(user):
    from attendance.models import AttendanceLocation

    locs = AttendanceLocation.objects.filter(is_active=True).prefetch_related("departments").order_by("name")
    if user.department_id:
        return [loc for loc in locs if loc.applies_to_department(user.department_id)]
    return list(locs)


def today(user):
    from attendance import codes, services
    from attendance.models import AttendanceRecord

    day = timezone.localdate()
    reason = services.check_in_block_reason(user, day)
    record = (AttendanceRecord.objects.filter(user=user, date=day).select_related("location").first())
    return {
        "date": day,
        "is_working_day": not services.non_working_reason(day, user),
        "checked_in": bool(record and record.status in AttendanceRecord.STATUSES_PRESENT),
        "record": record_item(record) if record else None,
        "can_check_in": not reason,
        "reason": reason,
        "locations": [location_item(loc) for loc in my_locations(user)],
        "max_accuracy_meters": services._cfg_int("attendance.max_gps_accuracy_m", 200),
        "manual_check_in_enabled": bool(codes.manual_checkin_enabled()),
        "check_out_supported": False,
    }


def history_queryset(user, year, month=None, status=""):
    from attendance.models import AttendanceRecord
    from attendance.views import _backfill_absent_range

    if month:
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
    else:
        first, last = date(year, 1, 1), date(year, 12, 31)
    _backfill_absent_range([user], first, last)
    qs = (AttendanceRecord.objects.filter(user=user, date__gte=first, date__lte=last)
          .select_related("location", "recorded_by", "leave_application__request")
          .prefetch_related("leave_application__request__approvals__actor").order_by("-date"))
    if status:
        qs = qs.filter(status=status)
    return qs


def _tally(t):
    return {k: t[k] for k in ("present", "late", "absent", "excused", "holiday", "on_leave", "credited",
                              "expected")} | {"percent": t["pct"]}


def summary(user):
    """This week (Monday to today) day by day, plus the last 30 days."""
    from attendance import services
    from attendance.models import AttendanceRecord

    day = timezone.localdate()
    monday = day - timedelta(days=day.weekday())
    week_end = monday + timedelta(days=6)
    records = {r.date: r for r in AttendanceRecord.objects.filter(user=user, date__gte=monday, date__lte=week_end)
               .select_related("location")}
    week = services.summarize_period([user], monday, day, today=day)[user.pk]
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        rec = records.get(d)
        days.append({"date": d, "weekday": d.strftime("%a"), "is_working_day": services.is_working_day(d),
                     "is_future": d > day, "status": rec.status if rec else None,
                     "status_display": rec.get_status_display() if rec else None,
                     "check_in_at": iso(rec.check_in_at) if rec else None})
    stats = services.attendance_stats(user, days=30)
    return {"week_start": monday, "week_end": week_end, "week": _tally(week), "days": days,
            "last_30_days": {"present": stats["present"], "late": stats["late"], "absent": stats["absent"],
                             "excused": stats["excused"], "holiday": stats["holiday"],
                             "on_leave": stats["on_leave"], "credited": stats["credited"],
                             "expected": stats["total_expected"], "percent": stats["present_pct"]},
            "streak": stats["streak"]}
