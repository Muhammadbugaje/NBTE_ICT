"""Data gathering for role dashboards.

Every external read is wrapped in `_safe()` so that a module that isn't
installed (or a schema that differs from expectation) degrades to an
empty state instead of 500ing the page.
"""
from datetime import timedelta

from django.apps import apps
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify


# ---------------------------------------------------------------------------
# Safe accessor + empty shapes
# ---------------------------------------------------------------------------
def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _chart_empty():
    return {"labels": [], "series": []}


def _donut_empty():
    return {"labels": [], "values": [], "colors": []}


# ---------------------------------------------------------------------------
# Workflow internals — mirror workflow.services's private step keys
# ---------------------------------------------------------------------------
def _step_key(step):
    if step.pk:
        return f"db:{step.pk}"
    return f"syn:{slugify(step.name)}"


def _current_step_of(request):
    from workflow.services import build_runtime_steps

    key = (request.metadata or {}).get("current_step_key")
    if not key:
        return None
    for s in build_runtime_steps(request):
        if _step_key(s) == key:
            return s
    return None


def _age_days(ts):
    if not ts:
        return None
    return max(0, (timezone.now() - ts).days)


def _age_bucket(days):
    if days is None:
        return "fresh"
    if days <= 1:
        return "fresh"
    if days <= 7:
        return "warm"
    if days <= 30:
        return "aging"
    return "stale"


# ---------------------------------------------------------------------------
# Cross-module counts
# ---------------------------------------------------------------------------
def _staff_total():
    # Phase 8: Staff has no `is_active` field; the old filter raised a
    # FieldError that _safe() turned into a permanent 0.
    from . import counts
    return _safe(lambda: counts.active_staff().count(), 0)


def _users_total():
    from . import counts
    return _safe(lambda: counts.active_users().count(), 0)


def _staff_by_department():
    def _q():
        from django.db.models import Count, Q as _Q
        from staff.models import Department
        rows = (Department.objects.filter(is_active=True)
                .annotate(n=Count("staff", filter=_Q(staff__is_deleted=False))).order_by("name"))
        return [{"department": d, "count": d.n} for d in rows if d.n]
    return _safe(_q, []) or []


def _unread_notifications(user):
    def _q():
        from notifications.models import Notification
        return Notification.objects.filter(recipient=user, is_read=False).count()
    return _safe(_q, 0)


def _leadership_current():
    def _q():
        from staff.models import ExecutivePosition
        rows = []
        for p in ExecutivePosition.objects.filter(is_active=True).order_by("order", "name"):
            t = p.current_tenure
            if t:
                rows.append({"position": p, "tenure": t})
        return rows
    return _safe(_q, []) or []


def _requests(user=None):
    """Requests a dashboard figure may count or list.

    `user=None` keeps the old system-wide behaviour (chairman / super-admin
    boards). The director, deputy and ES boards pass the viewer, so they only
    ever count and list what that person may see (workflow/access.py): a
    director sees their own department, only holders of "view all requests"
    (Executive Secretary by default) see everything."""
    from workflow.models import Request
    if user is None:
        return Request.objects.all()
    from workflow.access import visible_requests_queryset
    return visible_requests_queryset(user)


# ---------------------------------------------------------------------------
# Workflow KPIs
# ---------------------------------------------------------------------------
def _workflow_kpis(user=None):
    from workflow.models import Request

    from . import counts

    # Phase 8: "In flight" used to count drafts and everything still pending
    # approval too, so it never matched the "In flight" bars or the office
    # table. It now means approved and being worked on, like they do.
    pending_statuses = counts.pending_statuses()
    in_flight_statuses = counts.in_flight_statuses()

    def _q():
        return {
            "pending":   _requests(user).filter(status__in=pending_statuses).count(),
            "in_flight": _requests(user).filter(status__in=in_flight_statuses).count(),
            "completed_30d": _requests(user).filter(
                status=Request.COMPLETED,
                completed_at__gte=timezone.now() - timedelta(days=30),
            ).count(),
            "rejected_30d": _requests(user).filter(
                status=Request.REJECTED,
                closed_at__gte=timezone.now() - timedelta(days=30),
            ).count(),
            "aged": _aged_approval_count(7, user),
        }

    return _safe(_q, {
        "pending": 0, "in_flight": 0, "completed_30d": 0,
        "rejected_30d": 0, "aged": 0,
    }) or {
        "pending": 0, "in_flight": 0, "completed_30d": 0,
        "rejected_30d": 0, "aged": 0,
    }


def _aged_approval_count(days=7, user=None):
    from workflow.models import Request
    threshold = timezone.now() - timedelta(days=days)
    return _requests(user).filter(
        status__in=[Request.AWAITING, Request.AWAITING_RECEIVING],
        current_step_started_at__lt=threshold,
    ).count()


