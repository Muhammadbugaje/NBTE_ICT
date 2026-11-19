"""Display helpers for the SOP-02 (QA request) screens. Read-only: nothing
here changes a request. The internal detail and list (qa_views.request_detail
/ request_list) call ``summary`` and ``list_meta``; the templates do the rest.

    summary(req, user, ...)  -> the summary-first header: internal stage, who
                                holds it, days waiting, the viewer's action,
                                the seven-step SOP-02 track.
    list_meta(req)           -> internal stage + "with whom" for one list row.
    note_is_system(row)      -> True when an HND row's prerequisite note was
                                written by the system, not the institution.
"""
from __future__ import annotations

from django.utils import timezone

from qa_core import flow

# The fixed sentence qa_services._prerequisite_note writes when the ND is on the same request.
SYSTEM_PREREQUISITE_NOTE = "The ND of the same programme is on this request."

# (step code, label, who acts, the request statuses that sit on this step)
TRACK = [
    ("draft", "Draft", "Institution", ("draft",)),
    ("submitted", "Submitted", "Automatic", ("submitted",)),
    ("clearance", "Programme clearance", "Director", ("programme_clearance",)),
    ("fee", "QA fee", "Institution pays", ("fee_due",)),
    ("finance", "Finance confirmation", "Director of Finance", ("payment_pending_confirmation", "fee_confirmed")),
    ("ssq", "Self-study (SSQs)", "Institution, then NBTE", ("ssq_in_progress", "ssq_submitted")),
    ("case", "QA case", "SOP-03 workflow", ("in_workflow",)),
]
STEP_OF = {status: i for i, (_code, _label, _who, statuses) in enumerate(TRACK) for status in statuses}
CHANGE_STAGES = ("draft", "submitted", "fee_due", "payment_pending_confirmation")  # R-25


def note_is_system(row):
    return (getattr(row, "prerequisite_note", "") or "").strip() == SYSTEM_PREREQUISITE_NOTE


def stage_label(req):
    """The internal stage name (FlowStage.label), never the institution-facing one."""
    instance = flow.instance_for(req, req.flow_code)
    if instance is not None:
        stages = flow.stages_of(instance)
        if stages:
            return stages[0].label
    return (req.status or "").replace("_", " ").capitalize()


def _since(req, events=None):
    """When the request entered its current stage."""
    if events is None:
        instance = flow.instance_for(req, req.flow_code)
        events = flow.history(instance) if instance is not None else []
    entered = None
    for e in events:
        if e.to_stage == req.status and e.from_stage != e.to_stage:
            entered = e.at
    return entered or req.submitted_at or req.created_at


def _days(since):
    if since is None:
        return None
    return max(0, (timezone.now() - since).days)


def _department(req):
    from institutions import services as inst_services

    fn = getattr(inst_services, "effective_handling_department", None)
    if fn is not None:
        return fn(req.institution)
    return req.institution.handling_department if req.institution.handling_department_id else None


def _short(dept):
    """A department's short name: its code in capitals ("DPAP"), else its name."""
    code = (getattr(dept, "code", "") or "").strip()
    return code.upper() if code else (getattr(dept, "name", "") or "")


def holder(req, *, department=None, fee_issued=True):
    """(short text, who kind) for "with whom": kind is institution | nbte | system | none."""
    st = req.status
    dept = department or _department(req)
    dept_name = _short(dept) or "the handling department"
    if st == "draft":
        return ("Institution — to correct and resubmit" if req.submitted_at else "Institution — not submitted yet",
                "institution")
    if st == "submitted":
        return "System — routing the request", "system"
    if st == "programme_clearance":
        from . import qa_services as svc

        cdept = svc.clearance_department(req) or dept
        name = _short(cdept) or dept_name
        return f"Director, {name}", "nbte"
    if st == "fee_due":
        if req.fee_note and not fee_issued:
            return "NBTE — the fee has not been issued", "nbte"
        return "Institution — to pay the QA fee", "institution"
    if st == "payment_pending_confirmation":
        return "Director of Finance", "nbte"
    if st == "fee_confirmed":
        return "System — opening the SSQs", "system"
    if st == "ssq_in_progress":
        return "Institution — completing the SSQs", "institution"
    if st == "ssq_submitted":
        return f"{dept_name} — SSQ review", "nbte"
    if st == "in_workflow":
        return f"{dept_name} — QA case (SOP-03)", "nbte"
    return "—", "none"


