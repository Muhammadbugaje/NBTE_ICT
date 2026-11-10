"""The institution's plain-words journey (v2.1 X6; spec_v21 G1.d "Watered-down portal").

The portal never shows NBTE's internal stages (SOP-02/SOP-03 steps, CQAC /
EXCO, officers, which department holds a file). It shows milestones: an
ordered list in the setting ``qa.portal.milestones``, each mapping internal
stages (``"<flow>:<stage>"``) to one plain label.

    milestones_for(flow_code, stage_code) -> [{"code", "label", "state"}]   # done / current / todo
    milestone_label(flow_code, stage_code) -> str
    scrub(text) -> text with forbidden terms and staff names removed
    forbidden_terms() -> list
"""
from __future__ import annotations

import json
import re

ONBOARDING_REVIEW = ["onboarding:submitted", "onboarding:clearance", "onboarding:legal_review",
                     "onboarding:finance_review", "onboarding:academic_planning_review",
                     "onboarding:physical_planning_review", "onboarding:clearance_join"]
QA_CASE_STAGES = [f"qa_case:{s}" for s in (
    "case_opened", "assign_supervising_staff", "assign_audit_officer", "assign_finance_officer",
    "assign_ict_officer", "supervisors_assigned", "generate_cost", "add_personnel", "confirm_personnel",
    "audit_personnel_cost", "confirm_audit_cost", "es_approve_cost", "add_visit_details",
    "confirm_visit_details", "es_approve_visit", "request_fund_release", "post_visit_report",
    "post_visit_recommendation", "cqac_recommendation", "cqac_chair_approval", "es_final_approval",
    "programme_auto_update")]

# Order follows the running onboarding flow (version 1). The owner's new order
# (X14: profile → documents → fee → automatic submission → review) is a new
# flow version; reorder this setting when it goes live.
DEFAULT_MILESTONES = [
    {"code": "profile", "label": "Profile and documents",
     "hint": "Complete your profile and upload the required documents.",
     "stages": ["onboarding:draft", "qa_request:draft"]},
    {"code": "document_review", "label": "Documents under review",
     "hint": "NBTE is reviewing your documents. You will be told if anything needs to change.",
     "stages": ONBOARDING_REVIEW},
    {"code": "fee", "label": "Processing fee",
     "hint": "Pay the processing fee; it is confirmed by NBTE.",
     "stages": ["onboarding:institutional_fee_due", "onboarding:payment_pending_confirmation",
                "onboarding:fee_confirmed"]},
    {"code": "programmes", "label": "Choose your programmes",
     "hint": "Select the programmes you will offer.",
     "stages": ["onboarding:programme_selection", "qa_request:submitted"]},
    {"code": "programme_review", "label": "Programmes under review",
     "hint": "NBTE is reviewing the programmes you chose.",
     "stages": ["onboarding:programme_clearance", "qa_request:programme_clearance"]},
    {"code": "programme_fee", "label": "Programme fee",
     "hint": "Pay the programme processing fee.",
     "stages": ["onboarding:programme_fee_due", "onboarding:programme_payment_pending",
                "onboarding:programme_fee_confirmed", "qa_request:fee_due", "qa_request:payment_pending_confirmation",
                "qa_request:fee_confirmed"]},
    {"code": "ssq", "label": "Self-study questionnaire",
     "hint": "Complete and submit your self-study questionnaire (SSQ).",
     "stages": ["onboarding:ssq_in_progress", "onboarding:ssq_submitted", "qa_request:ssq_in_progress",
                "qa_request:ssq_submitted"]},
    {"code": "review", "label": "Under review by NBTE",
     "hint": "NBTE is reviewing your request. There is nothing for you to do now.",
     "stages": ["qa_request:in_workflow"] + QA_CASE_STAGES[:15]},
    {"code": "visit_decision", "label": "Visit and decision",
     "hint": "NBTE visits, then the decision is issued.",
     "stages": QA_CASE_STAGES[15:] + ["qa_request:decided", "qa_case:closed"]},
]
FLOW_FAMILIES = {"onboarding": ("onboarding",), "qa_request": ("qa_request", "qa_case", "ssq", "inspection"),
                 "qa_case": ("qa_request", "qa_case", "ssq", "inspection")}
END_LABELS = {"withdrawn": "Withdrawn", "cancelled": "Cancelled"}

DEFAULT_FORBIDDEN = ["CQAC", "EXCO", "SOP-01", "SOP-1", "SOP-2", "SOP-02", "SOP-03", "SOP-3", "SOP 1", "SOP 2",
                     "SOP 3", "desk officer", "first pass",
                     "Committee on Quality Assurance"]


