"""Template helpers for the dashboard templates.

Only one filter: `pct_class`, which turns a value and a total into a
`pct-N` class name. N is the nearest multiple of 5 (0–100).

Why: writing bar widths via `style="width: N%"` means embedding Django
tags inside a CSS value, which editors flag as invalid CSS. Class-based
widths are pure CSS and lint clean.
"""
from django import template

register = template.Library()


@register.filter
def pct_class(value, total):
    try:
        v = float(value or 0)
        t = float(total or 0)
    except (TypeError, ValueError):
        return "pct-0"
    if t <= 0:
        return "pct-0"
    pct = (v / t) * 100
    bucket = int(round(pct / 5.0) * 5)
    bucket = max(0, min(100, bucket))
    return f"pct-{bucket}"
