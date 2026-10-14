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
    def _q():
        from staff.models import Staff
        return Staff.objects.filter(is_active=True).count()
    return _safe(_q, 0)


def _users_total():
    def _q():
        from accounts.models import User
        return User.objects.filter(is_active=True).count()
    return _safe(_q, 0)


def _staff_by_department():
    def _q():
        from staff.models import Department, Staff
        return [
            {"department": d, "count": n}
            for d in Department.objects.filter(is_active=True).order_by("name")
            for n in [Staff.objects.filter(is_active=True, department=d).count()]
            if n
        ]
    return _safe(_q, []) or []


def _attendance_excused_today():
    def _q():
        from attendance.models import AttendanceRecord
        today = timezone.localdate()
        return AttendanceRecord.objects.filter(
            date=today, status=AttendanceRecord.EXCUSED,
        ).count()
    return _safe(_q, 0)


def _attendance_present_today():
    def _q():
        from attendance.models import AttendanceRecord
        today = timezone.localdate()
        return AttendanceRecord.objects.filter(
            date=today,
            status__in=[AttendanceRecord.PRESENT, AttendanceRecord.LATE],
        ).count()
    return _safe(_q, 0)


def _attendance_absent_today():
    def _q():
        from attendance.models import AttendanceRecord
        today = timezone.localdate()
        return AttendanceRecord.objects.filter(
            date=today, status=AttendanceRecord.ABSENT,
        ).count()
    return _safe(_q, 0)


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


# ---------------------------------------------------------------------------
# Workflow KPIs
# ---------------------------------------------------------------------------
def _workflow_kpis():
    from workflow.models import Request

    pending_statuses = [Request.AWAITING, Request.AWAITING_RECEIVING]
    open_statuses = [
        Request.DRAFT, Request.AWAITING, Request.AWAITING_RECEIVING,
        Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT,
        Request.RETURNED, Request.READY,
    ]

    def _q():
        return {
            "pending":   Request.objects.filter(status__in=pending_statuses).count(),
            "in_flight": Request.objects.filter(status__in=open_statuses).count(),
            "completed_30d": Request.objects.filter(
                status=Request.COMPLETED,
                completed_at__gte=timezone.now() - timedelta(days=30),
            ).count(),
            "rejected_30d": Request.objects.filter(
                status=Request.REJECTED,
                closed_at__gte=timezone.now() - timedelta(days=30),
            ).count(),
            "aged": _aged_approval_count(7),
        }

    return _safe(_q, {
        "pending": 0, "in_flight": 0, "completed_30d": 0,
        "rejected_30d": 0, "aged": 0,
    }) or {
        "pending": 0, "in_flight": 0, "completed_30d": 0,
        "rejected_30d": 0, "aged": 0,
    }


def _aged_approval_count(days=7):
    from workflow.models import Request
    threshold = timezone.now() - timedelta(days=days)
    return Request.objects.filter(
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
def _activity_series(days=30):
    from workflow.models import Request

    end = timezone.localdate()
    start = end - timedelta(days=days - 1)

    opened = list(Request.objects.filter(
        created_at__date__gte=start,
    ).values_list("created_at__date", flat=True))
    completed = list(Request.objects.filter(
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


def _status_mix():
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
    rows = Request.objects.values("status").annotate(n=Count("id"))
    labels, values, colors = [], [], []
    for r in rows:
        label, colour = palette.get(r["status"], (r["status"], "#94A3B8"))
        labels.append(label)
        values.append(r["n"])
        colors.append(colour)
    return {"labels": labels, "values": values, "colors": colors}


def _categories_series(days=30):
    from workflow.models import Request, RequestCategory

    since = timezone.now() - timedelta(days=days)
    labels, pending, inflight, done = [], [], [], []

    for c in RequestCategory.objects.filter(is_active=True).order_by("sort_order", "name"):
        base = Request.objects.filter(request_type__category=c, created_at__gte=since)
        p = base.filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING]).count()
        i = base.filter(status__in=[
            Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT, Request.READY,
        ]).count()
        d = base.filter(status=Request.COMPLETED).count()
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


def _approvals_series(weeks=12):
    from workflow.models import RequestApproval

    end = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(weeks=weeks)

    rows = RequestApproval.objects.filter(
        decided_at__gte=start, decision="approve",
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


def _dept_activity_chart(days=30):
    from workflow.models import Request
    from staff.models import Department

    since = timezone.now() - timedelta(days=days)
    labels, pending, inflight, done = [], [], [], []

    for d in Department.objects.filter(is_active=True).order_by("name"):
        base = Request.objects.filter(receiving_department=d, created_at__gte=since)
        p = base.filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING]).count()
        i = base.filter(status__in=[
            Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT, Request.READY,
        ]).count()
        c = base.filter(status=Request.COMPLETED).count()
        if p + i + c == 0:
            continue
        labels.append(d.name)
        pending.append(p)
        inflight.append(i)
        done.append(c)

    return {
        "labels": labels,
        "series": [
            {"name": "Pending",   "values": pending,  "color": "#F59E0B"},
            {"name": "In flight", "values": inflight, "color": "#0369A1"},
            {"name": "Completed", "values": done,     "color": "#1F6B3E"},
        ],
    }


