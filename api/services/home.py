"""GET home/ - three shapes, one per user_type.

Staff numbers reuse the website's own helpers:
* tasks           dashboards.counts.my_open_tasks (the home "My tasks" card,
                  the work board's Tasks panel)
* approvals       workflow.services.candidate_requests_for +
                  _resolve_current_step + resolve_approver (exactly the
                  sidebar's "awaiting" badge loop in core.context_processors)
                  plus core.workboard.confirmations_panel()["total"] (QA
                  two-step confirmations and final payment decisions)
* memos           messaging.memos.awaiting_memos_for
* unread chats    api.services.me.unread_chat_count
* pinned_chats    messaging.chat_services.inbox_threads (pinned first)
* announcement    newest live action_needed/urgent announcement still waiting
                  for the user (api.services.announcements.home_announcement)
* attendance      attendance.services.non_working_reason + today's
                  AttendanceRecord + active AttendanceLocation (check_in_page)
Institution: institutions.portal_views.dashboard_context (the portal
dashboard), accreditation.services.stage_board / public_status.
Resource person: inspection.services.rp_of / rp_engagements / rp_service /
findings_open (the portal's engagements and service-history pages).
"""
import datetime
import logging

from django.utils import timezone

logger = logging.getLogger("api")

UP_NEXT_LIMIT = 8


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------
def greeting(now=None):
    hour = timezone.localtime(now or timezone.now()).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def date_label(day=None):
    day = day or timezone.localdate()
    return f"{day:%A} {day.day} {day:%B}"


def due_label(due, today=None):
    if not due:
        return ""
    today = today or timezone.localdate()
    if due < today:
        return "Overdue"
    if due == today:
        return "Today"
    if due == today + datetime.timedelta(days=1):
        return "Tomorrow"
    return f"{due:%a} {due.day} {due:%b}"


def money(value):
    from payments.money import naira, to_money

    return f"{to_money(value):.2f}", naira(value)


def _safe(label, fn, default):
    try:
        return fn()
    except Exception:
        logger.exception("home/: %s failed", label)
        return default


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
def attendance_today(user):
    from attendance import services
    from attendance.models import AttendanceLocation, AttendanceRecord

    today = timezone.localdate()
    record = AttendanceRecord.objects.filter(user=user, date=today).select_related("location").first()
    checked_in = bool(record and record.status in AttendanceRecord.STATUSES_PRESENT)
    location_name = record.location.name if checked_in and record.location_id else None
    if location_name is None:
        for loc in AttendanceLocation.objects.filter(is_active=True).order_by("name"):
            if not user.department_id or loc.applies_to_department(user.department_id):
                location_name = loc.name
                break
    # Same refusals as attendance.services.check_in_user, before the GPS check.
    can_check_in = (not services.non_working_reason(today, user) and bool(user.department_id)
                    and (record is None or record.status == AttendanceRecord.ON_LEAVE))
    checked_at = timezone.localtime(record.check_in_at).isoformat() if checked_in and record.check_in_at else None
    return {"checked_in": checked_in, "checked_in_at": checked_at, "location_name": location_name,
            "can_check_in": can_check_in}


def awaiting_requests(user):
    """Workflow requests whose current step resolves to ``user`` (the
    sidebar's "awaiting" badge; superusers are skipped there too)."""
    if user.is_superuser:
        return []
    from workflow import services

    out = []
    candidates = (services.candidate_requests_for(user)
                  .select_related("requester", "requester__department", "receiving_department", "request_type")
                  .order_by("-created_at"))
    for req in candidates:
        step = services._resolve_current_step(req)
        if step is None:
            continue
        actor = services.resolve_approver(step, req)
        if actor and actor.pk == user.pk:
            req.api_step = step
            out.append(req)
    return out


def _initials(title):
    words = [w for w in (title or "").replace("-", " ").split() if w[:1].isalnum()]
    letters = "".join(w[0] for w in words[:3]).upper()
    return letters or "?"


PINNED_LIMIT = 6


def pinned_chats(user):
    """Pinned conversations (ThreadParticipant.is_pinned) in the inbox's
    order, with unread counts (messaging.chat_services.inbox_threads)."""
    from messaging.chat_services import inbox_threads

    out = []
    for t in inbox_threads(user):
        if not t.is_pinned:
            break  # inbox_threads sorts pinned first
        out.append({"thread_id": t.pk, "title": t.display_title, "initials": _initials(t.display_title),
                    "unread": t.unread or 0})
        if len(out) >= PINNED_LIMIT:
            break
    return out


def home_announcement(user):
    from .announcements import home_announcement as newest

    return newest(user)


