"""Ported from EE Hub results/signature.py (image processing only).

Lift a signature from a photo of ink on paper (Pillow only).

1. Orientation from EXIF, greyscale, downscale.
2. Flatten the lighting: estimate the paper with a wide blur and take the
   difference (paper - pixel) = "darkness", so shadows and uneven light fade.
3. Otsu threshold on the darkness histogram, moved by the sensitivity slider.
4. Alpha ramps smoothly between a low and a high threshold (smooth edges),
   small specks are removed, ruled lines can be dropped.
5. Crop to the ink with a margin, resize, paint in a solid ink colour.
Result: a transparent PNG.
"""
import io

from PIL import Image, ImageChops, ImageFilter, ImageOps

INKS = {"navy": (20, 33, 74), "blue": (29, 78, 216), "black": (17, 17, 17)}
MAX_W, MAX_H = 1200, 480


class SignatureError(ValueError):
    pass


def _otsu(hist):
    total = sum(hist)
    if not total:
        return 0
    sum_all = sum(i * h for i, h in enumerate(hist))
    w_b = sum_b = 0
    best, threshold = -1.0, 0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, threshold = between, t
    return threshold


def _remove_lines(mask):
    """Blank rows/columns that are mostly ink (ruled lines, page edges)."""
    w, h = mask.size
    out = mask.copy()
    px = out.load()
    for y in range(h):
        row = mask.crop((0, y, w, y + 1)).histogram()
        if sum(row[128:]) > 0.55 * w:
            for x in range(w):
                px[x, y] = 0
    for x in range(w):
        col = mask.crop((x, 0, x + 1, h)).histogram()
        if sum(col[128:]) > 0.7 * h:
            for y in range(h):
                px[x, y] = 0
    return out