def _parked_requests(cap=10):
    from workflow.models import Request
    from workflow.services import resolve_approver

    rows = (
        Request.objects
        .filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])
        .select_related("requester", "request_type", "receiving_department")
        .order_by("current_step_started_at")[:200]
    )
    result = []
    for r in rows:
        step = _current_step_of(r)
        if step is None:
            continue
        if resolve_approver(step, r) is None:
            result.append({
                "request": r,
                "step": step,
                "age_days": _age_days(r.current_step_started_at),
            })
            if len(result) >= cap:
                break
    return result


# ---------------------------------------------------------------------------
# Chart builders — workflow
# ---------------------------------------------------------------------------
def _activity_series(days=30, user=None):
    from workflow.models import Request

    end = timezone.localdate()
    start = end - timedelta(days=days - 1)

    opened = list(_requests(user).filter(
        created_at__date__gte=start,
    ).values_list("created_at__date", flat=True))
    completed = list(_requests(user).filter(
        completed_at__date__gte=start,
    ).values_list("completed_at__date", flat=True))

    open_counts, done_counts = {}, {}
    for d in opened:
        open_counts[d] = open_counts.get(d, 0) + 1
    for d in completed:
        done_counts[d] = done_counts.get(d, 0) + 1

    labels, ov, dv = [], [], []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append(f"{d.strftime('%b')} {d.day}")   # ← Windows-safe
        ov.append(open_counts.get(d, 0))
        dv.append(done_counts.get(d, 0))

    return {
        "labels": labels,
        "series": [
            {"name": "Opened",    "values": ov, "color": "#1F6B3E"},
            {"name": "Completed", "values": dv, "color": "#0369A1"},
        ],
    }


def _status_mix(user=None):
    from workflow.models import Request

    palette = {
        Request.AWAITING:             ("Awaiting approval",   "#F59E0B"),
        Request.AWAITING_RECEIVING:   ("Awaiting receiving",  "#0369A1"),
        Request.IN_PROGRESS:          ("In progress",         "#0F766E"),
        Request.AWAITING_FULFILLMENT: ("Awaiting fulfilment", "#7E22CE"),
        Request.READY:                ("Ready for handover",  "#4F46E5"),
        Request.COMPLETED:            ("Completed",           "#1F6B3E"),
        Request.REJECTED:             ("Rejected",            "#DC2626"),
        Request.RETURNED:             ("Returned",            "#BE185D"),
        Request.CANCELLED:            ("Cancelled",           "#94A3B8"),
    }
    rows = _requests(user).values("status").annotate(n=Count("id"))
    labels, values, colors = [], [], []
    for r in rows:
        label, colour = palette.get(r["status"], (r["status"], "#94A3B8"))
        labels.append(label)
        values.append(r["n"])
        colors.append(colour)
    return {"labels": labels, "values": values, "colors": colors}


def _categories_series(days=30, user=None):
    from workflow.models import Request, RequestCategory

    since = timezone.now() - timedelta(days=days)
    labels, pending, inflight, done = [], [], [], []

    # v2.2: one grouped query instead of three counts per category.
    from django.db.models import Count, Q

    rows = (_requests(user).filter(created_at__gte=since, request_type__category__is_active=True)
            .values("request_type__category")
            .annotate(p=Count("pk", filter=Q(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])),
                      i=Count("pk", filter=Q(status__in=[Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT,
                                                         Request.READY])),
                      d=Count("pk", filter=Q(status=Request.COMPLETED))))
    counts = {r["request_type__category"]: (r["p"], r["i"], r["d"]) for r in rows}
    for c in RequestCategory.objects.filter(is_active=True).order_by("sort_order", "name"):
        p, i, d = counts.get(c.pk, (0, 0, 0))
        if p + i + d == 0:
            continue
        labels.append(c.name)
        pending.append(p)
        inflight.append(i)
        done.append(d)

    return {
        "labels": labels,
        "series": [
            {"name": "Pending",   "values": pending,  "color": "#F59E0B"},
            {"name": "In flight", "values": inflight, "color": "#0369A1"},
            {"name": "Completed", "values": done,     "color": "#1F6B3E"},
        ],
    }


def _approvals_series(weeks=12, user=None):
    from workflow.models import RequestApproval

    end = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(weeks=weeks)

    rows = RequestApproval.objects.filter(
        decided_at__gte=start, decision="approve",
        request__in=_requests(user).values("pk"),
    ).values_list("decided_at", flat=True)

    buckets = {}
    for ts in rows:
        wk = ts.date() - timedelta(days=ts.weekday())
        buckets[wk] = buckets.get(wk, 0) + 1

    labels, values = [], []
    cursor = start.date() - timedelta(days=start.weekday())
    for _ in range(weeks):
        labels.append(f"W{cursor.isocalendar().week}")
        values.append(buckets.get(cursor, 0))
        cursor += timedelta(weeks=1)

    return {"labels": labels, "series": [{"name": "Approvals", "values": values, "color": "#1F6B3E"}]}