def _setting(key, default):
    try:
        import config
        raw = config.get(key, None)
    except Exception:
        return default
    if raw in (None, ""):
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return default
    return raw


def milestones():
    rows = _setting("qa.portal.milestones", DEFAULT_MILESTONES)
    return rows if isinstance(rows, list) and rows else DEFAULT_MILESTONES


def forbidden_terms():
    rows = _setting("qa.portal.forbidden_terms", DEFAULT_FORBIDDEN)
    return [str(t) for t in rows] if isinstance(rows, list) else DEFAULT_FORBIDDEN


def _family(flow_code):
    return FLOW_FAMILIES.get(flow_code, (flow_code,))


def milestones_for(flow_code, stage_code):
    """The milestone bar for one journey: ``[{"code", "label", "hint", "state"}]``."""
    fam = _family(flow_code)
    rows = [m for m in milestones() if any(s.split(":", 1)[0] in fam for s in m.get("stages", []))]
    key = f"{flow_code}:{stage_code}"
    current = next((i for i, m in enumerate(rows) if key in m.get("stages", [])), None)
    terminal_done = stage_code in ("closed", "decided")
    out = []
    for i, m in enumerate(rows):
        if current is None:
            state = "todo"
        elif i < current or (i == current and terminal_done):
            state = "done"
        elif i == current:
            state = "current"
        else:
            state = "todo"
        out.append({"code": m.get("code", ""), "label": m.get("label", ""), "hint": m.get("hint", ""),
                    "state": state})
    return out


def show_department_names():
    raw = _setting("qa.portal.show_department_names", False)
    return raw is True or str(raw).strip().lower() in ("1", "true", "yes", "on")


def application_stage(app):
    """The onboarding stage code to place on the milestone bar (parallel
    clearance branches count as "clearance")."""
    status = getattr(app, "status", "") or "draft"
    if status.endswith("_review"):
        return "clearance"
    return status


def portal_board(app):
    """X6: the onboarding milestone bar the institution sees."""
    return milestones_for("onboarding", application_stage(app))


def request_board(req):
    """X6: the milestone bar of a QA request (SOP-02/03 shown as plain steps)."""
    from accreditation.models import QACase
    if req.status == "cancelled":
        return []
    case = QACase.unscoped.filter(qa_request=req).only("status").first()
    if case is not None and req.status == "in_workflow":
        return milestones_for("qa_case", case.status)
    return milestones_for("qa_request", req.status)


def portal_status(flow_code, stage_code, default=""):
    """X6: the one-line status the institution sees (a milestone label)."""
    return milestone_label(flow_code, stage_code, default) or default or "In progress"


def milestone_label(flow_code, stage_code, default=""):
    if stage_code in END_LABELS:
        return END_LABELS[stage_code]
    key = f"{flow_code}:{stage_code}"
    for m in milestones():
        if key in m.get("stages", []):
            return m.get("label", default)
    if flow_code == "onboarding":
        return {
            "fee_due": "Payment due",
            "payment_pending_confirmation": "Payment awaiting confirmation",
            "document_upload": "Documents unlocked",
            "document_review": "Documents under review",
        }.get(stage_code, default)
    return default


def scrub(text, *, extra_names=()):
    """Remove forbidden internal terms (and the given staff names) from a
    portal string. Used as a render-time guard; the source of truth is that
    portal views only ever build text from milestones and public rows."""
    if not text:
        return text
    out = str(text)
    for term in list(forbidden_terms()) + [n for n in extra_names if n]:
        out = re.sub(re.escape(term), "NBTE", out, flags=re.IGNORECASE)
    return out


