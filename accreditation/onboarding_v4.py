"""SOP-01 onboarding v4 (v2.3): the SOP order, with a switch for when the
institutional fee is paid.

    after_clearance : profile → upload → departments review → (all cleared) →
    (SOP-01 order)    pay → Finance confirms → programmes → …
    before_review   : profile → pay → Finance confirms → upload → review →
                      (all cleared) → programmes → …

There is no extra approval after the documents: each department's clearance
of its own documents is the decision, as the SOP says. When the last document
is approved the application moves on by itself and the institution is told
what to do next. The institution itself is approved only at the end of SOP-03.

Setting: ``qa.onboarding.fee_timing`` (after_clearance | before_review). The
choice is fixed for each application when the Chief Executive declares, so a
change never strands an application half-way.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from qa_core import timeline

logger = logging.getLogger(__name__)

V4 = 4
AFTER, BEFORE = "after_clearance", "before_review"

ONBOARDING_FLOW_V4 = {
    "code": "onboarding",
    "name": "Onboarding (SOP-01) v4 — SOP order, fee timing by setting",
    "subject_model": "accreditation.Application",
    "description": "Profile → (institutional fee before or after the document review, per setting) → documents "
                   "→ per-document departmental review → programmes → programme fee → SSQ → Chief Executive "
                   "submits → SOP-03.",
    "stages": [
        ("draft", "Draft", "Complete your institutional profile", "start", "institution", {}),
        ("fee_due", "Institutional fee due", "Pay the institutional processing fee", "action", "institution", {}),
        ("payment_pending_confirmation", "Payment awaiting Finance confirmation",
         "Paid — awaiting confirmation by NBTE Finance", "action", "finance_director", {}),
        ("document_upload", "Documents", "Upload your required documents", "action", "institution", {}),
        ("document_review", "Departmental document review", "NBTE departments are reviewing your documents",
         "action", "role_in_department", {"sla_hours": 120}),
        ("cleared_fee_due", "Institutional fee due", "All documents approved — pay the institutional processing "
         "fee", "action", "institution", {}),
        ("cleared_payment_pending", "Payment awaiting Finance confirmation",
         "Paid — awaiting confirmation by NBTE Finance", "action", "finance_director", {}),
        ("programme_selection", "Programme selection", "Select the programmes you will offer", "action",
         "institution", {}),
        ("programme_clearance", "Programme clearance", "Programmes under review by NBTE", "action",
         "role_in_department", {"actor_scope": "ntird"}),
        ("programme_fee_due", "Programme fee due", "Programme processing fee due", "action", "institution", {}),
        ("programme_payment_pending", "Programme payment awaiting Finance confirmation",
         "Paid — awaiting confirmation by NBTE Finance", "action", "finance_director", {}),
        ("programme_fee_confirmed", "Programme fee confirmed", "Programme fee confirmed", "system", "system", {}),
        ("ssq_in_progress", "SSQ in progress", "Complete your self-study questionnaires (SSQ)", "action",
         "institution", {}),
        ("ssq_submitted", "SSQ submitted", "Questionnaires submitted to NBTE", "end", "system",
         {"is_terminal_success": True}),
        ("withdrawn", "Withdrawn", "Application withdrawn", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        # Fee first (before_review)
        ("draft", "fee_due", "declare_and_pay", "Declare and pay (Chief Executive)",
         {"effects": ["create_payment_obligation:institutional_processing_v3"]}),
        ("fee_due", "payment_pending_confirmation", "payment_claimed", "Payment claimed", {}),
        ("payment_pending_confirmation", "fee_due", "payment_rejected", "Payment rejected", {}),
        ("payment_pending_confirmation", "document_upload", "payment_confirmed", "Payment confirmed", {}),
        ("fee_due", "document_upload", "payment_confirmed", "Payment confirmed (waived/matched)", {}),
        # Documents first (after_clearance — the SOP order)
        ("draft", "document_upload", "declare_and_start", "Declare and start (Chief Executive)", {}),
        # Both
        ("document_upload", "document_review", "submit_documents", "Submit documents (Chief Executive)",
         {"effects": ["onboarding_v3_documents_submitted"]}),
        ("document_review", "programme_selection", "documents_cleared", "All documents approved", {}),
        ("document_review", "cleared_fee_due", "documents_cleared_pay", "All documents approved (fee due)",
         {"effects": ["create_payment_obligation:institutional_processing_v3"]}),
        ("cleared_fee_due", "cleared_payment_pending", "payment_claimed", "Payment claimed", {}),
        ("cleared_payment_pending", "cleared_fee_due", "payment_rejected", "Payment rejected", {}),
        ("cleared_payment_pending", "programme_selection", "payment_confirmed", "Payment confirmed", {}),
        ("cleared_fee_due", "programme_selection", "payment_confirmed", "Payment confirmed (waived/matched)", {}),
        # Programmes, fee, SSQ (as v3)
        ("programme_selection", "programme_clearance", "submit_programmes", "Submit programmes",
         {"effects": ["onboarding_physical_verification"]}),
        ("programme_clearance", "programme_fee_due", "clear_programmes", "Clear programmes",
         {"effects": ["create_payment_obligation:programme_processing"]}),
        ("programme_clearance", "programme_selection", "return", "Return programmes", {"requires_remark": True}),
        ("programme_fee_due", "programme_payment_pending", "payment_claimed", "Payment claimed", {}),
        ("programme_payment_pending", "programme_fee_due", "payment_rejected", "Payment rejected", {}),
        ("programme_payment_pending", "programme_fee_confirmed", "payment_confirmed", "Payment confirmed", {}),
        ("programme_fee_due", "programme_fee_confirmed", "payment_confirmed",
         "Payment confirmed (waived/matched)", {}),
        ("programme_fee_confirmed", "ssq_in_progress", "auto_open_ssq", "Open the SSQ", {}),
        ("ssq_in_progress", "ssq_submitted", "submit_ssq", "Submit SSQ", {}),
        ("draft", "withdrawn", "withdraw", "Withdraw application", {"requires_remark": True}),
    ],
}

FEE_STATES = ("fee_due", "payment_pending_confirmation", "cleared_fee_due", "cleared_payment_pending")


# ---------------------------------------------------------------------------
# Install / detect
# ---------------------------------------------------------------------------
def install_flow_v4(*, publish=True):
    """Create onboarding version 4 (idempotent). Returns (flow, created).

    An unused earlier draft of v4 (with the withdrawn approval step) is
    replaced; a version that already carries applications is never touched."""
    from accounts.models import Role
    from qa_core.departments import qa_department
    from qa_core.models import FlowDefinition, FlowInstance, FlowStage, FlowTransition

    spec = ONBOARDING_FLOW_V4
    existing = FlowDefinition.objects.filter(code=spec["code"], version=V4).first()
    if existing is not None:
        if existing.stages.filter(code="approval_pending").exists() and \
                not FlowInstance.objects.filter(flow_version=existing).exists():
            with transaction.atomic():
                FlowTransition.objects.filter(flow=existing).delete()
                FlowStage.objects.filter(flow=existing).delete()
                existing.delete()
        else:
            return existing, False
    with transaction.atomic():
        fd = FlowDefinition.objects.create(code=spec["code"], name=spec["name"], description=spec["description"],
                                           subject_model=spec["subject_model"], version=V4, is_active=True,
                                           published_at=timezone.now() if publish else None)
        by_code = {}
        for order, (code, label, public, kind, actor_kind, extra) in enumerate(spec["stages"]):
            extra = dict(extra)
            scope = extra.get("actor_scope", "")
            if scope:
                dept = qa_department(scope)
                extra["actor_scope"] = dept.code if dept is not None else scope
            if "actor_role" in extra:
                extra["actor_role"] = Role.objects.filter(code=extra["actor_role"]).first()
            by_code[code] = FlowStage.objects.create(flow=fd, code=code, label=label, public_label=public, kind=kind,
                                                     actor_kind=actor_kind, sort_order=(order + 1) * 10, **extra)
        for order, (src, dst, action, label, extra) in enumerate(spec["transitions"]):
            FlowTransition.objects.create(flow=fd, from_stage=by_code[src], to_stage=by_code[dst], action_code=action,
                                          label=label, sort_order=(order + 1) * 10, **extra)
    return fd, True


def is_v4(app):
    from . import services

    instance = services.instance_of(app) if app is not None else None
    return bool(instance is not None and instance.flow_version.version >= V4)


def fee_timing():
    import config

    value = str(config.get("qa.onboarding.fee_timing", AFTER) or AFTER).strip().lower()
    return BEFORE if value == BEFORE else AFTER


def app_fee_timing(app):
    """The order this application follows: fixed once the CE has declared."""
    if app is None or not is_v4(app):
        return BEFORE
    if app.status == "draft":
        return fee_timing()
    if app.status in ("cleared_fee_due", "cleared_payment_pending"):
        return AFTER
    from .onboarding_v2 import obligation_of

    ob = obligation_of(app)
    if app.status in ("fee_due", "payment_pending_confirmation") or (
            ob is not None and app.status in ("document_upload", "document_review")):
        return BEFORE
    if app.status in ("document_upload", "document_review"):
        return AFTER
    # Past the documents: an institutional bill raised after submission means "after".
    return AFTER if (ob is not None and app.submitted_at and ob.created_at > app.submitted_at) else BEFORE


def declare_action(app):
    """The flow action the Chief Executive's declaration takes."""
    return "declare_and_start" if is_v4(app) and fee_timing() == AFTER else "declare_and_pay"