def list_meta(req, *, labels=None, entered=None, departments=None):
    """Set ``req.stage_label``, ``req.with_whom``, ``req.with_kind`` and ``req.waiting_days`` for a list row.

    ``labels`` / ``entered`` / ``departments`` come from :func:`list_prefetch`
    (a few queries for the whole page instead of several per row)."""
    req.stage_label = (labels or {}).get(req.status) or stage_label(req)
    dept = None
    if departments is not None:
        key = req.institution_id
        if key not in departments:
            departments[key] = _department(req)
        dept = departments[key]
    req.with_whom, req.with_kind = holder(req, department=dept)
    if req.status in ("decided", "cancelled"):
        req.waiting_days = None
    elif entered is not None:
        req.waiting_days = _days(entered.get(req.pk) or req.submitted_at or req.created_at)
    else:
        req.waiting_days = _days(_since(req))
    return req


def list_prefetch(reqs):
    """(labels, entered) for a page of requests: internal stage labels of the
    current flow, and when each request entered its current stage."""
    from django.contrib.contenttypes.models import ContentType

    from qa_core.models import FlowEvent, FlowInstance, FlowStage

    from .models import QARequest

    labels = {}
    version = flow.current_version("qa_request")
    if version is not None:
        labels = dict(FlowStage.objects.filter(flow=version).values_list("code", "label"))
    reqs = list(reqs)
    if not reqs:
        return labels, {}
    ct = ContentType.objects.get_for_model(QARequest)
    by_req = {r.pk: r for r in reqs}
    entered = {}
    events = (FlowEvent.objects.filter(instance__content_type=ct, instance__object_id__in=list(by_req))
              .values_list("instance__object_id", "from_stage", "to_stage", "at").order_by("at", "id"))
    for object_id, from_stage, to_stage, at in events:
        req = by_req.get(int(object_id))
        if req is not None and to_stage == req.status and from_stage != to_stage:
            entered[req.pk] = at
    return labels, entered


def _track(req, rows, events, with_text, days):
    if req.status == "cancelled":
        return []
    current = len(TRACK) if req.status == "decided" else STEP_OF.get(req.status, 0)
    left = {}  # stage code -> when the request last left it
    for e in events:
        if e.from_stage and e.from_stage != e.to_stage:
            left[e.from_stage] = e.at
    needs_clearance = any(getattr(r, "is_new_programme", False) or getattr(r, "clearance_status", "")
                          for r in rows if getattr(r, "status", "active") == "active")
    out = []
    for i, (code, label, who, statuses) in enumerate(TRACK):
        step = {"no": i + 1, "code": code, "label": label, "who": who, "done_at": None, "note": ""}
        if i < current:
            step["state"] = "done"
            step["done_at"] = max((left[s] for s in statuses if s in left), default=None)
            if code == "clearance" and step["done_at"] is None:
                step["state"] = "skipped"
                step["note"] = "Not needed" if not needs_clearance else "Cleared earlier"
        elif i == current:
            step["state"] = "current"
            step["note"] = with_text
            step["days"] = days
        else:
            step["state"] = "todo"
            if code == "clearance" and not needs_clearance:
                step["note"] = "Only for new programmes"
        out.append(step)
    return out