# ===========================================================================
# The ONE linear journey the institution sees (portal dashboard, application
# page, QA request pages). Built from real data; never shows internal stage
# codes, staff names or jargon. Department names only when
# ``qa.portal.show_department_names`` is on.
#
#     linear_journey(institution, user=None) -> {
#         "steps": [{n, code, title, label, state, sentence, holder, todo,
#                    wait, programmes, since_days}],
#         "current": <step>, "position": n, "total": N, "pct": 0-100,
#         "headline": str, "mine": bool, "complete": bool, "extra": [<track>]}
#     request_journey(req, user=None) -> the same shape, request steps only
#
# state is one of: done / current (NBTE has it) / action (you must act) /
# upcoming.
# ===========================================================================
JARGON_PATTERNS = [
    (re.compile(r"\s*[(\[]\s*SOP[\s\-_]*0?\d\s*[)\]]", re.I), ""),
    (re.compile(r"\bSOP[\s\-_]*0?\d\s*(programme\s+)?self[\s-]*study", re.I), "programme self-study"),
    (re.compile(r"\bSOP[\s\-_]*0?\d\s*[·:\-–—]?\s*", re.I), ""),
    (re.compile(r"\bSOP\b", re.I), "process"),
    (re.compile(r"NBTE institution approval is now required before formal QA requests can begin\.?", re.I),
     "NBTE now checks each self-study, then approves your institution."),
    (re.compile(r"\bSSQs\b"), "self-studies"),
    (re.compile(r"\bSSQ\b"), "self-study"),
    (re.compile(r"\bCQAC\b"), "NBTE"),
    (re.compile(r"\bEXCO\b"), "NBTE"),
    (re.compile(r"Committee on Quality Assurance", re.I), "NBTE"),
]

NBTE = "NBTE"
WAIT = "NBTE has it. We'll tell you by e-mail and here on the portal as soon as it is your turn."

STEP_DEFS = [
    # code, label (to do), label (done)
    ("registered", "Register with NBTE", "Registered"),
    ("documents", "Send your documents", "Documents accepted"),
    ("programmes", "Choose & pay for programmes", "Programmes chosen & paid"),
    ("self_studies", "Fill the self-studies", "Self-studies submitted"),
    ("review", "NBTE reviews your self-studies", "Self-studies reviewed"),
    ("approval", "NBTE approves your institution", "Institution approved"),
    ("request", "Ask for accreditation & pay the fee", "Request sent & fee paid"),
    ("request_ssq", "Self-study for the visit", "Visit self-study done"),
    ("visit_planned", "NBTE plans your visit", "Visit planned"),
    ("visit", "Accreditation visit", "Visit done"),
    ("decision", "Report & decision", "Decision issued"),
]
REQUEST_CODES = ("request", "request_ssq", "visit_planned", "visit", "decision")

# Application status → the onboarding step it belongs to.
_DOC_STATUSES = ("draft", "fee_due", "payment_pending_confirmation", "fee_confirmed", "document_upload",
                 "document_review", "submitted", "clearance", "clearance_join")
_PROG_STATUSES = ("cleared_fee_due", "cleared_payment_pending", "approved_fee_due", "approved_payment_pending",
                  "institutional_fee_due", "institutional_payment_pending", "approval_pending",
                  "programme_selection", "programme_clearance", "programme_fee_due", "programme_payment_pending")
_SSQ_STATUSES = ("programme_fee_confirmed", "ssq_in_progress")

# Self-study review (per programme): code → (plain label, tone, finished?)
REVIEW_LABELS = {
    "draft": ("Self-study to fill in", "action", False),
    "awaiting_assignment": ("Received — NBTE is choosing a reviewer", "current", False),
    "under_assessment": ("NBTE is reviewing it", "current", False),
    "awaiting_director": ("Reviewed — waiting for final sign-off", "current", False),
    "cleared": ("Accepted ✓", "done", True),
    "approved": ("Approved ✓", "done", True),
    "returned_to_institution": ("Sent back to you — please make the changes asked", "action", False),
}

# QA case stages (case_services) collapsed into three friendly steps.
CASE_PLANNING = ("awaiting_sop03", "case_opened", "assign_supervising_staff", "assign_audit_officer",
                 "assign_finance_officer", "assign_ict_officer", "supervisors_assigned", "generate_cost",
                 "add_personnel", "confirm_personnel", "audit_personnel_cost", "confirm_audit_cost",
                 "es_approve_cost", "add_visit_details", "confirm_visit_details", "es_approve_visit")
CASE_VISIT = ("request_fund_release",)
CASE_DECISION = ("post_visit_report", "post_visit_recommendation", "cqac_recommendation", "cqac_chair_approval",
                 "es_final_approval", "programme_auto_update")
CASE_SENTENCES = {
    "planning": "NBTE is planning your visit — choosing the visiting team and agreeing the dates.",
    "visit": "Your visit is being arranged. NBTE's team will come to inspect the programmes.",
    "post_visit_report": "The visit is done. NBTE's team is writing the report.",
    "post_visit_recommendation": "NBTE is reading the visit report.",
    "deciding": "NBTE is making its decision on your programmes.",
    "programme_auto_update": "The decision is made and is being recorded. You will receive it shortly.",
}