# ---------------------------------------------------------------------------
# Attendance chart builders
# ---------------------------------------------------------------------------
def _attendance_today_kpis():
    """Today's attendance counts, plus total staff and unrecorded."""
    from attendance.models import AttendanceRecord
    from accounts.models import User

    today = timezone.localdate()

    def _q():
        qs = AttendanceRecord.objects.filter(date=today)
        return {
            "present": qs.filter(status=AttendanceRecord.PRESENT).count(),
            "late":    qs.filter(status=AttendanceRecord.LATE).count(),
            "absent":  qs.filter(status=AttendanceRecord.ABSENT).count(),
            "excused": qs.filter(status=AttendanceRecord.EXCUSED).count(),
        }

    stats = _safe(_q) or {"present": 0, "late": 0, "absent": 0, "excused": 0}

    total_staff = _safe(
        lambda: User.objects.filter(is_active=True, department__isnull=False).count(),
        0,
    ) or 0

    recorded = stats["present"] + stats["late"] + stats["absent"] + stats["excused"]
    stats["total_staff"] = total_staff
    stats["unrecorded"] = max(0, total_staff - recorded)
    stats["attendance_pct"] = round(
        ((stats["present"] + stats["late"]) / (recorded or 1)) * 100,
    )
    return stats


def _attendance_trend(days=30):
    """Line: attendance % per day over the last N calendar days."""
    from attendance.models import AttendanceRecord

    def _q():
        end = timezone.localdate()
        start = end - timedelta(days=days - 1)

        rows = (
            AttendanceRecord.objects
            .filter(date__gte=start, date__lte=end)
            .values_list("date", "status")
        )

        buckets = {}
        for d, status in rows:
            b = buckets.setdefault(d, {
                "present": 0, "late": 0, "absent": 0,
                "excused": 0, "holiday": 0,
            })
            b[status] = b.get(status, 0) + 1

        labels, pct = [], []
        for i in range(days):
            d = start + timedelta(days=i)
            labels.append(f"{d.strftime('%b')} {d.day}")   # ← Windows-safe
            b = buckets.get(d, {})
            p = b.get("present", 0) + b.get("late", 0)
            total = p + b.get("absent", 0) + b.get("excused", 0)
            pct.append(round((p / total) * 100) if total else 0)

        return {
            "labels": labels,
            "series": [{
                "name": "Attendance %", "values": pct,
                "color": "#1F6B3E", "fill": True,
            }],
        }

    return _safe(_q, _chart_empty()) or _chart_empty()


def _attendance_by_department(days=30):
    """Stacked bar: per department, present / late / absent / excused."""
    from attendance.models import AttendanceRecord
    from staff.models import Department

    def _q():
        since = timezone.localdate() - timedelta(days=days - 1)
        labels, present, late, absent, excused = [], [], [], [], []

        for d in Department.objects.filter(is_active=True).order_by("name"):
            qs = AttendanceRecord.objects.filter(user__department=d, date__gte=since)
            if not qs.exists():
                continue
            labels.append(d.name)
            present.append(qs.filter(status=AttendanceRecord.PRESENT).count())
            late.append(qs.filter(status=AttendanceRecord.LATE).count())
            absent.append(qs.filter(status=AttendanceRecord.ABSENT).count())
            excused.append(qs.filter(status=AttendanceRecord.EXCUSED).count())

        return {
            "labels": labels,
            "series": [
                {"name": "Present", "values": present, "color": "#10B981"},
                {"name": "Late",    "values": late,    "color": "#F59E0B"},
                {"name": "Absent",  "values": absent,  "color": "#EF4444"},
                {"name": "Excused", "values": excused, "color": "#6366F1"},
            ],
        }

    return _safe(_q, _chart_empty()) or _chart_empty()