def _dept_activity_chart(days=30, user=None):
    """v2.2: one grouped query instead of three counts per department (N+1)."""
    from django.db.models import Count, Q

    from workflow.models import Request
    from staff.models import Department

    since = timezone.now() - timedelta(days=days)
    labels, pending, inflight, done = [], [], [], []
    rows = (_requests(user).filter(created_at__gte=since, receiving_department__is_active=True)
            .order_by().values("receiving_department_id")
            .annotate(p=Count("pk", filter=Q(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])),
                      i=Count("pk", filter=Q(status__in=[Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT,
                                                         Request.READY])),
                      c=Count("pk", filter=Q(status=Request.COMPLETED))))
    by_dept = {r["receiving_department_id"]: r for r in rows}
    for d in Department.objects.filter(is_active=True, pk__in=list(by_dept)).order_by("name"):
        r = by_dept[d.pk]
        if r["p"] + r["i"] + r["c"] == 0:
            continue
        labels.append(d.name)
        pending.append(r["p"])
        inflight.append(r["i"])
        done.append(r["c"])

    return {
        "labels": labels,
        "series": [
            {"name": "Pending",   "values": pending,  "color": "#F59E0B"},
            {"name": "In flight", "values": inflight, "color": "#0369A1"},
            {"name": "Completed", "values": done,     "color": "#1F6B3E"},
        ],
    }


# ---------------------------------------------------------------------------
# Attendance dashboard (scoped + headcount based)
#
# Only real attendance RECORDS are counted (same rule as the reports):
#   Came     = Present + Late                         (people who actually came)
#   Counted  = Came + Excused + Holiday + On leave    (credited, like reports)
#   Absent   = records marked absent
# A day or a person with no record is simply not part of the calculation, so
# a system that only started recently is not diluted by days it never saw, and
# a weekend never shows up as a 0% day. The one exception is the "today"
# snapshot, which shows how many of the current staff headcount have checked
# in so far (the rest are listed as "not in yet").
#
# Scope (attendance/access.py): everyone with all-departments access sees the
# whole directorate; a director / department-permission holder sees ONLY their
# own department; anyone else gets no attendance section at all.
# ---------------------------------------------------------------------------
def _attendance_hidden():
    return {
        "attendance_visible": False,
        "attendance_scope": "none",
        "attendance_link": "",
        "attendance_title": "",
        "attendance_kpis": {},
        "attendance_charts": {"trend": _chart_empty(), "by_dept": _chart_empty()},
        "attendance_depts": [],
        "attendance_total": {},
    }


def _pct(part, whole, places=0):
    if not whole:
        return 0
    value = (part / whole) * 100
    return round(value, places) if places else round(value)


