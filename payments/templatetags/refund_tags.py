"""v2.5: cancellation / refund cards for the request and application pages.

    {% load refund_tags %}
    {% refund_cancel_card req %}        portal: Cancel button + refund preview, or the refund status
    {% nbte_withdraw_card req %}        staff: "Withdraw (NBTE)" link, or the cancellation and its refund
"""
from django import template
from django.urls import reverse

register = template.Library()


def _state(subject):
    from payments import cancellations
    from payments.models import RefundRequest

    c = cancellations.cancellation_of(subject)
    rr = RefundRequest.unscoped.filter(cancellation=c).first() if c is not None else None
    return c, rr


@register.inclusion_tag("qa_portal/refunds/_subject_card.html", takes_context=True)
def refund_cancel_card(context, subject):
    from payments import cancellations, refund_policy

    out = {"request": context.get("request"), "cancellation": None, "can_cancel": False, "ce_only": False}
    if subject is None or getattr(subject, "pk", None) is None:
        return out
    request = context.get("request")
    user = getattr(request, "user", None)
    c, rr = _state(subject)
    out.update(cancellation=c, refund=rr)
    if c is None and user is not None and cancellations.can_cancel_as_institution(user, subject) \
            and cancellations.is_cancellable(subject):
        kind = refund_policy.kind_of(subject)
        out.update(can_cancel=True, quote=refund_policy.refund_quote(subject),
                   noun="this request" if kind == "qa_request" else "the application",
                   cancel_url=(reverse("qa_portal:qa_request_cancel_refund", args=[subject.pk])
                               if kind == "qa_request" else reverse("qa_portal:application_cancel")))
    elif c is None and user is not None and cancellations.is_institution_non_ce(user, subject) \
            and cancellations.is_cancellable(subject):
        out["ce_only"] = True
    return out


@register.inclusion_tag("payments/refunds/_withdraw_card.html", takes_context=True)
def nbte_withdraw_card(context, subject):
    from payments import cancellations, refund_policy

    out = {"cancellation": None, "can_withdraw": False}
    if subject is None or getattr(subject, "pk", None) is None:
        return out
    user = getattr(context.get("request"), "user", None)
    c, rr = _state(subject)
    out.update(cancellation=c, refund=rr, subject=subject, kind=refund_policy.kind_of(subject))
    if c is None and user is not None and cancellations.can_withdraw_for_nbte(user) \
            and cancellations.is_cancellable(subject):
        out.update(can_withdraw=True, quote=refund_policy.refund_quote(subject, by_nbte=True))
    return out
