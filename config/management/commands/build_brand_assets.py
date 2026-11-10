"""python manage.py build_brand_assets

Builds the derived brand files in static/img/brand/ from the four master SVGs
(design/logo, v2.1 A5/A6): the white one-colour crest, the wordmark lockups
(light and dark) and the raster copies used where SVG cannot go: PDFs
(reportlab), favicon fallbacks (32/180/512) and link previews. Run it after a
master SVG changes; the outputs are committed, so servers do not need cairosvg.
"""
import re

from django.core.management.base import BaseCommand

from config.branding import BRAND_DIR, FAVICON_PNG, PDF_LOGO_FILE

WORDMARK = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100" role="img" aria-label="NBTE, National Board for Technical Education">
  <svg x="0" y="4" width="85" height="92" viewBox="0 0 520 560">{crest}</svg>
  <text x="100" y="52" font-family="Inter, 'Segoe UI', Helvetica, Arial, sans-serif" font-size="40" font-weight="800" letter-spacing="-1" fill="{ink}">NBTE</text>
  <text x="101" y="78" font-family="Inter, 'Segoe UI', Helvetica, Arial, sans-serif" font-size="15.5" font-weight="500" fill="{sub}">National Board for Technical Education</text>
</svg>
"""


def _inner(svg_text, suffix):
    body = re.sub(r"^.*?<svg[^>]*>", "", svg_text, count=1, flags=re.S)
    body = body.rsplit("</svg>", 1)[0]
    body = re.sub(r"<title[^>]*>.*?</title>", "", body, flags=re.S)
    for i in set(re.findall(r'id="([^"]+)"', body)):
        body = body.replace(f'id="{i}"', f'id="{i}{suffix}"').replace(f"url(#{i})", f"url(#{i}{suffix})")
    return body


class Command(BaseCommand):
    help = "Build derived brand SVGs and PNG copies in static/img/brand/."

    def handle(self, *args, **opts):
        light = (BRAND_DIR / "nbte-logo-light.svg").read_text()
        dark = (BRAND_DIR / "nbte-logo-dark.svg").read_text()
        mono = (BRAND_DIR / "nbte-logo-mono.svg").read_text()
        (BRAND_DIR / "nbte-logo-mono-white.svg").write_text(mono.replace("currentColor", "#ffffff"))
        (BRAND_DIR / "nbte-wordmark.svg").write_text(
            WORDMARK.format(crest=_inner(light, "w"), ink="#12301f", sub="#3d5a48"))
        (BRAND_DIR / "nbte-wordmark-dark.svg").write_text(
            WORDMARK.format(crest=_inner(dark, "wd"), ink="#f2f7f3", sub="#b9cdbf"))
        self.stdout.write("SVG: nbte-logo-mono-white.svg, nbte-wordmark.svg, nbte-wordmark-dark.svg")
        try:
            import cairosvg
        except ImportError:
            self.stdout.write(self.style.WARNING("cairosvg is not installed: PNG copies not rebuilt."))
            return
        cairosvg.svg2png(bytestring=light.encode(), write_to=str(PDF_LOGO_FILE), output_width=476, output_height=512)
        icon = (BRAND_DIR / "nbte-app-icon.svg").read_bytes()
        for size, name in FAVICON_PNG.items():
            cairosvg.svg2png(bytestring=icon, write_to=str(BRAND_DIR / name), output_width=size, output_height=size)
        self.stdout.write(self.style.SUCCESS(
            f"PNG: {PDF_LOGO_FILE.name}, " + ", ".join(FAVICON_PNG.values())))