def cleared_action(app):
    """The flow action taken when the last document is approved."""
    if is_v4(app) and app_fee_timing(app) == AFTER:
        return "documents_cleared_pay"
    return "documents_cleared"


def on_documents_cleared(app):
    """The last document was approved: tell the institution its next step (and
    the handling department, the ES and super admins, for information)."""
    from .onboarding_v2 import obligation_of

    app.refresh_from_db()
    inst = app.institution
    if app.status == "cleared_fee_due":
        ob = obligation_of(app)
        if ob is not None and app.fee_obligation_id != ob.pk:
            app.fee_obligation = ob
            app.save(update_fields=["fee_obligation", "updated_at"])
        nxt = "pay the institutional processing fee; once NBTE Finance confirms it you can choose your programmes"
    else:
        nxt = "choose the programmes you will offer"
    timeline.record(inst, "application.documents_cleared", subject=app, visibility="public",
                    public_text=f"All your documents are approved. Next: {nxt}.")
    _tell_institution(inst, "All your documents are approved",
                      f"Every document has been reviewed and approved by NBTE. Next: {nxt}.", url="/application/")
    _tell_staff(inst, f"Documents cleared: {inst.name}",
                f"Every document of {inst.name} ({app.reference}) is approved by its department. The institution's "
                f"next step: {nxt}.", url=f"/qa/institutions/{inst.code}/")


