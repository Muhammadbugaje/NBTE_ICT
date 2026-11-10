"""QA workspace home (`/qa/`) by role and office (v2.1 N1, modified by X2 / X34).

One service builds every widget; the web page (``qa_core.views.qa_home``) and
the mobile summary (``api.services.qa_staff.summary``) both read it, so the
numbers on the phone and on the website are the same.

Need-to-know (spec_v21 G1, ``assignments.access``): every count comes from a
queryset that is already scoped to what the person may see —

* my work               the person's own ``WorkAssignment`` rows;
* decisions             ``qa_core.actions.visible_to`` + ``can_decide`` (the sidebar);
* document reviews      ``institutions.document_review.visible_reviews`` (the
                        director's department, the analyst's own reviews, or
                        everything for the ES / super admin / Director of ICT);
* clearances            ``accreditation.services.clearance_rows`` (department);
* SSQs                  ``SSQ.objects.for_user`` (the scoping chokepoint);
* payments              ``payments.services.queue_items`` (``for_user``);
* complaints            ``complaints.services.queue_counts`` (``visible_to``);
* cases / visits        stage tasks the person holds, visits they are on;
* supervised            ``access.supervised_institution_ids`` (X2);
* oversight             NBTE-wide aggregates, shown ONLY to full viewers
                        (``access.full_viewer_everywhere``: ES, super admin,
                        Director of ICT).

A widget the person may not open is not built at all (no "0" that hints at a
queue they cannot reach). Each widget carries a one-line ``why`` ("why you
see this").
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

SSQ_SLOT_LIMIT = 3  # SSQs an officer may hold at once (SOP; spec G5)


@dataclass
class Item:
    label: str
    sub: str = ""
    url: str = ""
    due_at: datetime.datetime | None = None
    overdue: bool = False


@dataclass
class Widget:
    key: str
    title: str
    why: str
    count: int = 0
    url: str = ""
    url_label: str = "Open"
    tone: str = "info"          # info | warn | danger | ok
    actionable: int = 0         # how many of ``count`` need this person today
    overdue: int = 0
    stats: list = field(default_factory=list)   # [(label, value)]
    items: list = field(default_factory=list)   # [Item]
    action: str = ""            # next-action sentence when ``actionable``
    priority: int = 50          # lower = earlier in "next actions"
    pipeline: list = field(default_factory=list)  # [{label, count}] (oversight)

    def as_dict(self):
        return {"key": self.key, "title": self.title, "why": self.why, "count": self.count, "url": self.url,
                "actionable": self.actionable, "overdue": self.overdue,
                "stats": [{"label": label, "value": value} for label, value in self.stats]}


def _url(name, *args, query=""):
    try:
        return reverse(name, args=args) + (f"?{query}" if query else "")
    except NoReverseMatch:
        return ""


def _safe(fn, *args, **kwargs):
    """One broken widget never breaks the home page."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # pragma: no cover - logged, the widget is skipped
        logger.exception("qa home: %s failed", getattr(fn, "__name__", fn))
        return None


