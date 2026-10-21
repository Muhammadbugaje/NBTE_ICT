"""Build the dotted Nigeria map used by the public hero sections.

    python scripts/build_nigeria_map.py

Writes templates/partials/vivid/nigeria_map.svg.html: a dot-matrix outline of
Nigeria (simplified border, the Niger and the Benue), NBTE headquarters in
Kaduna, and arcs to a selection of polytechnics and colleges across the
country. The arcs and pulses are animated in CSS (static/css/vivid.css) and
stand still for people who prefer reduced motion. The outline is simplified
for illustration; it is not a survey map.
"""
from __future__ import annotations

import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "templates", "partials", "vivid", "nigeria_map.svg.html")

# Simplified border, clockwise from the Benin border on the coast (lon, lat).
BORDER = [
    (2.69, 6.37), (2.72, 6.90), (2.76, 7.60), (2.70, 8.10), (2.76, 8.70), (2.82, 9.10), (3.10, 9.45),
    (3.40, 10.00), (3.60, 10.40), (3.66, 11.00), (3.60, 11.70), (3.65, 12.50), (4.10, 13.10), (4.22, 13.50),
    (4.90, 13.76), (5.50, 13.86), (6.20, 13.66), (6.70, 13.35), (7.30, 13.10), (7.80, 13.32), (8.50, 13.06),
    (9.00, 12.84), (9.60, 12.80), (10.10, 13.16), (10.70, 13.36), (11.40, 13.36), (12.20, 13.10),
    (12.50, 13.06), (13.10, 13.54), (13.62, 13.72), (14.08, 13.08), (14.20, 12.40), (14.50, 12.00),
    (14.62, 11.50), (14.20, 11.20), (13.70, 10.70), (13.30, 10.10), (13.20, 9.60), (12.90, 9.40),
    (12.80, 8.70), (12.40, 8.40), (12.20, 8.00), (11.85, 7.10), (11.60, 6.85), (11.10, 6.50), (10.60, 7.00),
    (10.20, 6.90), (9.70, 6.60), (9.30, 6.30), (8.90, 5.80), (8.80, 5.30), (8.50, 4.70), (8.25, 4.55),
    (7.50, 4.45), (7.00, 4.40), (6.50, 4.30), (6.00, 4.27), (5.60, 4.70), (5.30, 5.20), (5.00, 5.70),
    (4.60, 6.10), (4.30, 6.30), (3.80, 6.40), (3.40, 6.42),
]
NIGER = [(3.60, 11.70), (4.10, 11.00), (4.60, 10.30), (5.00, 9.60), (5.80, 9.20), (6.50, 8.60), (6.74, 7.80),
         (6.76, 7.00), (6.60, 6.20), (6.30, 5.40), (6.10, 4.60)]
BENUE = [(12.90, 9.30), (12.00, 9.45), (11.20, 9.05), (10.40, 8.60), (9.60, 8.20), (8.54, 7.73), (7.60, 7.76),
         (6.74, 7.80)]

HQ = ("NBTE, Kaduna", 7.44, 10.52)
# A spread of polytechnics and colleges (approximate positions).
PLACES = [
    ("Lagos", 3.38, 6.52), ("Ilaro", 3.01, 6.89), ("Abeokuta", 3.35, 7.15), ("Ibadan", 3.90, 7.40),
    ("Offa", 4.72, 8.15), ("Ado-Ekiti", 5.22, 7.62), ("Auchi", 6.27, 7.07), ("Idah", 6.73, 7.11),
    ("Nekede", 7.04, 5.48), ("Unwana", 7.90, 5.83), ("Bori", 7.36, 4.67), ("Calabar", 8.33, 4.96),
    ("Bida", 6.01, 9.08), ("Abuja", 7.49, 9.06), ("Nasarawa", 7.71, 8.54), ("Jos", 8.89, 9.89),
    ("Bali", 10.97, 7.85), ("Bauchi", 9.84, 10.31), ("Kaltungo", 11.30, 9.80), ("Mubi", 13.26, 10.27),
    ("Maiduguri", 13.15, 11.85), ("Damaturu", 11.96, 11.75), ("Kano", 8.52, 12.00), ("Katsina", 7.60, 12.99),
    ("Sokoto", 5.24, 13.06), ("Birnin Kebbi", 4.20, 12.45),
]

LON0, LAT1 = 2.4, 14.1      # top-left of the frame
SCALE = 46.0                # px per degree
W = round((14.95 - LON0) * SCALE)
H = round((LAT1 - 3.95) * SCALE)


