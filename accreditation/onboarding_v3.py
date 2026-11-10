"""SOP-01 onboarding v3: Finance confirms payment before document submission."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from institutions import services as inst_services
from qa_core import flow, timeline

V3 = 3

ONBOARDING_FLOW_V3 = {
    "code": "onboarding",
    "name": "Onboarding (SOP-01) v3 — pay before document review",
    "subject_model": "accreditation.Application",
    "description": "Complete the institutional profile → Chief Executive declares and pays → Finance confirms "
                   "payment → upload required documents → the Chief Executive submits them → per-document "
                   "departmental review → programme selection and clearance → programme self-study → explicit "
                   "NBTE institution approval before SOP-02 requests.",
    "stages": [
        ("draft", "Draft", "Complete your institutional profile", "start", "institution", {}),
        ("fee_due", "Institutional and processing fees due", "Pay before document processing", "action",
         "institution", {}),
        ("payment_pending_confirmation", "Payment awaiting Finance confirmation",
         "Paid — awaiting confirmation by NBTE Finance", "action", "finance_director", {}),
        ("document_upload", "Documents unlocked", "Payment confirmed — upload your required documents", "action",
         "institution", {}),
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
         {"effects": ["create_payment_obligation:institutional_processing_v3"]}),
        ("fee_due", "payment_pending_confirmation", "payment_claimed", "Payment claimed", {}),
        ("payment_pending_confirmation", "fee_due", "payment_rejected", "Payment rejected", {}),
        ("payment_pending_confirmation", "document_upload", "payment_confirmed", "Payment confirmed", {}),
        ("fee_due", "document_upload", "payment_confirmed", "Payment confirmed (waived/matched)", {}),
        ("document_upload", "document_review", "submit_documents", "Submit documents (Chief Executive)",
         {"effects": ["onboarding_v3_documents_submitted"]}),
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


def install_flow_v3(*, publish=True):
    """Create onboarding version 3 (idempotent). Returns (flow, created)."""
    from accounts.models import Role
    from qa_core.departments import qa_department
    from qa_core.models import FlowDefinition, FlowStage, FlowTransition

    spec = ONBOARDING_FLOW_V3
    existing = FlowDefinition.objects.filter(code=spec["code"], version=V3).first()
    if existing is not None:
        return existing, False
    with transaction.atomic():
        fd = FlowDefinition.objects.create(code=spec["code"], name=spec["name"], description=spec["description"],
                                           subject_model=spec["subject_model"], version=V3, is_active=True,
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


def is_v3(app):
    from . import services

    instance = services.instance_of(app) if app is not None else None
    return bool(instance is not None and instance.flow_version.version >= V3)


def latest_flow_is_v3():
    from qa_core.models import FlowDefinition

    latest = FlowDefinition.objects.filter(code="onboarding", is_active=True, published_at__isnull=False).order_by(
        "-version").first()
    return bool(latest and latest.version >= V3)


def document_upload_blocked(app):
    """New v3 uploads stay closed until Finance confirms the initial bill."""
    if not is_v3(app):
        return False
    return app.status != "document_upload"


def submission_blockers(app):
    from . import onboarding_v2

    out = []
    if not is_v3(app) or app.status != "document_upload":
        out.append("Documents can be submitted only after Finance confirms the institutional payment.")
    if inst_services.active_hold(app):
        out.append("The application is on hold.")
    complete, summary = onboarding_v2.documents_complete(app.institution)
    if not complete:
        out.append("Upload the required documents: " + ", ".join(r["kind"].name for r in summary["missing"]) + ".")
    return out


@transaction.atomic
def submit_documents(app, *, user):
    from . import services

    inst_services.require_chief_executive(user, "submitting the onboarding documents")
    app = app.__class__.unscoped.select_for_update().get(pk=app.pk)
    blockers = submission_blockers(app)
    if blockers:
        raise ValidationError(blockers)
    instance = services.instance_of(app)
    if instance is None or not flow.can(instance, "submit_documents"):
        raise ValidationError("The application is not ready for document submission.")
    flow.transition(instance, "submit_documents", user)
    return app.__class__.unscoped.get(pk=app.pk)


def on_documents_submitted(ctx):
    from institutions import document_review
    from institutions.models import Institution, InstitutionDocument

    app = ctx.subject
    now = timezone.now()
    app.round = (app.round or 0) + 1
    app.submitted_at = now
    app.submitted_by = app.declared_by
    app.auto_submitted = False
    app.save(update_fields=["round", "submitted_at", "submitted_by", "auto_submitted", "updated_at"])
    InstitutionDocument.unscoped.filter(
        institution=app.institution,
        status__in=(InstitutionDocument.SUBMITTED, InstitutionDocument.ACCEPTED),
    ).update(application=app)
    inst = app.institution
    if inst.status == Institution.PROPOSED:
        inst_services.lifecycle_move(inst, "submit_for_review", ctx.actor)
    timeline.record(inst, "application.submitted", subject=app, actor=ctx.actor, visibility="public",
                    public_text=f"The Chief Executive submitted application {app.reference}. "
                                "NBTE departments are reviewing your documents.")
    document_review.open_reviews(app)
    transaction.on_commit(lambda: inst_services.notify_institution(
        inst, "onboarding.documents_submitted", {"reference": app.reference}, url="/application/"))
    from . import onboarding_v2

    transaction.on_commit(lambda: onboarding_v2.maybe_clear(app))