# ---------------------------------------------------------------------------
# Who is this? (scope chips)
# ---------------------------------------------------------------------------
def profile(user):
    import org

    from assignments import access

    directed = org.directed_department(user)
    full = access.full_viewer_everywhere(user)
    supervised = set() if full else set(access.supervised_institution_ids(user))
    try:
        from payments.services import finance_department, is_final_approver
        fin = finance_department()
        final_approver = is_final_approver(user)
    except Exception:  # pragma: no cover
        fin, final_approver = None, False
    finance = bool(fin is not None and user.department_id == fin.pk)
    chips = []
    if user.department_id:
        chips.append(("Department", user.department.name))
    if directed is not None:
        chips.append(("Office", f"Director, {directed.name}"))
    elif access.is_department_lead(user):
        chips.append(("Office", "Delegate of the director"))
    elif getattr(user, "role", None) is not None:
        chips.append(("Office", user.role.name))
    if full:
        chips.append(("View", "Full oversight"))
    elif supervised:
        chips.append(("View", f"ICT supervisor · {len(supervised)} institution{'s' if len(supervised) != 1 else ''}"))
    return {"directed": directed, "full": full, "supervised": supervised, "finance": finance,
            "final_approver": final_approver, "lead": access.is_department_lead(user), "chips": chips}


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
def my_work_counts(user):
    """Open work assigned to ``user`` (assignments.work.my_open_work): total,
    overdue, due within 2 days. Shared with the mobile summary."""
    from assignments.work import my_open_work

    now = timezone.now()
    rows = list(my_open_work(user)[:200])
    overdue = sum(1 for wa in rows if wa.due_at and wa.due_at < now)
    soon = sum(1 for wa in rows if wa.due_at and now <= wa.due_at < now + datetime.timedelta(days=2))
    return {"open": len(rows), "overdue": overdue, "due_soon": soon, "rows": rows}


def w_my_work(user, prof):
    data = my_work_counts(user)
    now = timezone.now()
    ssq_held = sum(1 for wa in data["rows"] if wa.kind == "ssq_review")
    w = Widget("my_work", "Assigned to me", "Work your director or the rotation gave you.", count=data["open"],
               url=_url("assignments:my_work"), url_label="My work", actionable=data["open"],
               overdue=data["overdue"], priority=10,
               tone="danger" if data["overdue"] else ("warn" if data["due_soon"] else "ok"))
    w.stats = [("Overdue", data["overdue"]), ("Due in 2 days", data["due_soon"])]
    if ssq_held:
        w.stats.append(("SSQ slots", f"{ssq_held}/{SSQ_SLOT_LIMIT}"))
    for wa in data["rows"][:6]:
        w.items.append(Item(wa.get_kind_display(), wa.institution.name if wa.institution_id else "",
                            _url("assignments:my_work"), wa.due_at, bool(wa.due_at and wa.due_at < now)))
    if data["open"]:
        w.action = f"Work through {data['open']} assignment{'s' if data['open'] != 1 else ''}" + (
            f" ({data['overdue']} overdue)" if data["overdue"] else "")
    return w


def w_decisions(user, request):
    from .actions import can_decide, visible_to
    from .permissions import can_view_institutions, can_view_pending

    if not (can_view_pending(user) or can_view_institutions(user)):
        return None
    impersonating = bool(getattr(request, "is_impersonating", False)) if request is not None else False
    items = list(visible_to(user).filter(status="pending", expires_at__gt=timezone.now())
                 .exclude(initiated_by=user)[:200])
    decidable = [pa for pa in items if can_decide(pa, user, impersonating=impersonating)[0]]
    if not decidable and not can_view_pending(user):
        return None
    n = len(decidable)
    w = Widget("decisions", "Waiting for your confirmation", "Changes a colleague started that need a second "
               "officer, director or the ES.", count=n, url=_url("qa:pending_list", query="status=pending"),
               url_label="Confirmations", actionable=n, tone="warn" if n else "ok", priority=20)
    for pa in decidable[:5]:
        w.items.append(Item(pa.policy.label if pa.policy_id else "Change", pa.reference,
                            _url("qa:pending_detail", pa.pk)))
    if n:
        w.action = f"Confirm or reject {n} change{'s' if n != 1 else ''}"
    return w


