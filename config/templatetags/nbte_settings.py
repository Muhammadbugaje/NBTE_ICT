"""{% setting "key" "default" %} — read a site setting in a template
(v2.1 A9; ported from EE Hub). `{% setting "appearance.scene" "laminar" as scene %}`
stores it instead. Never raises: a fresh database shows the default."""
from django import template

import config

register = template.Library()


@register.simple_tag
def setting(key, default=""):
    try:
        value = config.get(key, default)
    except Exception:
        value = default
    return default if value is None else value