def _attendance_dashboard(user, days=30):
    from accounts.models import User
    from attendance import access, services as att
    from attendance.models import AttendanceRecord
    from django.urls import reverse
    from staff.models import Department

    scope = access.attendance_scope(user)
    if scope == "all":
        depts = list(Department.objects.filter(is_active=True).order_by("sort_order", "name"))
    elif scope == "department" and user.department_id:
        depts = list(Department.objects.filter(pk=user.department_id))
    else:
        depts = []
    if not depts:
        return _attendance_hidden()

    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    dept_ids = [d.pk for d in depts]

    staff = list(User.objects.filter(is_active=True, department_id__in=dept_ids))
    staff_by_dept = {}
    for u in staff:
        staff_by_dept.setdefault(u.department_id, []).append(u)

    status_of = {}
    for uid, d, st in (
        AttendanceRecord.objects
        .filter(user__is_active=True, user__department_id__in=dept_ids,
                date__gte=start, date__lte=today)
        .values_list("user_id", "date", "status")
    ):
        status_of[(uid, d)] = st

    working = [
        start + timedelta(days=i) for i in range(days)
        if att.is_working_day(start + timedelta(days=i))
    ]

    keys = ("present", "late", "excused", "holiday", "on_leave", "absent")

    def counts_for(users, d):
        """Status counts for `users` on day `d`, from attendance records only.
        `headcount` is the number of people who HAVE a record that day."""
        c = {k: 0 for k in keys}
        c["headcount"] = 0
        c["unrecorded"] = 0
        for u in users:
            st = status_of.get((u.pk, d))
            if st in c:
                c[st] += 1
                c["headcount"] += 1
            else:
                c["unrecorded"] += 1
        return c

    def came(c):
        return c["present"] + c["late"]

    def away(c):
        return c["excused"] + c["holiday"] + c["on_leave"]

    # ---- Today's snapshot ------------------------------------------------
    reason = att.non_working_reason(today)
    today_working = att.is_working_day(today)
    t = {k: 0 for k in keys}
    t["headcount"] = t["unrecorded"] = 0
    for d in depts:
        c = counts_for(staff_by_dept.get(d.pk, []), today)
        for k in t:
            t[k] += c[k]
    # Today only: headcount = current staff (people not in yet are listed).
    t["headcount"] = len(staff)
    kpis = {
        **{k: t[k] for k in keys},
        "unrecorded": t["unrecorded"],
        "total_staff": len(staff),
        "is_working_day": today_working and not reason,
        "reason": reason,
        "came": came(t),
        "away": t["excused"] + t["on_leave"],
        "credited": came(t) + away(t),
    }
    if kpis["is_working_day"] and t["headcount"]:
        kpis["attendance_pct"] = _pct(came(t), t["headcount"])
        kpis["credited_pct"] = _pct(kpis["credited"], t["headcount"])
    else:
        kpis["attendance_pct"] = None
        kpis["credited_pct"] = None

    # ---- Completed working days (today excluded: it is still in progress) --
    done = [d for d in working if d < today]
    per_dept_day = {}
    for dep in depts:
        users = staff_by_dept.get(dep.pk, [])
        for d in done:
            per_dept_day[(dep.pk, d)] = counts_for(users, d)

    labels, came_pct, counted_pct = [], [], []
    for d in done:
        hc = sum(per_dept_day[(dep.pk, d)]["headcount"] for dep in depts)
        if not hc:
            continue
        cm = sum(came(per_dept_day[(dep.pk, d)]) for dep in depts)
        aw = sum(away(per_dept_day[(dep.pk, d)]) for dep in depts)
        labels.append(f"{d.strftime('%b')} {d.day}")
        came_pct.append(_pct(cm, hc))
        counted_pct.append(_pct(cm + aw, hc))

    trend = _chart_empty()
    if labels:
        trend = {
            "labels": labels,
            "series": [
                {"name": "Came to work (present + late) %", "values": came_pct,
                 "color": "#1F6B3E", "fill": True},
                {"name": "Counted incl. leave, excused, holiday %",
                 "values": counted_pct, "color": "#6366F1", "fill": False},
            ],
        }

    # ---- Per department over the window ----------------------------------
    chart_labels, p_present, p_late, p_away, p_absent = [], [], [], [], []
    rows = []
    total = {k: 0 for k in keys}
    total["expected"] = 0
    for dep in depts:
        users = staff_by_dept.get(dep.pk, [])
        agg = {k: 0 for k in keys}
        expected = 0
        for d in done:
            c = per_dept_day[(dep.pk, d)]
            expected += c["headcount"]
            for k in keys:
                agg[k] += c[k]
        if not users or not expected:
            continue
        pr = _pct(agg["present"], expected, 1)
        lt = _pct(agg["late"], expected, 1)
        aw = _pct(away(agg), expected, 1)
        ab = max(0.0, round(100 - pr - lt - aw, 1))
        chart_labels.append(dep.name)
        p_present.append(pr)
        p_late.append(lt)
        p_away.append(aw)
        p_absent.append(ab)
        rows.append({
            "department": dep,
            "staff": len(users),
            "present": agg["present"], "late": agg["late"],
            "absent": agg["absent"], "excused": agg["excused"],
            "holiday": agg["holiday"], "on_leave": agg["on_leave"],
            "away": away(agg),
            "expected": expected,
            "came_pct": _pct(came(agg), expected),
            "pct": _pct(came(agg) + away(agg), expected),
        })
        for k in keys:
            total[k] += agg[k]
        total["expected"] += expected

    by_dept = _chart_empty()
    if chart_labels:
        by_dept = {
            "labels": chart_labels,
            "series": [
                {"name": "Present %", "values": p_present, "color": "#10B981"},
                {"name": "Late %", "values": p_late, "color": "#F59E0B"},
                {"name": "Excused / leave / holiday %", "values": p_away, "color": "#6366F1"},
                {"name": "Absent %", "values": p_absent, "color": "#EF4444"},
            ],
        }

    total_row = {}
    if total["expected"]:
        total_row = {
            **total,
            "staff": len(staff),
            "away": away(total),
            "came_pct": _pct(came(total), total["expected"]),
            "pct": _pct(came(total) + away(total), total["expected"]),
        }

    return {
        "attendance_visible": True,
        "attendance_scope": scope,
        "attendance_link": reverse(
            "attendance:all_departments" if scope == "all" else "attendance:department"
        ),
        "attendance_title": (
            "Across the directorate" if scope == "all" else f"{depts[0].name} only"
        ),
        "attendance_kpis": kpis,
        "attendance_charts": {"trend": trend, "by_dept": by_dept},
        "attendance_depts": rows,
        "attendance_total": total_row,
        "attendance_window_days": days,
    }


# ---------------------------------------------------------------------------
# Queues
# ---------------------------------------------------------------------------
def _queue_for_user(user, cap=20):
    from workflow.models import Request
    from workflow.services import resolve_approver

    rows = (
        Request.objects
        .filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])
        .select_related("requester", "request_type", "receiving_department",
                        "request_type__category")
        .order_by("current_step_started_at")[:250]
    )
    result = []
    for r in rows:
        step = _current_step_of(r)
        if not step:
            continue
        actor = resolve_approver(step, r)
        if actor and actor.pk == user.pk:
            age = _age_days(r.current_step_started_at)
            result.append({
                "request": r, "step": step,
                "age_days": age, "age_bucket": _age_bucket(age),
            })
            if len(result) >= cap:
                break
    return result


