"""v2.3 — numbers and charts for the QA home, by permission.

Everyone gets the panels their role may see:

* full viewers (ES, super admin, Director of ICT, superuser): everything;
* Finance (Director of Finance and anyone who may view the finance report):
  income vs expenditure;
* directors: their department's document reviews and cases.

Cached for two minutes per user group (the numbers are NBTE-wide counts, not
per-person data), so the home page stays instant.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

PALETTE = ["#1f6b3e", "#c8962a", "#2f7cf6", "#13a37c", "#9a4fd0", "#e5484d", "#0aa2c4", "#f08a00"]


def _full(user):
    import org
    from assignments import access

    return bool(user.is_superuser or org.is_super_admin(user) or org.is_es(user)
                or org.is_director(user, access.ict_department_code()))


def _finance(user):
    try:
        from honoraria import schedules as sched

        return bool(sched.can_view_finance_report(user))
    except Exception:
        return False


def _months(n=12):
    today = timezone.localdate().replace(day=1)
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(datetime.date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def institutions_panel():
    from institutions.models import Institution

    rows = dict(Institution.unscoped.values_list("status").annotate(n=Count("pk")))
    labels = {"proposed": "Proposed", "under_review": "Under review", "approved": "Approved",
              "delisted": "Delisted", "suspended": "Suspended"}
    data = [{"label": labels.get(k, k.replace("_", " ").title()), "value": v} for k, v in rows.items() if v]
    data.sort(key=lambda d: -d["value"])
    return data


def onboarding_pipeline():
    from accreditation.models import Application

    order = [("document_upload", "Uploading documents"), ("document_review", "Documents in review"),
             ("cleared_fee_due", "Institutional fee due"), ("fee_due", "Institutional fee due"),
             ("payment_pending_confirmation", "Payment with Finance"),
             ("cleared_payment_pending", "Payment with Finance"), ("programme_selection", "Choosing programmes"),
             ("programme_clearance", "Programme clearance"), ("programme_fee_due", "Programme fees due"),
             ("programme_payment_pending", "Programme payment with Finance"), ("ssq_in_progress", "Writing SSQs"),
             ("ssq_submitted", "SSQs submitted")]
    counts = dict(Application.unscoped.values_list("status").annotate(n=Count("pk")))
    merged = {}
    for code, label in order:
        if counts.get(code):
            merged[label] = merged.get(label, 0) + counts[code]
    return [{"label": k, "value": v} for k, v in merged.items()]


def documents_panel(department=None):
    from institutions.models import DocumentReview

    qs = DocumentReview.unscoped.exclude(status=DocumentReview.SUPERSEDED)
    if department is not None:
        qs = qs.filter(department=department)
    rows = dict(qs.values_list("status").annotate(n=Count("pk")))
    names = dict(DocumentReview.STATUS_CHOICES)
    return [{"label": names.get(k, k), "value": v} for k, v in rows.items() if v]


def cases_panel():
    from accreditation import case_services as cs
    from accreditation.models import QACase

    open_cases = QACase.unscoped.filter(completed_at__isnull=True)
    counts = {}
    for case in open_cases.only("pk")[:500]:
        try:
            nos = [s.sop_stage_no or 0 for s in cs.stage_objects(case)]  # stage 0 = case opened
        except Exception:
            nos = []
        if nos:
            stage = min(nos)
            counts[stage] = counts.get(stage, 0) + 1
    groups = [("Assign team (0–1)", range(0, 2)), ("Cost (2–7)", range(2, 8)), ("Visit prep (8–11)", range(8, 12)),
              ("Visit & report (12–13)", range(12, 14)), ("Decision (14–17)", range(14, 18))]
    return [{"label": label, "value": sum(counts.get(n, 0) for n in rng)} for label, rng in groups]


def income_expenditure(months=12):
    from honoraria.models import PaymentScheduleLine
    from payments.models import PaymentObligation

    out = []
    for start in _months(months):
        end = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        income = (PaymentObligation.unscoped.filter(confirmed_at__date__gte=start, confirmed_at__date__lt=end)
                  .aggregate(t=Sum("amount_paid"))["t"] or Decimal("0"))
        spend = (PaymentScheduleLine.objects.filter(paid_at__date__gte=start, paid_at__date__lt=end,
                                                     schedule__status__in=("approved", "paid"))
                 .aggregate(t=Sum("amount"))["t"] or Decimal("0"))
        out.append({"month": start.strftime("%b %y"), "income": float(income), "spend": float(spend)})
    return out


def _compact(v):
    v = float(v or 0)
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if v >= size:
            return f"₦{v / size:,.1f}{suffix}"
    return f"₦{v:,.0f}"


def build(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    full, finance = _full(user), _finance(user)
    import org

    directed = org.directed_department(user)
    key = f"qa:home_charts:v3:{'full' if full else 'part'}:{int(finance)}:{getattr(directed, 'pk', 0)}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    out = {"full": full, "finance": finance, "kpis": [], "panels": []}
    try:
        if full:
            inst = institutions_panel()
            total = sum(d["value"] for d in inst)
            approved = next((d["value"] for d in inst if d["label"] == "Approved"), 0)
            pipe = onboarding_pipeline()
            with_finance = next((d["value"] for d in pipe if d["label"] == "Payment with Finance"), 0)
            docs = documents_panel()
            in_review = sum(d["value"] for d in docs if d["label"] not in ("Approved", "Superseded"))
            cases = cases_panel()
            out["kpis"] += [
                {"label": "Institutions", "value": total, "hint": f"{approved} approved", "tone": ""},
                {"label": "Onboarding now", "value": sum(d["value"] for d in pipe), "hint": "SOP-01 applications",
                 "tone": "gold"},
                {"label": "Payments with Finance", "value": with_finance, "hint": "onboarding fees to confirm",
                 "tone": "amber" if with_finance else "teal", "url": "/qa/finance/queue/"},
                {"label": "Documents in review", "value": in_review, "hint": "across departments", "tone": "blue"},
                {"label": "Open SOP-03 cases", "value": sum(d["value"] for d in cases), "hint": "17-stage workflow",
                 "tone": "violet"},
            ]
            out["panels"] += [
                {"id": "pipeline", "title": "Onboarding pipeline", "sub": "Where every SOP-01 application is now",
                 "type": "bar", "data": pipe},
                {"id": "institutions", "title": "Institutions by status", "sub": "The whole register",
                 "type": "doughnut", "data": inst},
                {"id": "cases", "title": "SOP-03 cases by phase", "sub": "Open cases, earliest stage",
                 "type": "bar", "data": cases},
                {"id": "documents", "title": "Document reviews", "sub": "Every department", "type": "doughnut",
                 "data": docs},
            ]
        elif directed is not None:
            docs = documents_panel(directed)
            out["kpis"].append({"label": f"{directed.name} documents", "value": sum(d["value"] for d in docs),
                                "hint": "all rounds", "tone": "blue"})
            out["panels"].append({"id": "documents", "title": f"Document reviews — {directed.name}",
                                  "sub": "Your department", "type": "doughnut", "data": docs})
        if finance or full:
            series = income_expenditure()
            inc = sum(m["income"] for m in series)
            exp = sum(m["spend"] for m in series)
            out["kpis"].append({"label": "Income (12 months)", "value": _compact(inc),
                                "hint": f"{_compact(exp)} paid in honoraria", "tone": "teal",
                                "url": "/qa/finance/honoraria/income/"})
            out["panels"].insert(0, {"id": "money", "title": "Income vs expenditure",
                                     "sub": "Confirmed fees in, honoraria out — last 12 months", "type": "money",
                                     "data": series, "wide": True})
    except Exception:  # charts never break the home page
        import logging

        logging.getLogger(__name__).exception("home charts failed")
    for p in out["panels"]:
        p["colors"] = PALETTE
        if p["type"] != "money" and not any(d.get("value") for d in p["data"]):
            p["data"] = []          # an all-zero chart says "No data yet" instead of empty axes
    cache.set(key, out, 120)
    return out
