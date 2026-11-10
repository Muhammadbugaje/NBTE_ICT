"""v2.5 — the cancellation & refund policy (pure: reads, never writes).

A cancellation falls in one *band*, decided by where the subject is in its
workflow; each band's percentage is a setting (Settings → QA · Fees & refunds):

    before_assignment    qa.refund.percent.before_assignment    90   no NBTE officer assigned / no work yet
    under_assessment     qa.refund.percent.under_assessment     50   officer assigned / assessment started,
                                                                     visit cost not yet approved by the ES
    after_cost_approval  qa.refund.percent.after_cost_approval   0   ES approved the visit cost, fund
                                                                     release, visit, report, decision
    nbte_withdrawal      qa.refund.percent.nbte_withdrawal     100   NBTE withdraws / rejects (NBTE's side)

``STAGE_BANDS`` maps ``flow:stage`` (the real stage codes of the
``qa_request``, ``qa_case`` and ``onboarding`` flows, every version) to a
band; ``qa.refund.stage_bands`` (JSON) overrides single keys. ``none`` means
the subject can no longer be cancelled (decided, closed, withdrawn…).

``refund_quote(subject)`` → {stage, stage_label, band, percent, paid,
admin_fee, refund_amount, rule_label, pending, obligations, cancellable}.
"""
from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

import config

ZERO = Decimal("0.00")
BEFORE, UNDER, AFTER, NBTE, NONE = ("before_assignment", "under_assessment", "after_cost_approval",
                                    "nbte_withdrawal", "none")
BANDS = (BEFORE, UNDER, AFTER, NBTE)
DEFAULT_PERCENT = {BEFORE: 90, UNDER: 50, AFTER: 0, NBTE: 100}
BAND_LABELS = {
    BEFORE: "Cancelled before an NBTE officer was assigned",
    UNDER: "Cancelled after the assessment started, before the visit cost was approved",
    AFTER: "Cancelled after the Executive Secretary approved the visit cost",
    NBTE: "Withdrawn by NBTE",
}
# Plain English for institutions (the cancel dialog and the policy page).
BAND_PLAIN = {
    BEFORE: "Before NBTE assigns an officer or starts work on it",
    UNDER: "After an NBTE officer is assigned or the assessment has started, but before the visit cost is approved",
    AFTER: "After the visit cost is approved (funds released, visit done or decision pending)",
    NBTE: "NBTE withdraws or rejects it for reasons on NBTE's side",
}

# Stages after which the ES has approved the visit cost (qa_case flow order).
_CASE_AFTER = ("add_visit_details", "confirm_visit_details", "es_approve_visit", "request_fund_release",
               "post_visit_report", "post_visit_recommendation", "cqac_recommendation", "cqac_chair_approval",
               "es_final_approval")
_CASE_UNDER = ("case_opened", "assign_supervising_staff", "assign_audit_officer", "assign_finance_officer",
               "assign_ict_officer", "supervisors_assigned", "generate_cost", "add_personnel", "confirm_personnel",
               "audit_personnel_cost", "confirm_audit_cost", "es_approve_cost")