def w_document_reviews(user, prof):
    from institutions import document_review as dr
    from institutions.models import DocumentReview

    directed = prof["directed"]
    qs = dr.visible_reviews(user)
    mine = qs.filter(analyst=user, status__in=(DocumentReview.ASSIGNED, DocumentReview.SENT_BACK)).count()
    to_assign = to_decide = 0
    if directed is not None:
        dept_qs = qs.filter(department=directed)
        to_assign = dept_qs.filter(status=DocumentReview.AWAITING).count()
        to_decide = dept_qs.filter(status=DocumentReview.RECOMMENDED).count()
    total_open = None
    if prof["full"]:
        total_open = qs.filter(status__in=DocumentReview.OPEN).count()
    if not (mine or directed is not None or prof["full"]):
        return None
    actionable = mine + to_assign + to_decide
    w = Widget("document_reviews", "Document reviews",
               (f"You direct {directed.name}: you assign each document and decide on the officer's recommendation."
                if directed is not None else ("You see every journey (full oversight)." if prof["full"]
                                              else "Documents you were assigned to analyse.")),
               count=actionable if total_open is None else total_open, url=_url("qa_app:docrev_queue"),
               url_label="Document queue", actionable=actionable, tone="warn" if actionable else "ok", priority=25)
    if directed is not None:
        w.stats += [("Need an officer", to_assign), ("Awaiting your decision", to_decide)]
    if mine:
        w.stats.append(("To analyse (you)", mine))
    if total_open is not None:
        w.stats.append(("Open, NBTE-wide", total_open))
    parts = []
    if to_assign:
        parts.append(f"assign {to_assign} document{'s' if to_assign != 1 else ''}")
    if to_decide:
        parts.append(f"decide on {to_decide} recommendation{'s' if to_decide != 1 else ''}")
    if mine:
        parts.append(f"analyse {mine} document{'s' if mine != 1 else ''}")
    if parts:
        w.action = ("; ".join(parts)).capitalize()
    return w


def w_clearances(user, prof):
    from .permissions import can_view_applications

    if not (can_view_applications(user) and user.department_id):
        return None
    from accreditation.services import clearance_rows

    rows = clearance_rows(user, status="pending")
    n = len(rows)
    if not n and prof["directed"] is None:
        return None
    w = Widget("clearances", "Department clearances", f"Applications waiting for {user.department.name}'s "
               "clearance.", count=n, url=_url("qa_app:clearance"), url_label="Clearance queue", actionable=n,
               tone="warn" if n else "ok", priority=30)
    for row in rows[:5]:
        app = getattr(row, "application", None)
        inst = getattr(app, "institution", None)
        w.items.append(Item(inst.name if inst else "Application", getattr(row.department, "name", "")))
    if n:
        w.action = f"Clear {n} application{'s' if n != 1 else ''} for your department"
    return w


def w_ssq(user, prof):
    from assignments.access import can_access_any_ssq

    if not can_access_any_ssq(user):
        return None
    from accreditation.models import SSQ

    waiting = SSQ.objects.for_user(user).filter(status="submitted", is_current=True).count()
    if not waiting:
        return None
    w = Widget("ssq", "SSQs submitted", "Self-study questionnaires waiting for review in what you may see.",
               count=waiting, url=_url("qa_req:ssq_queue"), url_label="SSQ queue",
               actionable=waiting if prof["directed"] is not None else 0, tone="warn",
               priority=35)
    if w.actionable:
        w.action = f"Assign or review {waiting} submitted SSQ{'s' if waiting != 1 else ''}"
    return w


def w_cases(user, prof):
    from accreditation import case_services as cs

    if not cs.can_view_cases(user):
        return None
    links = cs.my_stage_links(user)
    now = timezone.now()
    overdue = sum(1 for link in links if link.due_at and link.due_at < now)
    if not links and not prof["full"] and prof["directed"] is None:
        return None
    w = Widget("cases", "QA case stages with you", "SOP-03 stages you hold as the next actor.", count=len(links),
               url=_url("qa_case:board"), url_label="Case board", actionable=len(links), overdue=overdue,
               tone="danger" if overdue else ("warn" if links else "ok"), priority=15)
    w.stats = [("Overdue", overdue)]
    for link in links[:5]:
        w.items.append(Item(link.case.institution.name if link.case.institution_id else link.case.reference,
                            getattr(link, "stage_label", "") or link.stage_code, "", link.due_at,
                            bool(link.due_at and link.due_at < now)))
    if links:
        w.action = f"Act on {len(links)} case stage{'s' if len(links) != 1 else ''}"
    return w


