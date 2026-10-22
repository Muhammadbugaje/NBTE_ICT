"""dashboard/executive/ (staff door) - the leadership dashboards of the
website (dashboards.views director / deputy / es / chairman) as one JSON
shape.

Access is the website's ``dashboards.views._guard`` for those four boards:
a superuser; role ``executive-secretary``, ``director`` or
``deputy-director``; or the ``accounts.view_chairman_dashboard`` permission.
Which board a user gets follows the role (ES, director, deputy), then the
chairman permission (superusers get the chairman board, which is
NBTE-wide).

Numbers come from the website's own helpers in ``dashboards.services`` (the
director/deputy/ES boards pass the viewer, so they only count requests that
person may see - workflow.access) and ``dashboards.qa_metrics`` (the QA
overview panel every leadership board shows). Only the parts the app shows
are computed (no per-department loops), so the payload stays cheap.
"""
from types import SimpleNamespace

from django.utils import timezone

from .notifications import target_for

BOARDS = {
    "es": "Executive Secretary",
    "director": "Director",
    "deputy": "Deputy Director",
    "chairman": "Chairman",
}
ROLE_BOARDS = {"executive-secretary": "es", "director": "director", "deputy-director": "deputy"}
CHAIRMAN_PERM = "accounts.view_chairman_dashboard"


def board_for(user):
    """The leadership board ``user`` may open on the website, or None."""
    if not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "staff") != "staff":
        return None
    role = user.role.code if user.role_id else ""
    if role in ROLE_BOARDS:
        return ROLE_BOARDS[role]
    if user.is_superuser or user.has_perm(CHAIRMAN_PERM):
        return "chairman"
    return None


def can_view(user):
    return board_for(user) is not None


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------
def _target(url, request):
    return target_for(SimpleNamespace(url=url, metadata={}), request) if url else None


def _num(value):
    return 0 if value is None else value


def _kpi(key, label, value, *, display=None, unit=None, delta=None, delta_label=None, tone="neutral",
         hint=None, target=None):
    value = _num(value)
    if display is None:
        display = f"{value:,}" if isinstance(value, int) else f"{value:,.1f}"
    return {"key": key, "label": label, "value": value, "display": display, "unit": unit, "delta": delta,
            "delta_label": delta_label, "tone": tone, "hint": hint, "target": target}


def _naira_short(amount):
    amount = float(amount or 0)
    for size, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(amount) >= size:
            return f"₦{amount / size:,.1f}{suffix}"
    return f"₦{amount:,.0f}"


def _series(key, label, kind, labels, values, unit=None):
    points = [{"label": str(lab), "value": v} for lab, v in zip(labels, values) if v is not None]
    return {"key": key, "label": label, "kind": kind, "unit": unit, "points": points}


def _age_tone(bucket):
    return {"fresh": "good", "warm": "neutral", "aging": "warn", "stale": "bad"}.get(bucket, "neutral")


