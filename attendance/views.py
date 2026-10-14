"""Attendance views — personal, department, upload, export."""
import calendar
import io
import json
from datetime import date as date_cls, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from django.urls import reverse

from accounts.models import User

from . import services
from .forms import ManualEditForm, ReportFilterForm, UploadMatrixForm
from .models import AttendanceLocation, AttendanceRecord
from .services import is_working_day

PER_PAGE = 30


# ===========================================================================
# Permission helpers
# ===========================================================================
def _is_director(user):
    return bool(
        user.is_authenticated
        and user.department_id
        and user.role_id
        and user.role.code in (
            "director", "deputy-director",
            "office-head-admin", "office-head-ts", "office-head-dev",
        )
    )


def _can_view_all_attendance(user):
    """Cross-department attendance access.

    Only:
      - superusers
      - the Super Admin role
      - the Executive Secretary role
      - anyone explicitly granted `accounts.access_all_departments`

    A plain Director or Deputy Director is NOT included — they see only
    their own department. To grant one of them cross-department access,
    give them the `accounts.access_all_departments` permission.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.role_id and user.role.code in {"super-admin", "executive-secretary"}:
        return True
    return user.has_perm("accounts.access_all_departments")


def _can_view_department(user, department):
    if user.is_superuser:
        return True
    if _can_view_all_attendance(user):
        return department is not None
    if not user.department_id or department is None:
        return False
    if user.department_id != getattr(department, "pk", department):
        return False
    return _is_director(user)


def _can_upload(user):
    return user.is_superuser or _is_director(user)


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

    to_create = []
    for uid in user_ids:
        jd = joined.get(uid)
        for d in working_days:
            if (uid, d) in existing:
                continue
            if jd is not None and d < jd:
                continue
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
    is_working = services.is_working_day(today)

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
    is_working = services.is_working_day(today)
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

    lat = data.get("lat")
    lng = data.get("lng")
    if lat is None or lng is None:
        return JsonResponse({"ok": False, "error": "Missing coordinates."}, status=400)

    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid coordinates."}, status=400)

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
        .select_related("location")
        .order_by("-date")
    )

    if status:
        qs = qs.filter(status=status)

    # ---- Pagination ---------------------------------------------------------
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get("page") or 1)

    # ---- 90-day stats (always "last 90 days") ------------------------------
    ninety_days_ago = today - timedelta(days=90)
    recent = AttendanceRecord.objects.filter(
        user=user, date__gte=ninety_days_ago, date__lte=today,
    )
    present = recent.filter(status=AttendanceRecord.PRESENT).count()
    late    = recent.filter(status=AttendanceRecord.LATE).count()
    absent  = recent.filter(status=AttendanceRecord.ABSENT).count()
    excused = recent.filter(status=AttendanceRecord.EXCUSED).count()
    on_leave = recent.filter(status=AttendanceRecord.ON_LEAVE).count()
    counted = present + late + absent + excused
    present_pct = round((present / counted) * 100) if counted else 0

    stats = {
        "present": present,
        "late": late,
        "absent": absent,
        "excused": excused,
        "on_leave": on_leave,
        "present_pct": present_pct,
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

    records = (
        AttendanceRecord.objects
        .filter(user__in=staff, date__gte=first_day, date__lte=last_day)
        .select_related("location")
    )

    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, {})[r.date.day] = r

    range_days = [
        first_day + timedelta(days=i)
        for i in range((last_day - first_day).days + 1)
    ]
    working_days_total = sum(1 for d in range_days if is_working_day(d))

    # ---- Rows ---------------------------------------------------------------
    rows = []
    for u in staff:
        user_records = by_user.get(u.pk, {})

        present = late = absent = excused = holiday = on_leave = 0
        for rec in user_records.values():
            if rec.status == AttendanceRecord.PRESENT:
                present += 1
            elif rec.status == AttendanceRecord.LATE:
                late += 1
            elif rec.status == AttendanceRecord.ABSENT:
                absent += 1
            elif rec.status == AttendanceRecord.EXCUSED:
                excused += 1
            elif rec.status == AttendanceRecord.HOLIDAY:
                holiday += 1
            elif rec.status == AttendanceRecord.ON_LEAVE:
                on_leave += 1

        cells = []
        if month:
            days_in_month = calendar.monthrange(year, month)[1]
            for d in range(1, days_in_month + 1):
                day = date_cls(year, month, d)
                rec = user_records.get(d)
                if rec is None:
                    cells.append({
                        "date": day,
                        "record": None,
                        "status": "" if is_working_day(day) else "off",
                    })
                else:
                    cells.append({
                        "date": day,
                        "record": rec,
                        "status": rec.status,
                    })

        total_expected = working_days_total - holiday - on_leave or 1
        pct = round(((present + late) / total_expected) * 100) if total_expected else 0

        rows.append({
            "user": u,
            "cells": cells,
            "present": present,
            "late": late,
            "absent": absent,
            "excused": excused,
            "holiday": holiday,
            "on_leave": on_leave,
            "pct": pct,
        })

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

    allowed = (
        request.user.is_superuser
        or record.user_id == request.user.pk
        or (
            _is_director(request.user)
            and record.user.department_id == request.user.department_id
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
@login_required
def export_excel(request):
    """Export the current user's department attendance as .xlsx."""
    if not _can_view_department(request.user, request.user.department):
        raise PermissionDenied("Only department directors can export attendance.")

    dept = request.user.department
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

    records = (
        AttendanceRecord.objects
        .filter(user__in=staff, date__gte=first_day, date__lte=last_day)
        .select_related("user", "location")
    )

    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, {})[r.date] = r

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
    header_font = Font(color="FFFFFF", bold=True, size=11)
    border = Border(
        left=Side(style="thin", color="D9DCE0"),
        right=Side(style="thin", color="D9DCE0"),
        top=Side(style="thin", color="D9DCE0"),
        bottom=Side(style="thin", color="D9DCE0"),
    )
    center = Alignment(horizontal="center", vertical="center")

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
    c = ws.cell(row=1, column=1,
                value=f"{dept.name} — attendance for {period_label}")
    c.font = Font(bold=True, size=12, color="1F6B3E")
    c.alignment = Alignment(horizontal="left", vertical="center")

    headers = ["Email", "Name", "Present", "Late", "Absent", "Attendance %"]
    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=i, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = border

    for r_idx, u in enumerate(staff, start=4):
        user_recs = by_user.get(u.pk, {})
        present = late = absent = 0
        for rec in user_recs.values():
            if rec.status == AttendanceRecord.PRESENT:
                present += 1
            elif rec.status == AttendanceRecord.LATE:
                late += 1
            elif rec.status == AttendanceRecord.ABSENT:
                absent += 1

        total = present + late + absent or 1
        pct = round(((present + late) / total) * 100)

        row_vals = [
            u.email,
            u.get_full_name() or u.email,
            present, late, absent, pct,
        ]
        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=r_idx, column=col_idx, value=val)
            cell.border = border
            cell.alignment = Alignment(horizontal="left", vertical="center")

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 28
    for col in "CDEF":
        ws.column_dimensions[col].width = 12

    ws.freeze_panes = "A4"

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
    if not _can_view_department(request.user, request.user.department):
        raise PermissionDenied("Only department directors can export.")

    dept = request.user.department
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

    rows = [[
        Paragraph("Staff", cell_head),
        Paragraph("Present", cell_head),
        Paragraph("Late", cell_head),
        Paragraph("Absent", cell_head),
        Paragraph("Attendance %", cell_head),
    ]]
    for row in summary["users"]:
        rows.append([
            Paragraph(row["user"].get_full_name() or row["user"].email, cell_style),
            Paragraph(str(row["present"]), cell_style),
            Paragraph(str(row["late"]), cell_style),
            Paragraph(str(row["absent"]), cell_style),
            Paragraph(f"{row['pct']}%", cell_style),
        ])

    t = Table(rows, colWidths=[7.2 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm, 3.5 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), HexColor(GREEN)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("ALIGN",         (1, 1), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID",          (0, 0), (-1, -1), 0.25, HexColor(BORDER)),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [white, HexColor("#F8FAFC")]),
    ]))
    story.append(t)

    story.append(Spacer(1, 18))
    legend = Paragraph(
        "<b>Legend:</b> Present = on time, Late = after grace window, "
        "Absent = no record or marked absent. "
        "Attendance % is presence over the working days in the period, "
        "excluding holidays.",
        cell_style,
    )
    story.append(legend)

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

    records = (
        AttendanceRecord.objects
        .filter(user__in=all_users, date__gte=first_day, date__lte=last_day)
        .select_related("location")
    )
    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, {})[r.date] = r

    range_days = [
        first_day + timedelta(days=i)
        for i in range((last_day - first_day).days + 1)
    ]
    working_days_total = sum(1 for d in range_days if is_working_day(d))

    flat = []
    for u in all_users:
        user_recs = by_user.get(u.pk, {})
        present = late = absent = excused = holiday = on_leave = 0
        for rec in user_recs.values():
            s = rec.status
            if s == AttendanceRecord.PRESENT:
                present += 1
            elif s == AttendanceRecord.LATE:
                late += 1
            elif s == AttendanceRecord.ABSENT:
                absent += 1
            elif s == AttendanceRecord.EXCUSED:
                excused += 1
            elif s == AttendanceRecord.HOLIDAY:
                holiday += 1
            elif s == AttendanceRecord.ON_LEAVE:
                on_leave += 1
        total_expected = working_days_total - holiday - on_leave or 1
        pct = round(((present + late) / total_expected) * 100) if total_expected else 0
        flat.append({
            "user": u,
            "present": present,
            "late": late,
            "absent": absent,
            "excused": excused,
            "holiday": holiday,
            "on_leave": on_leave,
            "pct": pct,
        })

    grouped = []
    for dept in departments:
        dept_rows = [r for r in flat if r["user"].department_id == dept.pk]
        if not dept_rows:
            continue
        agg = {
            "present": sum(r["present"] for r in dept_rows),
            "late":    sum(r["late"]    for r in dept_rows),
            "absent":  sum(r["absent"]  for r in dept_rows),
            "excused": sum(r["excused"] for r in dept_rows),
            "holiday": sum(r["holiday"] for r in dept_rows),
            "on_leave": sum(r["on_leave"] for r in dept_rows),
        }
        denom = agg["present"] + agg["late"] + agg["absent"] + agg["excused"] or 1
        agg_pct = round(((agg["present"] + agg["late"]) / denom) * 100)
        grouped.append({
            "department": dept,
            "rows": dept_rows,
            "staff_count": len(dept_rows),
            "aggregate": agg,
            "aggregate_pct": agg_pct,
        })

    totals = {
        "staff":   len(flat),
        "present": sum(r["present"] for r in flat),
        "late":    sum(r["late"]    for r in flat),
        "absent":  sum(r["absent"]  for r in flat),
        "excused": sum(r["excused"] for r in flat),
        "on_leave": sum(r["on_leave"] for r in flat),
    }
    grand_denom = totals["present"] + totals["late"] + totals["absent"] + totals["excused"] or 1
    totals["pct"] = round(((totals["present"] + totals["late"]) / grand_denom) * 100)

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

    records = (
        AttendanceRecord.objects
        .filter(user__in=all_users, date__gte=first_day, date__lte=last_day)
    )
    by_user = {}
    for r in records:
        by_user.setdefault(r.user_id, {})[r.date] = r

    range_days = [
        first_day + timedelta(days=i)
        for i in range((last_day - first_day).days + 1)
    ]
    working_days_total = sum(1 for d in range_days if is_working_day(d))

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

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=9)
    c = ws.cell(row=1, column=1,
                value=f"ICT Directorate — attendance for {period_label}")
    c.font = Font(bold=True, size=12, color="1F6B3E")
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 22

    headers = ["Department", "Email", "Name",
               "Present", "Late", "Absent", "Excused", "On leave", "Attendance %"]
    for i, h in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=i, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = border

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
        c.alignment = Alignment(horizontal="left", vertical="center")
        for col in range(1, len(headers) + 1):
            ws.cell(row=r_idx, column=col).border = border
        r_idx += 1

        for u in dept_users:
            user_recs = by_user.get(u.pk, {})
            present = late = absent = excused = on_leave = 0
            for rec in user_recs.values():
                s = rec.status
                if s == AttendanceRecord.PRESENT:
                    present += 1
                elif s == AttendanceRecord.LATE:
                    late += 1
                elif s == AttendanceRecord.ABSENT:
                    absent += 1
                elif s == AttendanceRecord.EXCUSED:
                    excused += 1
                elif s == AttendanceRecord.ON_LEAVE:
                    on_leave += 1
            pct = round(((present + late) / (working_days_total - on_leave or 1)) * 100)

            row_vals = [
                dept.name, u.email, u.get_full_name() or u.email,
                present, late, absent, excused, on_leave, pct,
            ]
            for col_idx, val in enumerate(row_vals, start=1):
                cell = ws.cell(row=r_idx, column=col_idx, value=val)
                cell.border = border
                if col_idx >= 4:
                    cell.alignment = center
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
            r_idx += 1

    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 26
    for col in "DEFGH":
        ws.column_dimensions[col].width = 12
    ws.freeze_panes = "A4"

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