# ---------------------------------------------------------------------------
# Automatic steps that did not run (e.g. a missing fee schedule or template)
# ---------------------------------------------------------------------------
def stalled_step(app):
    """A plain sentence when the application waits on an automatic step that
    should already have happened, else ''. Display only."""
    from institutions.models import DocumentReview

    from .models import OnboardingSSQ

    if app is None or not is_v4(app):
        return ""
    if app.status == "document_review":
        reviews = DocumentReview.unscoped.filter(application=app).exclude(status=DocumentReview.SUPERSEDED)
        if reviews.exists() and not reviews.exclude(status=DocumentReview.APPROVED).exists():
            return "Every document is approved, but the next step (institutional fee / programmes) did not start."
    elif app.status == "programme_fee_confirmed":
        return "The programme fee is confirmed, but the self-study questionnaires did not open."
    elif app.status == "ssq_in_progress":
        from django.core.exceptions import ValidationError

        from .onboarding_ssq import cleared_reviews

        try:
            cleared = {r.programme_id for r in cleared_reviews(app)}
        except ValidationError:
            cleared = set()
        opened = set(OnboardingSSQ.unscoped.filter(application=app).values_list("programme_id", flat=True))
        if not cleared or cleared - opened:
            return "The self-study questionnaires are not open for every cleared programme."
    return ""