def _request_item(row, request):
    r = row["request"]
    step = row.get("step")
    age = row.get("age_days")
    subtitle = " · ".join(filter(None, [r.reference, getattr(step, "name", ""),
                                        r.requester.get_full_name() if r.requester_id else ""]))
    return {"title": r.subject, "subtitle": subtitle,
            "value": None if age is None else (f"{age} day" + ("" if age == 1 else "s")),
            "tone": _age_tone(row.get("age_bucket") or ("stale" if (age or 0) > 30 else
                                                         "aging" if (age or 0) > 7 else "warm")),
            "target": _target(f"/workflow/{r.pk}/", request)}


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------
def executive_payload(user, request=None):
    from dashboards import qa_metrics
    from dashboards import services as ds
    from workflow.access import can_view_all_requests

    board = board_for(user)
    viewer = None if board == "chairman" else user  # the chairman board is NBTE-wide on the website
    organisation = viewer is None or can_view_all_requests(user)
    scope = "organisation" if organisation else "department"
    if organisation:
        scope_label = "All of NBTE"
    else:
        scope_label = f"{user.department.name} (requests you can see)" if user.department_id \
            else "Requests you can see"

    wf = ds._workflow_kpis(viewer)
    att = ds._attendance_block(user)
    akpi = att.get("attendance_kpis") or {}
    qa = ds._safe(qa_metrics.overview, {}) or {}
    rev = qa.get("revenue") or {}
    inst = qa.get("institutions") or {}
    sla = qa.get("sla") or {}
    cases = qa.get("cases") or {}
    complaints = qa.get("complaints") or {}

    approvals_url = _target("/workflow/", request)
    kpis = [
        _kpi("approvals_pending", "Awaiting approval", wf["pending"], tone="warn" if wf["pending"] else "good",
             hint="Requests waiting at an approval step.", target=approvals_url),
        _kpi("approvals_aged", "Waiting over 7 days", wf["aged"], tone="bad" if wf["aged"] else "good",
             hint="Approvals stuck at the same step for more than a week.", target=approvals_url),
        _kpi("requests_in_flight", "In flight", wf["in_flight"], hint="Approved and being worked on."),
        _kpi("requests_completed_30d", "Completed (30 days)", wf["completed_30d"], tone="good"),
        _kpi("requests_rejected_30d", "Rejected (30 days)", wf["rejected_30d"]),
    ]
    if board == "es":
        from dashboards import counts

        es_pending = ds._safe(lambda: counts.es_signoff().count(), 0)
        filing = ds._safe(lambda: counts.awaiting_filing().count(), 0)
        kpis += [
            _kpi("es_signoff", "Awaiting your sign-off", es_pending, tone="warn" if es_pending else "good",
                 target=approvals_url),
            _kpi("filing_queue", "Awaiting filing", filing),
        ]
    if board in ("director", "deputy", "chairman"):
        kpis.append(_kpi("staff_total", "Staff", ds._staff_total(), hint="Active staff on the nominal roll."))
    if att.get("attendance_visible"):
        pct = akpi.get("attendance_pct")
        kpis.append(_kpi("present_today", "In today", akpi.get("came", 0),
                         display=f"{akpi.get('came', 0):,} of {akpi.get('total_staff', 0):,}",
                         delta=pct, delta_label=None if pct is None else f"{pct}% of staff",
                         tone="neutral" if pct is None else ("good" if pct >= 80 else "warn" if pct >= 50
                                                             else "bad"),
                         hint=akpi.get("reason") or att.get("attendance_title") or None,
                         target=_target(att.get("attendance_link") or "", request)))
        kpis.append(_kpi("on_leave_today", "Away today", akpi.get("away", 0), hint="On leave or excused."))
    # QA overview (the panel every leadership board shows)
    if rev:
        kpis.append(_kpi("qa_revenue_year", "QA revenue this year", float(rev.get("year") or 0),
                         display=_naira_short(rev.get("year")), unit="NGN", delta=rev.get("trend"),
                         delta_label=None if rev.get("trend") is None else "this month vs last month",
                         tone="neutral" if rev.get("trend") is None else ("good" if rev["trend"] >= 0 else "warn"),
                         hint="Confirmed payments."))
    if inst:
        kpis.append(_kpi("institutions_approved", "Approved institutions", inst.get("approved", 0),
                         hint=f"{inst.get('total', 0):,} institutions in all."))
    if cases:
        kpis.append(_kpi("qa_cases_open", "Open QA cases", cases.get("open", 0)))
    if complaints:
        breached = complaints.get("breached", 0)
        kpis.append(_kpi("complaints_open", "Open complaints", complaints.get("open", 0),
                         delta=breached or None, delta_label=f"{breached} past their deadline" if breached else None,
                         tone="bad" if breached else "neutral"))
    if sla:
        rate = sla.get("rate")
        kpis.append(_kpi("qa_sla", "QA tasks on time", rate if rate is not None else 0,
                         display="—" if rate is None else f"{rate:g}%", unit="%",
                         tone="neutral" if rate is None else ("good" if rate >= 80 else "warn" if rate >= 60
                                                              else "bad"),
                         hint=f"{sla.get('overdue_now', 0)} overdue now." if sla.get("overdue_now") else None))
    kpis.append(_kpi("unread_notifications", "Unread notifications", ds._unread_notifications(user),
                     target=None))

    # ---- series -------------------------------------------------------------
    series = []
    activity = ds._safe(lambda: ds._activity_series(user=viewer), None) or ds._chart_empty()
    for key, item in zip(("requests_opened", "requests_completed"), activity.get("series", [])):
        series.append(_series(key, f"Requests {item['name'].lower()} (30 days)", "line",
                              activity["labels"], item["values"]))
    approvals = ds._safe(lambda: ds._approvals_series(user=viewer), None) or ds._chart_empty()
    for item in approvals.get("series", []):
        series.append(_series("approvals_weekly", "Approvals per week", "bar", approvals["labels"], item["values"]))
    if rev.get("labels"):
        series.append(_series("qa_revenue_monthly", "QA revenue per month", "bar", rev["labels"],
                              rev.get("values", []), unit="NGN"))
    trend = (att.get("attendance_charts") or {}).get("trend") or {}
    if trend.get("series"):
        series.append(_series("attendance_daily", "Came to work (%)", "line", trend["labels"],
                              trend["series"][0]["values"], unit="%"))
    if sla.get("labels"):
        series.append(_series("qa_sla_monthly", "QA tasks on time (%)", "line", sla["labels"],
                              sla.get("values", []), unit="%"))
    mix = ds._safe(lambda: ds._status_mix(user=viewer), None) or ds._donut_empty()
    if mix.get("labels"):
        series.append(_series("requests_by_status", "Requests by status", "bar", mix["labels"], mix["values"]))

    # ---- lists --------------------------------------------------------------
    lists = []
    if board == "es":
        queue = ds._safe(lambda: ds._queue_es_signoff(cap=10), []) or []
        lists.append({"key": "es_signoff", "label": "Awaiting your sign-off",
                      "items": [_request_item(r, request) for r in queue]})
        filing = ds._safe(lambda: ds._queue_filing(cap=10), []) or []
        lists.append({"key": "filing", "label": "Awaiting filing",
                      "items": [_request_item(r, request) for r in filing]})
    elif board in ("director", "deputy"):
        queue = ds._safe(lambda: ds._queue_for_user(user, cap=10), []) or []
        lists.append({"key": "my_queue", "label": "Waiting for you",
                      "items": [_request_item(r, request) for r in queue]})
    aged_days = {"es": 5, "director": 7, "deputy": 3, "chairman": 7}[board]
    aged = ds._safe(lambda: ds._aged_approvals(aged_days, 10, viewer), []) or []
    lists.append({"key": "aged_approvals", "label": f"Approvals waiting over {aged_days} days",
                  "items": [_request_item(r, request) for r in aged]})
    depts = att.get("attendance_depts") or []
    if depts:
        lists.append({"key": "attendance_by_department", "label": "Attendance by department (30 days)",
                      "items": [{"title": row["department"].name,
                                 "subtitle": f"{row['staff']} staff · {row['absent']} absent days",
                                 "value": f"{row['came_pct']}%",
                                 "tone": "good" if row["came_pct"] >= 80 else "warn" if row["came_pct"] >= 50
                                 else "bad", "target": None} for row in depts]})
    if cases.get("by_group"):
        lists.append({"key": "qa_cases_by_stage", "label": "Open QA cases by stage",
                      "items": [{"title": g["label"], "subtitle": "", "value": str(g["count"]), "tone": None,
                                 "target": None} for g in cases["by_group"]]})
    if complaints.get("top"):
        lists.append({"key": "complaints_by_layer", "label": "Open complaints by layer",
                      "items": [{"title": t["label"], "subtitle": "", "value": str(t["count"]), "tone": None,
                                 "target": None} for t in complaints["top"]]})
    if board in ("es", "chairman"):
        leaders = ds._leadership_current()
        if leaders:
            lists.append({"key": "leadership", "label": "Leadership", "items": [
                {"title": str(row["position"].name),
                 "subtitle": (getattr(row["tenure"].staff, "full_name", "") or "")
                 + (" (acting)" if row["tenure"].is_acting else ""),
                 "value": None, "tone": None, "target": None} for row in leaders]})

    return {"title": f"{BOARDS[board]} dashboard", "board": board, "scope": scope, "scope_label": scope_label,
            "generated_at": timezone.now(), "kpis": kpis, "series": series, "lists": lists}
