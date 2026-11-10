"""Role / account audience badges: NBTE staff vs institution vs other portal."""
from django import template
from django.utils.html import format_html

from accounts import audience

register = template.Library()


@register.filter
def role_audience(role):
    return audience.role_audience(role)


@register.simple_tag
def audience_badge(role):
    """<span class="um-aud um-aud--institution">Institution users</span>"""
    if role is None:
        return ""
    aud = audience.role_audience(role)
    short = {"staff": "NBTE staff", "institution": "Institution", "resource_person": "Resource person",
             "professional_body": "Professional body"}[aud]
    return format_html('<span class="um-aud um-aud--{}" title="For {}">{}</span>', aud,
                       audience.AUDIENCE_LABELS[aud].lower(), short)
