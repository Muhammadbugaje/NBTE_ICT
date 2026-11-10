"""NBTE brand marks (v2.1 Phase A: A5, A6, A7, T-CONS-7; ported from EE Hub).

Which mark appears where is decided per *slot* (Settings → Branding):
    primary   sidebar, top bars, page loader, admin header
    formal    letters, receipts, records and other PDFs; ID cards
    app_icon  favicon, installed phone app, social previews
    wordmark  public website header and footer, e-mails

Built-in marks are the SVG files in static/img/brand/ (light + dark). An admin
can upload a PNG/JPEG/WebP mark (with an optional dark version) and choose it
for any slot; nothing else in the code names a logo file.
"""
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.templatetags.static import static
from django.utils.html import escape
from django.utils.safestring import mark_safe

BRAND_STATIC = "img/brand"
BRAND_DIR = Path(settings.BASE_DIR) / "static" / "img" / "brand"

# code, name, tagline, use note, light file, dark file, aspect (w/h), wordmark?
BUILTINS = [
    ("nbte-crest", "NBTE crest", "Full-colour crest",
     "The redrawn crest. Light and dark versions switch with the theme.",
     "nbte-logo-light.svg", "nbte-logo-dark.svg", Decimal("0.929"), False),
    ("nbte-crest-mono", "NBTE crest (one colour)", "Single-colour crest",
     "For watermarks, stamps and print on coloured grounds.",
     "nbte-logo-mono.svg", "nbte-logo-mono-white.svg", Decimal("0.929"), False),
    ("nbte-app-icon", "NBTE app icon", "Rounded-square icon",
     "Favicon, installed app and social previews.",
     "nbte-app-icon.svg", "", Decimal("1.000"), False),
    ("nbte-wordmark", "NBTE wordmark", "Crest and name, side by side",
     "Public site header and footer, e-mails.",
     "nbte-wordmark.svg", "nbte-wordmark-dark.svg", Decimal("4.000"), True),
]
SLOTS = [
    ("primary", "Primary logo", "Sidebar, top bars, page loader and admin header"),
    ("formal", "Formal seal", "Letters, receipts, records (PDF) and ID cards"),
    ("app_icon", "App icon", "Browser tab, installed app and link previews"),
    ("wordmark", "Wordmark", "Public website header and footer"),
]
SLOT_DEFAULTS = {"primary": "nbte-crest", "formal": "nbte-crest", "app_icon": "nbte-app-icon",
                 "wordmark": "nbte-wordmark"}
# Raster copies rendered once from the SVGs (manage.py build_brand_assets) for
# PDFs (reportlab), e-mail clients and favicon fallbacks.
PDF_LOGO_FILE = BRAND_DIR / "nbte-logo-512.png"
FAVICON_PNG = {32: "nbte-icon-32.png", 180: "nbte-icon-180.png", 512: "nbte-icon-512.png"}


def seed_builtin_logos(ctx=None):
    """Create the built-in marks (idempotent; never changes an edited row)."""
    from .models import BrandLogo

    created = 0
    for order, (code, name, tagline, note, light, dark, aspect, wordmark) in enumerate(BUILTINS):
        _obj, was_created = BrandLogo.objects.get_or_create(
            code=code,
            defaults={"name": name, "tagline": tagline, "use_note": note,
                      "static_path": f"{BRAND_STATIC}/{light}",
                      "static_path_dark": f"{BRAND_STATIC}/{dark}" if dark else "",
                      "aspect": aspect, "is_builtin": True, "is_wordmark": wordmark, "order": order})
        created += int(was_created)
    return created


class _Snapshot:
    """Picklable view of a BrandLogo (what the markup needs)."""

    def __init__(self, code, name, light_url, dark_url, aspect, is_wordmark):
        self.code, self.name = code, name
        self.light_url, self.dark_url = light_url, dark_url
        self.aspect, self.is_wordmark = float(aspect or 1), is_wordmark