def summary(req, user, *, rows=(), events=None, can_clear=False, clear_reason="", can_retry_fee=False,
            ssqs=(), obligations=(), case=None, department=None):
    """Everything the summary-first header of the internal request page shows."""
    if events is None:
        instance = flow.instance_for(req, req.flow_code)
        events = flow.history(instance) if instance is not None else []
    dept = department or _department(req)
    fee_issued = any(getattr(ob, "status", "") not in ("cancelled", "void", "voided") for ob in obligations)
    with_text, kind = holder(req, department=dept, fee_issued=fee_issued)
    closed = req.status in ("decided", "cancelled")
    since = _since(req, events)
    days = None if closed else _days(since)

    action = None
    st = req.status
    if st == "programme_clearance" and can_clear:
        n = sum(1 for r in rows if getattr(r, "clearance_status", "") == "pending")
        action = {"title": "Clear or return the new programmes",
                  "text": f"{n} new programme{'s' if n != 1 else ''} wait{'s' if n == 1 else ''} for your decision. "
                          "Clearing issues the QA fee; returning sends the request back to the institution.",
                  "href": "#req-clear", "label": "Go to the decision"}
    elif st == "fee_due" and can_retry_fee:
        action = {"title": "Issue the QA fee",
                  "text": "The fee could not be issued automatically. Issue it now so the institution can pay.",
                  "href": "#req-fee-note", "label": "Go to the fee"}
    elif st in ("ssq_in_progress", "ssq_submitted") and ssqs:
        mine = [s for s in ssqs
                if getattr(s, "status", "") == "submitted"
                and getattr((getattr(s, "who", None) or {}).get("holder"), "pk", None) == getattr(user, "pk", 0)]
        if mine:
            action = {"title": f"{len(mine)} SSQ{'s' if len(mine) != 1 else ''} wait{'s' if len(mine) == 1 else ''} "
                               "for you",
                      "text": "Open each one below to assign, assess or approve it.",
                      "href": "#req-ssq", "label": "Go to the SSQs"}

    if st == "draft":
        next_text = "The Chief Executive submits the request on the portal."
    elif st == "submitted":
        next_text = "The system routes it: new programmes go to programme clearance, otherwise the QA fee is issued."
    elif st == "programme_clearance":
        next_text = "The Director clears the new programmes (the QA fee is issued) or returns the request."
    elif st == "fee_due":
        next_text = "The institution pays the QA fee and uploads proof, or pays online."
    elif st == "payment_pending_confirmation":
        next_text = "The Director of Finance confirms the payment. The SSQs then open automatically."
    elif st == "fee_confirmed":
        next_text = "The SSQs open automatically, one per programme."
    elif st == "ssq_in_progress":
        next_text = "The institution completes one SSQ per programme; NBTE reviews each one as it comes in."
    elif st == "ssq_submitted":
        next_text = "NBTE approves every SSQ. The QA case (SOP-03) then opens automatically."
    elif st == "in_workflow":
        next_text = "The request continues as a QA case in the SOP-03 workflow."
    elif st == "decided":
        next_text = "The decision has been issued."
    else:
        next_text = ""

    can_change = st in CHANGE_STAGES
    if can_change:
        change_text = "Yes — the institution may still drop or replace a programme (R-25)"
    elif st == "programme_clearance":
        change_text = "No — not during programme clearance. Return the request if the list must change"
    elif st == "cancelled":
        change_text = "No — the request was cancelled"
    else:
        change_text = "No — the fee is confirmed"
    return {
        "stage": stage_label(req), "with": with_text, "with_kind": kind, "since": since, "days": days,
        "closed": closed, "action": action, "can_act": bool(action), "next": next_text,
        "track": _track(req, list(rows), events, with_text, days),
        "department": dept, "department_short": _short(dept), "can_change": can_change, "change_text": change_text,
        "returned": st == "draft" and req.submitted_at is not None,
        "clear_reason": clear_reason if st == "programme_clearance" and not can_clear else "",
        "case": case,
    }