def _queue_at_step_kind(kind, cap=20):
    from workflow.models import Request

    rows = (
        Request.objects
        .filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])
        .select_related("requester", "request_type", "receiving_department")
        .order_by("current_step_started_at")[:300]
    )
    result = []
    for r in rows:
        step = _current_step_of(r)
        if step and step.kind == kind:
            age = _age_days(r.current_step_started_at)
            result.append({"request": r, "step": step, "age_days": age,
                           "age_bucket": _age_bucket(age)})
            if len(result) >= cap:
                break
    return result


def _queue_es_signoff(cap=20):
    # Phase 8: a direct query on the current step (was: scan the oldest 300
    # waiting requests in Python, so anything past them was never counted).
    from . import counts

    rows = (counts.es_signoff()
            .select_related("requester", "request_type", "receiving_department")
            .order_by("current_step_started_at")[:cap])
    result = []
    for r in rows:
        step = _current_step_of(r)
        age = _age_days(r.current_step_started_at)
        result.append({"request": r, "step": step, "age_days": age, "age_bucket": _age_bucket(age)})
    return result


def _queue_filing(cap=20):
    # Phase 8: the "Awaiting filing" queue. It used to look for requests at a
    # *closure* step among those awaiting approval, but a closure step always
    # carries the "ready for handover" status, so it was always empty. Filing
    # is the "Records filing" gate a request type adds.
    from . import counts

    rows = (counts.awaiting_filing()
            .select_related("requester", "request_type", "receiving_department")
            .order_by("current_step_started_at")[:cap])
    result = []
    for r in rows:
        step = _current_step_of(r)
        age = _age_days(r.current_step_started_at)
        result.append({"request": r, "step": step, "age_days": age, "age_bucket": _age_bucket(age)})
    return result


def _aged_approvals(days=7, cap=10, user=None):
    from workflow.models import Request

    threshold = timezone.now() - timedelta(days=days)
    rows = (
        _requests(user)
        .filter(
            status__in=[Request.AWAITING, Request.AWAITING_RECEIVING],
            current_step_started_at__lt=threshold,
        )
        .select_related("requester", "request_type", "receiving_department")
        .order_by("current_step_started_at")[:cap]
    )
    return [{"request": r, "age_days": _age_days(r.current_step_started_at)} for r in rows]


def _office_breakdown(user=None):
    """v2.2: one grouped query (was three counts per department)."""
    from django.db.models import Count, Q

    from workflow.models import Request
    from staff.models import Department

    since = timezone.now() - timedelta(days=30)
    agg = (_requests(user).order_by().values("receiving_department_id")
           .annotate(p=Count("pk", filter=Q(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])),
                     i=Count("pk", filter=Q(status__in=[Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT,
                                                        Request.READY])),
                     c=Count("pk", filter=Q(status=Request.COMPLETED, completed_at__gte=since))))
    by_dept = {r["receiving_department_id"]: r for r in agg}
    rows = []
    for d in Department.objects.filter(is_active=True, pk__in=list(by_dept)).order_by("name"):
        r = by_dept[d.pk]
        if r["p"] + r["i"] + r["c"] == 0:
            continue
        rows.append({"department": d, "pending": r["p"], "in_flight": r["i"], "completed": r["c"],
                     "total_30d": r["p"] + r["i"] + r["c"]})
    return rows


def _recent_activity(cap=10, user=None):
    from workflow.models import RequestActivity
    return list(
        RequestActivity.objects
        .filter(request__in=_requests(user).values("pk"))
        .select_related("request", "actor", "request__request_type")
        .order_by("-created_at")[:cap]
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def _audit_model():
    for label in ("core.AuditLogEntry", "audit.AuditLogEntry", "core.AuditLog"):
        try:
            return apps.get_model(label)
        except LookupError:
            continue
    return None


def _normalize_audit_row(entry):
    when = None
    for name in ("created_at", "timestamp", "when", "time"):
        when = getattr(entry, name, None)
        if when is not None:
            break

    actor = None
    for name in ("actor", "user", "performed_by", "changed_by"):
        actor = getattr(entry, name, None)
        if actor is not None:
            break

    actor_name = "system"
    actor_dept = ""
    if actor is not None:
        actor_name = actor.get_full_name() or actor.email or str(actor)
        dept = getattr(actor, "department", None)
        if dept is not None:
            actor_dept = getattr(dept, "name", "")

    action = ""
    for name in ("action", "verb", "event", "kind"):
        val = getattr(entry, name, None)
        if val:
            action = str(val)
            break

    summary = ""
    for name in ("object_repr", "message", "note", "summary", "description"):
        val = getattr(entry, name, None)
        if val:
            summary = str(val)
            break

    detail = ""
    note = getattr(entry, "note", None)
    if note and str(note) != summary:
        detail = str(note)
    else:
        changes = getattr(entry, "changes", None)
        if changes:
            try:
                detail = f"changes: {changes}"
            except Exception:
                detail = ""

    return {
        "when":       when,
        "actor_name": actor_name,
        "actor_dept": actor_dept,
        "summary":    summary or "—",
        "detail":     detail,
        "action":     action or "—",
        "object_id":  getattr(entry, "object_id", None),
        "raw":        entry,
    }


def audit_queryset(*, user=None, department=None, action=None, actor=None,
                    since=None, search=None):
    model = _audit_model()
    if model is None:
        return None

    qs = model._default_manager.all()
    field_names = {f.name for f in model._meta.get_fields()}
    actor_field = next((n for n in ("actor", "user", "performed_by") if n in field_names), None)
    action_field = next((n for n in ("action", "verb", "event", "kind") if n in field_names), None)
    ts_field = next((n for n in ("created_at", "timestamp", "when", "time") if n in field_names), None)

    if actor and actor_field:
        qs = qs.filter(**{f"{actor_field}_id": actor.pk})
    if action and action_field:
        qs = qs.filter(**{f"{action_field}__iexact": action})
    if since and ts_field:
        qs = qs.filter(**{f"{ts_field}__gte": since})
    if department and actor_field:
        qs = qs.filter(**{f"{actor_field}__department": department})
    if search:
        for n in ("object_repr", "note", "message", "changes"):
            if n in field_names:
                qs = qs.filter(**{f"{n}__icontains": search})
                break
    if ts_field:
        qs = qs.order_by(f"-{ts_field}", "-pk")
    return qs.select_related(actor_field) if actor_field else qs


def audit_page(*, user, page=1, per_page=40, **filters):
    qs = audit_queryset(user=user, **filters)
    if qs is None:
        return [], 0, 1
    total = qs.count()
    page_count = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, page_count))
    offset = (page - 1) * per_page
    rows = [_normalize_audit_row(e) for e in qs[offset:offset + per_page]]
    return rows, total, page_count