def extract(data, sensitivity=50, ink="navy", remove_lines=True, smooth=True):
    """bytes of a photo -> (PNG bytes, info dict)."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except Exception:
        raise SignatureError("That file is not a photo we can read. Use a JPG or PNG.")
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    grey = img.convert("L")
    grey.thumbnail((1600, 1600))
    w, h = grey.size
    if w < 60 or h < 30:
        raise SignatureError("The photo is too small. Take it closer to the signature.")
    paper = grey.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(max(12, min(w, h) // 18)))
    dark = ImageChops.subtract(paper, grey)              # 0 = paper, high = ink
    dark = ImageOps.autocontrast(dark, cutoff=0.5)
    t = _otsu(dark.histogram())
    s = max(0, min(100, int(sensitivity)))
    factor = 1.6 - 1.1 * s / 100                          # 0 -> strict (heavy marker), 100 -> faint pen
    t = max(8, min(250, int(t * factor)))
    lo, hi = max(1, int(t * 0.65)), min(255, max(int(t * 1.25), int(t * 0.65) + 4))
    lut = [0 if v <= lo else 255 if v >= hi else int(255 * (v - lo) / (hi - lo)) for v in range(256)]
    alpha = dark.point(lut)
    mask = alpha.point(lambda v: 255 if v >= 128 else 0)
    mask = mask.filter(ImageFilter.MedianFilter(3))       # drop specks
    if remove_lines:
        mask = _remove_lines(mask)
    keep = mask.filter(ImageFilter.MaxFilter(5))          # keep soft edges near real strokes only
    alpha = ImageChops.multiply(alpha, keep)
    if smooth:
        alpha = alpha.filter(ImageFilter.SMOOTH)
    box = alpha.point(lambda v: 255 if v > 60 else 0).getbbox()
    if not box:
        raise SignatureError("No ink was found. Sign with a dark pen on plain white paper and try again, or raise the sensitivity.")
    pad = max(8, int(0.04 * max(box[2] - box[0], box[3] - box[1])))
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad))
    alpha = alpha.crop(box)
    ink_px = sum(alpha.point(lambda v: 1 if v > 128 else 0).histogram()[1:])
    ratio = ink_px / float(alpha.size[0] * alpha.size[1])
    if ratio > 0.45:
        raise SignatureError("Too much of the photo looks like ink. Use plain paper, good light and lower the sensitivity.")
    scale = min(MAX_W / alpha.size[0], MAX_H / alpha.size[1], 2.0)
    size = (max(1, int(alpha.size[0] * scale)), max(1, int(alpha.size[1] * scale)))
    alpha = alpha.resize(size, Image.LANCZOS)
    out = Image.new("RGBA", size, INKS.get(ink, INKS["navy"]) + (0,))
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"width": size[0], "height": size[1], "ink_ratio": round(ratio, 4), "threshold": t}


def demo_photo(text_seed=7, size=(1400, 900)):
    """A synthetic 'photo' of a signature on paper with uneven light (tests and
    demo data; never a real person's signature)."""
    import math
    import random

    from PIL import ImageDraw
    rnd = random.Random(text_seed)
    w, h = size
    small = Image.new("L", (36, 24), 0)      # light falls off to one corner
    sp = small.load()
    for y in range(24):
        for x in range(36):
            sp[x, y] = int(200 + 40 * (1 - (x / 35) * 0.6 - (y / 23) * 0.4))
    img = small.resize(size, Image.BILINEAR).convert("RGB")
    d = ImageDraw.Draw(img)
    for y in range(140, h, 70):     # faint ruled lines
        d.line([(0, y), (w, y)], fill=(170, 180, 200), width=2)
    pts = []
    for i in range(240):
        t = i / 239
        x = 220 + t * 880 + 38 * math.cos(t * math.pi * 12)
        y = 430 + 75 * math.sin(t * math.pi * 12) * (1 - t * 0.55) - 50 * math.cos(t * math.pi * 2) + rnd.random() * 2
        pts.append((x, y))
    d.line(pts, fill=(30, 40, 90), width=7, joint="curve")
    d.line([(240, 560), (1150, 520)], fill=(30, 40, 90), width=5)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()




def from_drawing(data, ink="navy"):
    """PNG (or a data: URL) from the signature pad -> cleaned transparent PNG.

    The pad draws dark strokes on a transparent (or white) canvas. Keep the
    strokes, crop to them with a margin and repaint them in the ink colour."""
    import base64
    if isinstance(data, str):
        if "," in data and data.strip().startswith("data:"):
            data = data.split(",", 1)[1]
        try:
            data = base64.b64decode(data)
        except Exception:
            raise SignatureError("The drawing could not be read. Try again.")
    if not data or len(data) > 4 * 1024 * 1024:
        raise SignatureError("The drawing could not be read. Try again.")
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        raise SignatureError("The drawing could not be read. Try again.")
    r, g, b, a = img.split()
    lum = Image.merge("RGB", (r, g, b)).convert("L")
    darkness = ImageOps.invert(lum)                      # dark ink -> high
    alpha = ImageChops.multiply(a, darkness.point(lambda v: min(255, v * 2)))
    box = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        raise SignatureError("The pad is empty. Sign inside the box first.")
    w, h = alpha.size
    if (box[2] - box[0]) < 24 or (box[3] - box[1]) < 8:
        raise SignatureError("That is too small to be a signature. Sign larger, across the box.")
    pad = max(8, int(0.04 * max(box[2] - box[0], box[3] - box[1])))
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(w, box[2] + pad), min(h, box[3] + pad))
    alpha = alpha.crop(box)
    scale = min(MAX_W / alpha.size[0], MAX_H / alpha.size[1], 2.0)
    size = (max(1, int(alpha.size[0] * scale)), max(1, int(alpha.size[1] * scale)))
    alpha = alpha.resize(size, Image.LANCZOS)
    out = Image.new("RGBA", size, INKS.get(ink, INKS["navy"]) + (0,))
    out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    ink_px = sum(alpha.point(lambda v: 1 if v > 128 else 0).histogram()[1:])
    return buf.getvalue(), {"width": size[0], "height": size[1], "ink_ratio": round(ink_px / float(size[0] * size[1]), 4)}