def _attendance_department_rows(days=30):
    """One row per department with % and counts — for the drill-down table."""
    from attendance.models import AttendanceRecord
    from staff.models import Department

    def _q():
        since = timezone.localdate() - timedelta(days=days - 1)
        rows = []
        for d in Department.objects.filter(is_active=True).order_by("name"):
            qs = AttendanceRecord.objects.filter(user__department=d, date__gte=since)
            total = qs.count()
            if total == 0:
                continue
            present = qs.filter(status=AttendanceRecord.PRESENT).count()
            late    = qs.filter(status=AttendanceRecord.LATE).count()
            absent  = qs.filter(status=AttendanceRecord.ABSENT).count()
            excused = qs.filter(status=AttendanceRecord.EXCUSED).count()
            pct = round(((present + late) / total) * 100) if total else 0
            rows.append({
                "department": d,
                "present": present, "late": late, "absent": absent,
                "excused": excused, "total": total, "pct": pct,
            })
        return rows

    return _safe(_q, []) or []


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
    from workflow.models import ApprovalStep, Request

    rows = (
        Request.objects
        .filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING])
        .select_related("requester", "request_type", "receiving_department")
        .order_by("current_step_started_at")[:300]
    )
    result = []
    for r in rows:
        step = _current_step_of(r)
        if step and step.approver_kind == ApprovalStep.APPROVER_ES:
            age = _age_days(r.current_step_started_at)
            result.append({"request": r, "step": step, "age_days": age,
                           "age_bucket": _age_bucket(age)})
            if len(result) >= cap:
                break
    return result


def _aged_approvals(days=7, cap=10):
    from workflow.models import Request

    threshold = timezone.now() - timedelta(days=days)
    rows = (
        Request.objects
        .filter(
            status__in=[Request.AWAITING, Request.AWAITING_RECEIVING],
            current_step_started_at__lt=threshold,
        )
        .select_related("requester", "request_type", "receiving_department")
        .order_by("current_step_started_at")[:cap]
    )
    return [{"request": r, "age_days": _age_days(r.current_step_started_at)} for r in rows]


def _office_breakdown():
    from workflow.models import Request
    from staff.models import Department

    since = timezone.now() - timedelta(days=30)
    rows = []
    for d in Department.objects.filter(is_active=True).order_by("name"):
        base = Request.objects.filter(receiving_department=d)
        pending   = base.filter(status__in=[Request.AWAITING, Request.AWAITING_RECEIVING]).count()
        inflight  = base.filter(status__in=[
            Request.IN_PROGRESS, Request.AWAITING_FULFILLMENT, Request.READY,
        ]).count()
        completed = base.filter(status=Request.COMPLETED, completed_at__gte=since).count()
        if pending + inflight + completed == 0:
            continue
        rows.append({
            "department": d,
            "pending": pending, "in_flight": inflight, "completed": completed,
            "total_30d": pending + inflight + completed,
        })
    return rows