def resume(app, actor=None):
    """Run the automatic step the application is waiting on (idempotent).
    Raises ValidationError with the reason when it still cannot run."""
    from django.core.exceptions import ValidationError

    from qa_core import flow

    from . import onboarding_ssq, onboarding_v2, services

    app.refresh_from_db()
    if app.status == "document_review":
        if not onboarding_v2.maybe_clear(app):
            raise ValidationError("Not every document is approved yet.")
        return "Documents cleared: the institution's next step has started."
    if app.status == "programme_fee_confirmed":
        instance = services.instance_of(app)
        if instance is not None and flow.can(instance, "auto_open_ssq"):
            flow.transition(instance, "auto_open_ssq", actor)
        app.refresh_from_db()
    if app.status == "ssq_in_progress":
        onboarding_ssq.open_for_application(app)
        return "The self-study questionnaires are open on the institution's portal."
    raise ValidationError("Nothing to resume at this stage.")


# ---------------------------------------------------------------------------
# Approval of the institution at the end of SOP-03
# ---------------------------------------------------------------------------
def finalise_institution(case, actor=None):
    """Stage 17 for a proposed institution: it becomes Approved when at least
    one programme came out approved, and moves to its category's department."""
    from institutions import services as inst_services
    from institutions.models import Institution, Programme

    inst = Institution.unscoped.get(pk=case.institution_id)
    if inst.status not in (Institution.PROPOSED, Institution.UNDER_REVIEW):
        return False
    good = Programme.unscoped.filter(institution=inst, status__in=(
        "accredited", "approval_to_commence", "interim", "approved_pending_body_letter")).exists()
    if not good:
        return False
    try:
        inst_services.lifecycle_move(inst, "approve", actor, remark=f"SOP-03 {case.reference} completed",
                                     confirmed=True)
    except Exception:
        logger.exception("lifecycle approve failed for %s; setting the status directly", inst.code)
        Institution.unscoped.filter(pk=inst.pk).update(status=Institution.APPROVED)
    inst.refresh_from_db()
    new_dept = inst.derive_handling_department(status=Institution.APPROVED)
    inst.handling_department = new_dept or inst.handling_department
    inst.approved_at = inst.approved_at or timezone.now()
    inst.save(update_fields=["handling_department", "approved_at", "updated_at"])
    timeline.record(inst, "institution.approved", actor=actor, visibility="public",
                    public_text=f"{inst.name} is now an NBTE-approved institution.",
                    internal_text=f"Approved at the end of SOP-03 {case.reference}; handling department: "
                                  f"{getattr(inst.handling_department, 'name', '—')}.")
    _tell_institution(inst, f"{inst.name} is approved",
                      "NBTE's quality assurance process is complete and your institution is approved. You can now "
                      "make quality assurance requests from your portal.", url="/")
    return True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _tell_institution(inst, title, body, *, url=""):
    from institutions import services as inst_services
    from notifications.models import Notification
    from notifications.services import notify
    from qa_core.notify import _email

    users = list(inst_services.institution_users(inst))
    for u in users:
        notify(u, title=title, body=body, url=url, category=Notification.QA, metadata={"event": "onboarding"})
    try:
        _email(title, f"<p>{body}</p>", users)
    except Exception:
        logger.exception("institution e-mail failed")


def _tell_staff(inst, title, body, *, url=""):
    """For information: the handling department's director, the ES and super admins (in-app)."""
    import org
    from notifications.models import Notification
    from notifications.services import notify

    people = {}
    for u in org.users_with_role(org.ES_ROLE, staff_only=True):
        people[u.pk] = u
    for u in org.users_with_role("super-admin"):
        people[u.pk] = u
    dept = inst.handling_department or inst.derive_handling_department()
    director = org.director_of(dept) if dept is not None else None
    if director is not None:
        people[director.pk] = director
    for u in people.values():
        notify(u, title=title, body=body, url=url, category=Notification.QA, metadata={"event": "onboarding.cleared"})