def plain(text):
    """Strip internal jargon (SOP-01/02/03, SSQ, committee names) from an
    institution-facing string."""
    if not text:
        return text
    out = str(text)
    for pattern, repl in JARGON_PATTERNS:
        out = pattern.sub(repl, out)
    return re.sub(r"\s{2,}", " ", out).strip()


def nbte_holder(department=None):
    if department is not None and show_department_names():
        name = getattr(department, "name", "") or ""
        if name:
            return f"NBTE – {name}"
    return NBTE


def _url(name, *args):
    from django.urls import NoReverseMatch, reverse
    try:
        return reverse(f"qa_portal:{name}", args=args)
    except NoReverseMatch:
        return ""


def _blank_steps():
    out = []
    for i, (code, todo, done) in enumerate(STEP_DEFS, start=1):
        out.append({"n": i, "code": code, "title": todo, "label_todo": todo, "label_done": done,
                    "state": "upcoming", "sentence": "", "holder": "", "todo": None, "wait": WAIT,
                    "programmes": [], "since_days": None})
    return out


def _set(step, state, sentence="", *, holder="", todo=None, programmes=None, since_days=None):
    step["state"] = state
    step["sentence"] = plain(sentence) if sentence else step.get("sentence", "")
    step["holder"] = holder or step.get("holder") or (NBTE if state == "current" else "")
    step["todo"] = todo
    if programmes is not None:
        step["programmes"] = programmes
    if since_days is not None:
        step["since_days"] = since_days
    return step


def _done(step, sentence=""):
    return _set(step, "done", sentence)


def _todo(title, text, cta_label="", cta_url=""):
    return {"title": plain(title), "text": plain(text), "cta_label": cta_label, "cta_url": cta_url}


def _days_since(when):
    if not when:
        return None
    from django.utils import timezone
    try:
        return max((timezone.now() - when).days, 0)
    except TypeError:
        return None


def _finance_department():
    try:
        from qa_core.departments import qa_department
        return qa_department("finance")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Onboarding (steps 1–6)
# ---------------------------------------------------------------------------
def _latest_application(inst):
    from accreditation.models import Application
    return (Application.unscoped.filter(institution=inst).exclude(status="withdrawn")
            .order_by("-created_at", "-id").first())


def _onboarding_card(inst, app, user):
    """The application's "what is happening / what next" (journey_state)."""
    from accreditation.portal_views import journey_state
    from accreditation import onboarding_v2, onboarding_v3
    from institutions import services as inst_services

    head = None
    try:
        head = onboarding_v2.header(app, inst) if (app is None or onboarding_v2.is_v2(app)) else None
    except Exception:
        head = None
    obligations, returned, studies = [], 0, None
    if app is not None:
        from django.contrib.contenttypes.models import ContentType
        from payments.models import PaymentObligation
        from accreditation.models import Application, OnboardingSSQ
        from institutions.models import DocumentReview

        obligations = list(PaymentObligation.unscoped.filter(
            source_type=ContentType.objects.get_for_model(Application), source_id=str(app.pk))
            .order_by("-created_at")) if hasattr(PaymentObligation, "unscoped") else list(
            PaymentObligation.objects.filter(source_type=ContentType.objects.get_for_model(Application),
                                             source_id=str(app.pk)).order_by("-created_at"))
        manager = getattr(DocumentReview, "unscoped", DocumentReview.objects)
        returned = manager.filter(application=app, status=DocumentReview.RETURNED).count()
        if app.status == "ssq_in_progress":
            studies = list(OnboardingSSQ.unscoped.filter(application=app)[:1])
    is_ce = bool(user is not None and getattr(user, "is_authenticated", False)
                 and inst_services.is_chief_executive(user))
    return journey_state(
        inst, app, head=head, obligations=obligations, is_ce=is_ce, returned=returned,
        docs=inst_services.checklist_summary(inst_services.checklist(inst)), self_studies=studies,
        hold=inst_services.active_hold(app) if app is not None else None,
        v3=onboarding_v3.is_v3(app) if app is not None else onboarding_v3.latest_flow_is_v3())


def _apply_card(step, card, inst):
    """Turn a journey_state card into the step's state, sentence, holder and box."""
    mine = bool(card.get("mine"))
    if mine:
        _set(step, "action", card.get("body", ""), holder=card.get("who") or "You",
             todo=_todo(card.get("title", ""), card.get("body", ""), card.get("cta_label", ""),
                        card.get("cta_url", "")))
        step["sentence"] = plain(card.get("title", ""))
    else:
        who = card.get("who") or NBTE
        dept = _finance_department() if "Finance" in who else getattr(inst, "handling_department", None)
        _set(step, "current", card.get("title", ""), holder=nbte_holder(dept))
        step["detail"] = plain(card.get("body", ""))
    return step