def staff_home(user, request):
    from core.workboard import confirmations_panel
    from dashboards.counts import my_open_tasks
    from messaging.memos import awaiting_memos_for

    from .me import unread_chat_count

    today = timezone.localdate()
    tasks_qs = my_open_tasks(user)
    tasks = _safe("tasks", lambda: sorted(
        tasks_qs.only("pk", "reference", "title", "due_date", "priority", "state")[:60],
        key=lambda t: (t.due_date is None, t.due_date or today, t.pk)), [])
    approvals = _safe("approvals", lambda: awaiting_requests(user), [])
    confirmations = _safe("confirmations", lambda: confirmations_panel(user)["total"], 0)
    memos = _safe("memos", lambda: awaiting_memos_for(user), [])

    urgent_tasks, later_tasks = [], []
    for t in tasks:
        item = {"kind": "task", "id": t.pk, "title": t.title, "subtitle": t.reference,
                "due_label": due_label(t.due_date, today), "route": f"/tasks/{t.pk}"}
        (urgent_tasks if t.due_date and t.due_date <= today else later_tasks).append(item)
    approval_items = [{"kind": "approval", "id": r.pk, "title": r.subject,
                       "subtitle": f"{r.reference} · {getattr(r.api_step, 'name', '')}".strip(" ·"),
                       "due_label": "Waiting", "route": f"/approvals/{r.pk}"} for r in approvals[:3]]
    memo_items = [{"kind": "memo", "id": m.pk, "title": m.subject, "subtitle": m.reference,
                   "due_label": "Urgent" if m.priority == "urgent" else "Waiting", "route": f"/memos/{m.pk}"}
                  for m in memos[:3]]
    up_next = (urgent_tasks + approval_items + memo_items + later_tasks)[:UP_NEXT_LIMIT]

    return {
        "greeting": greeting(),
        "date_label": date_label(today),
        "attendance": _safe("attendance", lambda: attendance_today(user),
                            {"checked_in": False, "checked_in_at": None, "location_name": None,
                             "can_check_in": False}),
        "counts": {
            "tasks_open": _safe("tasks_open", tasks_qs.count, 0),
            "tasks_due_today": _safe("tasks_due_today", lambda: tasks_qs.filter(due_date=today).count(), 0),
            "approvals_waiting": len(approvals) + confirmations,
            "unread_chats": _safe("unread_chats", lambda: unread_chat_count(user), 0),
        },
        "pinned_chats": _safe("pinned_chats", lambda: pinned_chats(user), []),
        "up_next": up_next,
        "announcement": _safe("announcement", lambda: home_announcement(user), None),
    }


# ---------------------------------------------------------------------------
# Institution
# ---------------------------------------------------------------------------
def application_summary(app):
    if app is None:
        return None
    from accreditation import services

    board = services.stage_board(app)
    stage = next((i + 1 for i, row in enumerate(board) if row["state"] == "current"), None)
    if stage is None:
        stage = sum(1 for row in board if row["state"] == "done") or 1
    return {"stage": stage, "stages": len(board), "headline": str(services.public_status(app)),
            "route": "/application"}


def payments_by_month(user, institution, months=12, today=None):
    """Confirmed payments per month, the last ``months`` months (oldest first)."""
    from django.db.models import Count
    from django.db.models.functions import TruncMonth

    from payments.models import PaymentObligation

    today = today or timezone.localdate()
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    keys.reverse()
    start = timezone.make_aware(datetime.datetime(keys[0][0], keys[0][1], 1))
    rows = (PaymentObligation.objects.for_user(user)
            .filter(institution=institution, confirmed_at__gte=start)
            .annotate(m=TruncMonth("confirmed_at")).values("m").annotate(n=Count("pk")))
    counts = {}
    for row in rows:
        if row["m"] is not None:
            counts[(row["m"].year, row["m"].month)] = row["n"]
    return [{"month": f"{y:04d}-{m:02d}", "count": counts.get((y, m), 0)} for y, m in keys]


def institution_home(user, request):
    from institutions.portal_views import dashboard_context
    from qa_core.portal_views import _institution_for

    from .me import institution_dict

    institution = _institution_for(user)
    if institution is None:
        return {"institution": None, "application": None, "bills_due": [], "programmes_expiring": [],
                "payments_by_month": [], "action_items": [], "recent_activity": [], "programme_board": [],
                "programme_count": 0, "sections": []}
    ctx = dashboard_context(request, institution)
    today = timezone.localdate()
    bills = []
    for ob in ctx.get("open_bills", []):
        due = ob.balance if ob.balance and ob.balance > 0 else ob.amount
        amount, display = money(due)
        bills.append({"id": ob.pk, "title": ob.description, "amount": amount, "amount_display": display,
                      "due_date": timezone.localdate(ob.due_at).isoformat() if ob.due_at else None,
                      "status": ob.status})
    expiring = [{"id": p.pk, "name": p.name, "level": p.level.name if p.level_id else None,
                 "expires_on": p.expiry_date.isoformat(), "days_left": (p.expiry_date - today).days}
                for p in ctx.get("expiring", [])]
    return {
        "institution": institution_dict(institution),
        "application": application_summary(ctx.get("application")),
        "bills_due": bills,
        "programmes_expiring": expiring,
        "payments_by_month": _safe("payments_by_month", lambda: payments_by_month(user, institution), []),
        # Phase 9 portal API: the rest of the portal dashboard
        "action_items": _safe("action_items", lambda: action_items(request, institution, ctx), []),
        "recent_activity": _safe("recent_activity", lambda: recent_activity(user), []),
        "programme_board": [{"label": str(label), "count": n} for label, n in ctx.get("programme_board", [])],
        "programme_count": ctx.get("programme_count", 0),
        "sections": portal_sections(user, institution),
    }


