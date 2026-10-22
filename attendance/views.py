"""Attendance views — personal, department, upload, export."""
import calendar
import io
import json
from datetime import date as date_cls, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count, Min
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from django.urls import reverse

from accounts.models import User

from . import access, services
from .forms import ManualEditForm, ReportFilterForm, UploadMatrixForm
from .models import AttendanceLocation, AttendanceRecord
from .services import is_working_day

PER_PAGE = 30


# ===========================================================================
# Permission helpers
# ===========================================================================
def _is_director(user):
    return access.is_director(user)


def _can_view_all_attendance(user):
    """Cross-department attendance access (see attendance/access.py).

    Super admin automatically; everyone else (Executive Secretary, HR, a
    director given the right...) needs `attendance.view_all_attendance`.
    `accounts.access_all_departments` no longer grants this: every director
    role carries it, which let every director see every department.
    """
    return access.can_view_all_attendance(user)


def _can_view_department(user, department):
    """Own-department access: that department's director / deputy / office
    head, or a holder of `attendance.view_department_attendance`. Viewing
    another department needs the all-departments permission."""
    return access.can_view_department(user, department)


def _can_upload(user):
    return user.is_superuser or _is_director(user)


def _upload_department(user):
    """Department an uploader is limited to (None = every department).
    Review H4: directors may only upload for their own department."""
    if user.is_superuser or _can_view_all_attendance(user):
        return None
    return user.department


# ===========================================================================
# Auto-absent backfill
# ===========================================================================
def _backfill_absent_range(users, start, end, actor=None):
    """Create ABSENT records for past working days with no record.

    Rules:
      - Only days strictly BEFORE today are touched.
      - Only working days are touched.
      - A day already having ANY record (including approved leave) is left alone.
      - Won't create records for days before a user's `date_joined`.

    Idempotent — safe to call on every page load.
    Returns the count of records created.
    """
    users = list(users)
    if not users:
        return 0

    today = timezone.localdate()
    latest = min(end, today - timedelta(days=1))

    # Never invent absences from before attendance was being recorded: start
    # no earlier than the first attendance record that exists at all.
    first_recorded = AttendanceRecord.objects.aggregate(m=Min("date"))["m"]
    if first_recorded is None:
        return 0
    start = max(start, first_recorded)
    if start > latest:
        return 0

    # Working days in range.
    working_days = [
        start + timedelta(days=i)
        for i in range((latest - start).days + 1)
        if is_working_day(start + timedelta(days=i))
    ]
    if not working_days:
        return 0

    user_ids = [u.pk for u in users]

    # (user_id, date) pairs that already have a record.
    existing = set(
        AttendanceRecord.objects
        .filter(user_id__in=user_ids, date__in=working_days)
        .values_list("user_id", "date")
    )

    # Earliest allowed day per user — their join date, if set.
    joined = {}
    for u in users:
        dj = getattr(u, "date_joined", None)
        if dj is not None:
            try:
                joined[u.pk] = dj.date() if hasattr(dj, "date") else dj
            except (AttributeError, TypeError):
                joined[u.pk] = None
        else:
            joined[u.pk] = None

    holidays_by_day = {d: services.holidays_on(d) for d in working_days}
    users_by_id = {u.pk: u for u in users}

    to_create = []
    for uid in user_ids:
        jd = joined.get(uid)
        for d in working_days:
            if (uid, d) in existing:
                continue
            if jd is not None and d < jd:
                continue
            if holidays_by_day[d] and services.is_holiday_for(users_by_id[uid], d, holidays_by_day[d]):
                continue  # review M12: never auto-mark absent on a holiday
            to_create.append(AttendanceRecord(
                user_id=uid,
                date=d,
                status=AttendanceRecord.ABSENT,
                source=AttendanceRecord.SOURCE_MANUAL,
                recorded_by=actor,
                notes="Auto-marked absent (no record).",
            ))

    if to_create:
        AttendanceRecord.objects.bulk_create(to_create, ignore_conflicts=True)

    return len(to_create)


# ===========================================================================
# Personal home
# ===========================================================================
@login_required
def attendance_home(request):
    """Personal landing page. Shows today's status, this month's calendar,
    and quick stats."""
    today = timezone.localdate()
    is_working = not services.non_working_reason(today, request.user)

    today_record = AttendanceRecord.objects.filter(
        user=request.user, date=today,
    ).first()

    stats = services.attendance_stats(request.user, days=30)

    # Month grid — the current month by default, or ?month=YYYY-MM
    month_param = request.GET.get("month")
    try:
        if month_param:
            y, m = (int(x) for x in month_param.split("-"))
        else:
            y, m = today.year, today.month
    except (ValueError, TypeError):
        y, m = today.year, today.month

    month_grid = _month_grid(request.user, y, m)

    locations_count = AttendanceLocation.objects.filter(is_active=True).count()
    my_dept_locations = [
        loc for loc in AttendanceLocation.objects.filter(is_active=True)
        if loc.applies_to_department(request.user.department_id)
    ]

    return render(request, "attendance/home.html", {
        "today": today,
        "is_working_day": is_working,
        "today_record": today_record,
        "stats": stats,
        "month_grid": month_grid,
        "month_label": date_cls(y, m, 1).strftime("%B %Y"),
        "prev_month": _shift_month(y, m, -1),
        "next_month": _shift_month(y, m, +1),
        "can_upload": _can_upload(request.user),
        "can_view_department": _can_view_department(request.user, request.user.department),
        "locations_count": locations_count,
        "my_dept_locations": my_dept_locations,
        "can_view_all": _can_view_all_attendance(request.user),
        "has_department": bool(request.user.department_id),
    })