STAGE_BANDS = {
    # QA request (SOP-02). in_workflow is resolved through the QA case;
    # ssq_in_progress through the SSQs (any already with NBTE → under_assessment).
    "qa_request:draft": BEFORE, "qa_request:submitted": BEFORE, "qa_request:programme_clearance": BEFORE,
    "qa_request:fee_due": BEFORE, "qa_request:payment_pending_confirmation": BEFORE,
    "qa_request:fee_confirmed": BEFORE, "qa_request:ssq_in_progress": BEFORE,
    "qa_request:ssq_in_review": UNDER, "qa_request:ssq_submitted": UNDER, "qa_request:in_workflow": UNDER,
    "qa_request:decided": NONE, "qa_request:cancelled": NONE,
    # QA case (SOP-03)
    **{f"qa_case:{c}": UNDER for c in _CASE_UNDER},
    **{f"qa_case:{c}": AFTER for c in _CASE_AFTER},
    "qa_case:programme_auto_update": NONE, "qa_case:closed": NONE, "qa_case:cancelled": NONE,
    # Onboarding application (SOP-01, v1–v4)
    "onboarding:draft": BEFORE, "onboarding:submitted": BEFORE, "onboarding:fee_due": BEFORE,
    "onboarding:payment_pending_confirmation": BEFORE, "onboarding:fee_confirmed": BEFORE,
    "onboarding:document_upload": BEFORE,
    "onboarding:clearance": UNDER, "onboarding:legal_review": UNDER, "onboarding:finance_review": UNDER,
    "onboarding:academic_planning_review": UNDER, "onboarding:physical_planning_review": UNDER,
    "onboarding:clearance_join": UNDER, "onboarding:institutional_fee_due": UNDER,
    "onboarding:document_review": UNDER, "onboarding:cleared_fee_due": UNDER,
    "onboarding:cleared_payment_pending": UNDER, "onboarding:approval_pending": UNDER,
    "onboarding:programme_selection": UNDER, "onboarding:programme_clearance": UNDER,
    "onboarding:programme_fee_due": UNDER, "onboarding:programme_payment_pending": UNDER,
    "onboarding:programme_fee_confirmed": UNDER, "onboarding:ssq_in_progress": UNDER,
    "onboarding:ssq_submitted": UNDER, "onboarding:withdrawn": NONE,
}
PAID_STATUSES = ("confirmed", "overpaid", "partially_paid")
UNPAID_VOIDABLE = ("issued", "awaiting_payment")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool(key, default):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def percent_for(band):
    if band not in BANDS:
        return 0
    return min(100, max(0, _int(f"qa.refund.percent.{band}", DEFAULT_PERCENT[band])))


def min_admin_fee():
    try:
        return max(ZERO, Decimal(str(config.get("qa.refund.min_admin_fee", "0") or "0")))
    except Exception:
        return ZERO


def window_days():
    return max(1, _int("qa.refund.request_window_days", 90))


def requires_es_approval():
    return _bool("qa.refund.requires_es_approval", True)


def void_unpaid_bills():
    return _bool("qa.refund.void_unpaid_bills", True)


def stage_bands():
    """The built-in table with ``qa.refund.stage_bands`` applied on top."""
    table = dict(STAGE_BANDS)
    raw = config.get("qa.refund.stage_bands", {}) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    if isinstance(raw, dict):
        for key, band in raw.items():
            if band in BANDS + (NONE,) and band != NBTE:
                table[str(key)] = band
    return table


def policy_rows():
    """[{band, when, percent, keep}] for the plain-English policy table."""
    rows = []
    for band in (BEFORE, UNDER, AFTER, NBTE):
        pct = percent_for(band)
        rows.append({"band": band, "when": BAND_PLAIN[band], "percent": pct, "keep": 100 - pct})
    return {"rows": rows, "min_admin_fee": min_admin_fee(), "window_days": window_days(),
            "requires_es": requires_es_approval(), "void_unpaid": void_unpaid_bills()}


# ---------------------------------------------------------------------------
# Where is the subject?
# ---------------------------------------------------------------------------
def kind_of(subject):
    from accreditation.models import Application, QARequest

    if isinstance(subject, QARequest):
        return "qa_request"
    if isinstance(subject, Application):
        return "application"
    raise TypeError(f"No refund policy for {type(subject).__name__}.")


def _flow_label(subject, code, stage_code):
    from qa_core import flow

    instance = flow.instance_for(subject, code)
    if instance is not None:
        st = flow.stage(instance, stage_code)
        if st is not None:
            return st.public_label or st.label
    return stage_code.replace("_", " ").capitalize()


def _request_position(req):
    """(key, label) — the precise policy key of a QA request."""
    from accreditation.models import SSQ, FundRelease, QACase

    status = req.status or "draft"
    if status == "in_workflow":
        case = QACase.unscoped.filter(qa_request=req).first()
        if case is not None:
            if FundRelease.unscoped.filter(case=case).exclude(status="returned").exists() \
                    and case.status in _CASE_UNDER:
                return "qa_case:request_fund_release", "Visit funds requested"
            return f"qa_case:{case.status}", _flow_label(case, "qa_case", case.status)
    if status == "ssq_in_progress":
        with_nbte = SSQ.unscoped.filter(qa_request_programme__qa_request=req, is_current=True) \
            .exclude(status__in=SSQ.EDITABLE_STATUSES).exists()
        if with_nbte:
            return "qa_request:ssq_in_review", "Self-study questionnaires under NBTE review"
    return f"qa_request:{status}", _flow_label(req, None, status)