def _recent_activity(cap=10):
    from workflow.models import RequestActivity
    return list(
        RequestActivity.objects
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
    return [_normalize_audit_row(e) for e in qs[:cap]]


# ---------------------------------------------------------------------------
# System health + user role breakdown
# ---------------------------------------------------------------------------
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
    from accounts.models import Role, User
    rows = []
    for r in Role.objects.order_by("-rank", "name"):
        n = User.objects.filter(is_active=True, role=r).count()
        if n:
            rows.append({"role": r, "count": n})
    return rows


# ---------------------------------------------------------------------------
# Attendance + workflow sub-dicts (shared by every context)
# ---------------------------------------------------------------------------
def _attendance_block():
    return {
        "attendance_kpis": _safe(_attendance_today_kpis, {}) or {},
        "attendance_charts": {
            "trend":   _safe(_attendance_trend, _chart_empty()) or _chart_empty(),
            "by_dept": _safe(_attendance_by_department, _chart_empty()) or _chart_empty(),
        },
        "attendance_depts": _safe(_attendance_department_rows, []) or [],
    }


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------
def director_context(user):
    kpis = _workflow_kpis()
    return {
        "kpis": {
            **kpis,
            "staff_total":    _staff_total(),
            "staff_on_leave": _attendance_excused_today(),
            "unread":         _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(_activity_series, _chart_empty()) or _chart_empty(),
            "status_mix": _safe(_status_mix, _donut_empty()) or _donut_empty(),
            "categories": _safe(_categories_series, _chart_empty()) or _chart_empty(),
            "approvals":  _safe(_approvals_series, _chart_empty()) or _chart_empty(),
            "depts":      _safe(_dept_activity_chart, _chart_empty()) or _chart_empty(),
        },
        "queue":            _safe(lambda: _queue_for_user(user, cap=15), []) or [],
        "aged_approvals":   _safe(lambda: _aged_approvals(7, 8), []) or [],
        "office_breakdown": _safe(_office_breakdown, []) or [],
        "recent_activity":  _safe(lambda: _recent_activity(8), []) or [],
        **_attendance_block(),
    }


def deputy_context(user):
    kpis = _workflow_kpis()
    return {
        "kpis": {
            **kpis,
            "staff_total":    _staff_total(),
            "present_today":  _attendance_present_today(),
            "unread":         _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(_activity_series, _chart_empty()) or _chart_empty(),
            "status_mix": _safe(_status_mix, _donut_empty()) or _donut_empty(),
            "approvals":  _safe(_approvals_series, _chart_empty()) or _chart_empty(),
            "depts":      _safe(_dept_activity_chart, _chart_empty()) or _chart_empty(),
        },
        "queue":            _safe(lambda: _queue_for_user(user, cap=25), []) or [],
        "aged_approvals":   _safe(lambda: _aged_approvals(3, 12), []) or [],
        "office_breakdown": _safe(_office_breakdown, []) or [],
        **_attendance_block(),
    }


def es_context(user):
    from workflow.models import ApprovalStep
    kpis = _workflow_kpis()
    return {
        "kpis": {
            **kpis,
            "es_pending":   len(_safe(lambda: _queue_es_signoff(cap=100), []) or []),
            "filing_queue": len(_safe(lambda: _queue_at_step_kind(
                ApprovalStep.KIND_CLOSURE, cap=100), []) or []),
            "unread":       _unread_notifications(user),
        },
        "charts": {
            "activity":   _safe(_activity_series, _chart_empty()) or _chart_empty(),
            "status_mix": _safe(_status_mix, _donut_empty()) or _donut_empty(),
            "approvals":  _safe(_approvals_series, _chart_empty()) or _chart_empty(),
        },
        "queue":         _safe(lambda: _queue_es_signoff(cap=20), []) or [],
        "queue_filing":  _safe(lambda: _queue_at_step_kind(
            ApprovalStep.KIND_CLOSURE, cap=20), []) or [],
        "aged_approvals": _safe(lambda: _aged_approvals(5, 10), []) or [],
        "leadership":    _leadership_current(),
        **_attendance_block(),
    }


def chairman_context(user):
    kpis = _workflow_kpis()
    return {
        "kpis": {
            **kpis,
            "staff_total":    _staff_total(),
            "users_total":    _users_total(),
            "present_today":  _attendance_present_today(),
        },
        "charts": {
            "activity":   _safe(_activity_series, _chart_empty()) or _chart_empty(),
            "status_mix": _safe(_status_mix, _donut_empty()) or _donut_empty(),
            "depts":      _safe(_dept_activity_chart, _chart_empty()) or _chart_empty(),
            "approvals":  _safe(_approvals_series, _chart_empty()) or _chart_empty(),
        },
        "dept_summary": _safe(_staff_by_department, []) or [],
        "leadership":   _leadership_current(),
        **_attendance_block(),
    }


def superadmin_context(user):
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
        "health":           _safe(system_health, {}) or {},
        "parked":           _safe(lambda: _parked_requests(cap=10), []) or [],
        "aged_approvals":   _safe(lambda: _aged_approvals(7, 10), []) or [],
        "critical_audit":   _safe(lambda: recent_audit_critical(cap=8), []) or [],
        "role_breakdown":   _safe(user_role_breakdown, []) or [],
        "office_breakdown": _safe(_office_breakdown, []) or [],
        **_attendance_block(),
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
            "count": tickets.filter(state__in=Ticket.OPEN_STATES).count(),
            "count_label": "open",
            "items": list(
                tickets.select_related("category")
                .order_by("-updated_at")[:4]
            ),
        })

    tasks_url = module_url("tasks", "tasks:assigned_to_me")
    if tasks_url:
        from tasks.models import Task

        tasks = (
            Task.objects
            .filter(assignee_rows__user=user)
            .exclude(state__in=Task.CLOSED_STATES)
            .select_related("category")
            .distinct()
        )
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