def w_finance(user, prof):
    from .permissions import can_view_finance

    if not can_view_finance(user):
        return None
    from payments import services as pay

    items = pay.queue_items(user)
    overdue = sum(1 for i in items if i.overdue)
    ready = sum(1 for i in items if i.ready) if prof["final_approver"] else 0
    w = Widget("finance", "Payments to confirm", "Bills and proofs in the Finance queue (SOP: confirm within 1 day).",
               count=len(items), url=_url("qa_fin:queue"), url_label="Payment queue", overdue=overdue,
               tone="danger" if overdue else ("warn" if items else "ok"), priority=12)
    w.stats = [("Awaiting Director decision", len(items)), ("Past the 1-day SLA", overdue)]
    w.actionable = ready if prof["final_approver"] else len(items)
    if w.actionable:
        w.action = f"Review {w.actionable} payment{'s' if w.actionable != 1 else ''} in the Finance queue"
    for item in items[:5]:
        ob = item.obligation
        w.items.append(Item(ob.institution.name if ob.institution_id else ob.reference, ob.reference,
                            _url("qa_fin:item", item.pk), item.sla_due_at, item.overdue))
    return w


def w_honoraria(user, prof):
    user_type = getattr(user, "user_type", "")
    if user_type == "resource_person":
        from honoraria import schedules

        accrued_records = schedules.unclaimed_resource_person_records(user)
        lines = schedules.payee_lines_for(user).exclude(schedule__status="cancelled")
        accrued = sum(row.visit_count for row in accrued_records) * schedules.academic_rate()
        scheduled = sum((line.amount for line in lines if line.paid_at is None), 0)
        paid = sum((line.amount for line in lines if line.paid_at and not line.confirmed_at), 0)
        received = sum((line.amount for line in lines if line.confirmed_at), 0)
        count = accrued_records.count() + lines.count()
        if not count:
            return None
        widget = Widget("honoraria", "My honoraria", "Visit work, scheduled payments, and receipt confirmations.",
                        count=count, url=_url("honoraria_earnings:list"),
                        url_label="View earnings and confirm receipt", tone="info", priority=25)
        widget.stats = [("Awaiting schedule", f"₦{accrued:,.2f}"),
                        ("Scheduled", f"₦{scheduled:,.2f}"),
                        ("Paid, awaiting receipt", f"₦{paid:,.2f}"),
                        ("Receipt confirmed", f"₦{received:,.2f}")]
        return widget
    if user_type != "staff":
        return None
    from django.db.models import Sum

    from honoraria.models import HonorariumEntry
    from honoraria import schedules

    entries = schedules.earnings_for(user).filter(status=HonorariumEntry.ACCRUED)
    lines = schedules.earning_lines_for(user)
    accrued = entries.aggregate(total=Sum("amount"))["total"] or 0
    scheduled = sum((line.amount for line in lines if line.paid_at is None), 0)
    paid = sum((line.amount for line in lines if line.paid_at and not line.confirmed_at), 0)
    received = sum((line.amount for line in lines if line.confirmed_at), 0)
    count = entries.count() + lines.count()
    if not count:
        return None
    if schedules.can_view_all(user):
        title = "Honoraria due"
        accrued_label, scheduled_label = "All accrued", "All scheduled"
    else:
        from org import is_director

        if is_director(user):
            title = "Department honoraria due"
            accrued_label, scheduled_label = "Department accrued", "Department scheduled"
        else:
            title = "My honoraria"
            accrued_label, scheduled_label = "Accrued", "Scheduled"
    w = Widget("honoraria", title, "Accrued entitlements and scheduled payments for completed payable work.",
               count=count, url=_url("honoraria_earnings:list"), url_label="View earnings and confirm receipt",
               tone="info", priority=25)
    w.stats = [(accrued_label, f"₦{accrued:,.2f}"), (scheduled_label, f"₦{scheduled:,.2f}"),
               ("Paid, awaiting receipt", f"₦{paid:,.2f}"),
               ("Receipt confirmed", f"₦{received:,.2f}")]
    return w