def _snapshot_of(logo):
    light = logo.image.url if logo.image else (static(logo.static_path) if logo.static_path else "")
    dark = logo.image_dark.url if logo.image_dark else (
        "" if logo.image else (static(logo.static_path_dark) if logo.static_path_dark else ""))
    return _Snapshot(logo.code, logo.name, light, dark, logo.aspect, logo.is_wordmark)


def _builtin_snapshot(code):
    for c, name, _t, _n, light, dark, aspect, wordmark in BUILTINS:
        if c == code:
            return _Snapshot(c, name, static(f"{BRAND_STATIC}/{light}"),
                             static(f"{BRAND_STATIC}/{dark}") if dark else "", aspect, wordmark)
    return None


def slot_code(slot):
    import config

    try:
        return config.get(f"brand.logo.{slot}", SLOT_DEFAULTS.get(slot, "nbte-crest")) or SLOT_DEFAULTS.get(slot)
    except Exception:
        return SLOT_DEFAULTS.get(slot, "nbte-crest")


def active_logo(slot="primary"):
    """The snapshot for a slot: the chosen mark, else the slot's built-in."""
    from django.core.cache import cache

    code = slot_code(slot)
    key = f"brandlogo:v1:{slot}:{code}"
    snap = cache.get(key)
    if snap is not None:
        return snap
    snap = None
    try:
        from .models import BrandLogo

        logo = BrandLogo.objects.filter(code=code).first()
        if logo is not None and (logo.image or logo.static_path):
            snap = _snapshot_of(logo)
    except Exception:  # table missing (fresh database): built-in files
        snap = None
    if snap is None:
        snap = _builtin_snapshot(code) or _builtin_snapshot(SLOT_DEFAULTS.get(slot, "nbte-crest"))
    cache.set(key, snap, 600)
    return snap


def invalidate():
    from django.core.cache import cache

    for slot, *_ in SLOTS:
        for code in [c[0] for c in BUILTINS] + [slot_code(slot)]:
            cache.delete(f"brandlogo:v1:{slot}:{code}")
    try:
        from .models import BrandLogo

        for code in BrandLogo.objects.values_list("code", flat=True):
            for slot, *_ in SLOTS:
                cache.delete(f"brandlogo:v1:{slot}:{code}")
    except Exception:
        pass


def logo_markup(snap, size=40, css="", alt=""):
    """<img> markup for a mark; when a dark version exists both are emitted
    and CSS (.brand-logo__dark) shows the one that suits the theme."""
    if snap is None:
        return ""
    h = int(size)
    w = max(1, round(h * snap.aspect))
    alt_attr = escape(alt)
    cls = f"brand-logo brand-logo--{escape(snap.code)} {escape(css)}".strip()
    light = (f'<img class="brand-logo__img brand-logo__light" src="{escape(snap.light_url)}" alt="{alt_attr}" '
             f'width="{w}" height="{h}" decoding="async">')
    if not snap.dark_url:
        return mark_safe(f'<span class="{cls}">{light}</span>')
    dark = (f'<img class="brand-logo__img brand-logo__dark" src="{escape(snap.dark_url)}" alt="" '
            f'width="{w}" height="{h}" decoding="async" aria-hidden="true">')
    return mark_safe(f'<span class="{cls} has-dark">{light}{dark}</span>')


def active_logo_markup(slot="primary", size=40, css="", alt=""):
    return logo_markup(active_logo(slot), size, css, alt)


def logo_url(slot="primary", dark=False):
    snap = active_logo(slot)
    if snap is None:
        return ""
    return (snap.dark_url or snap.light_url) if dark else snap.light_url


def pdf_logo_path(slot="formal"):
    """A raster file reportlab can draw: an uploaded PNG/JPEG for the slot,
    else the PNG rendered from the built-in SVG (build_brand_assets)."""
    try:
        from .models import BrandLogo

        logo = BrandLogo.objects.filter(code=slot_code(slot)).first()
        if logo is not None and logo.image:
            path = Path(logo.image.path)
            if path.exists():
                return path
    except Exception:
        pass
    configured = getattr(settings, "PDF_LOGO_PATH", None)
    if configured and Path(configured).exists():
        return Path(configured)
    return PDF_LOGO_FILE if PDF_LOGO_FILE.exists() else None
