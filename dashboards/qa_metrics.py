"""QA executive metrics for the leadership dashboards (Phase 7 / §17 Phase 8
"QA executive dashboard": revenue, institutions, SLA compliance, cases by
stage, top complaint layers).

Every function is read-only, defensive (a missing app or table gives zeros)
and cheap: a handful of aggregate queries. The leadership dashboards are
NBTE-wide by role, so these queries use the unscoped managers on purpose.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

logger = logging.getLogger(__name__)

STAGE_GROUPS = [  # (label, first stage no, last stage no) — the Case Board's columns
    ("Opening", 0, 1), ("Cost", 2, 7), ("Visit & funds", 8, 11), ("Report", 12, 13),
    ("Decision", 14, 17),
]


def _months(n, today=None):
    today = today or timezone.localdate()
    first = today.replace(day=1)
    out = []
    for i in range(n - 1, -1, -1):
        y, m = first.year, first.month - i
        while m <= 0:
            m += 12
            y -= 1
        out.append(datetime.date(y, m, 1))
    return out


def revenue(months=12):
    """Confirmed QA revenue (amount actually paid) per month and this year."""
    try:
        from payments.models import PaymentObligation
    except Exception:  # pragma: no cover
        return {"labels": [], "values": [], "year": 0, "month": 0, "previous_month": 0}
    keys = _months(months)
    since = timezone.make_aware(datetime.datetime.combine(keys[0], datetime.time.min))
    paid = (PaymentObligation.unscoped
            .filter(confirmed_at__gte=since, status__in=("confirmed", "overpaid", "refund_requested", "refunded"))
            .annotate(m=TruncMonth("confirmed_at")).values("m").annotate(total=Sum("amount_paid")))
    by_month = {}
    for row in paid:
        if row["m"] is not None:
            m = row["m"].date() if hasattr(row["m"], "date") else row["m"]
            by_month[m.replace(day=1)] = row["total"] or Decimal("0")
    values = [float(by_month.get(k, 0)) for k in keys]
    this_year = timezone.localdate().year
    year_total = sum(v for k, v in zip(keys, values) if k.year == this_year)
    return {"labels": [k.strftime("%b") for k in keys], "values": values, "year": year_total,
            "month": values[-1] if values else 0, "previous_month": values[-2] if len(values) > 1 else 0}


def institutions():
    try:
        from institutions.models import Institution
    except Exception:  # pragma: no cover
        return {"total": 0, "by_status": [], "approved": 0}
    rows = dict(Institution.unscoped.values_list("status").annotate(n=Count("id")))
    order = [("approved", "Approved"), ("under_review", "Under review"), ("proposed", "Proposed"),
             ("suspended", "Suspended"), ("delisted", "Delisted")]
    by_status = [{"code": c, "label": label, "count": rows.get(c, 0)} for c, label in order if rows.get(c)]
    return {"total": sum(rows.values()), "approved": rows.get("approved", 0), "by_status": by_status}


def sla(months=6):
    """Stage tasks closed on or before their deadline, over the last
    ``months`` months, overall and per month; plus what is overdue now."""
    try:
        from qa_core.models import QATaskLink
    except Exception:  # pragma: no cover
        return {"rate": None, "labels": [], "values": [], "overdue_now": 0, "closed": 0}
    keys = _months(months)
    since = timezone.make_aware(datetime.datetime.combine(keys[0], datetime.time.min))
    closed = QATaskLink.objects.filter(closed_at__gte=since, due_at__isnull=False)
    rows = (closed.annotate(m=TruncMonth("closed_at")).values("m")
            .annotate(total=Count("id"), on_time=Count("id", filter=Q(closed_at__lte=F("due_at")))))
    per = {}
    for r in rows:
        m = r["m"].date() if hasattr(r["m"], "date") else r["m"]
        per[m.replace(day=1)] = (r["on_time"], r["total"])
    values = []
    for k in keys:
        on_time, total = per.get(k, (0, 0))
        values.append(round(100.0 * on_time / total, 1) if total else None)
    total = sum(t for _o, t in per.values())
    on_time = sum(o for o, _t in per.values())
    overdue = QATaskLink.objects.filter(closed_at__isnull=True, due_at__lt=timezone.now()).count()
    complaints = complaint_sla()
    if complaints["closed"]:
        total += complaints["closed"]
        on_time += complaints["on_time"]
    return {"rate": round(100.0 * on_time / total, 1) if total else None, "closed": total,
            "labels": [k.strftime("%b") for k in keys], "values": values, "overdue_now": overdue}


def complaint_sla():
    """Complaint stages left within their SLA (Phase 7)."""
    try:
        from complaints.models import ComplaintStage
    except Exception:
        return {"closed": 0, "on_time": 0}
    try:
        left = ComplaintStage.objects.filter(left_at__isnull=False, due_at__isnull=False)
        return {"closed": left.count(), "on_time": left.filter(breached_at__isnull=True).count()}
    except Exception:  # table not migrated yet
        return {"closed": 0, "on_time": 0}


def cases():
    try:
        from accreditation.case_demo import STAGE_NUMBERS
        from accreditation.models import QACase
    except Exception:  # pragma: no cover
        return {"open": 0, "by_group": [], "closed": 0}
    counts = {label: 0 for label, _a, _b in STAGE_GROUPS}
    open_cases, closed = 0, 0
    # Phase 8: stage numbers come from the live flow too, so stages the
    # demo map does not list (e.g. "supervisors_assigned") and cases not yet
    # started ("awaiting_sop03") are counted as open instead of dropped.
    stage_no = dict(STAGE_NUMBERS)
    try:
        from qa_core.models import FlowStage

        for code, no in FlowStage.objects.filter(flow__code="qa_case", sop_stage_no__isnull=False) \
                .values_list("code", "sop_stage_no"):
            stage_no.setdefault(code, no)
    except Exception:  # pragma: no cover - flows not seeded
        pass
    for status, n in (QACase.unscoped.order_by().values_list("status").annotate(n=Count("id"))):
        no = stage_no.get(status, 0)
        if status == "closed" or no >= 18:
            closed += n
            continue
        open_cases += n
        for label, a, b in STAGE_GROUPS:
            if a <= no <= b:
                counts[label] += n
    return {"open": open_cases, "closed": closed,
            "by_group": [{"label": k, "count": v} for k, v in counts.items()]}


def complaints():
    try:
        from complaints.models import Complaint
    except Exception:
        return {"open": 0, "breached": 0, "top": []}
    try:
        open_qs = Complaint.unscoped.filter(closed_at__isnull=True).exclude(status="resolved")
        top = list(open_qs.values("layer__name").annotate(n=Count("id")).order_by("-n")[:5])
        breached = open_qs.filter(stages__breached_at__isnull=False, stages__left_at__isnull=True).distinct().count()
        return {"open": open_qs.count(), "breached": breached,
                "top": [{"label": t["layer__name"] or "Other", "count": t["n"]} for t in top]}
    except Exception:
        return {"open": 0, "breached": 0, "top": []}


def overview():
    """Everything the QA overview panel shows, in one dict (JSON-safe parts
    under ``charts``)."""
    data = {}
    for key, fn in (("revenue", revenue), ("institutions", institutions), ("sla", sla), ("cases", cases),
                    ("complaints", complaints)):
        try:
            data[key] = fn()
        except Exception:  # never break a dashboard over one tile
            logger.exception("qa overview: %s failed", key)
            data[key] = {}
    rev = data.get("revenue") or {}
    prev, cur = rev.get("previous_month") or 0, rev.get("month") or 0
    rev["trend"] = round(100.0 * (cur - prev) / prev, 1) if prev else None
    data["charts"] = {
        "revenue": {"labels": rev.get("labels", []), "values": rev.get("values", [])},
        "institutions": [{"label": r["label"], "count": r["count"], "code": r["code"]}
                         for r in (data.get("institutions") or {}).get("by_status", [])],
        "sla": {"labels": (data.get("sla") or {}).get("labels", []),
                "values": (data.get("sla") or {}).get("values", [])},
        "cases": (data.get("cases") or {}).get("by_group", []),
    }
    return data