def w_honoraria_financial(user, prof):
    """QA-wide fee income and honoraria totals for the ES and Finance director."""
    from django.db.models import F, Sum

    import org
    from honoraria.models import HonorariumEntry, PaymentScheduleLine
    from payments.models import PaymentObligation
    from payments.services import is_final_approver

    if not (org.is_es(user) or org.is_super_admin(user) or is_final_approver(user)):
        return None

    accrued_entries = HonorariumEntry.objects.filter(status=HonorariumEntry.ACCRUED)
    live_lines = PaymentScheduleLine.objects.exclude(schedule__status="cancelled")
    accrued = accrued_entries.aggregate(total=Sum("amount"))["total"] or 0
    earned_lines = live_lines.aggregate(total=Sum(F("rate") * F("quantity")))["total"] or 0
    earned = accrued + earned_lines
    outstanding = accrued + (live_lines.filter(paid_at__isnull=True).aggregate(
        total=Sum("amount"))["total"] or 0)
    spent = live_lines.filter(paid_at__isnull=False).aggregate(
        total=Sum("amount"))["total"] or 0
    income = PaymentObligation.unscoped.filter(confirmed_at__isnull=False).aggregate(
        total=Sum("amount_paid"))["total"] or 0
    count = accrued_entries.count() + live_lines.count()
    widget = Widget("honoraria_finance", "QA honoraria finance",
                    "Cumulative confirmed fee income, payable entitlements and honoraria disbursements.",
                    count=count, url=_url("honoraria_sched:income_report"), url_label="Income / expenditure report",
                    tone="info", priority=24)
    widget.stats = [("Earned", f"₦{earned:,.2f}"),
                    ("Outstanding", f"₦{outstanding:,.2f}"),
                    ("Confirmed fee income", f"₦{income:,.2f}"),
                    ("Honoraria paid out", f"₦{spent:,.2f}"),
                    ("Recorded net", f"₦{income - spent:,.2f}")]
    widget.items = [Item("Payment schedules", "Review, approve and record staff payments.",
                         _url("honoraria_sched:list"))]
    return widget


def w_complaints(user, prof):
    from complaints import views as cv

    if not cv.can_open(user):
        return None
    from complaints.services import queue_counts

    counts = queue_counts(user)
    mine = counts.get("mine", 0)
    breached = counts.get("breached", 0)
    w = Widget("complaints", "Complaints", "Complaints routed to you or your department (SERVICOM timers).",
               count=mine + (counts.get("department", 0) if user.department_id else 0),
               url=_url("qa_complaints:queue"), url_label="Complaints", actionable=mine, overdue=breached,
               tone="danger" if breached else ("warn" if mine else "ok"), priority=18)
    w.stats = [("With you", mine), ("Your department", counts.get("department", 0)), ("Past SLA", breached)]
    if mine:
        w.action = f"Answer {mine} complaint{'s' if mine != 1 else ''} assigned to you"
    return w


def w_visits(user, prof):
    from accreditation import case_services as cs

    if not cs.can_view_cases(user):
        return None
    from django.db.models import Q

    from inspection import services as insp

    today = timezone.localdate()
    qs = (insp.visible_inspections(user).filter(Q(team_lead=user) | Q(team__staff__user=user)).distinct()
          .filter(scheduled_end__gte=today, scheduled_start__lte=today + datetime.timedelta(days=7))
          .exclude(status="cancelled").select_related("case__institution"))
    rows = list(qs[:6])
    if not rows:
        return None
    w = Widget("visits", "Visits this week", "Visits you lead or are on the team of.", count=len(rows),
               url=_url("qa_insp:inspections"), url_label="Visits", tone="info", priority=40)
    for v in rows:
        inst = getattr(v.case, "institution", None) if v.case_id else None
        w.items.append(Item(inst.name if inst is not None else "Visit",
                            f"{v.scheduled_start:%d %b} – {v.scheduled_end:%d %b}"))
    return w