def _shift_month(y, m, delta):
    total = y * 12 + (m - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _month_grid(user, year, month):
    """Return a list of weeks; each week is a list of cell dicts."""
    first_day = date_cls(year, month, 1)
    last_day = date_cls(year, month, calendar.monthrange(year, month)[1])

    # Backfill for this user for the visible month so the grid never shows
    # a stale "not recorded" for a past working day.
    _backfill_absent_range([user], first_day, last_day)

    records = {
        r.date: r
        for r in AttendanceRecord.objects.filter(
            user=user, date__gte=first_day, date__lte=last_day,
        )
    }

    weeks = []
    cal = calendar.Calendar(firstweekday=0)
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            if d.month != month:
                row.append({"date": d, "in_month": False, "record": None,
                            "is_working": False})
            else:
                rec = records.get(d)
                row.append({
                    "date": d,
                    "in_month": True,
                    "record": rec,
                    "is_working": services.is_working_day(d),
                })
        weeks.append(row)
    return weeks


# ===========================================================================
# Check-in (page + JSON API)
# ===========================================================================
@login_required
def check_in_page(request):
    today = timezone.localdate()
    is_working = not services.non_working_reason(today, request.user)
    today_record = AttendanceRecord.objects.filter(
        user=request.user, date=today,
    ).first()

    my_locations = []
    if request.user.department_id:
        my_locations = [
            loc for loc in AttendanceLocation.objects.filter(is_active=True)
            if loc.applies_to_department(request.user.department_id)
        ]
    else:
        my_locations = list(AttendanceLocation.objects.filter(is_active=True))

    return render(request, "attendance/check_in.html", {
        "today": today,
        "is_working_day": is_working,
        "today_record": today_record,
        "my_locations": my_locations,
    })


@login_required
@require_POST
def check_in_api(request):
    """JSON endpoint used by the check-in page."""
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (ValueError, AttributeError):
        data = request.POST

    # Phase 9: the reading checks live in services.validate_gps_reading.
    try:
        lat, lng, _accuracy = services.validate_gps_reading(
            data.get("lat"), data.get("lng"), data.get("accuracy"))
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": exc.messages[0]}, status=400)

    result = services.check_in_user(request.user, lat, lng)

    payload = {
        "ok": result.ok,
        "status": result.status,
        "message": result.message,
        "distance_meters": result.distance_meters,
        "location": result.location.name if result.location else None,
    }
    return JsonResponse(payload, status=200 if result.ok else 400)


@login_required
@require_GET
def location_preview_api(request):
    """Return the closest active location to a lat/lng + the distance."""
    try:
        lat = float(request.GET.get("lat"))
        lng = float(request.GET.get("lng"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid coordinates."}, status=400)

    loc, dist = services.find_matching_location(request.user, lat, lng)
    if loc is not None:
        return JsonResponse({
            "ok": True,
            "in_range": True,
            "location": loc.name,
            "distance_meters": dist,
            "effective_radius_meters": loc.effective_radius_meters,
        })

    from .utils import haversine_meters
    nearest = None
    nearest_dist = None
    for candidate in AttendanceLocation.objects.filter(is_active=True):
        if not candidate.applies_to_department(request.user.department_id):
            continue
        d = haversine_meters(candidate.latitude, candidate.longitude, lat, lng)
        if d is None:
            continue
        if nearest_dist is None or d < nearest_dist:
            nearest = candidate
            nearest_dist = d

    if nearest is None:
        return JsonResponse({
            "ok": True, "in_range": False, "location": None,
            "distance_meters": None, "effective_radius_meters": None,
            "message": "No attendance location is configured for your department.",
        })

    return JsonResponse({
        "ok": True,
        "in_range": False,
        "location": nearest.name,
        "distance_meters": nearest_dist,
        "effective_radius_meters": nearest.effective_radius_meters,
    })


# ===========================================================================
# My attendance
# ===========================================================================
@login_required
def my_attendance(request):
    """The signed-in user's own attendance history, filterable by
    year / month / status. Paginated."""
    user = request.user
    today = timezone.localdate()

    year = services._valid_year(request.GET.get("year"), today.year)

    month_raw = request.GET.get("month")
    month = services._valid_month(month_raw, None) if month_raw else None

    status_field = AttendanceRecord._meta.get_field("status")
    status_choices = list(status_field.choices)
    valid_statuses = {c[0] for c in status_choices}

    status_raw = (request.GET.get("status") or "").strip()
    status = status_raw if status_raw in valid_statuses else ""

    # ---- Determine date range + backfill -----------------------------------
    if month:
        first_day = date_cls(year, month, 1)
        last_day = date_cls(year, month, calendar.monthrange(year, month)[1])
        period_label = date_cls(year, month, 1).strftime("%B %Y")
    else:
        first_day = date_cls(year, 1, 1)
        last_day = date_cls(year, 12, 31)
        period_label = str(year)

    _backfill_absent_range([user], first_day, last_day)

    # ---- Query --------------------------------------------------------------
    qs = (
        AttendanceRecord.objects
        .filter(user=user, date__gte=first_day, date__lte=last_day)
        .select_related("location", "recorded_by", "leave_application__request")
        .prefetch_related("leave_application__request__approvals__actor")
        .order_by("-date")
    )

    if status:
        qs = qs.filter(status=status)

    # ---- Pagination ---------------------------------------------------------
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page") or 1)
    # Say *who approved* an approved-leave day (and who excused an excused day).
    page.object_list = list(page.object_list)
    for rec in page.object_list:
        rec.detail = (
            services.record_detail(rec)
            if rec.status in services.NEUTRAL_STATUSES else ""
        )

    # ---- 90-day stats (always "last 90 days") ------------------------------
    ninety_days_ago = today - timedelta(days=90)
    recent = AttendanceRecord.objects.filter(
        user=user, date__gte=ninety_days_ago, date__lte=today,
    )
    # Excused, holiday and approved-leave days are credited like present days.
    t = services.summarize_period([user], ninety_days_ago, today, today=today)[user.pk]
    stats = {
        "present": t["present"],
        "late": t["late"],
        "absent": t["absent"],
        "excused": t["excused"],
        "holiday": t["holiday"],
        "on_leave": t["on_leave"],
        "credited": t["credited"],
        "expected": t["expected"],
        "present_pct": t["pct"],
    }

    years_with_data = list(
        AttendanceRecord.objects.filter(user=user).dates("date", "year")
    )
    years_available = sorted(
        {d.year for d in years_with_data} | {today.year},
        reverse=True,
    )

    return render(request, "attendance/my_attendance.html", {
        "page": page,
        "stats": stats,
        "years_available": years_available,
        "year_filter": year,
        "month_filter": str(month) if month else "",
        "status_filter": status,
        "status_choices": status_choices,
        "period_label": period_label,
        "total_records": paginator.count,
        "today": today,
    })


# ===========================================================================
# Department report
# ===========================================================================
@login_required
def department_report(request):
    """Department attendance.

    Two modes:
      - month mode: day-by-day matrix for a specific month
      - year mode : summary table for a whole year (no day cells)
    """
    from staff.models import Department

    # ---- Resolve the target department --------------------------------------
    if _can_view_all_attendance(request.user):
        dept_pk = request.GET.get("department")
        target = None
        if dept_pk:
            try:
                target = Department.objects.filter(pk=int(dept_pk), is_active=True).first()
            except (TypeError, ValueError):
                target = None
        dept = target or request.user.department or Department.objects.filter(is_active=True).first()
    else:
        dept = request.user.department

    if not _can_view_department(request.user, dept):
        raise PermissionDenied("Only department directors can view this page.")

    today = timezone.localdate()

    # ---- Parse filters ------------------------------------------------------
    year = services._valid_year(request.GET.get("year"), today.year)

    month_raw = request.GET.get("month")
    month = services._valid_month(month_raw, None) if month_raw else None

    if month:
        first_day = date_cls(year, month, 1)
        last_day = date_cls(year, month, calendar.monthrange(year, month)[1])
        month_label = date_cls(year, month, 1).strftime("%B %Y")
        view_mode = "month"

        if month == 1:
            prev_month_year, prev_month = year - 1, 12
        else:
            prev_month_year, prev_month = year, month - 1

        if month == 12:
            next_month_year, next_month = year + 1, 1
        else:
            next_month_year, next_month = year, month + 1
    else:
        first_day = date_cls(year, 1, 1)
        last_day = date_cls(year, 12, 31)
        month_label = str(year)
        view_mode = "year"
        prev_month_year = prev_month = None
        next_month_year = next_month = None

    # ---- Staff + records ----------------------------------------------------
    staff = list(
        User.objects
        .filter(is_active=True, department=dept)
        .order_by("first_name", "last_name", "email")
    )

    # Auto-mark past working days as absent, in scope, before we compute rows.
    _backfill_absent_range(staff, first_day, last_day, actor=request.user)

    records = services.period_records(staff, first_day, last_day)

    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, {})[r.date.day] = r

    range_days = [
        first_day + timedelta(days=i)
        for i in range((last_day - first_day).days + 1)
    ]
    working_days_total = sum(1 for d in range_days if is_working_day(d))

    # ---- Rows ---------------------------------------------------------------
    # Present + Late = attended. Excused / Holiday / On approved leave are not
    # attendance but are credited exactly like it (see services.summarize_period).
    tallies = services.summarize_period(staff, first_day, last_day, records=records)
    NEUTRAL = services.NEUTRAL_STATUSES
    rows = []
    for u in staff:
        user_records = by_user.get(u.pk, {})
        t = tallies[u.pk]

        cells = []
        if month:
            days_in_month = calendar.monthrange(year, month)[1]
            for d in range(1, days_in_month + 1):
                day = date_cls(year, month, d)
                rec = user_records.get(d)
                if rec is None:
                    if day in t["holiday_dates"]:
                        cells.append({"date": day, "record": None,
                                      "status": AttendanceRecord.HOLIDAY,
                                      "detail": "Holiday"})
                    else:
                        cells.append({
                            "date": day,
                            "record": None,
                            "status": "" if is_working_day(day) else "off",
                            "detail": "",
                        })
                else:
                    cells.append({
                        "date": day,
                        "record": rec,
                        "status": rec.status,
                        "detail": services.record_detail(rec) if rec.status in NEUTRAL else "",
                    })

        rows.append({
            "user": u,
            "cells": cells,
            "present": t["present"],
            "late": t["late"],
            "absent": t["absent"],
            "excused": t["excused"],
            "holiday": t["holiday"],
            "on_leave": t["on_leave"],
            "credited": t["credited"],
            "expected": t["expected"],
            "pct": t["pct"],
        })

    dept_total = services.combine_tallies([tallies[u.pk] for u in staff])

    # ---- Day headers --------------------------------------------------------
    if month:
        days_in_month = calendar.monthrange(year, month)[1]
        days = [date_cls(year, month, d) for d in range(1, days_in_month + 1)]
        day_headers = [
            {
                "day": d.day,
                "weekday": d.strftime("%a")[0],
                "working": is_working_day(d),
            }
            for d in days
        ]
    else:
        days = []
        day_headers = []

    # ---- Years available ----------------------------------------------------
    years_with_data = list(
        AttendanceRecord.objects
        .filter(user__department=dept)
        .dates("date", "year")
    )
    years_available = sorted(
        {d.year for d in years_with_data} | {today.year},
        reverse=True,
    )

    # ---- Department picker (all-access viewers only) -----------------------
    departments = (
        Department.objects.filter(is_active=True).order_by("sort_order", "name")
        if _can_view_all_attendance(request.user)
        else []
    )

    return render(request, "attendance/department.html", {
        "department": dept,
        "rows": rows,
        "days": days,
        "day_headers": day_headers,
        "month_label": month_label,
        "month": month,
        "year": year,
        "year_filter": year,
        "month_filter": str(month) if month else "",
        "years_available": years_available,
        "prev_month": prev_month,
        "prev_month_year": prev_month_year,
        "next_month": next_month,
        "next_month_year": next_month_year,
        "prev_year": year - 1,
        "next_year": year + 1,
        "today": today,
        "working_days": working_days_total,
        "staff_count": len(staff),
        "dept_total": dept_total,
        "can_upload": _can_upload(request.user),
        "can_view_all": _can_view_all_attendance(request.user),
        "departments": departments,
        "export_label": month_label,
        "view_mode": view_mode,
    })


# ===========================================================================
# Upload
# ===========================================================================
@login_required
def upload_matrix(request):
    if not _can_upload(request.user):
        raise PermissionDenied("Only directors can upload attendance.")

    if request.method == "POST":
        action = request.POST.get("action", "preview")

        if action == "apply":
            form = UploadMatrixForm(request.POST, request.FILES)
            if not form.is_valid():
                messages.error(request, "Please check the form.")
                return redirect("attendance:upload")

            today = timezone.localdate()
            year  = services._valid_year(form.cleaned_data.get("year"),  today.year)
            month = services._valid_month(form.cleaned_data.get("month"), today.month)

            try:
                parsed = services.parse_matrix(
                    form.cleaned_data["file"],
                    month=month,
                    year=year,
                    department=_upload_department(request.user),
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("attendance:upload")

            result = services.apply_matrix(
                parsed, request.user,
                overwrite=form.cleaned_data.get("overwrite", True),
            )
            messages.success(
                request,
                f"Upload applied — {result.created} new, {result.updated} updated, "
                f"{result.skipped} skipped."
            )
            c = result.counts
            if c:
                messages.info(
                    request,
                    "Days recorded: "
                    f"{c.get('present', 0)} present, {c.get('late', 0)} late, "
                    f"{c.get('absent', 0)} absent, {c.get('excused', 0)} excused, "
                    f"{c.get('holiday', 0)} holiday — "
                    f"{result.credited} credited (present, late, excused and holiday "
                    "days all count towards attendance)."
                )
            if result.errors:
                messages.warning(request, f"{len(result.errors)} row(s) had issues.")

            return redirect(
                f"{reverse('attendance:department')}"
                f"?year={parsed.year}&month={parsed.month}"
            )

        form = UploadMatrixForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Please check the form.")
            return redirect("attendance:upload")

        today = timezone.localdate()
        year  = services._valid_year(form.cleaned_data.get("year"),  today.year)
        month = services._valid_month(form.cleaned_data.get("month"), today.month)

        try:
            parsed = services.parse_matrix(
                form.cleaned_data["file"],
                month=month,
                year=year,
                department=_upload_department(request.user),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("attendance:upload")

        return render(request, "attendance/upload.html", {
            "form": form,
            "preview": parsed,
            "preview_mode": True,
            "file_missing": True,
        })

    today = timezone.localdate()
    year  = services._valid_year(request.GET.get("year"),  today.year)
    month = services._valid_month(request.GET.get("month"), today.month)

    form = UploadMatrixForm(initial={
        "year": year,
        "month": month,
        "overwrite": True,
    })
    return render(request, "attendance/upload.html", {
        "form": form,
        "preview": None,
        "preview_mode": False,
    })


# ===========================================================================
# Manual edit
# ===========================================================================
@login_required
@require_POST
def manual_edit(request, pk):
    record = get_object_or_404(AttendanceRecord, pk=pk)

    # Review H4: staff may no longer edit their own records. Only
    # superusers, cross-department attendance viewers, and the record
    # owner's department director/deputy/office heads may.
    allowed = request.user.is_superuser or (
        record.user_id != request.user.pk
        and (
            _can_view_all_attendance(request.user)
            or (
                _is_director(request.user)
                and record.user.department_id == request.user.department_id
            )
        )
    )
    if not allowed:
        raise PermissionDenied("You cannot edit this record.")
    if record.status == AttendanceRecord.ON_LEAVE:
        messages.error(
            request,
            "Approved leave attendance is managed through the leave request. "
            "Use GPS check-in if you worked that day.",
        )
        return redirect(request.META.get("HTTP_REFERER", "attendance:home"))

    form = ManualEditForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect(request.META.get("HTTP_REFERER", "attendance:home"))

    services.mark_status(
        record.user, record.date,
        form.cleaned_data["status"],
        source=AttendanceRecord.SOURCE_MANUAL,
        actor=request.user,
        notes=form.cleaned_data.get("notes") or "",
    )
    messages.success(request, f"Updated {record.user.get_full_name() or record.user.email} · {record.date}.")
    return redirect(request.META.get("HTTP_REFERER", "attendance:home"))


# ===========================================================================
# Exports
# ===========================================================================
def _export_department(request):
    """Department an export is for: the one picked on the report page for
    all-departments viewers, otherwise the user's own. Raises PermissionDenied
    unless the user may view it."""
    from staff.models import Department

    dept = request.user.department
    if _can_view_all_attendance(request.user):
        raw = request.GET.get("department")
        try:
            picked = (Department.objects.filter(pk=int(raw), is_active=True).first()
                      if raw else None)
        except (TypeError, ValueError):
            picked = None
        dept = picked or dept or Department.objects.filter(is_active=True).first()
    if dept is None or not _can_view_department(request.user, dept):
        raise PermissionDenied("You don't have permission to export this department.")
    return dept


def _credited_detail_rows(records):
    """Excused / holiday / approved-leave records (working days that have
    already happened), sorted by person then date, for the detail tables."""
    today = timezone.localdate()
    rows = [
        r for r in records
        if r.status in services.NEUTRAL_STATUSES
        and is_working_day(r.date) and r.date <= today
    ]
    rows.sort(key=lambda r: ((r.user.get_full_name() or r.user.email).lower(), r.date))
    return rows


def _excel_detail_sheet(wb, records, header_fill, header_font, border):
    """Second sheet: excused / holiday / approved-leave stretches, one row per
    unbroken run with the same approver, so the summary numbers can be traced."""
    from openpyxl.styles import Alignment

    ws = wb.create_sheet("Leave, excused & holiday")
    headers = ["Name", "Email", "From", "To", "Days", "Status",
               "Approved / recorded by", "Notes"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.fill = header_fill
        c.font = header_font
        c.border = border
        c.alignment = Alignment(horizontal="left", vertical="center")

    r_idx = 2
    for g in services.group_credited_days(_credited_detail_rows(records)):
        vals = [
            g["user"].get_full_name() or g["user"].email,
            g["user"].email,
            g["start"].strftime("%a %d %b %Y"),
            g["end"].strftime("%a %d %b %Y"),
            g["days"],
            g["status_label"],
            g["authority"],
            g["notes_text"],
        ]
        for col, v in enumerate(vals, start=1):
            c = ws.cell(row=r_idx, column=col, value=v)
            c.border = border
            c.alignment = Alignment(horizontal="left", vertical="center")
        r_idx += 1

    for col, w in zip("ABCDEFGH", (28, 34, 16, 16, 7, 20, 28, 40)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    return ws


@login_required
def export_excel(request):
    """Export the current user's department attendance as .xlsx."""
    dept = _export_department(request)
    today = timezone.localdate()

    year = services._valid_year(request.GET.get("year"), today.year)
    month_raw = request.GET.get("month")
    month = services._valid_month(month_raw, None) if month_raw else None

    if month:
        first_day = date_cls(year, month, 1)
        last_day = date_cls(year, month, calendar.monthrange(year, month)[1])
        period_label = date_cls(year, month, 1).strftime("%B %Y")
    else:
        first_day = date_cls(year, 1, 1)
        last_day = date_cls(year, 12, 31)
        period_label = str(year)

    staff = list(
        User.objects
        .filter(is_active=True, department=dept)
        .order_by("first_name", "last_name", "email")
    )

    # Backfill missing past days before exporting.
    _backfill_absent_range(staff, first_day, last_day, actor=request.user)

    records = services.period_records(staff, first_day, last_day)
    tallies = services.summarize_period(staff, first_day, last_day, records=records)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ValidationError("openpyxl is not installed.")

    wb = Workbook()
    ws = wb.active
    ws.title = f"{dept.code or 'export'}-{year}"[:31]

    header_fill = PatternFill(start_color="1F6B3E", end_color="1F6B3E", fill_type="solid")
    total_fill  = PatternFill(start_color="E8F3EE", end_color="E8F3EE", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    border = Border(
        left=Side(style="thin", color="D9DCE0"),
        right=Side(style="thin", color="D9DCE0"),
        top=Side(style="thin", color="D9DCE0"),
        bottom=Side(style="thin", color="D9DCE0"),
    )
    center = Alignment(horizontal="center", vertical="center")

    headers = ["Email", "Name", "Present", "Late", "Absent", "Excused",
               "Holiday", "On approved leave", "Credited total",
               "Recorded days counted", "Attendance %"]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1,
                value=f"{dept.name} — attendance for {period_label}")
    c.font = Font(bold=True, size=12, color="1F6B3E")
    c.alignment = Alignment(horizontal="left", vertical="center")

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    c = ws.cell(
        row=2, column=1,
        value=("Credited total = Present + Late + Excused + Holiday + On approved leave. "
               "Excused, holiday and approved-leave days count as if present; only "
               "Absent days are not credited. Only days with an attendance record "
               "are counted (no record = not counted). Attendance % = Credited "
               "total / Recorded days counted."),
    )
    c.font = Font(italic=True, size=9, color="64748B")
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 30

    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=i, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="left" if i <= 2 else "center",
                                   vertical="center", wrap_text=True)
        cell.border = border
    ws.row_dimensions[3].height = 32

    def _row_vals(t):
        return [t["present"], t["late"], t["absent"], t["excused"],
                t["holiday"], t["on_leave"], t["credited"], t["expected"],
                t["pct"]]

    r_idx = 4
    for u in staff:
        t = tallies[u.pk]
        row_vals = [u.email, u.get_full_name() or u.email] + _row_vals(t)
        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=r_idx, column=col_idx, value=val)
            cell.border = border
            cell.alignment = (Alignment(horizontal="left", vertical="center")
                              if col_idx <= 2 else center)
        r_idx += 1

    # Department aggregate row.
    agg = services.combine_tallies([tallies[u.pk] for u in staff])
    agg_vals = ["", f"Department total ({len(staff)} staff)"] + _row_vals(agg)
    for col_idx, val in enumerate(agg_vals, start=1):
        cell = ws.cell(row=r_idx, column=col_idx, value=val)
        cell.border = border
        cell.fill = total_fill
        cell.font = Font(bold=True)
        cell.alignment = (Alignment(horizontal="left", vertical="center")
                          if col_idx <= 2 else center)

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 28
    for i in range(3, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    ws.freeze_panes = "A4"

    _excel_detail_sheet(wb, records, header_fill, header_font, border)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"attendance-{dept.code or 'dept'}-{year}"
    if month:
        filename += f"-{month:02d}"
    filename += ".xlsx"

    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@login_required
def export_pdf(request):
    """Export the current user's department attendance as a PDF."""
    dept = _export_department(request)
    today = timezone.localdate()

    year = services._valid_year(request.GET.get("year"), today.year)
    month_raw = request.GET.get("month")
    month = services._valid_month(month_raw, None) if month_raw else None

    if month:
        first_day = date_cls(year, month, 1)
        last_day = date_cls(year, month, calendar.monthrange(year, month)[1])
        period_label = date_cls(year, month, 1).strftime("%B %Y")
        file_month = f"-{month:02d}"
    else:
        first_day = date_cls(year, 1, 1)
        last_day = date_cls(year, 12, 31)
        period_label = str(year)
        file_month = ""

    # Backfill before computing the summary.
    staff_qs = User.objects.filter(is_active=True, department=dept)
    _backfill_absent_range(list(staff_qs), first_day, last_day, actor=request.user)

    summary = services.department_summary(dept, first_day, last_day)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor, white
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        )
        from reportlab.lib.utils import ImageReader
    except ImportError:
        raise ValidationError("ReportLab is not installed.")

    from pathlib import Path
    from django.conf import settings as _s

    def _cfg(key, default=""):
        try:
            import config as _c
            return _c.get(key, default)
        except Exception:
            return default

    def _find_logo():
        candidates = [
            getattr(_s, "PDF_LOGO_PATH", None),
            Path(_s.BASE_DIR) / "static" / "img"      / "NBTE_logo.png",
            Path(_s.BASE_DIR) / "static" / "images"   / "NBTE_logo.png",
            Path(_s.BASE_DIR) / "static" / "branding" / "NBTE_logo.png",
            Path(_s.BASE_DIR) / "static" / "logo.png",
        ]
        for c in candidates:
            if not c:
                continue
            try:
                p = Path(c)
                if p.is_file():
                    return p
            except (TypeError, OSError):
                continue
        return None

    def _transparent_logo(logo_path, opacity=0.06):
        if not logo_path:
            return None
        try:
            from PIL import Image
            img = Image.open(logo_path).convert("RGBA")
            r, g, b, a = img.split()
            a = a.point(lambda v: int(v * opacity))
            img.putalpha(a)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf
        except Exception:
            return None

    GREEN = "#1F6B3E"
    INK   = "#0F172A"
    MUTED = "#64748B"
    BORDER = "#D9DCE0"

    org_name = _cfg("site.organisation_name",
                    "National Board for Technical Education")
    site_name = _cfg("site.department_name", "ICT Directorate")
    contact_addr = _cfg("site.address", "")
    contact_email = _cfg("site.email", "")
    contact_phone = _cfg("site.phone", "")

    logo_path = _find_logo()
    watermark_buf = _transparent_logo(logo_path, opacity=0.06)

    def on_page(canvas, doc):
        canvas.saveState()
        width, height = doc.pagesize

        if watermark_buf is not None:
            try:
                watermark_buf.seek(0)
                wm_size = 12 * cm
                canvas.drawImage(
                    ImageReader(watermark_buf),
                    (width - wm_size) / 2,
                    (height - wm_size) / 2,
                    width=wm_size, height=wm_size, mask="auto",
                )
            except Exception:
                pass

        top_y = height - 1.4 * cm
        if logo_path:
            try:
                canvas.drawImage(
                    ImageReader(str(logo_path)),
                    2 * cm, top_y - 1.5 * cm + 0.15 * cm,
                    width=1.5 * cm, height=1.5 * cm,
                    preserveAspectRatio=True, anchor="sw", mask="auto",
                )
            except Exception:
                pass

        canvas.setFillColor(HexColor(GREEN))
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawRightString(width - 2 * cm, top_y - 0.35 * cm, org_name)

        canvas.setFillColor(HexColor(INK))
        canvas.setFont("Helvetica", 9.5)
        canvas.drawRightString(width - 2 * cm, top_y - 0.85 * cm, site_name)

        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica", 8)
        contact_line = " · ".join([x for x in (contact_addr, contact_email, contact_phone) if x])
        if contact_line:
            canvas.drawRightString(width - 2 * cm, top_y - 1.25 * cm, contact_line)

        rule_y = top_y - 1.55 * cm
        canvas.setStrokeColor(HexColor(GREEN))
        canvas.setLineWidth(1.2)
        canvas.line(2 * cm, rule_y, width - 2 * cm, rule_y)
        canvas.setStrokeColor(HexColor(BORDER))
        canvas.setLineWidth(0.3)
        canvas.line(2 * cm, rule_y - 1.2, width - 2 * cm, rule_y - 1.2)

        canvas.setStrokeColor(HexColor(BORDER))
        canvas.setLineWidth(0.4)
        canvas.line(2 * cm, 1.55 * cm, width - 2 * cm, 1.55 * cm)

        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            2 * cm, 1.15 * cm,
            f"Generated {timezone.localtime().strftime('%b %d, %Y · %H:%M')}",
        )
        canvas.drawCentredString(
            width / 2, 1.15 * cm,
            f"{dept.name} — {period_label}",
        )
        canvas.drawRightString(
            width - 2 * cm, 1.15 * cm,
            f"Page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "RecTitle", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=20, leading=24,
        textColor=HexColor(GREEN), spaceAfter=2,
    )
    ref_style = ParagraphStyle(
        "RecRef", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=HexColor(MUTED), spaceAfter=18,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=HexColor(GREEN), spaceBefore=10, spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8.5, leading=11,
        textColor=HexColor(INK),
    )
    cell_head = ParagraphStyle(
        "CellHead", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8.5, leading=11,
        textColor=white,
    )

    story = []
    story.append(Paragraph("Attendance Report", title_style))
    story.append(Paragraph(f"{dept.name} · {period_label}", ref_style))

    overview = (
        f"<b>Period:</b> {period_label} &nbsp;·&nbsp; "
        f"<b>Working days:</b> {summary['working_days']} &nbsp;·&nbsp; "
        f"<b>Staff:</b> {len(summary['users'])}"
    )
    story.append(Paragraph(overview, cell_style))
    story.append(Spacer(1, 14))

    story.append(Paragraph("Per-staff summary", section_style))

    cell_c = ParagraphStyle("CellC", parent=cell_style, alignment=1)
    head_c = ParagraphStyle("HeadC", parent=cell_head, alignment=1)

    def _num_row(label, r, bold=False):
        def P(txt, st):
            return Paragraph(f"<b>{txt}</b>" if bold else str(txt), st)
        return [
            P(label, cell_style),
            P(r["present"], cell_c), P(r["late"], cell_c), P(r["absent"], cell_c),
            P(r["excused"], cell_c), P(r["holiday"], cell_c),
            P(r["on_leave"], cell_c), P(r["credited"], cell_c),
            P(f"{r['pct']}%", cell_c),
        ]

    rows = [[
        Paragraph("Staff", cell_head),
        Paragraph("Present", head_c),
        Paragraph("Late", head_c),
        Paragraph("Absent", head_c),
        Paragraph("Excused", head_c),
        Paragraph("Holiday", head_c),
        Paragraph("Leave", head_c),
        Paragraph("Credited", head_c),
        Paragraph("Att. %", head_c),
    ]]
    for row in summary["users"]:
        rows.append(_num_row(row["user"].get_full_name() or row["user"].email, row))
    total = summary["total"]
    rows.append(_num_row(f"Department total ({len(summary['users'])})", total, bold=True))

    t = Table(
        rows,
        colWidths=[4.4 * cm, 1.55 * cm, 1.3 * cm, 1.45 * cm, 1.6 * cm,
                   1.55 * cm, 1.3 * cm, 1.75 * cm, 2.1 * cm],
        repeatRows=1,
    )
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), HexColor(GREEN)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.25, HexColor(BORDER)),
        ("ROWBACKGROUNDS",(0, 1), (-1, -2), [white, HexColor("#F8FAFC")]),
        ("BACKGROUND",    (0, -1), (-1, -1), HexColor("#E8F3EE")),
    ]))
    story.append(t)

    story.append(Spacer(1, 12))
    legend = Paragraph(
        "<b>Legend:</b> Present = on time, Late = after grace window, "
        "Absent = no record or marked absent, Excused, Holiday and Leave "
        "(approved leave) = day away that is counted as if present. "
        "<b>Credited</b> = Present + Late + Excused + Holiday + Leave. "
        "<b>Att. %</b> = Credited over the recorded days in the period; only "
        "Absent days lower it. Days with no attendance record are not counted.",
        cell_style,
    )
    story.append(legend)

    groups = summary.get("detail_groups") or []
    if groups:
        story.append(Spacer(1, 16))
        story.append(Paragraph(
            "Excused, holiday and approved-leave days", section_style))
        drows = [[
            Paragraph("Staff", cell_head),
            Paragraph("Dates", cell_head),
            Paragraph("Days", head_c),
            Paragraph("Status", cell_head),
            Paragraph("Approved / recorded by", cell_head),
        ]]
        for g in groups:
            drows.append([
                Paragraph(g["user"].get_full_name() or g["user"].email, cell_style),
                Paragraph(g["dates"], cell_style),
                Paragraph(str(g["days"]), cell_c),
                Paragraph(g["status_label"], cell_style),
                Paragraph(g["authority"] or "—", cell_style),
            ])
        dt = Table(drows, colWidths=[3.9 * cm, 5.3 * cm, 1.2 * cm, 2.9 * cm, 3.7 * cm],
                   repeatRows=1)
        dt.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), HexColor(GREEN)),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID",          (0, 0), (-1, -1), 0.25, HexColor(BORDER)),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [white, HexColor("#F8FAFC")]),
        ]))
        story.append(dt)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=4.2 * cm, bottomMargin=2.2 * cm,
        title=f"{dept.name} attendance — {period_label}",
        author=site_name,
        subject="Attendance report",
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

    filename = f"attendance-{dept.code or 'dept'}-{year}{file_month}.pdf"
    resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


