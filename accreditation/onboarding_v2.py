"""v2.1 F.15 (X14, X15): onboarding flow version 2 — pay to submit.

    draft ─declare_and_pay (Chief Executive; issues the fee)→ fee_due
    fee_due ─payment_claimed→ payment_pending_confirmation ─payment_rejected→ fee_due
    payment_pending_confirmation / fee_due ─payment_confirmed→ fee_confirmed
    fee_confirmed ─submit_automatically (on payments.signals.obligation_confirmed: the
                   Director of Finance's final confirmation, owner decision D-5)→ document_review
    document_review ─documents_cleared (every DocumentReview approved)→ programme_selection
    programme_selection … ssq (as v1; submit_programmes checks the X18 trigger)

Per-document review replaces the v1 department branches and the whole-
application return (X16/X17): there is no ``return`` and no resubmit in v2.
New applications start on the newest published version; v1 instances stay on
v1 (``FlowInstance.flow_version`` is pinned).
"""
from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from institutions import services as inst_services
from qa_core import flow, timeline

logger = logging.getLogger(__name__)

V2 = 2

ONBOARDING_FLOW_V2 = {
    "code": "onboarding",
    "name": "Onboarding (SOP-01) v2 — pay to submit",
    "subject_model": "accreditation.Application",
    "description": "v2.1 X14: draft → profile and documents → Declare and pay (Chief Executive) → fee confirmed by "
                   "the Director of Finance → submitted automatically → per-document departmental review → "
                   "programme selection … No resubmission: single documents come back (X17).",
    "stages": [
        ("draft", "Draft", "Complete your profile and upload your documents", "start", "institution", {}),
        ("fee_due", "Processing fee due", "Pay to submit — processing fee due", "action", "institution", {}),
        ("payment_pending_confirmation", "Payment awaiting Finance confirmation",
         "Paid — awaiting confirmation by NBTE Finance", "action", "finance_director", {}),
        ("fee_confirmed", "Fee confirmed", "Fee confirmed — submitting your application", "action", "system", {}),
        ("document_review", "Departmental document review", "Submitted — NBTE departments are reviewing your "
         "documents", "action", "role_in_department", {"sla_hours": 120}),
        ("programme_selection", "Programme selection", "Select the programmes you will offer", "action",
         "institution", {}),
        ("programme_clearance", "Programme clearance (NTIRD)", "Programmes under review by NBTE", "action",
         "role_in_department", {"actor_scope": "ntird"}),
        ("programme_fee_due", "Programme fee due", "Programme processing fee due", "action", "institution", {}),
        ("programme_payment_pending", "Programme payment awaiting Finance confirmation",
         "Institution claims paid — awaiting Finance confirmation", "action", "finance_director", {}),
        ("programme_fee_confirmed", "Programme fee confirmed", "Programme fee confirmed", "system", "system", {}),
        ("ssq_in_progress", "SSQ in progress", "Complete your questionnaire (SSQ)", "action", "institution", {}),
        ("ssq_submitted", "SSQ submitted", "Questionnaire submitted", "end", "system",
         {"is_terminal_success": True}),
        ("withdrawn", "Withdrawn", "Application withdrawn", "end", "system", {"is_terminal_failure": True}),
    ],
    "transitions": [
        ("draft", "fee_due", "declare_and_pay", "Declare and pay (Chief Executive)",
         {"effects": ["create_payment_obligation:institutional_processing"]}),
        ("fee_due", "payment_pending_confirmation", "payment_claimed", "Payment claimed", {}),
        ("payment_pending_confirmation", "fee_due", "payment_rejected", "Payment rejected", {}),
        ("payment_pending_confirmation", "fee_confirmed", "payment_confirmed", "Payment confirmed", {}),
        ("fee_due", "fee_confirmed", "payment_confirmed", "Payment confirmed (waived/matched)", {}),
        ("fee_confirmed", "document_review", "submit_automatically", "Submitted automatically",
         {"effects": ["onboarding_v2_submitted"]}),
        ("document_review", "programme_selection", "documents_cleared", "All documents approved", {}),
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


def install_flow_v2(*, publish=True):
    """Create onboarding version 2 (idempotent). Returns (flow, created)."""
    from accounts.models import Role
    from qa_core.departments import qa_department
    from qa_core.models import FlowDefinition, FlowStage, FlowTransition

    spec = ONBOARDING_FLOW_V2
    existing = FlowDefinition.objects.filter(code=spec["code"], version=V2).first()
    if existing is not None:
        return existing, False
    with transaction.atomic():
        fd = FlowDefinition.objects.create(code=spec["code"], name=spec["name"], description=spec["description"],
                                           subject_model=spec["subject_model"], version=V2, is_active=True,
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


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def is_v2(app):
    from . import services

    instance = services.instance_of(app) if app is not None else None
    return bool(instance is not None and instance.flow_version.version >= V2)


def _latest_is_v4():
    from qa_core.models import FlowDefinition

    latest = FlowDefinition.objects.filter(code="onboarding", is_active=True, published_at__isnull=False).order_by(
        "-version").first()
    return bool(latest and latest.version >= 4)


def _is_v3(app):
    from . import onboarding_v3

    return onboarding_v3.is_v3(app)


def obligation_of(app):
    if app.fee_obligation_id:
        return app.fee_obligation
    from django.contrib.contenttypes.models import ContentType

    from payments.models import PaymentObligation

    from .models import Application

    ct = ContentType.objects.get_for_model(Application)
    return PaymentObligation.unscoped.filter(source_type=ct, source_id=str(app.pk)).order_by("-created_at").first()


def documents_complete(inst):
    summary = inst_services.checklist_summary(inst_services.checklist(inst))
    return not summary["missing"], summary


def pay_blockers(app):
    """Why the Chief Executive cannot "Declare and pay" yet ([] = can)."""
    from institutions import profile

    from . import services

    out = []
    if app.status != "draft":
        return ["The application is past the payment step."]
    inst = app.institution
    if inst.status == "delisted" or not inst.portal_enabled:
        out.append("Portal access is switched off for this institution.")
    if not profile.profile_complete(inst):
        out.append("Finish your setup checklist (profile, accounts and management team).")
    if not _is_v3(app):
        ok, summary = documents_complete(inst)
        if not ok:
            out.append("Upload the required documents: " + ", ".join(r["kind"].name for r in summary["missing"]) + ".")
    if inst_services.active_hold(app):
        out.append("The application is on hold.")
    _cycle, accepting = services.open_cycle()
    if not accepting:
        out.append("Applications are closed at the moment.")
    return out


# ---------------------------------------------------------------------------
# Declare and pay (CE only) — X12 + X15
# ---------------------------------------------------------------------------
@transaction.atomic
def declare_and_pay(app, *, user, declaration=False):
    from . import services
    from .models import Application

    inst_services.require_chief_executive(user, "the declaration and payment")
    app = Application.unscoped.select_for_update().get(pk=app.pk)
    if not is_v2(app):
        raise ValidationError("This application uses the earlier process: submit it from the application page.")
    if not declaration:
        raise ValidationError("Confirm the declaration that the information and documents are true.")
    blockers = pay_blockers(app)
    if blockers:
        raise ValidationError(blockers)
    now = timezone.now()
    app.declared_by, app.declared_at, app.declaration_accepted = user, now, True
    app.save(update_fields=["declared_by", "declared_at", "declaration_accepted", "updated_at"])
    from . import onboarding_v4

    flow.transition(services.instance_of(app), onboarding_v4.declare_action(app), user)
    app.refresh_from_db()
    ob = obligation_of(app)
    if ob is not None and app.fee_obligation_id != ob.pk:
        app.fee_obligation = ob
        app.save(update_fields=["fee_obligation", "updated_at"])
    if app.status == "document_upload":
        public = (f"The Chief Executive signed the declaration for {app.reference}. Next: upload the required "
                  "documents and submit them for review.")
    else:
        next_step = ("the institutional and processing fees; upload documents after Finance confirms payment."
                     if _is_v3(app) else
                     "the processing fee; the application submits itself once payment is confirmed.")
        public = f"The Chief Executive signed the declaration for {app.reference}. Pay {next_step}"
    timeline.record(app.institution, "application.declared", subject=app, actor=user, visibility="public",
                    public_text=public)
    return app


# ---------------------------------------------------------------------------
# Auto-submit (D-5): on the Director of Finance's final confirmation
# ---------------------------------------------------------------------------
def _app_for_obligation(obligation):
    from . import services

    return services._app_for(obligation)


def auto_submit(app, *, reason="finance_confirmed"):
    """Move fee_confirmed → document_review (idempotent). Returns True when moved."""
    from . import services

    instance = services.instance_of(app)
    if instance is None or instance.flow_version.version != V2 or not flow.can(instance, "submit_automatically"):
        return False
    flow.transition(instance, "submit_automatically", None, metadata={"trigger": reason})
    return True


def on_obligation_confirmed(sender, obligation=None, **kwargs):
    """payments.signals.obligation_confirmed receiver (sent after commit)."""
    app = _app_for_obligation(obligation) if obligation is not None else None
    if app is None:
        return
    import config

    if str(config.get("qa.onboarding.autosubmit_trigger", "finance_confirmed")) != "finance_confirmed":
        return
    with transaction.atomic():
        # payment_settled (also sent on commit) may not have run yet: move the fee stage first.
        from . import services

        instance = services.instance_of(app)
        if instance is not None and flow.can(instance, "payment_confirmed"):
            services.payment_settled(obligation)
        auto_submit(app, reason="finance_confirmed")


def on_payment_settled_v2(obligation):
    """A waived fee has no Finance confirmation: submit on settlement."""
    if getattr(obligation, "waived_at", None) is None:
        return
    app = _app_for_obligation(obligation)
    if app is not None:
        auto_submit(app, reason="waived")


def on_submitted(ctx):
    """Effect ``onboarding_v2_submitted``: record the submission (by the
    declaring CE, automatically) and open the per-document reviews."""
    from institutions import document_review
    from institutions.models import Institution, InstitutionDocument

    app = ctx.subject
    now = timezone.now()
    app.round = (app.round or 0) + 1
    app.submitted_at = now
    app.submitted_by = app.declared_by
    app.auto_submitted = True
    app.save(update_fields=["round", "submitted_at", "submitted_by", "auto_submitted", "updated_at"])
    InstitutionDocument.unscoped.filter(institution=app.institution,
                                        status__in=(InstitutionDocument.SUBMITTED, InstitutionDocument.ACCEPTED)
                                        ).update(application=app)
    inst = app.institution
    if inst.status == Institution.PROPOSED:
        inst_services.lifecycle_move(inst, "submit_for_review", None)
    timeline.record(inst, "application.submitted", subject=app, visibility="public",
                    public_text=f"Payment confirmed. Application {app.reference} was submitted automatically; "
                                "NBTE departments are reviewing your documents.")
    document_review.open_reviews(app)
    transaction.on_commit(lambda: inst_services.notify_institution(
        inst, "onboarding.submitted_automatically", {"reference": app.reference}, url="/application/"))
    transaction.on_commit(lambda: maybe_clear(app))


def maybe_clear(app):
    """All reviews approved → the next SOP-01 step (no reviews at all clears too).
    v4: programme selection, or the institutional fee first when the fee comes
    after the document review (qa.onboarding.fee_timing)."""
    from institutions.models import DocumentReview

    from . import onboarding_v4, services

    with transaction.atomic():
        instance = services.instance_of(app)
        action = onboarding_v4.cleared_action(app)
        if instance is None or not flow.can(instance, action):
            return False
        reviews = DocumentReview.unscoped.filter(application=app).exclude(status=DocumentReview.SUPERSEDED)
        if reviews.exclude(status=DocumentReview.APPROVED).exists():
            return False
        flow.transition(instance, action, None)
        if onboarding_v4.is_v4(app):
            transaction.on_commit(lambda: onboarding_v4.on_documents_cleared(app))
        else:
            timeline.record(app.institution, "application.documents_cleared", subject=app, visibility="public",
                            public_text="All your documents are accepted. Next: choose your programmes.")
            inst_services.notify_institution(app.institution, "onboarding.documents_cleared",
                                             {"reference": app.reference}, url="/application/")
        try:  # v2.3: SOP-01 documents complete — their honoraria go to the institution's file
            from honoraria import files as honoraria_files

            transaction.on_commit(lambda: honoraria_files.stage_completed(app.institution, "sop01"))
        except Exception:
            logger.exception("honoraria hook failed for %s", app.pk)
    return True


# ---------------------------------------------------------------------------
# X18 (D-6): physical verification when more than N programmes are proposed
# ---------------------------------------------------------------------------
def needs_physical_verification(n):
    import config

    try:
        t = int(config.get("qa.onboarding.physical_verification_threshold", 5))
    except (TypeError, ValueError):
        t = 5
    comp = str(config.get("qa.onboarding.physical_verification_comparator", "gt") or "gt")
    return n >= t if comp == "gte" else n > t


def on_programmes_submitted(ctx):
    """Effect ``onboarding_physical_verification``: more than five programmes →
    a physical-verification assignment the Director of ICT hands to an ICT officer."""
    from institutions.models import Programme

    app = ctx.subject
    n = Programme.unscoped.filter(institution=app.institution, source_application=app).exclude(
        status="dropped").count()
    if needs_physical_verification(n):
        open_physical_verification(app, programme_count=n)


def open_physical_verification(app, *, programme_count):
    import org
    import config

    from assignments.models import WorkAssignment
    from django.contrib.contenttypes.models import ContentType

    ict = org.department(config.get("qa.privacy.ict_department", "ict") or "ict")
    director = org.director_of(ict) if ict is not None else None
    if director is None:
        logger.warning("No Director of ICT: physical verification for %s not opened", app.reference)
        return None
    ct = ContentType.objects.get_for_model(app, for_concrete_model=False)
    existing = WorkAssignment.objects.filter(kind=WorkAssignment.PHYSICAL_VERIFICATION, subject_ct=ct,
                                             subject_id=str(app.pk)).open().first()
    if existing is not None:
        return existing
    wa = WorkAssignment.objects.create(
        institution=app.institution, kind=WorkAssignment.PHYSICAL_VERIFICATION, subject_ct=ct, subject_id=str(app.pk),
        department=ict, assignee=director, role_on_work="decider", status=WorkAssignment.OFFERED,
        meta={"awaiting_officer": True, "programme_count": programme_count})
    timeline.record(app.institution, "application.physical_verification", subject=app, visibility="internal",
                    internal_text=f"{programme_count} programmes proposed (more than the threshold): the Director "
                                  "of ICT assigns an ICT officer for physical verification.")
    transaction.on_commit(lambda: inst_services.notify_role(
        "onboarding.physical_verification", {"reference": app.reference, "institution": app.institution.name,
                                             "count": programme_count},
        [director], url="/qa/applications/documents/"))
    return wa


# ---------------------------------------------------------------------------
# Portal header (design 22): Profile → Documents → Pay → Submitted → Review
# ---------------------------------------------------------------------------
def header(app, inst):
    from institutions import profile
    from . import onboarding_v3

    profile_ok = profile.profile_complete(inst)
    docs_ok, summary = documents_complete(inst)
    status = app.status if app is not None else "draft"
    ob = obligation_of(app) if app is not None else None
    use_v3 = _is_v3(app) or (app is None and onboarding_v3.latest_flow_is_v3())
    from . import onboarding_v4

    use_v4 = (app is not None and onboarding_v4.is_v4(app)) or (app is None and _latest_is_v4())
    if use_v4:
        timing = onboarding_v4.app_fee_timing(app) if app is not None else onboarding_v4.fee_timing()
        order = ["draft", "fee_due", "payment_pending_confirmation", "document_upload", "document_review",
                 "cleared_fee_due", "cleared_payment_pending", "programme_selection",
                 "programme_clearance", "programme_fee_due", "programme_payment_pending", "programme_fee_confirmed",
                 "ssq_in_progress", "ssq_submitted"]
        pos = order.index(status) if status in order else 0

        def past(code):
            return pos > order.index(code)

        fee_note_pending = "Awaiting Finance confirmation"
        docs_step = {"label": "Upload and submit documents",
                     "note": f"{summary['ready']} of {summary['mandatory']} uploaded",
                     "done": past("document_upload")}
        review_step = {"label": "NBTE departments review", "note": "Each document goes to its own department",
                       "done": past("document_review")}
        if timing == onboarding_v4.BEFORE:
            fee_step = {"label": "Pay the institutional fee",
                        "note": fee_note_pending if status == "payment_pending_confirmation"
                        else "Confirmed by Finance" if past("payment_pending_confirmation") else "Due before documents",
                        "done": past("payment_pending_confirmation")}
            steps = [{"label": "Profile complete", "note": "Management staff and contacts", "done": profile_ok},
                     fee_step, docs_step, review_step]
        else:
            fee_step = {"label": "Pay the institutional fee",
                        "note": fee_note_pending if status == "cleared_payment_pending"
                        else "Confirmed by Finance" if past("cleared_payment_pending")
                        else "Due when all documents are approved",
                        "done": past("cleared_payment_pending")}
            steps = [{"label": "Profile complete", "note": "Management staff and contacts", "done": profile_ok},
                     docs_step, review_step, fee_step]
        steps += [{"label": "Choose programmes", "note": "Cleared by NBTE", "done": past("programme_clearance")},
                  {"label": "Pay programme fees", "note": "Per cleared programme",
                   "done": past("programme_payment_pending")},
                  {"label": "Self-study (SSQ)", "note": "The Chief Executive submits",
                   "done": status == "ssq_submitted"}]
        after_pay = status not in ("draft", "fee_due", "payment_pending_confirmation")
        paid = fee_step["done"]
    elif use_v3:
        after_pay = status not in ("draft", "fee_due", "payment_pending_confirmation")
        paid = after_pay
        submitted = status not in ("draft", "fee_due", "payment_pending_confirmation", "document_upload")
        review_done = status in ("programme_selection", "programme_clearance", "programme_fee_due",
                                 "programme_payment_pending", "programme_fee_confirmed", "ssq_in_progress",
                                 "ssq_submitted")
        steps = [
            {"label": "Profile complete", "note": "Management staff and contacts", "done": profile_ok},
            {"label": "Pay institutional and processing fees",
             "note": ("Confirmed by Finance" if paid else "Awaiting Finance confirmation"
                      if status == "payment_pending_confirmation" else "Chief Executive declares and pays"),
             "done": paid},
            {"label": "Upload required documents",
             "note": f"{summary['ready']} of {summary['mandatory']} uploaded",
             "done": submitted},
            {"label": "Submit documents", "note": "The Chief Executive submits after upload",
             "done": submitted},
            {"label": "Departments review", "note": "Each document goes to its own department",
             "done": status in ("programme_selection", "programme_clearance", "programme_fee_due",
                                "programme_payment_pending", "programme_fee_confirmed", "ssq_in_progress",
                                "ssq_submitted")},
        ]
    else:
        after_pay = status not in ("draft", "fee_due", "payment_pending_confirmation", "fee_confirmed")
        paid = status == "fee_confirmed" or after_pay
        review_done = after_pay and status != "document_review"
        steps = [
            {"label": "Profile complete", "note": "Management staff and contacts", "done": profile_ok},
            {"label": "Upload documents", "note": f"{summary['ready']} of {summary['mandatory']} uploaded",
             "done": docs_ok or after_pay},
            {"label": "Pay processing fee",
             "note": ("Paid" if paid else "Awaiting Finance confirmation" if status == "payment_pending_confirmation"
                      else "Due now" if status == "fee_due" else "Chief Executive declares and pays"),
             "done": paid},
            {"label": "Submitted automatically", "note": "Happens the moment payment is confirmed", "done": after_pay},
            {"label": "Departments review", "note": "Each document goes to its own department", "done": review_done},
        ]
    current = next((i for i, s in enumerate(steps) if not s["done"]), len(steps))
    for i, s in enumerate(steps):
        s["state"] = "done" if s["done"] else ("current" if i == current else "todo")
    estimate = None
    if ob is None:
        try:
            from payments.services import fee_estimate

            fee_code = "institutional_processing_v3" if use_v3 else "institutional_processing"
            estimate = fee_estimate(inst, fee_code, [])
        except Exception:  # an unset fee must never break the page
            logger.exception("fee estimate failed")
    fee_timing = None
    if use_v4:
        fee_timing = timing
    return {"steps": steps, "obligation": ob, "status": status, "after_pay": after_pay, "estimate": estimate,
            "current": steps[current] if current < len(steps) else None, "fee_timing": fee_timing,
            "is_v4": use_v4}
