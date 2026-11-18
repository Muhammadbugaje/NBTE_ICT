"""Guided tours ("Guide me") in templates. See qa_core/tours.py.

    {% load tour_tags %}
    {% tour_boot %}                       → loads the engine + this person's tours (base templates)
    {% tour_guides %}                     → "Guide me through it" list for the page's audience
    {% tour_guides guide="payments" %}    → only the tours that belong to one written guide
    {% tour_guides host="staff" %}        → the NBTE staff tours for the QA workspace
    {% tour_guides host="staff" area="main" %}   → the tours for the internal (non-QA) system
"""
from django import template

from qa_core import tours

register = template.Library()


def _host(context):
    request = context.get("request")
    try:
        from qa_core.hosts import is_portal_request

        return "portal" if is_portal_request(request) else "staff"
    except Exception:
        return "staff"


@register.inclusion_tag("partials/tour_boot.html", takes_context=True)
def tour_boot(context):
    request = context.get("request")
    data = None
    try:
        if request is not None:
            data = tours.boot(request, host=_host(context), institution=context.get("institution"))
    except Exception:  # a guide must never break a page
        import logging

        logging.getLogger(__name__).exception("tour boot failed")
    return {"tour_data": data}


@register.inclusion_tag("partials/tour_guides.html", takes_context=True)
def tour_guides(context, guide=None, host=None, audience=None, variant="list", limit=0, area=None):
    request = context.get("request")
    user = getattr(request, "user", None)
    host = host or _host(context)
    signed_in = bool(user is not None and user.is_authenticated)
    if audience is None:
        audience = "staff" if host == "staff" else "institution"
        if signed_in and host == "portal" and getattr(user, "user_type", "") in (
                "resource_person", "professional_body"):
            audience = user.user_type
    if area is None and host == "staff" and not guide:
        area = "qa"  # the staff guides pages belong to the QA workspace
    rows = tours.public_tours(host=host, audience=audience, guide=guide, area=area)
    if signed_in and audience == getattr(user, "user_type", "staff"):
        # Only offer what this person can actually run (e.g. not onboarding tours once approved).
        try:
            allowed = {t["id"]: t for t in tours.tours_for_request(request, host=host,
                                                                   institution=context.get("institution"))}
            rows = [dict(r, url=f"{allowed[r['id']]['start']}?tour={r['id']}") for r in rows if r["id"] in allowed]
        except Exception:
            pass
    if limit:
        rows = rows[:limit]
    return {"tour_rows": rows, "tour_variant": variant, "tour_signed_in": signed_in, "tour_guide": guide}


def tours_institution(user, host):
    if host != "portal" or getattr(user, "user_type", "") != "institution":
        return None
    try:
        from qa_core.portal_views import _institution_for

        return _institution_for(user)
    except Exception:
        return None