def _route(url):
    return (url or "").rstrip("/") or "/"


def action_items(request, institution, ctx):
    """The portal dashboard's "Needs your attention" list (SOP-02 items
    first, as on the website), with app routes instead of page URLs."""
    from accreditation.qa_portal_views import qa_action_items

    items = qa_action_items(request, institution) + ctx.get("action_items", [])
    return [{"tone": a.get("tone", "info"), "title": str(a.get("title", "")), "body": str(a.get("body") or ""),
             "cta": str(a.get("cta") or ""), "route": _route(a.get("url"))} for a in items]


def recent_activity(user):
    from qa_core.models import TimelineEvent

    return [{"at": e.at, "text": e.public_text}
            for e in TimelineEvent.objects.for_user(user).filter(visibility="public")[:6]]


def portal_sections(user, institution):
    """The sections the portal offers this account (its "Go to" tiles), in order."""
    from qa_core.portal_views import portal_nav

    return [{"key": item["key"], "label": str(item["label"]), "route": _route(item["url"])}
            for item in portal_nav(user, institution, "dashboard") if item["key"] not in ("dashboard", "account")]


# ---------------------------------------------------------------------------
# Resource person
# ---------------------------------------------------------------------------
def resource_person_home(user, request):
    from inspection import services
    from inspection.models import VISIT_DUTIES, InspectionTeamMember, QAMapping

    rp = services.rp_of(user)
    if rp is None:
        return {"next_visit": None, "todo": [], "history": [], "sections": portal_sections(user, None)}
    today = timezone.localdate()
    engagements = services.rp_engagements(rp)
    live = engagements.filter(status__in=QAMapping.LIVE)

    next_visit = None
    first = live.filter(duty__in=VISIT_DUTIES, to_date__gte=today).order_by("from_date", "pk").first()
    if first is not None:
        member = (InspectionTeamMember.unscoped.filter(qa_mapping=first).exclude(role_on_team="")
                  .values_list("role_on_team", flat=True).first())
        programmes = (live.filter(case=first.case, duty__in=VISIT_DUTIES)
                      .values("qa_request_programme").distinct().count())
        next_visit = {"id": first.pk, "institution": first.case.institution.name,
                      "date": first.from_date.isoformat(), "role": member or first.get_duty_display(),
                      "programmes": programmes, "route": f"/visits/{first.pk}"}

    todo = []
    for m in live.filter(duty="ssq_review").order_by("to_date", "pk")[:10]:
        todo.append({"kind": "ssq_review", "id": m.pk,
                     "title": f"Review SSQ: {m.qa_request_programme.programme.name} ({m.case.institution.name})",
                     "due_label": due_label(m.to_date, today)})
    for m in engagements.filter(duty__in=VISIT_DUTIES, findings="", from_date__lte=today).order_by("to_date")[:20]:
        if services.findings_open(m)[0]:
            todo.append({"kind": "report", "id": m.pk,
                         "title": f"Write findings: {m.qa_request_programme.programme.name} "
                                  f"({m.case.institution.name})",
                         "due_label": due_label(m.to_date, today)})

    history = [{"institution": r.institution.name if r.institution_id else r.person_name,
                "date": r.served_from.isoformat(), "allowance_status": "paid" if r.allowance_paid else "pending"}
               for r in services.rp_service(rp)[:10]]
    return {"next_visit": next_visit, "todo": todo, "history": history, "sections": portal_sections(user, None)}


def features(user):
    """Which optional app screens this account has (staff door only):
    search/ (everyone on the staff door), dashboard/executive/ (the website's
    leadership-board rule, api.services.dashboard.can_view) and office-code
    check-in (attendance.codes.manual_checkin_enabled)."""
    if user.user_type != "staff":
        return {"search": False, "executive_dashboard": False, "office_code_check_in": False}
    from attendance.codes import manual_checkin_enabled

    from .dashboard import can_view

    return {"search": True, "executive_dashboard": bool(_safe("features", lambda: can_view(user), False)),
            "office_code_check_in": bool(_safe("features", manual_checkin_enabled, False))}


def home_payload(user, request):
    if user.user_type == "institution":
        payload = institution_home(user, request)
    elif user.user_type == "resource_person":
        payload = resource_person_home(user, request)
    else:
        payload = staff_home(user, request)
    return {**payload, "features": features(user)}