def _programme_name(programme):
    return str(programme) if programme is not None else "Programme"


def review_code(ssq):
    """The per-programme review stage of an onboarding self-study, as one of
    REVIEW_LABELS' codes. Uses ``onboarding_ssq.review_stage`` when present,
    else the institution approval stage."""
    submitted = getattr(ssq, "status", "") == "submitted"
    try:
        from accreditation import onboarding_ssq
        fn = getattr(onboarding_ssq, "review_stage", None)
        if fn is not None:
            stage = fn(ssq) or {}
            code = stage.get("code") or ""
            if code == "returned_to_institution":  # back with the institution (a draft again)
                return code, stage.get("days_at_stage")
            if not submitted:
                return "draft", None
            if code not in REVIEW_LABELS:
                code = "under_assessment"
            return code, stage.get("days_at_stage")
    except Exception:
        pass
    if not submitted:
        return "draft", None
    try:
        from accreditation.onboarding_ssq import approval_stage
        stage = approval_stage(ssq.application)
        code = {"approved": "approved", "approval_pending": "awaiting_director"}.get(stage.get("code"),
                                                                                     "under_assessment")
        return code, _days_since(stage.get("at") or ssq.submitted_at)
    except Exception:
        return "under_assessment", _days_since(getattr(ssq, "submitted_at", None))


def correction_notes(ssq):
    """The notes NBTE wrote when it returned a self-study for correction."""
    try:
        from accreditation.models import OnboardingSSQReview
        return (OnboardingSSQReview.objects.filter(ssq=ssq).values_list("director_remark", flat=True).first()
                or "").strip()
    except Exception:
        return ""


def _self_studies(inst):
    from accreditation.models import OnboardingSSQ
    rows = []
    for ssq in (OnboardingSSQ.unscoped.filter(application__institution=inst)
                .exclude(application__status="withdrawn")
                .select_related("programme", "programme__level", "application").order_by("created_at", "id")):
        code, days = review_code(ssq)
        label, tone, finished = REVIEW_LABELS[code]
        notes = correction_notes(ssq) if code == "returned_to_institution" else ""
        if notes:
            label = f"{label}. NBTE's notes: {notes}"
        rows.append({"name": _programme_name(ssq.programme), "code": code, "label": label, "state": tone,
                     "finished": finished, "days": days, "ssq": ssq, "notes": notes,
                     "url": _url("application_ssq", ssq.pk)})
    return rows


def _onboarding(steps, inst, user):
    s = {st["code"]: st for st in steps}
    _done(s["registered"], f"{inst.name} is registered on the NBTE portal.")
    approved = getattr(inst, "status", "") == "approved"
    app = _latest_application(inst)
    studies = _self_studies(inst)
    if approved:
        for code in ("documents", "programmes", "self_studies", "review"):
            _done(s[code])
        _done(s["approval"], f"NBTE approved {inst.name}.")
        _later_programmes(s, studies)
        return
    try:  # v2.1 X13: the profile set-up comes first (until the application is under way)
        from institutions import profile
        early = app is None or getattr(app, "status", "") == "draft"
        if early and profile.gate_enabled() and not profile.profile_complete(inst):
            _, pct = profile.setup_checklist(inst)
            _set(s["documents"], "action", "Finish setting up your portal first.", holder="You",
                 todo=_todo("Finish setting up your portal",
                            f"{pct}% done. Tell NBTE who runs {inst.name}. Everything else opens when you finish.",
                            "Continue setup", _url("setup")))
            return
    except Exception:
        pass
    status = getattr(app, "status", "") if app is not None else ""
    try:
        card = _onboarding_card(inst, app, user)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("journey card failed")
        card = {"title": "Your application is with NBTE", "body": "", "mine": False}
    if app is None or status in _DOC_STATUSES or status.endswith("_review"):
        _apply_card(s["documents"], card, inst)
        return
    _done(s["documents"], "NBTE accepted all your documents.")
    if status in _PROG_STATUSES:
        _apply_card(s["programmes"], card, inst)
        return
    _done(s["programmes"], "Your programmes are chosen and the fee is paid.")
    if status in _SSQ_STATUSES:
        _apply_card(s["self_studies"], card, inst)
        s["self_studies"]["programmes"] = [dict(r, label=("Ready to send" if r["code"] == "draft" and not _problems(r)
                                                          else r["label"])) for r in studies]
        return
    if status != "ssq_submitted":
        _apply_card(s["documents" if not status else "self_studies"], card, inst)
        return
    _done(s["self_studies"], "Your Chief Executive sent the self-studies to NBTE.")
    _review_step(s["review"], inst, studies)
    if s["review"]["state"] != "done":
        return
    # Every self-study is reviewed: the institution's approval.
    try:
        from accreditation.onboarding_ssq import approval_stage
        stage = approval_stage(app)
    except Exception:
        stage = {"code": "under_review"}
    sentence = ("NBTE has reviewed your self-studies. The approval of your institution is waiting for final "
                "sign-off." if stage.get("code") == "approval_pending" else
                "NBTE is preparing the approval of your institution.")
    _set(s["approval"], "current", sentence, holder=nbte_holder(getattr(inst, "handling_department", None)),
         since_days=_days_since(stage.get("at")))