def _onboarding_requests(app):
    """Open onboarding-SSQ QA requests that belong to this application."""
    from accreditation.models import QARequest

    return list(QARequest.unscoped.filter(institution_id=app.institution_id, qa_type__code="onboarding_ssq")
                .exclude(status__in=("decided", "cancelled")).select_related("qa_type"))


def _application_position(app):
    status = app.status or "draft"
    if status == "ssq_submitted":
        for req in _onboarding_requests(app):  # the self-study is with NBTE: its stage decides
            key, label = _request_position(req)
            return key, label
    return f"onboarding:{status}", _flow_label(app, "onboarding", status)


def position(subject):
    """(stage key, label, band) — band ``none`` = cannot be cancelled."""
    kind = kind_of(subject)
    if kind == "application":
        inst = subject.institution
        if getattr(inst, "status", "") == "approved":
            return f"onboarding:{subject.status}", "Institution approved", NONE
        key, label = _application_position(subject)
    else:
        key, label = _request_position(subject)
    band = stage_bands().get(key, UNDER if not key.endswith((":decided", ":cancelled", ":closed", ":withdrawn"))
                             else NONE)
    return key, label, band


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------
def obligations_of(subject):
    """Every bill raised for the subject (and its QA case / onboarding SSQ requests)."""
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    from accreditation.models import QACase, QARequest

    from .models import PaymentObligation

    pairs = []
    if kind_of(subject) == "qa_request":
        pairs.append((ContentType.objects.get_for_model(QARequest), subject.pk))
        case = QACase.unscoped.filter(qa_request=subject).first()
        if case is not None:
            pairs.append((ContentType.objects.get_for_model(QACase), case.pk))
        extra_ids = []
    else:
        pairs.append((ContentType.objects.get_for_model(type(subject)), subject.pk))
        for req in _onboarding_requests(subject):
            pairs.append((ContentType.objects.get_for_model(QARequest), req.pk))
        extra_ids = [subject.fee_obligation_id] if subject.fee_obligation_id else []
    q = Q(pk__in=extra_ids)
    for ct, pk in pairs:
        q |= Q(source_type=ct, source_id=str(pk))
    return list(PaymentObligation.unscoped.filter(q).order_by("created_at", "id"))


def _refunded_already(ob):
    from django.db.models import Sum

    from .models import Refund

    return Refund.unscoped.filter(obligation=ob).exclude(status="rejected").aggregate(s=Sum("amount"))["s"] or ZERO


def money(subject):
    paid, pending, rows = ZERO, ZERO, []
    for ob in obligations_of(subject):
        if ob.status in PAID_STATUSES:
            amount = max(ZERO, (ob.amount_paid or ZERO) - _refunded_already(ob))
            if amount > 0:
                paid += amount
                rows.append({"id": ob.pk, "reference": ob.reference, "paid": str(amount)})
        elif ob.status in ob.CLAIMED:
            pending += ob.amount or ZERO
    return paid, pending, rows


def _q(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute(paid, percent, *, apply_min_fee=True):
    """(admin_fee, refund_amount) for ``paid`` at ``percent``."""
    paid = _q(paid or 0)
    refund = _q(paid * Decimal(percent) / Decimal(100))
    fee = paid - refund
    floor = min(min_admin_fee(), paid) if apply_min_fee and percent < 100 else ZERO
    if fee < floor:
        fee = floor
        refund = paid - fee
    return _q(fee), _q(refund)


def refund_quote(subject, *, by_nbte=False):
    """What the institution would get back if ``subject`` were cancelled now."""
    stage, label, band = position(subject)
    cancellable = band != NONE
    if by_nbte and cancellable:
        band = NBTE
    percent = percent_for(band) if cancellable else 0
    paid, pending, rows = money(subject)
    fee, refund = compute(paid, percent, apply_min_fee=band != NBTE) if cancellable else (paid, ZERO)
    return {"stage": stage, "stage_label": label, "band": band, "percent": percent, "paid": _q(paid),
            "admin_fee": fee, "refund_amount": refund, "pending": _q(pending), "obligations": rows,
            "rule_label": BAND_LABELS.get(band, "Cannot be cancelled at this stage"), "cancellable": cancellable,
            "kind": kind_of(subject)}


def quote_for_storage(quote):
    """JSON-safe copy (Decimals as strings)."""
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in quote.items()}