def w_supervised(user, prof):
    ids = prof["supervised"]
    if not ids:
        return None
    from institutions.models import Institution

    insts = list(Institution.unscoped.filter(pk__in=ids).order_by("name")[:8])
    w = Widget("supervised", "Institutions you supervise", "You are their ICT supervisor: you see the whole "
               "journey of these institutions only.", count=len(ids), url=_url("qa_inst:list"),
               url_label="Institutions", tone="info", priority=45)
    for inst in insts:
        w.items.append(Item(inst.name, inst.get_status_display() if hasattr(inst, "get_status_display") else "",
                            _url("qa_inst:detail", inst.code)))
    return w


def w_official_letters(user, prof):
    """Provisional letters waiting for their signed official copy: the
    handling department's Director (own department), the ES and super admins.
    Staff asked to prepare one see their own letters instead."""
    from documents import official_letters as ol

    if ol.can_open_queue(user):
        rows = ol.rows(user)
        stats = ol.summary(rows)
        n, ready, overdue = stats["total"], stats["draft_ready"], stats["overdue"]
        why = ("Provisional letters NBTE issued for your department's institutions that still need the signed "
               "official copy." if not ol.is_full(user) else
               "Every provisional letter still waiting for its signed official copy (ES / super admin).")
        w = Widget("official_letters", "Official letters to upload", why, count=n,
                   url=_url("documents_register:official_letters"), url_label="Official letters", actionable=n,
                   overdue=overdue, priority=22, tone="danger" if overdue else ("warn" if n else "ok"))
        w.stats = [("Draft ready", ready), ("With staff", stats["with_staff"]), ("Overdue", overdue)]
        for r in sorted(rows, key=lambda r: (r["state"] != "draft_ready", -r["days"]))[:6]:
            sub = f"{r['letter']} · {r['days']} day{'s' if r['days'] != 1 else ''} waiting"
            if r["state"] == "draft_ready":
                sub += " · draft ready"
            w.items.append(Item(r["institution"].name, sub, r["url"], None, r["overdue"]))
        if n:
            w.action = (f"Upload {n} official letter{'s' if n != 1 else ''}"
                        + (f" ({ready} draft{'s' if ready != 1 else ''} ready)" if ready else "")
                        + (f", {overdue} overdue" if overdue else ""))
        return w
    jobs = ol.my_letters_to_prepare(user)
    if not jobs:
        return None
    today = timezone.localdate()
    late = sum(1 for j in jobs if j.due_date and j.due_date < today)
    w = Widget("official_letters", "Official letters to prepare",
               "Your Director asked you to prepare these official letters and send the draft back.",
               count=len(jobs), url=_url("documents_register:official_letter", jobs[0].document_id),
               url_label="Open the first letter", actionable=len(jobs), overdue=late, priority=21,
               tone="danger" if late else "warn")
    for j in jobs[:6]:
        sub = ol.letter_label(j.document) + (f" · due {j.due_date:%d %b}" if j.due_date else "")
        if j.state == j.RETURNED:
            sub += " · sent back"
        w.items.append(Item(j.document.institution.name, sub,
                            _url("documents_register:official_letter", j.document_id), None,
                            bool(j.due_date and j.due_date < today)))
    w.action = f"Prepare {len(jobs)} official letter{'s' if len(jobs) != 1 else ''}"
    return w