@login_required
def download_sample(request):
    """Generate an Excel template pre-filled with the current department's
    staff and correct day columns."""
    if not _can_upload(request.user):
        raise PermissionDenied("Only directors can download the template.")

    dept = request.user.department
    today = timezone.localdate()

    year  = services._valid_year(request.GET.get("year"),  today.year)
    month = services._valid_month(request.GET.get("month"), today.month)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ValidationError("openpyxl is not installed.")

    wb = Workbook()
    ws = wb.active
    ws.title = f"{dept.code or 'template'}-{year}-{month:02d}"[:31]

    header_fill = PatternFill(start_color="1F6B3E", end_color="1F6B3E", fill_type="solid")
    off_fill    = PatternFill(start_color="94A3B8", end_color="94A3B8", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    muted_font  = Font(color="64748B", size=10, italic=True)
    border = Border(
        left=Side(style="thin", color="D9DCE0"),
        right=Side(style="thin", color="D9DCE0"),
        top=Side(style="thin", color="D9DCE0"),
        bottom=Side(style="thin", color="D9DCE0"),
    )
    center = Alignment(horizontal="center", vertical="center")

    days_in_month = calendar.monthrange(year, month)[1]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=days_in_month + 1)
    c = ws.cell(
        row=1, column=1,
        value=f"{dept.name} — attendance for {date_cls(year, month, 1).strftime('%B %Y')}",
    )
    c.font = Font(bold=True, size=12, color="1F6B3E")
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=days_in_month + 1)
    c = ws.cell(
        row=2, column=1,
        value=("Fill each day cell with 1 (present), L (late), "
               "0 (absent), E (excused), H (holiday). Blank = skip."),
    )
    c.font = muted_font
    ws.row_dimensions[2].height = 18

    cell = ws.cell(row=3, column=1, value="Email")
    cell.fill = header_fill
    cell.font = header_font
    cell.alignment = Alignment(horizontal="left", vertical="center")
    cell.border = border

    for d in range(1, days_in_month + 1):
        day = date_cls(year, month, d)
        cell = ws.cell(row=3, column=d + 1, value=d)
        cell.fill = off_fill if not is_working_day(day) else header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    staff = list(
        User.objects
        .filter(is_active=True, department=dept)
        .order_by("first_name", "last_name", "email")
    )
    for r_idx, u in enumerate(staff, start=4):
        cell = ws.cell(row=r_idx, column=1, value=u.email)
        cell.border = border
        cell.alignment = Alignment(horizontal="left", vertical="center")
        for d in range(1, days_in_month + 1):
            c2 = ws.cell(row=r_idx, column=d + 1, value="")
            c2.border = border
            c2.alignment = center

    ws.column_dimensions["A"].width = 34
    for d in range(1, days_in_month + 1):
        ws.column_dimensions[get_column_letter(d + 1)].width = 4

    ws.freeze_panes = "B4"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"attendance-template-{dept.code or 'dept'}-{year}-{month:02d}.xlsx"
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ===========================================================================
# All-departments report
# ===========================================================================
@login_required
def all_departments_report(request):
    """Cross-department attendance for ES / Super Admin / permission holders."""
    if not _can_view_all_attendance(request.user):
        raise PermissionDenied("You don't have permission to view all departments.")

    from staff.models import Department

    today = timezone.localdate()

    year = services._valid_year(request.GET.get("year"), today.year)
    month_raw = request.GET.get("month")
    month = services._valid_month(month_raw, None) if month_raw else None

    if month:
        first_day = date_cls(year, month, 1)
        last_day = date_cls(year, month, calendar.monthrange(year, month)[1])
        month_label = date_cls(year, month, 1).strftime("%B %Y")
        view_mode = "month"
        if month == 1:
            prev_month_year, prev_month = year - 1, 12
        else:
            prev_month_year, prev_month = year, month - 1
        if month == 12:
            next_month_year, next_month = year + 1, 1
        else:
            next_month_year, next_month = year, month + 1
    else:
        first_day = date_cls(year, 1, 1)
        last_day = date_cls(year, 12, 31)
        month_label = str(year)
        view_mode = "year"
        prev_month_year = prev_month = None
        next_month_year = next_month = None

    departments = list(
        Department.objects.filter(is_active=True).order_by("sort_order", "name")
    )

    all_users = list(
        User.objects
        .filter(is_active=True, department__in=departments)
        .select_related("department", "role")
        .order_by(
            "department__sort_order", "department__name",
            "first_name", "last_name", "email",
        )
    )

    # Auto-mark past working days as absent for everyone in scope.
    _backfill_absent_range(all_users, first_day, last_day, actor=request.user)

    records = services.period_records(all_users, first_day, last_day)

    range_days = [
        first_day + timedelta(days=i)
        for i in range((last_day - first_day).days + 1)
    ]
    working_days_total = sum(1 for d in range_days if is_working_day(d))

    tallies = services.summarize_period(all_users, first_day, last_day, records=records)

    flat = []
    for u in all_users:
        t = tallies[u.pk]
        flat.append({
            "user": u,
            "present": t["present"],
            "late": t["late"],
            "absent": t["absent"],
            "excused": t["excused"],
            "holiday": t["holiday"],
            "on_leave": t["on_leave"],
            "credited": t["credited"],
            "expected": t["expected"],
            "pct": t["pct"],
        })

    grouped = []
    for dept in departments:
        dept_rows = [r for r in flat if r["user"].department_id == dept.pk]
        if not dept_rows:
            continue
        agg = services.combine_tallies([tallies[r["user"].pk] for r in dept_rows])
        grouped.append({
            "department": dept,
            "rows": dept_rows,
            "staff_count": len(dept_rows),
            "aggregate": agg,
            "aggregate_pct": agg["pct"],
        })

    totals = services.combine_tallies(tallies.values())
    totals["staff"] = len(flat)

    years_with_data = list(
        AttendanceRecord.objects
        .filter(user__department__in=departments)
        .dates("date", "year")
    )
    years_available = sorted(
        {d.year for d in years_with_data} | {today.year},
        reverse=True,
    )

    return render(request, "attendance/all.html", {
        "grouped": grouped,
        "departments_count": len(grouped),
        "month_label": month_label,
        "month": month,
        "year": year,
        "year_filter": year,
        "month_filter": str(month) if month else "",
        "years_available": years_available,
        "prev_month": prev_month,
        "prev_month_year": prev_month_year,
        "next_month": next_month,
        "next_month_year": next_month_year,
        "prev_year": year - 1,
        "next_year": year + 1,
        "today": today,
        "working_days": working_days_total,
        "staff_count": totals["staff"],
        "can_upload": _can_upload(request.user),
        "view_mode": view_mode,
        "totals": totals,
        "export_label": month_label,
    })


