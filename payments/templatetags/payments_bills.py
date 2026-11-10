"""v2.4: bills Finance made by hand that have not reached the institution yet
(shown on the staff institution record, Bills tab)."""
from django import template

register = template.Library()


@register.simple_tag
def bill_requests_for(inst, user):
    from payments.bill_requests import can_view
    from payments.models import BillRequest

    if inst is None or not can_view(user):
        return []
    return list(BillRequest.unscoped.filter(institution=inst, status__in=(BillRequest.DRAFT, BillRequest.AWAITING,
                                                                          BillRequest.REJECTED))
                .order_by("-created_at")[:5])
