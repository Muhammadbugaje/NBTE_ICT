"""python manage.py showcase_seed --count 5

Creates placeholder showcase images so the sign-in carousel, landing hero
and dashboard banner work out of the box. The pictures are generated here
(brand-green gradients, soft geometric light, the NBTE wordmark and logo)
— no stock photos, no third-party licensing. Re-running replaces earlier
placeholders (title starts with "Placeholder") unless --keep is given.
"""
import io
import math
import random

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from showcase.models import ShowcaseImage

PALETTES = [
    ((6, 28, 14), (31, 107, 62), (45, 138, 86)),
    ((13, 48, 26), (25, 87, 48), (90, 160, 114)),
    ((10, 13, 16), (19, 67, 38), (31, 107, 62)),
    ((6, 28, 14), (21, 94, 117), (31, 107, 62)),
    ((24, 28, 32), (31, 107, 62), (183, 121, 31)),
]
CAPTIONS = [
    ("One workspace for the directorate", "Helpdesk, assets, approvals and staff records in one place."),
    ("Approvals that move", "Requests route to the right desk and every step is recorded."),
    ("Know where everything is", "Every asset tagged, assigned and accounted for."),
    ("Built for the way we work", "Attendance, leave and tasks for every office."),
    ("Secure by default", "Every action is audited. Every file is protected."),
]


class Command(BaseCommand):
    help = "Generate placeholder showcase images (procedural gradients with the NBTE wordmark)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=5)
        parser.add_argument("--keep", action="store_true", help="Keep earlier placeholders.")
        parser.add_argument("--width", type=int, default=1920)
        parser.add_argument("--height", type=int, default=1080)

    def handle(self, *args, count, keep, width, height, **opts):
        from PIL import Image  # noqa: F401  (Pillow is already a project dependency)

        if not keep:
            old = ShowcaseImage.objects.filter(title__startswith="Placeholder")
            n = old.count()
            for obj in old:
                obj.delete()
            if n:
                self.stdout.write(f"Removed {n} earlier placeholder(s).")
        made = []
        for i in range(max(0, count)):
            data = self._render(i, width, height)
            heading, sub = CAPTIONS[i % len(CAPTIONS)]
            obj = ShowcaseImage(
                title=f"Placeholder {i + 1}: {heading}",
                caption=sub,
                alt_text=f"Decorative green background with the NBTE wordmark ({i + 1} of {count})",
                order=i,
                is_active=True,
            )
            codes = [ShowcaseImage.SIGNIN_BACKGROUND, ShowcaseImage.HERO]
            if i == 0:
                codes.append(ShowcaseImage.DASHBOARD_BANNER)
            obj.set_placements(codes)
            obj.image.save(f"placeholder-{i + 1}.jpg", ContentFile(data), save=False)
            obj.save()
            made.append(obj)
        self.stdout.write(self.style.SUCCESS(f"Created {len(made)} showcase image(s)."))

    # ------------------------------------------------------------------
    def _render(self, i, w, h):
        from PIL import Image, ImageDraw, ImageFilter, ImageFont

        rnd = random.Random(1000 + i)
        c0, c1, c2 = PALETTES[i % len(PALETTES)]
        # Diagonal three-stop gradient
        base = Image.new("RGB", (w, h))
        px = base.load()
        ang = math.radians(25 + 20 * i)
        dx, dy = math.cos(ang), math.sin(ang)
        span = abs(w * dx) + abs(h * dy)
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                t = max(0.0, min(1.0, (x * dx + y * dy) / span))
                if t < 0.55:
                    k = t / 0.55
                    col = tuple(int(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
                else:
                    k = (t - 0.55) / 0.45
                    col = tuple(int(c1[j] + (c2[j] - c1[j]) * k) for j in range(3))
                px[x, y] = col
                if x + 1 < w: px[x + 1, y] = col
                if y + 1 < h:
                    px[x, y + 1] = col
                    if x + 1 < w: px[x + 1, y + 1] = col
        # Soft light orbs
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        for _ in range(6):
            r = rnd.randint(h // 6, h // 2)
            cx, cy = rnd.randint(0, w), rnd.randint(0, h)
            g.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, rnd.randint(10, 26)))
        glow = glow.filter(ImageFilter.GaussianBlur(h // 10))
        img = Image.alpha_composite(base.convert("RGBA"), glow)
        # Fine grid lines (drawn on their own layer so alpha blends)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        lines = ImageDraw.Draw(layer)
        step = h // 8
        for x in range(0, w, step):
            lines.line([(x, 0), (x, h)], fill=(255, 255, 255, 8), width=1)
        for y in range(0, h, step):
            lines.line([(0, y), (w, y)], fill=(255, 255, 255, 8), width=1)
        img = Image.alpha_composite(img, layer)
        # Faded NBTE logo, bottom right
        logo_path = settings.BASE_DIR / "static" / "img" / "MBTE_logo.png"
        try:
            logo = Image.open(logo_path).convert("RGBA")
            size = int(h * 0.62)
            logo = logo.resize((size, size))
            alpha = logo.getchannel("A").point(lambda a: int(a * 0.10))
            logo.putalpha(alpha)
            img.alpha_composite(logo, (w - int(size * 0.8), h - int(size * 0.85)))
        except (OSError, ValueError):
            pass
        # Wordmark
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(text_layer)
        try:
            font = ImageFont.load_default(size=int(h * 0.075))
            small = ImageFont.load_default(size=int(h * 0.024))
        except TypeError:  # Pillow < 10.1
            font = small = ImageFont.load_default()
        # Bottom right, under the crest: clear of every page's navigation,
        # brand and headline (which sit top/left).
        tw = d.textlength("NBTE", font=font)
        sw = d.textlength("ICT DIRECTORATE", font=small)
        y = int(h * 0.80)
        d.text((int(w * 0.95 - tw), y), "NBTE", font=font, fill=(255, 255, 255, 40))
        d.text((int(w * 0.95 - sw), y + int(h * 0.09)), "ICT DIRECTORATE", font=small, fill=(255, 255, 255, 50))
        img = Image.alpha_composite(img, text_layer)
        out = io.BytesIO()
        img.convert("RGB").save(out, "JPEG", quality=84, optimize=True, progressive=True)
        return out.getvalue()