def _problems(row):
    try:
        from accreditation.onboarding_ssq import submission_problems
        return submission_problems(row["ssq"])
    except Exception:
        return []


def _review_step(step, inst, studies):
    submitted = [r for r in studies if r["code"] != "draft"]
    rows = submitted or studies
    if rows and all(r["finished"] for r in rows):
        _done(step, "NBTE reviewed every self-study.")
        step["programmes"] = rows
        return
    back = [r for r in rows if r["code"] == "returned_to_institution"]
    dept = getattr(inst, "handling_department", None)
    if back:
        first = back[0]
        notes = f" NBTE's notes: “{first['notes']}”" if first.get("notes") else ""
        _set(step, "action", f"Needs your action: correct and resubmit {first['name']}.", holder="You",
             todo=_todo(f"Correct and resubmit {first['name']}",
                        f"NBTE asked for some changes.{notes} Open the self-study, make the changes, then your "
                        "Chief Executive presses “Resubmit to NBTE”. Only this programme goes back for review.",
                        "Correct the self-study", first["url"]),
             programmes=rows)
        return
    busy = next((r for r in rows if not r["finished"]), None)
    sentence = (f"NBTE is reviewing {busy['name']}." if busy is not None else "NBTE is reviewing your self-studies.")
    _set(step, "current", sentence, holder=nbte_holder(dept), programmes=rows,
         since_days=busy["days"] if busy is not None else None)


def _later_programmes(s, studies):
    """Programmes added after the institution was approved: each needs only
    its own self-study. Shown as a side track."""
    pending = [r for r in studies if not r["finished"]]
    s["approval"]["extra"] = pending


# ---------------------------------------------------------------------------
# QA requests and cases (steps 7–11)
# ---------------------------------------------------------------------------
def _active_request(inst):
    from accreditation.models import QARequest
    qs = QARequest.unscoped.filter(institution=inst).exclude(status="cancelled").select_related("qa_type")
    open_req = qs.exclude(status="decided").order_by("-created_at", "-id").first()
    return open_req or qs.order_by("-created_at", "-id").first()


def _request_obligation(req):
    try:
        from django.contrib.contenttypes.models import ContentType
        from payments.models import PaymentObligation
        from accreditation.models import QARequest
        manager = getattr(PaymentObligation, "unscoped", PaymentObligation.objects)
        return (manager.filter(source_type=ContentType.objects.get_for_model(QARequest), source_id=str(req.pk))
                .exclude(status="cancelled").order_by("-created_at").first())
    except Exception:
        return None


def _request_ssq_rows(req):
    from accreditation.models import SSQ
    rows = []
    for ssq in (SSQ.unscoped.filter(qa_request_programme__qa_request=req, is_current=True)
                .select_related("qa_request_programme__programme__level")):
        prog = ssq.qa_request_programme.programme
        status = ssq.status
        if status in ("accepted", "analysed"):
            label, tone, finished = "Accepted ✓", "done", True
        elif status in ("draft", "returned", "returned_to_department", "submitted_by_department"):
            label = "Sent back to you — please make the changes asked" if status == "returned" else \
                "Self-study to fill in"
            tone, finished = "action", False
        else:
            try:
                from accreditation import ssq_routing
                label = ssq_routing.public_stage(ssq) or "Received by NBTE"
            except Exception:
                label = "Received by NBTE"
            tone, finished = "current", False
        rows.append({"name": _programme_name(prog), "code": status, "label": plain(label), "state": tone,
                     "finished": finished, "days": _days_since(ssq.submitted_at),
                     "url": _url("ssq", req.pk, ssq.pk)})
    return rows


