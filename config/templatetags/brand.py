"""Brand tags (v2.1 A5/A6/A7).

    {% load brand %}
    {% brand_logo "primary" 40 %}            the mark chosen for a slot (light/dark aware)
    {% brand_logo "wordmark" 36 css="nav-brand__logo" alt="NBTE" %}
    {% brand_logo_url "app_icon" %}          URL of the slot's light file
    {% crest "wm" css="hero-crest__svg" %}    inline crest (templates/partials/crest.html)
"""
import itertools

from django import template
from django.template.loader import render_to_string

from config import branding

register = template.Library()
_uid = itertools.count(1)


@register.simple_tag
def brand_logo(slot="primary", size=40, css="", alt=""):
    return branding.active_logo_markup(slot, size, css, alt)


@register.simple_tag
def brand_logo_url(slot="primary", dark=False):
    return branding.logo_url(slot, dark=bool(dark))


@register.simple_tag
def crest(kind="wm", css="", label=""):
    return render_to_string("partials/crest.html",
                            {"kind": kind, "css": css, "label": label, "uid": next(_uid)})