def w_oversight(user, prof):
    """NBTE-wide numbers: full viewers only (ES, super admin, Director of ICT)."""
    if not prof["full"]:
        return None
    from dashboards import qa_metrics

    data = qa_metrics.overview()
    sla = data.get("sla") or {}
    cases = data.get("cases") or {}
    comp = data.get("complaints") or {}
    inst = data.get("institutions") or {}
    w = Widget("oversight", "Oversight", "You see every journey (ES, super admin, Director of ICT).",
               count=cases.get("open", 0), url=_url("qa_case:board"), url_label="Case board", tone="info",
               overdue=sla.get("overdue_now", 0), priority=60)
    w.stats = [("Open QA cases", cases.get("open", 0)),
               ("Stages overdue now", sla.get("overdue_now", 0)),
               ("On time (6 months)", f"{sla['rate']}%" if sla.get("rate") is not None else "–"),
               ("Open complaints", comp.get("open", 0)), ("Complaints past SLA", comp.get("breached", 0)),
               ("Institutions", inst.get("total", 0))]
    groups = cases.get("by_group", [])
    top = max([g["count"] for g in groups] + [1])
    w.pipeline = [{"label": g["label"], "count": g["count"], "pct": round(100 * g["count"] / top)} for g in groups]
    return w


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------
QUICK_LINKS = (  # (qa_nav key or callable, label, url name)
    ("qa_cases", "Case board", "qa_case:board"),
    ("qa_requests", "QA requests", "qa_req:list"),
    ("institutions", "Institutions", "qa_inst:list"),
    ("applications", "Applications", "qa_app:list"),
    ("finance", "Finance queue", "qa_fin:queue"),
    ("complaints", "Complaints", "qa_complaints:queue"),
    ("inspection", "Visits", "qa_insp:inspections"),
    ("pending", "Confirmations", "qa:pending_list"),
    ("documents_register", "Issued documents", "documents_register:list"),
    ("settings", "QA settings", "qa:settings"),
)


def _public_portal_url():
    try:
        from .hosts import portal_absolute

        return portal_absolute("/", None)
    except Exception:
        return ""


def quick_links(nav):
    out = [{"label": "My work", "url": _url("assignments:my_work")},
           {"label": "My earnings", "url": _url("honoraria_earnings:list")},
           {"label": "Facilities & equipment", "url": _url("facilities:index")},   # v2.2
           {"label": "Staff guides", "url": _url("qa:staff_guides")},              # v2.2
           {"label": "Public QA portal", "url": _public_portal_url()}]
    for key, label, name in QUICK_LINKS:
        if nav.get(key):
            url = _url(name)
            if url:
                out.append({"label": label, "url": url})
    return out


def build(user, request=None):
    """Everything the QA home shows for ``user``."""
    from .context_processors import qa_nav

    prof = profile(user)
    builders = (w_my_work, w_finance, w_honoraria_financial, w_honoraria, w_cases, w_complaints, w_document_reviews, w_clearances,
                w_ssq, w_visits, w_supervised, w_official_letters, w_oversight)
    widgets = [_safe(b, user, prof) for b in builders] + [_safe(w_decisions, user, request)]
    widgets = [w for w in widgets if w is not None]
    widgets.sort(key=lambda w: w.priority)
    actions = sorted((w for w in widgets if w.actionable and w.action),
                     key=lambda w: (0 if w.overdue else 1, w.priority))
    nav = {}
    if request is not None:
        nav = qa_nav(request).get("qa_nav") or {}
    journey_actions = []
    if getattr(user, "user_type", "staff") == "staff":
        from accreditation import journey  # onboarding: assign / clear / approve / confirm / assess

        journey_actions = _safe(journey.next_actions, user) or []
    return {
        "journey_actions": journey_actions,
        "profile": prof, "chips": prof["chips"], "widgets": widgets,
        "by_key": {w.key: w for w in widgets},
        "next_actions": [{"text": w.action, "url": w.url, "overdue": w.overdue} for w in actions],
        "today": sum(w.actionable for w in widgets),
        "overdue": sum(w.overdue for w in widgets if w.key != "oversight"),
        "links": quick_links(nav),
    }