def _request_steps(s, inst, req, user):
    type_name = getattr(req.qa_type, "name", "accreditation") if req is not None else "accreditation"
    dept = getattr(inst, "handling_department", None)
    if req is None:
        s["request"]["title"] = "Ask NBTE for accreditation"
        _set(s["request"], "action", "Your institution is approved. You can now ask NBTE to look at your "
                                     "programmes.", holder="You",
             todo=_todo("Ask NBTE for your first visit",
                        "Start a request for a Resource Inspection of your programmes. NBTE then tells you the "
                        "fee and what to send.", "Start a request",
                        _url("qa_request_new") + "?type=resource_inspection"))
        return
    s["request"]["label_todo"] = s["request"]["title"] = f"Ask for {type_name} & pay the fee"
    s["request"]["label_done"] = f"{type_name} requested & paid"
    req_url = _url("qa_request", req.pk)
    st = req.status
    finance = nbte_holder(_finance_department())
    if st == "draft":
        returned = req.submitted_at is not None
        _set(s["request"], "action", f"Your {type_name} request is waiting for you to send it.", holder="You",
             todo=_todo("NBTE sent your request back — fix it and send again" if returned else
                        "Finish and send your request",
                        "Check the programmes listed, then your Chief Executive sends the request to NBTE.",
                        "Open the request", req_url))
        return
    if st in ("submitted", "programme_clearance"):
        _set(s["request"], "current", f"NBTE is checking your {type_name} request.", holder=nbte_holder(dept),
             since_days=_days_since(req.submitted_at))
        return
    if st == "fee_due":
        bill = _request_obligation(req)
        if bill is None:
            _set(s["request"], "current", "NBTE is preparing your bill. It will appear under Payments.",
                 holder=finance)
        else:
            from payments.money import naira
            _set(s["request"], "action", f"Pay the {type_name} fee.", holder="You",
                 todo=_todo(f"Pay the {type_name} fee",
                            f"Pay {naira(bill.balance)} online or by bank transfer. NBTE confirms the payment and "
                            "then the next step opens.", "Pay now", _url("payment", bill.pk)))
        return
    if st == "payment_pending_confirmation":
        _set(s["request"], "current", "You paid. NBTE is confirming the payment — online payments usually take "
                                      "minutes, bank transfers a few working days.", holder=finance)
        return
    _done(s["request"], f"You asked for {type_name} and the fee is paid.")
    if st in ("fee_confirmed", "ssq_in_progress"):
        rows = _request_ssq_rows(req)
        _set(s["request_ssq"], "action", "Fill in the self-study for each programme in your request.",
             holder="You", programmes=rows,
             todo=_todo("Fill in the self-study for each programme",
                        "Your answers save as you type. When every programme is complete, your Chief Executive "
                        "sends them to NBTE.", "Open the self-studies", req_url))
        return
    if st == "ssq_submitted":
        rows = _request_ssq_rows(req)
        back = [r for r in rows if r["state"] == "action"]
        if back:
            _set(s["request_ssq"], "action", f"NBTE sent back the self-study for {back[0]['name']}.",
                 holder="You", programmes=rows,
                 todo=_todo(f"Update the self-study for {back[0]['name']}",
                            "Make the changes NBTE asked for and send it again.", "Open the self-study",
                            back[0]["url"]))
        else:
            busy = next((r for r in rows if not r["finished"]), None)
            _set(s["request_ssq"], "current",
                 f"NBTE is reviewing the self-study for {busy['name']}." if busy else
                 "NBTE is reviewing your self-studies.", holder=nbte_holder(dept), programmes=rows)
        return
    _done(s["request_ssq"], "NBTE accepted your self-studies.")
    if st == "decided":
        for code in ("visit_planned", "visit", "decision"):
            _done(s[code])
        s["decision"]["sentence"] = "NBTE's decision has been issued. You can see it under Letters."
        return
    _case_steps(s, inst, req)


