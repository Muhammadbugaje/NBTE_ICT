"""Workflow effect: create a payment obligation for the flow's subject
(``create_payment_obligation:<fee schedule code>``, §10.2 step 1)."""
from qa_core.flow import register_effect


@register_effect("create_payment_obligation")
def create_payment_obligation(ctx):
    from . import services

    subject = ctx.subject
    institution = getattr(subject, "institution", None)
    if institution is None:
        raise ValueError("create_payment_obligation needs a subject with an institution.")
    services.issue_obligation(institution, ctx.argument, source=subject, actor=ctx.actor)
