"""{% site_text "home.title.about" %} — an editable piece of page text.

Reads the config registry; a missing or blank value falls back to the default
declared in config/defaults.py (HOME_TEXT), so pages never show an empty title.
"""
from django import template

import config
from config.defaults import HOME_TEXT_DEFAULTS

register = template.Library()


@register.simple_tag
def site_text(key, fallback=""):
    default = HOME_TEXT_DEFAULTS.get(key, fallback)
    try:
        value = config.get(key, default)
    except Exception:  # registry not ready (fresh database): show the default
        value = default
    value = str(value or "").strip()
    return value or default