def _case_steps(s, inst, req):
    from accreditation.models import QACase
    case = QACase.unscoped.filter(qa_request=req).select_related("owning_department").first()
    dept = getattr(case, "owning_department", None) or getattr(inst, "handling_department", None)
    holder = nbte_holder(dept)
    stage = getattr(case, "status", "") or "awaiting_sop03"
    visit = None
    if case is not None:
        try:
            from accreditation.models import VisitDetail
            visit = VisitDetail.unscoped.filter(case=case).first()
        except Exception:
            visit = None
    when = ""
    if visit is not None and visit.planned_start and visit.status in ("confirmed", "es_approved"):
        end = visit.planned_end or visit.planned_start
        when = visit.planned_start.strftime("%-d %b %Y") if end == visit.planned_start else \
            f"{visit.planned_start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}"
    since = _days_since(getattr(case, "updated_at", None))
    if stage == "closed":
        for code in ("visit_planned", "visit", "decision"):
            _done(s[code])
        s["decision"]["sentence"] = "NBTE's decision has been issued. You can see it under Letters."
        return
    if stage in CASE_PLANNING or stage not in CASE_VISIT + CASE_DECISION:
        _set(s["visit_planned"], "current", CASE_SENTENCES["planning"], holder=holder, since_days=since)
        return
    _done(s["visit_planned"], f"Your visit is planned{(' for ' + when) if when else ''}.")
    if stage in CASE_VISIT:
        sentence = (f"NBTE's team visits you on {when}. Please have your staff, facilities and records ready."
                    if when else CASE_SENTENCES["visit"])
        _set(s["visit"], "current", sentence, holder=holder, since_days=since)
        s["visit"]["wait"] = ("Get ready for the visit: make sure the people and records for each programme are "
                              "available. Nothing to send now — we'll notify you of any change.")
        return
    _done(s["visit"], "NBTE's team visited your institution.")
    key = stage if stage in ("post_visit_report", "post_visit_recommendation", "programme_auto_update") \
        else "deciding"
    _set(s["decision"], "current", CASE_SENTENCES[key], holder=holder, since_days=since)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _finish(steps, *, extra=None):
    current = next((st for st in steps if st["state"] != "done"), None)
    if current is not None and current["state"] == "upcoming":
        current["state"] = "current"
        current["holder"] = current["holder"] or NBTE
    for st in steps:
        st["label"] = st["label_done"] if st["state"] == "done" else st["title"]
    done = sum(1 for st in steps if st["state"] == "done")
    total = len(steps)
    headline = (current["sentence"] or current["title"]) if current is not None else "Every step is complete."
    return {"steps": steps, "current": current, "position": current["n"] if current else total,
            "total": total, "pct": round(100 * done / total) if total else 0, "headline": headline,
            "mine": bool(current and current["state"] == "action"), "complete": current is None,
            "extra": extra or [], "show_departments": show_department_names()}


class _PortalUrls:
    """Resolve ``qa_portal:`` names even outside a portal request (tests,
    tasks): switch to the portal URLconf for the duration, then restore."""

    def __enter__(self):
        from django.urls import NoReverseMatch, get_urlconf, reverse, set_urlconf
        self.previous = get_urlconf()
        self.switched = False
        try:
            reverse("qa_portal:dashboard")
        except NoReverseMatch:
            set_urlconf("qa_core.portal_urls")
            self.switched = True
        return self

    def __exit__(self, *exc):
        if self.switched:
            from django.urls import set_urlconf
            set_urlconf(self.previous)
        return False


def linear_journey(institution, user=None):
    with _PortalUrls():
        return _linear_journey(institution, user)


def request_journey(req, user=None):
    """The compact journey of one QA request (steps 7–11)."""
    with _PortalUrls():
        return _request_journey(req, user)


def _linear_journey(institution, user=None):
    """The whole journey, registration to accreditation decision, as one
    ordered list of plain-words steps. Never raises: a part that fails shows
    as "with NBTE" rather than breaking the portal."""
    steps = _blank_steps()
    if institution is None:
        return _finish(steps)
    s = {st["code"]: st for st in steps}
    try:
        _onboarding(steps, institution, user)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("linear journey (onboarding) failed")
    extra = list(s["approval"].pop("extra", []) or [])
    if s["approval"]["state"] == "done":
        try:
            _request_steps(s, institution, _active_request(institution), user)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("linear journey (requests) failed")
    out = _finish(steps, extra=extra)
    if extra:  # a programme added later needs only its own self-study
        first = extra[0]
        out["side_track"] = {
            "title": "New programme: only its own self-study is needed",
            "programmes": extra,
            "todo": _todo(f"Fill in the self-study for {first['name']}",
                          "Your institution is already approved, so only this programme's self-study is needed.",
                          "Open the self-study", first["url"]) if first["state"] == "action" else None,
        }
    return out


def _request_journey(req, user=None):
    steps = [st for st in _blank_steps() if st["code"] in REQUEST_CODES]
    for i, st in enumerate(steps, start=1):
        st["n"] = i
    if req is None or getattr(req, "status", "") == "cancelled":
        return None
    s = {st["code"]: st for st in steps}
    try:
        _request_steps(s, req.institution, req, user)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("request journey failed")
    return _finish(steps)