def recent_audit_critical(cap=8):
    model = _audit_model()
    if model is None:
        return []
    qs = model._default_manager.all()
    field_names = {f.name for f in model._meta.get_fields()}
    action_field = next((n for n in ("action", "verb", "event", "kind") if n in field_names), None)
    ts_field = next((n for n in ("created_at", "timestamp", "when", "time") if n in field_names), None)
    if action_field:
        qs = qs.filter(**{f"{action_field}__in": [
            "delete", "state_change", "override", "permission_change",
            "role_change", "login_failed",
        ]})
    if ts_field:
        qs = qs.order_by(f"-{ts_field}", "-pk")
    if "actor" in field_names:
        qs = qs.select_related("actor", "actor__department")   # v2.2: no query per row
    return [_normalize_audit_row(e) for e in qs[:cap]]


# ---------------------------------------------------------------------------
# System health + user role breakdown
# ---------------------------------------------------------------------------
def _system_health_cached():
    """v2.2: the health probe (DB, cache, disk and a Celery ping that can wait
    1.5 s) is shared for 60 s instead of running on every dashboard load."""
    from django.core.cache import cache

    hit = cache.get("dash:system_health")
    if hit is None:
        hit = system_health()
        cache.set("dash:system_health", hit, 60)
    return hit


def system_health():
    import shutil
    from pathlib import Path

    from django.conf import settings
    from django.core.cache import cache
    from django.db import connection

    checks = {"database": "down", "cache": "down",
              "media": "missing", "disk_percent": None, "disk_free_gb": None,
              "debug": bool(getattr(settings, "DEBUG", False))}

    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        pass

    try:
        cache.set("__superadmin_probe", "1", 5)
        checks["cache"] = "ok" if cache.get("__superadmin_probe") == "1" else "down"
    except Exception:
        pass

    try:
        media_root = Path(settings.MEDIA_ROOT)
        checks["media"] = "ok" if media_root.exists() and media_root.is_dir() else "missing"
        root = media_root if media_root.exists() else Path(settings.BASE_DIR)
        usage = shutil.disk_usage(root)
        checks["disk_percent"] = round(usage.used * 100 / usage.total, 1)
        checks["disk_free_gb"] = round(usage.free / 1_000_000_000, 1)
    except Exception:
        pass

    try:
        from config.celery import app as celery_app
        insp = celery_app.control.inspect(timeout=1.5)
        active = insp.active() if insp else None
        checks["celery"] = "ok" if active is not None else "unknown"
    except Exception:
        checks["celery"] = "unknown"

    return checks


def user_role_breakdown():
    # v2.2: one grouped query instead of a count per role.
    from django.db.models import Count, Q

    from accounts.models import Role
    roles = (Role.objects.annotate(n=Count("users", filter=Q(users__is_active=True)))
             .filter(n__gt=0).order_by("-rank", "name"))
    return [{"role": r, "count": r.n} for r in roles]