def _catmull(points, n=14):
    out = []
    pts = [points[0]] + list(points) + [points[-1]]
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in (0, 1)))
    out.append(points[-1])
    return out


def demo_signature_png(seed="demo", ink="navy", width=900, height=300):
    """A fictional, generated handwriting-like signature (demo data only;
    never a real person's signature): a slanted capital loop, a run of
    cursive humps of varying height, a closing flourish that sweeps back
    into an underline. Pen pressure varies along the stroke."""
    import math
    import random

    from PIL import ImageDraw
    rnd = random.Random(str(seed))
    k = 3
    W, H = width * k, height * k
    base = H * 0.62
    slant = rnd.uniform(0.25, 0.42)
    ctrl = []
    # capital: up a tall stem, over a loop, back down through the base
    x0 = W * 0.08
    cap = H * rnd.uniform(0.40, 0.52)
    ctrl += [(x0, base + H * 0.04), (x0 + W * 0.02, base - cap * 0.55), (x0 + W * 0.05, base - cap),
             (x0 + W * 0.10, base - cap * 0.82), (x0 + W * 0.07, base - cap * 0.45), (x0 + W * 0.02, base - cap * 0.30),
             (x0 + W * 0.09, base - cap * 0.25), (x0 + W * 0.12, base)]
    ctrl = [(px + rnd.uniform(-0.012, 0.012) * W, py + rnd.uniform(-0.05, 0.05) * H) for px, py in ctrl]
    x = x0 + W * 0.13
    n = rnd.randint(5, 8)
    for i in range(n):
        tall = rnd.random() < 0.25
        h = H * (rnd.uniform(0.28, 0.40) if tall else rnd.uniform(0.08, 0.16))
        w = W * rnd.uniform(0.035, 0.055)
        ctrl += [(x + w * 0.45, base - h), (x + w * (0.6 if tall else 0.8), base - h * 0.15), (x + w, base + H * 0.01)]
        x += w
        if rnd.random() < 0.18:     # a descender
            ctrl += [(x + w * 0.2, base + H * 0.18), (x + w * 0.05, base + H * 0.06)]
    # flourish: rise, then sweep back left under the name
    ctrl += [(x + W * 0.05, base - H * 0.10), (x + W * 0.08, base - H * 0.02), (x + W * 0.02, base + H * 0.14),
             (x0 + W * 0.25, base + H * 0.20), (x0 + W * 0.05, base + H * 0.17)]
    path = _catmull(ctrl, 16)
    path = [(px + (base - py) * slant, py) for px, py in path]
    path = [(px + rnd.uniform(-0.3, 0.3) * k, py + rnd.uniform(-0.3, 0.3) * k) for px, py in path]
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    total = len(path)
    for i in range(total - 1):
        t = i / total
        pressure = 1.6 + 1.4 * math.sin(t * math.pi) + 0.6 * math.sin(i / 23.0)
        if t > 0.86:
            pressure *= max(0.35, (1 - t) / 0.14)   # lift off at the end
        wpen = max(1.6, pressure) * k
        d.line([path[i], path[i + 1]], fill=255, width=int(wpen))
        r = wpen / 2
        d.ellipse((path[i][0] - r, path[i][1] - r, path[i][0] + r, path[i][1] + r), fill=255)
    if rnd.random() < 0.7:      # a dot (as over an i)
        px, py = path[len(path) // 2]
        d.ellipse((px + 8 * k, base - H * 0.30, px + 13 * k, base - H * 0.30 + 5 * k), fill=255)
    img = img.filter(ImageFilter.GaussianBlur(k * 0.45)).resize((width, height), Image.LANCZOS)
    box = img.point(lambda v: 255 if v > 30 else 0).getbbox() or (0, 0, width, height)
    img = img.crop((max(0, box[0] - 10), max(0, box[1] - 10), min(width, box[2] + 10), min(height, box[3] + 10)))
    out = Image.new("RGBA", img.size, INKS.get(ink, INKS["navy"]) + (0,))
    out.putalpha(img)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"width": img.size[0], "height": img.size[1], "ink_ratio": 0.05}
