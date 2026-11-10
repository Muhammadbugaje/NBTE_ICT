"""{% css_bundle "core" %} — the consolidated stylesheets (v2.1 V7).

Prints one <link> to static/css/nbte-<name>.bundle.css, or — when the
setting ui.legacy_css is on (fallback for one release) — the original files
one by one, in the same order. See core/css_bundles.py.
"""
from django import template
from django.templatetags.static import static
from django.utils.html import format_html_join

from core import css_bundles

register = template.Library()


def _legacy(context):
    appearance = context.get("appearance") if hasattr(context, "get") else None
    if isinstance(appearance, dict) and "legacy_css" in appearance:
        return bool(appearance["legacy_css"])
    try:
        import config

        return bool(config.get("ui.legacy_css", False))
    except Exception:
        return False


@register.simple_tag(takes_context=True)
def css_bundle(context, name):
    if name not in css_bundles.BUNDLES:
        raise template.TemplateSyntaxError(f"Unknown CSS bundle {name!r}")
    if _legacy(context):
        files = [f for _layer, f in css_bundles.source_files(name)]
    else:
        files = [css_bundles.bundle_filename(name)]
    return format_html_join("\n  ", '<link rel="stylesheet" href="{}">', ((static(f"css/{f}"),) for f in files))