def xy(lon, lat):
    return round((lon - LON0) * SCALE, 1), round((LAT1 - lat) * SCALE, 1)


def path(points, close=False):
    pts = [xy(*p) for p in points]
    d = "M" + " L".join(f"{x} {y}" for x, y in pts)
    return d + (" Z" if close else "")


def smooth(points):
    """Catmull-Rom through the points, as cubic Béziers (for the rivers)."""
    pts = [xy(*p) for p in points]
    d = [f"M{pts[0][0]} {pts[0][1]}"]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]} {p2[1]}")
    return " ".join(d)


def inside(lon, lat):
    hit = False
    n = len(BORDER)
    for i in range(n):
        x1, y1 = BORDER[i]
        x2, y2 = BORDER[(i + 1) % n]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def dots(step=0.24):
    out = []
    lat = 13.9
    row = 0
    while lat > 4.2:
        lon = 2.7 + (step / 2 if row % 2 else 0)
        while lon < 14.7:
            if inside(lon, lat):
                x, y = xy(lon, lat)
                out.append(f'<circle cx="{x}" cy="{y}" r="1.9"/>')
            lon += step
        lat -= step * 0.88
        row += 1
    return out


def arc(a, b, bend=0.18):
    (x1, y1), (x2, y2) = a, b
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    nx, ny = -dy / (length or 1), dx / (length or 1)
    cx, cy = mx + nx * length * bend, my + ny * length * bend - length * 0.12
    return f"M{x1} {y1} Q{cx:.1f} {cy:.1f} {x2} {y2}", length


def build():
    hq = xy(HQ[1], HQ[2])
    arcs, nodes = [], []
    for i, (name, lon, lat) in enumerate(PLACES):
        p = xy(lon, lat)
        d, length = arc(hq, p, bend=0.16 if i % 2 else -0.16)
        delay = f"{(i * 0.37) % 6.5:.2f}s"
        arcs.append(f'<path class="nm-arc" d="{d}" pathLength="1" style="--d:{delay}"/>')
        nodes.append(f'<g class="nm-node" style="--d:{delay}"><circle class="nm-ping" cx="{p[0]}" cy="{p[1]}" r="4"/>'
                     f'<circle class="nm-dot" cx="{p[0]}" cy="{p[1]}" r="3.2"><title>{name}</title></circle></g>')
    svg = f'''{{% comment %}}Generated by scripts/build_nigeria_map.py — do not edit by hand.{{% endcomment %}}
<svg class="nm" viewBox="0 0 {W} {H}" role="img" aria-labelledby="nm-title nm-desc" focusable="false">
  <title id="nm-title">NBTE and the institutions it serves</title>
  <desc id="nm-desc">A map of Nigeria with NBTE headquarters in Kaduna linked to polytechnics and colleges in every region.</desc>
  <defs>
    <clipPath id="nm-clip"><path d="{path(BORDER, close=True)}"/></clipPath>
    <radialGradient id="nm-hq-glow"><stop offset="0" stop-color="var(--nm-hq, #f5c04a)" stop-opacity=".55"/><stop offset="1" stop-color="var(--nm-hq, #f5c04a)" stop-opacity="0"/></radialGradient>
  </defs>
  <path class="nm-land" d="{path(BORDER, close=True)}"/>
  <g class="nm-dots">{"".join(dots())}</g>
  <g clip-path="url(#nm-clip)"><path class="nm-river" d="{smooth(NIGER)}"/><path class="nm-river" d="{smooth(BENUE)}"/></g>
  <path class="nm-edge" d="{path(BORDER, close=True)}"/>
  <g class="nm-arcs">{"".join(arcs)}</g>
  <g class="nm-nodes">{"".join(nodes)}</g>
  <g class="nm-hq"><circle cx="{hq[0]}" cy="{hq[1]}" r="34" fill="url(#nm-hq-glow)"/>
    <circle class="nm-hq-ring" cx="{hq[0]}" cy="{hq[1]}" r="11"/>
    <circle class="nm-hq-core" cx="{hq[0]}" cy="{hq[1]}" r="6.5"><title>{HQ[0]}</title></circle></g>
  <g class="nm-label" transform="translate({hq[0] + 16} {hq[1] - 14})"><rect x="0" y="-15" width="116" height="26" rx="13"/>
    <text x="12" y="3">NBTE · Kaduna</text></g>
</svg>
'''
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return OUT


if __name__ == "__main__":
    print(build(), f"{W}x{H}")