# ===========================================================================
# Excel export — all departments
# ===========================================================================
@login_required
def export_all_excel(request):
    """Whole-directorate attendance export, grouped by department."""
    if not _can_view_all_attendance(request.user):
        raise PermissionDenied("You don't have permission to export all departments.")

    from staff.models import Department

    today = timezone.localdate()
    year = services._valid_year(request.GET.get("year"), today.year)
    month_raw = request.GET.get("month")
    month = services._valid_month(month_raw, None) if month_raw else None

    if month:
        first_day = date_cls(year, month, 1)
        last_day = date_cls(year, month, calendar.monthrange(year, month)[1])
        period_label = date_cls(year, month, 1).strftime("%B %Y")
        file_month = f"-{month:02d}"
    else:
        first_day = date_cls(year, 1, 1)
        last_day = date_cls(year, 12, 31)
        period_label = str(year)
        file_month = ""

    departments = list(
        Department.objects.filter(is_active=True).order_by("sort_order", "name")
    )
    all_users = list(
        User.objects
        .filter(is_active=True, department__in=departments)
        .select_related("department", "role")
        .order_by(
            "department__sort_order", "department__name",
            "first_name", "last_name", "email",
        )
    )

    # Backfill missing past working days before exporting.
    _backfill_absent_range(all_users, first_day, last_day, actor=request.user)

    records = services.period_records(all_users, first_day, last_day)
    tallies = services.summarize_period(all_users, first_day, last_day, records=records)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    except ImportError:
        raise ValidationError("openpyxl is not installed.")

    wb = Workbook()
    ws = wb.active
    ws.title = f"All depts {year}{file_month}"[:31]

    header_fill = PatternFill(start_color="1F6B3E", end_color="1F6B3E", fill_type="solid")
    group_fill  = PatternFill(start_color="E8F3EE", end_color="E8F3EE", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    group_font  = Font(color="1F6B3E", bold=True, size=11)
    border = Border(
        left=Side(style="thin", color="D9DCE0"),
        right=Side(style="thin", color="D9DCE0"),
        top=Side(style="thin", color="D9DCE0"),
        bottom=Side(style="thin", color="D9DCE0"),
    )
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    headers = ["Department", "Email", "Name",
               "Present", "Late", "Absent", "Excused", "Holiday",
               "On approved leave", "Credited total",
               "Recorded days counted", "Attendance %"]

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    c = ws.cell(row=1, column=1,
                value=f"ICT Directorate — attendance for {period_label}")
    c.font = Font(bold=True, size=12, color="1F6B3E")
    c.alignment = left
    ws.row_dimensions[1].height = 22

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    c = ws.cell(
        row=2, column=1,
        value=("Credited total = Present + Late + Excused + Holiday + On approved leave. "
               "Excused, holiday and approved-leave days count as if present; only "
               "Absent days are not credited. Only days with an attendance record "
               "are counted (no record = not counted). Attendance % = Credited "
               "total / Recorded days counted."),
    )
    c.font = Font(italic=True, size=9, color="64748B")
    c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[2].height = 30

    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=i, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="left" if i <= 3 else "center",
                                   vertical="center", wrap_text=True)
        cell.border = border
    ws.row_dimensions[3].height = 32

    def _nums(t):
        return [t["present"], t["late"], t["absent"], t["excused"],
                t["holiday"], t["on_leave"], t["credited"], t["expected"],
                t["pct"]]

    def _write(r, vals, bold=False, fill=None):
        for col_idx, val in enumerate(vals, start=1):
            cell = ws.cell(row=r, column=col_idx, value=val)
            cell.border = border
            cell.alignment = left if col_idx <= 3 else center
            if bold:
                cell.font = Font(bold=True)
            if fill is not None:
                cell.fill = fill

    r_idx = 4
    for dept in departments:
        dept_users = [u for u in all_users if u.department_id == dept.pk]
        if not dept_users:
            continue

        ws.merge_cells(start_row=r_idx, start_column=1,
                       end_row=r_idx, end_column=len(headers))
        c = ws.cell(row=r_idx, column=1,
                    value=f"{dept.name} · {len(dept_users)} staff")
        c.fill = group_fill
        c.font = group_font
        c.alignment = left
        for col in range(1, len(headers) + 1):
            ws.cell(row=r_idx, column=col).border = border
        r_idx += 1

        for u in dept_users:
            _write(r_idx, [dept.name, u.email, u.get_full_name() or u.email]
                   + _nums(tallies[u.pk]))
            r_idx += 1

        dept_agg = services.combine_tallies([tallies[u.pk] for u in dept_users])
        _write(r_idx, [dept.name, "", f"{dept.name} total"] + _nums(dept_agg),
               bold=True, fill=group_fill)
        r_idx += 1

    grand = services.combine_tallies(tallies.values())
    _write(r_idx, ["All departments", "", f"Directorate total ({len(all_users)} staff)"]
           + _nums(grand), bold=True, fill=group_fill)

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 28
    for col in "DEFGHIJKL":
        ws.column_dimensions[col].width = 14
    ws.freeze_panes = "A4"

    _excel_detail_sheet(wb, records, header_fill, header_font, border)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"attendance-all-departments-{year}{file_month}.xlsx"
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp

