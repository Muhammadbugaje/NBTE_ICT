"""Template tags for the institution's linear journey (qa_core.portal_journey).

    {% load portal_journey_tags %}
    {% get_linear_journey institution as lj %}      → the journey dict
    {% include "qa_portal/_journey.html" with j=lj %}
    {% request_journey req %}                         → compact stepper for one QA request
    {{ text|plain }}                                  → strip internal jargon (SOP-01 …)
"""
from django import template

from qa_core import portal_journey

register = template.Library()


def _user(context):
    request = context.get("request")
    return getattr(request, "user", None)


@register.simple_tag(takes_context=True)
def get_linear_journey(context, institution):
    if institution is None:
        return None
    try:
        return portal_journey.linear_journey(institution, user=_user(context))
    except Exception:  # the portal must never break on the journey
        import logging
        logging.getLogger(__name__).exception("linear journey failed")
        return None


@register.inclusion_tag("qa_portal/_journey_compact.html", takes_context=True)
def request_journey(context, req):
    try:
        j = portal_journey.request_journey(req, user=_user(context))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("request journey failed")
        j = None
    return {"j": j, "req": req}


@register.filter
def plain(text):
    return portal_journey.plain(text)


@register.simple_tag
def show_department_names():
    """``{% show_department_names as show_departments %}`` (qa.portal.show_department_names)."""
    return portal_journey.show_department_names()


@register.filter
def correction_notes(ssq):
    """NBTE's notes on a self-study it returned for correction."""
    return portal_journey.correction_notes(ssq)