# ---------------------------------------------------------------------------
# Attendance + workflow sub-dicts (shared by every context)
# ---------------------------------------------------------------------------
def _attendance_block(user):
    return _safe(lambda: _attendance_dashboard(user), None) or _attendance_hidden()


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------
def director_context(user):
    att = _attendance_block(user)
    kpis = _workflow_kpis(user)
    return {
        "kpis": {
            **kpis,
            "staff_total":    _staff_total(),
            "staff_on_leave": att["attendance_kpis"].get("away", 0),
            "unread":         _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(lambda: _activity_series(user=user), _chart_empty()) or _chart_empty(),
            "status_mix": _safe(lambda: _status_mix(user=user), _donut_empty()) or _donut_empty(),
            "categories": _safe(lambda: _categories_series(user=user), _chart_empty()) or _chart_empty(),
            "approvals":  _safe(lambda: _approvals_series(user=user), _chart_empty()) or _chart_empty(),
            "depts":      _safe(lambda: _dept_activity_chart(user=user), _chart_empty()) or _chart_empty(),
        },
        "queue":            _safe(lambda: _queue_for_user(user, cap=15), []) or [],
        "aged_approvals":   _safe(lambda: _aged_approvals(7, 8, user), []) or [],
        "office_breakdown": _safe(lambda: _office_breakdown(user), []) or [],
        "recent_activity":  _safe(lambda: _recent_activity(8, user), []) or [],
        **att,
    }


def deputy_context(user):
    att = _attendance_block(user)
    kpis = _workflow_kpis(user)
    return {
        "kpis": {
            **kpis,
            "staff_total":    _staff_total(),
            "present_today":  att["attendance_kpis"].get("came", 0),
            "unread":         _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(lambda: _activity_series(user=user), _chart_empty()) or _chart_empty(),
            "status_mix": _safe(lambda: _status_mix(user=user), _donut_empty()) or _donut_empty(),
            "approvals":  _safe(lambda: _approvals_series(user=user), _chart_empty()) or _chart_empty(),
            "depts":      _safe(lambda: _dept_activity_chart(user=user), _chart_empty()) or _chart_empty(),
        },
        "queue":            _safe(lambda: _queue_for_user(user, cap=25), []) or [],
        "aged_approvals":   _safe(lambda: _aged_approvals(3, 12, user), []) or [],
        "office_breakdown": _safe(lambda: _office_breakdown(user), []) or [],
        **att,
    }


def es_context(user):
    att = _attendance_block(user)
    from . import counts
    kpis = _workflow_kpis(user)
    return {
        "kpis": {
            **kpis,
            "es_pending":   _safe(lambda: counts.es_signoff().count(), 0),
            "filing_queue": _safe(lambda: counts.awaiting_filing().count(), 0),
            "unread":       _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(lambda: _activity_series(user=user), _chart_empty()) or _chart_empty(),
            "status_mix": _safe(lambda: _status_mix(user=user), _donut_empty()) or _donut_empty(),
            "approvals":  _safe(lambda: _approvals_series(user=user), _chart_empty()) or _chart_empty(),
        },
        "queue":         _safe(lambda: _queue_es_signoff(cap=20), []) or [],
        "queue_filing":  _safe(lambda: _queue_filing(cap=20), []) or [],
        "aged_approvals": _safe(lambda: _aged_approvals(5, 10, user), []) or [],
        "leadership":    _leadership_current(),
        **att,
    }


def chairman_context(user):
    att = _attendance_block(user)
    kpis = _workflow_kpis()
    return {
        "kpis": {
            **kpis,
            "staff_total":    _staff_total(),
            "users_total":    _users_total(),
            "present_today":  att["attendance_kpis"].get("came", 0),
        },
        "charts": {
            "activity":   _safe(_activity_series, _chart_empty()) or _chart_empty(),
            "status_mix": _safe(_status_mix, _donut_empty()) or _donut_empty(),
            "depts":      _safe(_dept_activity_chart, _chart_empty()) or _chart_empty(),
            "approvals":  _safe(_approvals_series, _chart_empty()) or _chart_empty(),
        },
        "dept_summary": _safe(_staff_by_department, []) or [],
        "leadership":   _leadership_current(),
        **att,
    }


def superadmin_context(user):
    att = _attendance_block(user)
    kpis = _workflow_kpis()
    return {
        "kpis": {
            **kpis,
            "users_total":   _users_total(),
            "staff_total":   _staff_total(),
            "unread":        _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(_activity_series, _chart_empty()) or _chart_empty(),
            "status_mix": _safe(_status_mix, _donut_empty()) or _donut_empty(),
            "depts":      _safe(_dept_activity_chart, _chart_empty()) or _chart_empty(),
            "categories": _safe(_categories_series, _chart_empty()) or _chart_empty(),
        },
        "health":           _safe(_system_health_cached, {}) or {},
        "parked":           _safe(lambda: _parked_requests(cap=10), []) or [],
        "aged_approvals":   _safe(lambda: _aged_approvals(7, 10), []) or [],
        "critical_audit":   _safe(lambda: recent_audit_critical(cap=8), []) or [],
        "role_breakdown":   _safe(user_role_breakdown, []) or [],
        "office_breakdown": _safe(_office_breakdown, []) or [],
        **att,
    }


