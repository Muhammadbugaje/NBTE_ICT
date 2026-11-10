"""Workflow effects and payment signal receivers for SOP-01 (wired in apps.ready)."""
from django.db import transaction

from qa_core.flow import register_effect

from . import case_services, qa_services, services


@register_effect("onboarding_submitted")
def onboarding_submitted(ctx):
    services.on_submitted(ctx)


@register_effect("create_clearances")
def create_clearances(ctx):
    services.on_open_clearance(ctx)


@register_effect("clearance_returned")
def clearance_returned(ctx):
    services.on_returned(ctx)


# --- v2.1 F.15/F.16: onboarding v2 (pay to submit, per-document review) --------
@register_effect("onboarding_v2_submitted")
def onboarding_v2_submitted(ctx):
    from . import onboarding_v2

    onboarding_v2.on_submitted(ctx)


@register_effect("onboarding_v3_documents_submitted")
def onboarding_v3_documents_submitted(ctx):
    from . import onboarding_v3

    onboarding_v3.on_documents_submitted(ctx)


@register_effect("onboarding_physical_verification")
def onboarding_physical_verification(ctx):
    from . import onboarding_v2

    onboarding_v2.on_programmes_submitted(ctx)


# --- Phase 3: SOP-02 QA requests and SSQs ----------------------------------------
_P3 = {
    "qa_request_submitted": qa_services.on_submitted,
    "qa_request_clearance_opened": qa_services.on_clearance_opened,
    "qa_request_returned": qa_services.on_returned,
    "issue_qa_request_fee": qa_services.on_issue_fee,
    "qa_request_cancelled": qa_services.on_cancelled,
    "open_ssqs": qa_services.on_open_ssqs,
    "spawn_qa_case": qa_services.on_spawn_case,
    "ssq_submitted": qa_services.on_ssq_submitted,
    "ssq_accepted": qa_services.on_ssq_accepted,
    "ssq_returned": qa_services.on_ssq_returned,
}
for _name, _fn in _P3.items():
    register_effect(_name)(_fn)

# --- Phase 4: SOP-03 case stage data ------------------------------------------------
_P4 = {
    "case_opened": case_services.on_case_opened,
    "case_cost_submitted": case_services.on_cost_submitted,
    "case_personnel_submitted": case_services.on_personnel_submitted,
    "case_personnel_confirmed": case_services.on_personnel_confirmed,
    "case_cost_audited": case_services.on_cost_audited,
    "case_cost_audit_confirmed": case_services.on_cost_audit_confirmed,
    "case_cost_es_approved": case_services.on_cost_es_approved,
    "case_visit_submitted": case_services.on_visit_submitted,
    "case_visit_confirmed": case_services.on_visit_confirmed,
    "case_visit_es_approved": case_services.on_visit_es_approved,
    "case_funds_released": case_services.on_funds_released,
    "case_returned": case_services.on_returned,
    "case_completed": case_services.on_completed,
}
for _name, _fn in _P4.items():
    register_effect(_name)(_fn)


def _connect():
    from payments import services as pay

    def claimed(sender, obligation, **kwargs):
        with transaction.atomic():
            services.payment_claimed(obligation)
            qa_services.payment_claimed(obligation)

    def rejected(sender, obligation, **kwargs):
        with transaction.atomic():
            services.payment_rejected(obligation)
            qa_services.payment_rejected(obligation)

    def settled(sender, obligation, **kwargs):
        from . import onboarding_v2

        with transaction.atomic():
            services.payment_settled(obligation)
            qa_services.payment_settled(obligation)
            onboarding_v2.on_payment_settled_v2(obligation)  # v2.1: a waived fee submits too

    pay.payment_claimed.connect(claimed, dispatch_uid="acc-payment-claimed", weak=False)
    pay.payment_rejected.connect(rejected, dispatch_uid="acc-payment-rejected", weak=False)
    pay.payment_settled.connect(settled, dispatch_uid="acc-payment-settled", weak=False)

    # v2.1 F.15 (X14, D-5): the Director of Finance's final confirmation submits
    # the onboarding application automatically.
    from payments.signals import obligation_confirmed

    from . import onboarding_v2

    obligation_confirmed.connect(onboarding_v2.on_obligation_confirmed, dispatch_uid="acc-onboarding-autosubmit",
                                 weak=False)


_connect()