# ===========================================================================
# Location codes (privileged)
# ===========================================================================
def _can_view_location_codes(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.has_perm("attendance.view_location_codes")


@login_required
def location_codes_view(request):
    """Live view of every location's current check-in code."""
    if not _can_view_location_codes(request.user):
        raise PermissionDenied("You don't have permission to view location codes.")

    from . import codes as code_service

    now = timezone.now()
    locations = AttendanceLocation.objects.filter(is_active=True).order_by("name")

    rows = []
    for loc in locations:
        row = code_service.get_current_code(loc, now=now)
        rows.append({
            "location": loc,
            "code_row": row,
            "is_open": row is not None,
            "seconds_remaining": row.seconds_remaining if row else 0,
            "window_end": row.window_end if row else None,
        })

    return render(request, "attendance/location_codes.html", {
        "rows": rows,
        "rotation_minutes": code_service.rotation_minutes(),
        "seconds_until_rotation": code_service.seconds_until_next_rotation(now),
        "generated_at": now,
    })


@login_required
@require_GET
def location_codes_api(request):
    """JSON feed of live codes; polled by the codes page."""
    if not _can_view_location_codes(request.user):
        return JsonResponse({"ok": False, "error": "Forbidden."}, status=403)

    from . import codes as code_service

    now = timezone.now()
    locations = AttendanceLocation.objects.filter(is_active=True).order_by("name")

    payload = []
    for loc in locations:
        row = code_service.get_current_code(loc, now=now)
        payload.append({
            "location_id": loc.pk,
            "location": loc.name,
            "code": row.code if row else None,
            "is_open": row is not None,
            "seconds_remaining": row.seconds_remaining if row else 0,
            "window_end_iso": row.window_end.isoformat() if row else None,
        })

    return JsonResponse({
        "ok": True,
        "now": now.isoformat(),
        "seconds_until_rotation": code_service.seconds_until_next_rotation(now),
        "codes": payload,
    })


# ===========================================================================
# Manual check-in (code-based)
# ===========================================================================
@login_required
def check_in_manual(request):
    """Staff enter the rotating office code to record attendance."""
    from . import codes as code_service
    from .forms import ManualCodeCheckInForm

    today = timezone.localdate()

    # Same rule as GPS check-in: no attendance on a weekend, off day or holiday.
    blocked = services.non_working_reason(today, request.user)
    if blocked:
        messages.info(request, blocked)
        return redirect("attendance:home")

    if not code_service.manual_checkin_enabled():
        messages.info(
            request,
            "Manual check-in is currently disabled. Please use GPS check-in.",
        )
        return redirect("attendance:check_in")

    existing = AttendanceRecord.objects.filter(
        user=request.user, date=today,
    ).first()
    if existing is not None and existing.status != AttendanceRecord.ON_LEAVE:
        messages.info(request, "You already have an attendance record for today.")
        return redirect("attendance:home")

    # Early exit for approved leave — friendlier than letting mark_status
    # raise. Covers the case where the record was created by the leave
    # sync *after* the check above but before the POST is processed.
    if existing is not None and existing.status == AttendanceRecord.ON_LEAVE:
        messages.error(
            request,
            "You can't sign attendance because you're on approved leave today.",
        )
        return redirect("attendance:home")

    if request.method == "POST":
        form = ManualCodeCheckInForm(request.POST)
        if not form.is_valid():
            for err in form.errors.get("code", []):
                messages.error(request, err)
        else:
            try:
                location, code_row = code_service.verify_check_in_code(
                    request.user, form.cleaned_data["code"],
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                location = None

            if location is not None:
                status = services._status_for_check_in(
                    location, timezone.localtime(),
                )
                try:
                    services.mark_status(
                        request.user, today, status,
                        source=AttendanceRecord.SOURCE_MANUAL,
                        actor=request.user,
                        location=location,
                        check_in_at=timezone.now(),
                        notes=f"Manual check-in via location code · {location.name}",
                    )
                except ValidationError as exc:
                    # mark_status refuses to touch a day that's flagged
                    # ON_LEAVE. Translate that into plain English.
                    msgs = getattr(exc, "messages", None) or [str(exc)]
                    joined = " ".join(msgs)
                    if "approved leave" in joined.lower():
                        messages.error(
                            request,
                            "You can't sign attendance because you're on "
                            "approved leave today.",
                        )
                    else:
                        messages.error(request, joined)
                    return redirect("attendance:home")

                messages.success(
                    request,
                    f"Checked in via {location.name}. "
                    + ("You're on time." if status == AttendanceRecord.PRESENT
                       else "Recorded as late."),
                )
                return redirect("attendance:home")
            elif form.is_valid():
                # Invalid code, form itself was fine.
                messages.error(
                    request,
                    "That code isn't valid right now. Ask the office contact "
                    "for the current code and try again.",
                )
    else:
        form = ManualCodeCheckInForm()

    return render(request, "attendance/check_in_manual.html", {
        "form": form,
        "today": today,
        "attempts_remaining": code_service.attempts_remaining(request.user),
        "rotation_minutes": code_service.rotation_minutes(),
    })