# ---------------------------------------------------------------------------
# Audit log page
# ---------------------------------------------------------------------------
def audit_log_context(user, *, page=1, action=None, actor_id=None,
                       department_id=None, days=None, search=None):
    from accounts.models import User as _User
    from staff.models import Department

    department = None
    actor = None

    if department_id and (user.is_superuser or user.has_perm("accounts.access_all_departments")):
        department = Department.objects.filter(pk=department_id).first()
    if actor_id and user.is_superuser:
        actor = _User.objects.filter(pk=actor_id).first()

    since = None
    if days:
        try:
            since = timezone.now() - timedelta(days=int(days))
        except (TypeError, ValueError):
            since = None

    if not user.is_superuser and not user.has_perm("accounts.access_all_departments"):
        # Phase B (B10): a director sees their own department's entries;
        # everyone else only their own actions.
        import org
        if user.department_id and org.is_director(user, user.department_id):
            department = user.department
        else:
            actor = user

    rows, total, page_count = audit_page(
        user=user, page=page, per_page=40,
        actor=actor, department=department,
        action=action, since=since, search=search,
    )

    actions = []
    model = _audit_model()
    if model is not None:
        field_names = {f.name for f in model._meta.get_fields()}
        action_field = next((n for n in ("action", "verb", "event", "kind") if n in field_names), None)
        if action_field:
            actions = list(
                model._default_manager
                .values_list(action_field, flat=True)
                .distinct()
                .order_by(action_field)
            )

    return {
        "rows": rows,
        "total": total,
        "page": page,
        "page_count": page_count,
        "filters": {
            "action": action or "",
            "actor_id": actor_id or "",
            "department_id": department_id or "",
            "days": days or "",
            "search": search or "",
        },
        "actions": actions,
        "departments": Department.objects.filter(is_active=True).order_by("name"),
        "can_filter_actor": user.is_superuser,
        "can_filter_department": user.is_superuser or user.has_perm("accounts.access_all_departments"),
        "audit_available": model is not None,
    }


def self_service_context(user):
    """Build a personal dashboard from records owned or assigned to the user."""
    from django.urls import NoReverseMatch, reverse

    def module_url(app_label, url_name):
        if not apps.is_installed(app_label):
            return None
        try:
            return reverse(url_name)
        except NoReverseMatch:
            return None

    from staff.models import Staff

    staff = (
        Staff.objects
        .filter(user=user, is_deleted=False)
        .select_related("department", "state")
        .first()
    )

    widgets = []
    leave_data = None
    tickets_url = module_url("tickets", "tickets:my_tickets")
    if tickets_url:
        from tickets.models import Ticket

        tickets = Ticket.objects.filter(raised_by=user)
        widgets.append({
            "key": "tickets",
            "title": "My tickets",
            "url": tickets_url,
            "count": tickets.filter(state__in=Ticket.OPEN_STATES).count(),  # same rule as counts.open_tickets
            "count_label": "open",
            "items": list(
                tickets.select_related("category")
                .order_by("-updated_at")[:4]
            ),
        })

    tasks_url = module_url("tasks", "tasks:assigned_to_me")
    if tasks_url:
        from . import counts

        # Phase 8: same rule as the home work board — drafts and tasks where
        # this user's own part is done or declined are not "open" for them.
        tasks = counts.my_open_tasks(user).select_related("category")
        widgets.append({
            "key": "tasks",
            "title": "My tasks",
            "url": tasks_url,
            "count": tasks.count(),
            "count_label": "open",
            "items": list(tasks.order_by("due_date", "-updated_at")[:4]),
        })

    requests_url = module_url("workflow", "workflow:my_requests")
    if requests_url:
        from workflow.models import Request

        requests = (
            Request.objects
            .filter(requester=user)
            .select_related("request_type")
        )
        if apps.is_installed("leave"):
            from leave.services import dashboard_data
            leave_data = dashboard_data(user)
        widgets.append({
            "key": "requests",
            "title": "My leave & requests",
            "url": requests_url,
            "count": requests.exclude(status__in=Request.CLOSED_STATUSES).count(),
            "count_label": "active",
            "leave": leave_data,
            "items": list(requests.order_by("-updated_at")[:4]),
        })

    assets_url = module_url("assets", "assets:my_assets")
    if assets_url:
        from assets.models import AssetAssignment

        assignments = (
            AssetAssignment.objects
            .filter(assigned_to=user, returned_at__isnull=True)
            .select_related("asset", "asset__category")
        )
        widgets.append({
            "key": "assets",
            "title": "My assets",
            "url": assets_url,
            "count": assignments.count(),
            "count_label": "in your custody",
            "items": list(assignments[:4]),
        })

    pending_approvals = []
    if apps.is_installed("workflow"):
        from workflow import services as workflow_services
        from workflow.models import Request

        approvals = (
            Request.objects
            .filter(status__in=(Request.AWAITING, Request.AWAITING_RECEIVING))
            .select_related(
                "requester", "request_type", "receiving_department", "current_step"
            )
            .order_by("-created_at")
        )
        for approval in approvals:
            step = workflow_services._resolve_current_step(approval)
            if step is None:
                continue
            approver = workflow_services.resolve_approver(step, approval)
            if approver and approver.pk == user.pk:
                approval.active_step = step
                pending_approvals.append(approval)

    return {
        "staff_profile": staff,
        "widgets": widgets,
        "leave_data": leave_data,
        "pending_approvals": pending_approvals[:5],
    }