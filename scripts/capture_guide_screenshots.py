"""Capture the screenshots used by the institution guides (Phase 6).

    python scripts/capture_guide_screenshots.py [--only NAME ...] [--keep-db]

1. Copies the database to a temporary file and builds the guide demo states in
   the copy (scripts/guide_demo.py), so the real database is never touched.
2. Renders each portal screen with Django's test client, signed in as the
   right demo user, outlines the control the step talks about in gold with a
   numbered badge, and rasterises a region of the page with macOS Quick Look
   (``qlmanage``) at up to 2x, so text stays sharp in Word.
3. Writes docs/guides/images/*.png, the cover artwork and the flow diagrams,
   which ``manage.py generate_institution_guides`` embeds.

macOS only (Quick Look). On other systems take the screenshots by hand with
the same file names.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "guides", "images")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.dev")

GOLD = "#f2a900"
SENTINEL = (255, 0, 255)

# Who each shot signs in as (keys from guide_demo.build()); None = anonymous.
# Each shot: name, who, url, layout width, (x, y, w, h) region in CSS px,
# highlights [(selector, number)], extra options.
SHOTS = [
    # --- public pages
    dict(name="landing", who=None, url="/", width=1280, region=(0, 0, 1280, 880)),
    dict(name="how_to", who=None, url="/how-to/", width=1280, region=(0, 0, 1280, 780)),
    dict(name="login", who=None, url="/login/", width=1200, region=(560, 0, 640, 760),
         hl=[(".auth-form-inner form .field:nth-of-type(1)", 1), (".auth-form-inner form .field:nth-of-type(2)", 2),
             (".auth-submit", 3)]),
    dict(name="password_forgot", who=None, url="/password/forgot/", width=1200, region=(600, 0, 600, 640),
         hl=[(".auth-form-inner form .field", 1), (".auth-form-inner form button[type=submit]", 2)]),
    dict(name="password_set", who="token:qa", url="", width=1200, region=(600, 0, 600, 700),
         hl=[(".auth-form-inner form .field:nth-of-type(1)", 1), (".auth-form-inner form .field:nth-of-type(2)", 2),
             (".auth-form-inner form button[type=submit]", 3)]),
    # --- registration (onboarding step 1)
    dict(name="register_top", who=None, url="/register/", width=1200, region=(0, 0, 1200, 980),
         hl=[(".rg-path .is-here", 1), ("#rg-institution", 2)]),
    dict(name="register_location", who=None, url="/register/", width=1200, region=(0, 0, 1200, 900),
         crop_to="#rg-location", crop_width=860),
    dict(name="register_account", who=None, url="/register/", width=1200, region=(0, 0, 1200, 900),
         crop_to="#rg-account", crop_width=860),
    dict(name="register_submit", who=None, url="/register/", width=1200, region=(0, 0, 1200, 600),
         crop_to="#rg-declare", crop_width=860, hl=[("#rg-declare .qa-field--check", 1), (".rg-submit__btn", 2)]),
    # --- the signed-in portal (an approved institution)
    dict(name="dashboard", who="qa", url="/", width=1200, region=(0, 0, 1200, 980),
         hl=[(".qa-sections", 1), (".qa-dash-action", 2), ("section[aria-labelledby='qa-activity-title']", 3),
             (".qa-bell", 4)]),
    dict(name="timeline", who="qa", url="/timeline/", width=1200, region=(0, 60, 1200, 760),
         hl=[(".qa-filters, .qa-toolbar, form[role=search]", 1)]),
    dict(name="institution", who="qa", url="/institution/", width=1200, region=(0, 60, 1200, 520)),
    dict(name="account", who="qa", url="/account/", width=1200, region=(0, 60, 1200, 700)),
    # --- onboarding
    dict(name="application_draft", who="new", url="/application/", width=1200, region=(0, 60, 1200, 960)),
    dict(name="application_programmes", who="new", url="/application/", width=1200, region=(0, 60, 1200, 1600),
         hl=[("#qa-prog-card form", 1)], scroll_to="#qa-prog-card", crop_to="#qa-prog-card"),
    dict(name="application_submit", who="new", url="/application/", width=1200, region=(0, 60, 1200, 1600),
         hl=[("section[aria-labelledby='qa-submit-h'] button[type=submit]", 1)],
         crop_to="section[aria-labelledby='qa-submit-h']"),
    dict(name="documents", who="new", url="/documents/", width=1200, region=(0, 60, 1200, 1000),
         hl=[(".qa-upload__btn.btn-primary", 1)]),
    dict(name="application_clearance", who="partial", url="/application/", width=1200, region=(0, 60, 1200, 860)),
    dict(name="application_fee", who="cleared", url="/application/", width=1200, region=(0, 60, 1200, 900)),
    # --- QA requests
    dict(name="qa_requests", who="qa", url="/qa-requests/", width=1200, region=(0, 60, 1200, 760),
         hl=[("a[href='/qa-requests/new/']", 1)]),
    dict(name="qa_request_new", who="qa", url="/qa-requests/new/", width=1200, region=(0, 60, 1200, 1000),
         hl=[(".qa-choice-grid", 1)]),
    dict(name="qa_request_draft", who="qa", url="/qa-requests/{draft}/", width=1200, region=(0, 60, 1200, 1100),
         hl=[("section[aria-labelledby='rq-submit']", 1)]),
    dict(name="qa_request_fee", who="qa", url="/qa-requests/{due}/", width=1200, region=(0, 60, 1200, 900),
         hl=[("section[aria-labelledby='rq-fee']", 1), ("section[aria-labelledby='rq-progs'] .qa-menu", 2)]),
    dict(name="qa_request_ssq", who="qa", url="/qa-requests/{open}/", width=1200, region=(0, 60, 1200, 900),
         hl=[("section[aria-labelledby='rq-ssq']", 1)]),
    dict(name="ssq", who="qa", url="/qa-requests/{open}/ssq/{ssq}/", width=1240, region=(0, 60, 1240, 980),
         hl=[(".ssq-nav, nav[aria-label*='ection']", 1), ("[data-ssq-save-status], .ssq-status, .qa-save-status", 2),
             ("#ssq-sub-h", 3)]),
    dict(name="ssq_department", who="qa", url="/qa-requests/{open}/ssq/{ssq}/", width=1240,
         region=(0, 60, 1240, 2600), crop_to="details[data-qa-remember='ssq-dept']", crop_width=420, open_details_sel=["data-qa-remember=\"ssq-dept\""]),
    dict(name="queries", who="qa", url="/queries/", width=1200, region=(0, 60, 1200, 420)),
    dict(name="query", who="qa", url="/queries/{query}/", width=1200, region=(0, 60, 1200, 1000),
         hl=[("section[aria-labelledby='qr-h'] textarea", 1), ("section[aria-labelledby='qr-h'] input[type=file]", 2),
             ("section[aria-labelledby='qr-h'] button[type=submit]", 3)]),
    dict(name="inspections", who="qa", url="/inspections/", width=1200, region=(0, 60, 1200, 420)),
    # --- payments
    dict(name="payments", who="qa", url="/payments/", width=1200, region=(0, 60, 1200, 700)),
    dict(name="payment", who="qa", url="/payments/{bill}/", width=1200, region=(0, 60, 1200, 760),
         hl=[("#qa-payment .qa-steps, .qa-steps", 1), (".qa-amount, .qa-pay-amount", 2)]),
    dict(name="payment_options", who="qa", url="/payments/{bill}/", width=1200, region=(0, 60, 1200, 2000),
         crop_to="section[aria-labelledby='qa-pay-h']",
         hl=[(".qa-payopt:nth-child(1) button[type=submit]", 1), (".qa-payopt:nth-child(2) button[type=submit]", 2),
             (".qa-payopt:nth-child(3)", 3)]),
    dict(name="payment_proof", who="qa", url="/payments/{bill}/", width=1200, region=(0, 60, 1200, 2600),
         open_details=["qa-proof"], crop_to="#qa-proof", hl=[("#qa-proof button[type=submit]", 1)]),
    # --- phone
    dict(name="m_landing", who=None, url="/", width=390, region=(0, 0, 390, 844), phone=True),
    dict(name="m_dashboard", who="qa", url="/", width=390, region=(0, 0, 390, 844), phone=True),
    dict(name="m_qa_request", who="qa", url="/qa-requests/{open}/", width=390, region=(0, 0, 390, 844), phone=True),
    dict(name="m_register", who=None, url="/register/", width=390, region=(0, 0, 390, 844), phone=True),
]

FREEZE = ("<style>*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;"
          "transition:none!important}.qp-dock,.qa-tabbar,.app-tabbar{position:absolute!important}"
          "[data-vv-reveal]{opacity:1!important;transform:none!important}html{background:#f5f5f7}</style>")


def highlight_css(highlights):
    rules = []
    for selector, number in highlights:
        sel = ", ".join(s.strip() for s in selector.split(","))
        after = ", ".join(f"{s.strip()}::after" for s in selector.split(","))
        rules.append(f"{sel}{{outline:3px solid {GOLD}!important;outline-offset:5px!important;"
                     f"box-shadow:0 0 0 11px rgba(242,169,0,.22)!important;position:relative!important;"
                     f"overflow:visible!important;border-radius:12px}}")
        rules.append(f"{after}{{content:'{number}'!important;position:absolute!important;top:-17px!important;"
                     f"left:-17px!important;right:auto!important;bottom:auto!important;z-index:60!important;"
                     f"display:block!important;width:30px!important;height:30px!important;border-radius:50%!important;"
                     f"background:{GOLD}!important;color:#1c1300!important;font:800 15px/30px -apple-system,"
                     f"'Helvetica Neue',sans-serif!important;text-align:center!important;opacity:1!important;"
                     f"transform:none!important;box-shadow:0 2px 6px rgba(0,0,0,.35)!important;border:0!important;"
                     f"letter-spacing:0!important}}")
    return "<style>" + "".join(rules) + "</style>"


def setup(db_copy):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_copy}"
    import django

    django.setup()
    import logging

    logging.disable(logging.WARNING)


def ids():
    from accreditation.models import SSQ, QARequest, Query
    from institutions.models import Institution
    from payments.models import PaymentObligation

    kdp = Institution.unscoped.filter(code="KDP").first()
    req = lambda status: QARequest.unscoped.filter(institution=kdp, status=status).order_by("-pk").first()  # noqa: E731
    draft, due, open_ = req("draft"), req("fee_due"), req("ssq_in_progress")
    ssq = SSQ.unscoped.filter(qa_request_programme__qa_request=open_).order_by("pk").first() if open_ else None
    query = Query.unscoped.filter(institution=kdp).order_by("-pk").first()
    bill = PaymentObligation.unscoped.filter(institution=kdp, status="awaiting_payment").order_by("-pk").first()
    pk = lambda o: o.pk if o else 0  # noqa: E731
    return {"draft": pk(draft), "due": pk(due), "open": pk(open_), "ssq": pk(ssq), "query": pk(query),
            "bill": pk(bill)}


def password_set_url(email):
    from qa_core.portal_tokens import portal_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from accounts.models import User

    user = User.objects.get(email=email)
    return f"/password/set/{urlsafe_base64_encode(force_bytes(user.pk))}/{portal_token_generator.make_token(user)}/"


def render_html(client, url, shot):
    response = client.get(url, follow=True)
    if response.status_code != 200:
        return None, response.status_code
    static = os.path.join(ROOT, "static")
    html = response.content.decode()
    html = html.replace('href="/static/', f'href="file://{static}/').replace('src="/static/', f'src="file://{static}/')
    html = re.sub(r"\?v=\d+", "", html)
    html = re.sub(r"<html([^>]*)>", r'<html\1 data-theme="light">', html, count=1)
    for details_id in shot.get("open_details", []):
        html = html.replace(f'id="{details_id}"', f'id="{details_id}" open', 1)
    for attr in shot.get("open_details_sel", []):
        html = html.replace(attr, attr + " open", 1)
    css = FREEZE + highlight_css(shot.get("hl", []))
    if shot.get("crop_to"):
        css += isolate(shot["crop_to"], shot.get("crop_width", 880))
    html = html.replace("</head>", css + "</head>", 1)
    return html, 200


def isolate(selector, width=880):
    """Show one block on its own: everything else becomes invisible and the
    block is pinned to the top-left, so the shot is just that block."""
    sels = [x.strip() for x in selector.split(",")]
    shown = ", ".join(sels + [f"{x} *" for x in sels])
    return ("<style>html,body{background:#f5f5f7!important}body *{visibility:hidden!important}"
            f"{shown}{{visibility:visible!important}}"
            f"{', '.join(sels)}{{position:fixed!important;top:28px!important;left:28px!important;margin:0!important;"
            f"width:{width}px!important;max-width:none!important;z-index:9999!important}}</style>")


def _snapshot(path, out, size):
    """Rasterise a local HTML/SVG file: Quick Look on macOS, else headless Chromium (Playwright)."""
    if sys.platform == "darwin" and shutil.which("qlmanage"):
        subprocess.run(["qlmanage", "-t", "-s", str(size), "-o", os.path.dirname(out), path], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size, "height": 900})
        page.goto("file://" + path)
        page.wait_for_timeout(400)
        page.screenshot(path=out, full_page=True)
        browser.close()


def rasterise(html, shot, dest, tmp):
    width = shot["width"]
    x, y, w, h = shot["region"]
    if shot.get("crop_to"):  # the block is pinned at the top-left (isolate())
        x, y, w = 0, 0, shot.get("crop_width", 880) + 56
        h = min(h, 1400)
    zoom = min(2.0, 760.0 / w)
    page = os.path.join(tmp, "page.html")
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(html)
    frame = os.path.join(tmp, "frame.html")
    with open(frame, "w", encoding="utf-8") as fh:
        fh.write(f'<html><body style="margin:0;background:rgb{SENTINEL}">'
                 f'<div style="width:{w * zoom:.0f}px;height:{h * zoom:.0f}px;overflow:hidden">'
                 f'<div style="width:{w}px;height:{h}px;overflow:hidden;transform:scale({zoom});transform-origin:0 0">'
                 f'<iframe src="file://{page}" width="{width}" height="{y + h + 40}" scrolling="no" '
                 f'style="border:0;display:block;margin-left:-{x}px;margin-top:-{y}px"></iframe>'
                 f'</div></div></body></html>')
    thumb = frame + ".png"
    if os.path.exists(thumb):
        os.remove(thumb)
    _snapshot(frame, thumb, max(320, round(w * zoom) + 8))
    if not os.path.exists(thumb):
        return False
    from PIL import Image, ImageChops

    img = Image.open(thumb).convert("RGB")
    diff = ImageChops.difference(img, Image.new("RGB", img.size, SENTINEL)).convert("L")
    bbox = diff.point(lambda v: 255 if v > 24 else 0).getbbox()
    if bbox:
        # Drop the anti-aliased seam where the page meets the frame colour.
        img = img.crop((0, 0, max(1, bbox[2] - 3), max(1, bbox[3] - 3)))
    if not shot.get("crop_to") and not shot.get("phone"):
        # Trim empty page ground below the content (keep the full width).
        def blank(colour):
            return ImageChops.difference(img, Image.new("RGB", img.size, colour)).convert("L").point(
                lambda v: 255 if v > 10 else 0)

        content = ImageChops.multiply(blank((245, 245, 247)), blank((255, 255, 255))).getbbox()
        if content and content[3] < img.height - 40:
            img = img.crop((0, 0, img.width, min(img.height, content[3] + round(24 * zoom))))
    if shot.get("crop_to"):
        # Trim the empty page ground around the isolated block.
        def unlike(colour):
            return ImageChops.difference(img, Image.new("RGB", img.size, colour)).convert("L").point(
                lambda v: 255 if v > 10 else 0)

        inner = ImageChops.multiply(unlike((245, 245, 247)), unlike((255, 255, 255))).getbbox()
        if inner:
            pad = round(16 * zoom)
            img = img.crop((max(0, inner[0] - pad), max(0, inner[1] - pad), min(img.width, inner[2] + pad),
                            min(img.height, inner[3] + pad)))
    img.save(dest, optimize=True)
    return True


def cover_art():
    """The cover's map (on the cover's forest green) and the logo on a white disc."""
    import build_nigeria_map as nm
    from PIL import Image, ImageDraw

    tmp = tempfile.mkdtemp()
    try:
        nm.build()
        with open(nm.OUT, encoding="utf-8") as fh:
            svg = fh.read().split("\n", 1)[1]
        style = ("<style>.nm-land{fill:#0b4a2d}.nm-dots{fill:#49c98a;opacity:.6}.nm-edge{fill:none;stroke:#8ee8b4;"
                 "stroke-opacity:.5;stroke-width:1.2}.nm-river{fill:none;stroke:#6fe0d0;stroke-width:2.2;opacity:.8}"
                 ".nm-arc{fill:none;stroke:#f5c04a;stroke-width:1.4;opacity:.45}.nm-dot{fill:#fff}.nm-ping{display:none}"
                 ".nm-hq-core{fill:#f5c04a}.nm-hq-ring{fill:none;stroke:#f5c04a;stroke-width:1.6}.nm-label rect{fill:#fff}"
                 ".nm-label text{fill:#04261a;font:700 12px -apple-system,Helvetica,sans-serif}</style>")
        svg = svg.replace('<svg class="nm"', '<svg xmlns="http://www.w3.org/2000/svg" class="nm"', 1)
        svg = svg.replace("<defs>", style + '<rect x="-40" y="-40" width="700" height="560" fill="#04261a"/><defs>', 1)
        path = os.path.join(tmp, "map.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        subprocess.run(["qlmanage", "-t", "-s", "1600", "-o", tmp, path], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(path + ".png"):
            img = Image.open(path + ".png").convert("RGB")
            bbox = Image.eval(img, lambda p: 255 - p).getbbox()
            img = img.crop(bbox) if bbox else img
            img = img.crop((6, 6, img.width - 6, img.height - 6))  # drop the light thumbnail edge
            img.save(os.path.join(OUT, "cover_map.png"), optimize=True)
            print("cover_map.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    logo = Image.open(os.path.join(ROOT, "static", "img", "brand", "nbte-logo-512.png")).convert("RGBA")
    size = 600
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, size - 1, size - 1), fill=(255, 255, 255, 255))
    scale = 0.74 * size / max(logo.size)
    logo = logo.resize((round(logo.width * scale), round(logo.height * scale)), Image.LANCZOS)
    disc.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
    disc.save(os.path.join(OUT, "logo_disc.png"), optimize=True)
    print("logo_disc.png")


def diagrams():
    """Light-mode PNGs of the how-to diagrams (Quick Look renders the SVG)."""
    import build_howto_diagrams as b
    from PIL import Image

    tmp = tempfile.mkdtemp()
    try:
        for name, build in b.DIAGRAMS.items():
            svg_path = os.path.join(tmp, f"{name}.svg")
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(b.light_svg(build()))
            subprocess.run(["qlmanage", "-t", "-s", "2000", "-o", tmp, svg_path], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            thumb = svg_path + ".png"
            if not os.path.exists(thumb):
                continue
            img = Image.open(thumb).convert("RGB")
            bbox = Image.eval(img, lambda p: 255 - p).getbbox()  # trim the white margin
            if bbox:
                pad = 16
                img = img.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(img.width, bbox[2] + pad),
                                min(img.height, bbox[3] + pad)))
            img.save(os.path.join(OUT, f"diagram_{name}.png"), optimize=True)
            print(f"diagram_{name}.png")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="Capture only these shot names.")
    parser.add_argument("--keep-db", action="store_true", help="Reuse the demo database from the last run.")
    parser.add_argument("--no-extras", action="store_true", help="Skip the cover art and diagrams.")
    args = parser.parse_args(argv)
    if not (sys.platform == "darwin" and shutil.which("qlmanage")):
        try:
            import playwright  # noqa: F401  (v2.3: headless Chromium works on any system)
        except ImportError:
            print("Quick Look (macOS) or Playwright is needed to capture screenshots.", file=sys.stderr)
            return 2
    work = os.path.join(tempfile.gettempdir(), "nbte-guide-shots")
    os.makedirs(work, exist_ok=True)
    db_copy = os.path.join(work, "guide.sqlite3")
    fresh = not (args.keep_db and os.path.exists(db_copy))
    if fresh:
        shutil.copyfile(os.path.join(ROOT, "db.sqlite3"), db_copy)
    setup(db_copy)
    from django.conf import settings
    from django.test import Client

    from accounts.models import User

    settings.MEDIA_ROOT = os.path.join(work, "media")
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    import guide_demo

    if fresh:
        logins, log = guide_demo.build()
        failures = [line for line in log if line.startswith("FAIL")]
        print(f"demo states: {len(log) - len(failures)} steps ok, {len(failures)} failed")
        for line in failures:
            print("  " + line)
    else:
        logins = {"new": "registrar@northfield.demo.ng", "cleared": "registrar@gidankwano.demo.ng",
                  "partial": "registrar@riverside.demo.ng", "qa": "admin@p4demo.local"}
    os.makedirs(OUT, exist_ok=True)
    host = settings.QA_PORTAL_HOST.split(":")[0]
    values = ids()
    tmp = tempfile.mkdtemp()
    try:
        for shot in SHOTS:
            if args.only and shot["name"] not in args.only:
                continue
            client = Client(HTTP_HOST=host)
            who, url = shot["who"], shot["url"]
            if who and who.startswith("token:"):
                url = password_set_url(logins[who.split(":", 1)[1]])
            elif who:
                client.force_login(User.objects.get(email=logins[who]))
            html, status = render_html(client, url.format(**values), shot)
            if html is None:
                print(f"skip {shot['name']}: HTTP {status}")
                continue
            ok = rasterise(html, shot, os.path.join(OUT, f"{shot['name']}.png"), tmp)
            print(f"{shot['name']}.png" if ok else f"skip {shot['name']}: Quick Look failed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not args.no_extras and not args.only:
        cover_art()
        diagrams()
    return 0


if __name__ == "__main__":
    sys.exit(main())
