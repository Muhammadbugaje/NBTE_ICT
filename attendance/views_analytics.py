"""Attendance analytics pages (v2.1 Phase D, G5, G6, G8). Rules and figures
live in attendance/analytics.py; this module only renders them."""
import io

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from accounts.models import User

from . import analytics as _svc


def _context(request):
    scope, departments = _svc.scope_departments(request.user)
    if scope == "none":
        raise PermissionDenied("Attendance analytics are for HR, the ES and directors (their own department).")
    first, last = _svc.parse_period(request.GET)
    dept_id = None
    raw = request.GET.get("department") or ""
    if raw.isdigit() and departments.filter(pk=int(raw)).exists():
        dept_id = int(raw)
    users = _svc.users_in(departments, dept_id)
    data = _svc.compute(users, first, last)
    people = data["people"]
    q = (request.GET.get("q") or "").strip().lower()
    listed = [p for p in people if not q or q in p.name.lower() or q in (p.user.email or "").lower()]
    sort = request.GET.get("sort") or "name"
    keys = {
        "name": lambda p: p.name.lower(),
        "late": lambda p: (-p.late, p.name.lower()),
        "absent": lambda p: (-p.absent, p.name.lower()),
        "arrival": lambda p: (p.avg_arrival if p.avg_arrival is not None else 10 ** 6, p.name.lower()),
        "attendance": lambda p: (p.attendance_pct if p.counted else 101, p.name.lower()),
        "missing": lambda p: (-p.missing_checkout, p.name.lower()),
    }
    listed.sort(key=keys.get(sort, keys["name"]))
    return {
        "scope": scope, "departments": departments, "department_id": dept_id, "first": first, "last": last,
        "people": listed, "all_people": people, "totals": data["totals"], "weeks": data["weeks"],
        "boards": _svc.leaderboards(people), "dept_rows": _svc.department_rows(people)
        if scope == "all" and not dept_id else [],
        "q": request.GET.get("q") or "", "sort": sort,
        "chart": {
            "labels": [w["label"] for w in data["weeks"]],
            "present": [w["present"] for w in data["weeks"]],
            "late": [w["late"] for w in data["weeks"]],
            "absent": [w["absent"] for w in data["weeks"]],
            "away": [w["on_leave"] + w["on_duty"] + w["holiday"] + w["excused"] for w in data["weeks"]],
            "pct": [w["attendance_pct"] for w in data["weeks"]],
        },
    }


@login_required
def analytics_page(request):
    ctx = _context(request)
    if request.headers.get("HX-Request") == "true":
        return render(request, "attendance/_analytics_body.html", ctx)
    return render(request, "attendance/analytics.html", ctx)



@login_required
def person(request, user_id):
    person_user = get_object_or_404(User.objects.select_related("department"), pk=user_id, is_active=True)
    scope, _deps = _svc.scope_departments(request.user)
    if not _svc.can_view_person(request.user, person_user):
        raise PermissionDenied("You can only look at people in the departments you may see.")
    first, last = _svc.parse_period(request.GET)
    stats = _svc.compute([person_user], first, last)["people"][0]
    days = _svc.person_days(person_user, first, last)
    ctx = {"p": stats, "person": person_user, "days": days, "first": first, "last": last, "scope": scope}
    if request.headers.get("HX-Request") == "true":
        return render(request, "attendance/_analytics_person.html", ctx)
    return render(request, "attendance/analytics_person.html", ctx)


@login_required
def export(request):
    """Excel of the analytics table (G6: every status, check-out columns)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    ctx = _context(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Analytics"
    ws.append([f"Attendance analytics {ctx['first']:%d %b %Y} – {ctx['last']:%d %b %Y}"])
    ws.append([])
    head = ["Name", "Email", "Department", "Counted days", "Present", "Late", "Absent", "Excused", "Holiday",
            "On leave", "On official duty", "Missing check-out", "Early arrivals", "Average arrival",
            "Average departure", "Punctuality %", "Attendance %"]
    ws.append(head)
    for c in ws[3]:
        c.font = Font(bold=True)
    for p in ctx["people"]:
        ws.append([p.name, p.user.email, getattr(p.department, "name", ""), p.counted, p.present, p.late,
                   p.absent, p.excused, p.holiday, p.on_leave, p.on_duty, p.missing_checkout, p.early,
                   p.avg_arrival_label, p.avg_departure_label, p.punctuality_pct, p.attendance_pct])
    ws2 = wb.create_sheet("Weeks")
    ws2.append(["Week of", "Present", "Late", "Absent", "On leave", "On official duty", "Holiday", "Excused",
                "Early", "Counted", "Attendance %"])
    for w in ctx["weeks"]:
        ws2.append([w["week"], w["present"], w["late"], w["absent"], w["on_leave"], w["on_duty"], w["holiday"],
                    w["excused"], w["early"], w["counted"], w["attendance_pct"]])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="attendance_analytics_{ctx["first"]:%Y%m%d}_{ctx["last"]:%Y%m%d}.xlsx"'
    return resp
