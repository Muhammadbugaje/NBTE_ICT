"""Build the How-to page diagrams (Phase 6) as theme-aware inline SVG.

    python scripts/build_howto_diagrams.py

Writes templates/qa_portal/public/diagrams/*.svg.html. The SVGs use the
qp-d-* classes from static/css/qa-public.css, so they follow light and dark
mode and the NBTE accent. Stage names come from the flow definitions (the
"qa_case", "onboarding", "qa_request" and "payment" flows); keep them in step
when a flow changes. ``--png DIR`` also writes light-mode PNG copies (for the
Word guides) when cairosvg is installed.
"""
from __future__ import annotations

import argparse
import html
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "templates", "qa_portal", "public", "diagrams")

FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif"


def esc(text):
    return html.escape(str(text), quote=True)


def text_lines(x, y, lines, cls, *, anchor="middle", gap=15):
    out = []
    for i, line in enumerate(lines):
        out.append(f'<text x="{x}" y="{y + i * gap}" class="{cls}" text-anchor="{anchor}">{esc(line)}</text>')
    return "".join(out)


def wrap(label, width_chars):
    words, lines, cur = label.split(), [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) > width_chars and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def arrow_marker(mid, cls):
    return (f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="{cls}"/></marker>')


def svg(width, height, title, desc, body, uid):
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="{uid}-t {uid}-d" '
            f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}">'
            f'<title id="{uid}-t">{esc(title)}</title><desc id="{uid}-d">{esc(desc)}</desc>'
            f'<defs>{arrow_marker(uid + "-a", "qp-d-arrow")}{arrow_marker(uid + "-ay", "qp-d-arrow--you")}</defs>'
            f'{body}</svg>')


# ---------------------------------------------------------------------------
# Two-lane flow: "You" (the institution) above, "NBTE" below.
# ---------------------------------------------------------------------------
def lane_flow(uid, title, desc, steps, *, width=960):
    """steps: [(lane, label, sub, kind)] lane 'you'|'nbte'; kind ''|'end'|'optional'."""
    n = len(steps)
    lane_x = 64
    gap = 14
    box_w = (width - lane_x - 8 - gap * (n - 1)) / n
    box_h = 84
    you_y, nbte_y = 34, 176
    height = nbte_y + box_h + 22
    parts = [
        f'<rect x="0" y="{you_y - 14}" width="{width}" height="{box_h + 28}" rx="16" class="qp-d-box--soft"/>',
        f'<text x="14" y="{you_y + box_h / 2 + 4}" class="qp-d-lane">You</text>',
        f'<text x="14" y="{nbte_y + box_h / 2 + 4}" class="qp-d-lane">NBTE</text>',
    ]
    centers = []
    for i, (lane, label, sub, kind) in enumerate(steps):
        x = lane_x + i * (box_w + gap)
        y = you_y if lane == "you" else nbte_y
        cls = "qp-d-box--you" if lane == "you" else "qp-d-box"
        if kind == "end":
            cls = "qp-d-box--end"
        dash = ' stroke-dasharray="4 4"' if kind == "optional" else ""
        parts.append(f'<rect x="{x:.1f}" y="{y}" width="{box_w:.1f}" height="{box_h}" rx="12" class="{cls}"{dash}/>')
        chars = max(8, int((box_w - 14) / 7.9))
        t_lines = wrap(label, chars)
        s_lines = wrap(sub, int((box_w - 12) / 6.7)) if sub else []
        total = len(t_lines) * 15 + (len(s_lines) * 13 + 3 if s_lines else 0)
        ty = y + box_h / 2 - total / 2 + 11
        tcls, scls = ("qp-d-t qp-d-t--inv", "qp-d-s qp-d-s--inv") if kind == "end" else ("qp-d-t", "qp-d-s")
        parts.append(text_lines(x + box_w / 2, ty, t_lines, tcls))
        if s_lines:
            parts.append(text_lines(x + box_w / 2, ty + len(t_lines) * 15 + 2, s_lines, scls, gap=13))
        parts.append(f'<text x="{x + 8:.1f}" y="{y - 5}" class="qp-d-n" text-anchor="start">{i + 1}</text>')
        centers.append((x, y, lane))
    for i in range(n - 1):
        x1, y1, lane1 = centers[i]
        x2, y2, lane2 = centers[i + 1]
        sx, sy = x1 + box_w, y1 + box_h / 2
        ex, ey = x2, y2 + box_h / 2
        you = lane2 == "you"
        cls = "qp-d-line qp-d-line--you" if you else "qp-d-line"
        mid = uid + ("-ay" if you else "-a")
        if lane1 == lane2:
            d = f"M{sx:.1f},{sy:.1f} L{ex - 2:.1f},{ey:.1f}"
        else:
            cx = sx + gap / 2
            d = f"M{sx:.1f},{sy:.1f} C{cx + 18:.1f},{sy:.1f} {cx - 18:.1f},{ey:.1f} {ex - 2:.1f},{ey:.1f}"
        parts.append(f'<path d="{d}" class="{cls}" marker-end="url(#{mid})"/>')
    return svg(width, height, title, desc, "".join(parts), uid)


ONBOARDING = [
    ("you", "Submit your application", "Record and documents", ""),
    ("nbte", "Departmental clearance", "Legal, Finance, Planning", ""),
    ("you", "Pay the institutional fee", "Online or by bank", ""),
    ("nbte", "Finance confirms", "Receipt issued", ""),
    ("you", "Choose programmes", "ND, HND, NID, NVC", ""),
    ("nbte", "Programme clearance", "NTIRD checks each", ""),
    ("you", "Pay, then complete the SSQ", "Programme fee", ""),
    ("nbte", "Resource inspection", "QA begins", "end"),
]
QA_REQUEST = [
    ("you", "Start a QA request", "Type and programmes", ""),
    ("nbte", "Programme clearance", "New programmes only", "optional"),
    ("you", "Pay the QA fee", "Amount on your bill", ""),
    ("nbte", "Finance confirms", "SSQs open", ""),
    ("you", "Complete the SSQs", "One per programme", ""),
    ("nbte", "SSQ review", "Accepted or returned", ""),
    ("nbte", "Visit and report", "17-stage case", ""),
    ("nbte", "Decision", "Record updates", "end"),
]
PAYMENT = [
    ("nbte", "Bill issued", "Reference and amount", ""),
    ("you", "Pay online or by bank", "Remita, Paystack, transfer", ""),
    ("you", "Upload proof", "Bank transfers only", "optional"),
    ("nbte", "Finance confirms", "Checks the bank record", ""),
    ("nbte", "Receipt issued", "Next step opens", "end"),
]


# ---------------------------------------------------------------------------
# SOP-03: the 17 internal stages in five phases (columns).
# ---------------------------------------------------------------------------
SOP03 = [
    ("Assign the team", [(1, "Supervising Audit, Finance and ICT officers", False)]),
    ("Cost the visit", [(2, "Generate the cost sheet", False), (3, "Add the personnel list", False),
                        (4, "Confirm the personnel list", False), (5, "Audit the personnel cost", False),
                        (6, "Director of Audit confirms", False), (7, "Executive Secretary approves the cost", False)]),
    ("Prepare the visit", [(8, "Add the visit details", False), (9, "Confirm the visit details", False),
                           (10, "Executive Secretary approves the visit", True), (11, "Funds released", False)]),
    ("Visit and report", [(12, "Visit and post-visit report", True), (13, "Recommendation per programme", False)]),
    ("Decide", [(14, "CQAC recommendation", False), (15, "CQAC Chairman approves", False),
                (16, "Executive Secretary gives final approval", True), (17, "Your programme record updates", True)]),
]


def sop03(uid="sop03", width=960):
    cols = len(SOP03)
    gap = 14
    col_w = (width - gap * (cols - 1)) / cols
    row_h = 60
    head_h = 46
    max_rows = max(len(items) for _t, items in SOP03)
    col_h = head_h + max_rows * row_h + 12
    legend_y = col_h + 30
    height = legend_y + 16
    parts = []
    for c, (title, items) in enumerate(SOP03):
        x = c * (col_w + gap)
        parts.append(f'<rect x="{x:.1f}" y="0" width="{col_w:.1f}" height="{col_h}" rx="14" class="qp-d-box--soft"/>')
        parts.append(f'<text x="{x + 14:.1f}" y="22" class="qp-d-t" text-anchor="start">{esc(title)}</text>')
        first, last = items[0][0], items[-1][0]
        rng = f"Stage {first}" if first == last else f"Stages {first}–{last}"
        parts.append(f'<text x="{x + 14:.1f}" y="37" class="qp-d-s" text-anchor="start">{rng}</text>')
        for r, (num, label, visible) in enumerate(items):
            y = head_h + r * row_h
            cls = "qp-d-box--you" if visible else "qp-d-box"
            parts.append(f'<rect x="{x + 8:.1f}" y="{y}" width="{col_w - 16:.1f}" height="{row_h - 8}" rx="10" class="{cls}"/>')
            parts.append(f'<text x="{x + 20:.1f}" y="{y + (row_h - 8) / 2 + 4:.1f}" class="qp-d-n" text-anchor="start">{num}</text>')
            lines = wrap(label, int((col_w - 52) / 6.9))
            ty = y + (row_h - 8) / 2 + 4 - (len(lines) - 1) * 6.5
            parts.append(text_lines(x + 40, ty, lines, "qp-d-s", anchor="start", gap=13))
        if c < cols - 1:
            ax = x + col_w + 1
            parts.append(f'<path d="M{ax:.1f},23 L{ax + gap - 3:.1f},23" class="qp-d-line" marker-end="url(#{uid}-a)"/>')
    parts.append(f'<rect x="0" y="{legend_y - 11}" width="14" height="14" rx="4" class="qp-d-box--you"/>')
    parts.append(f'<text x="22" y="{legend_y}" class="qp-d-key">You see this stage on your timeline and get an e-mail.</text>')
    return svg(width, height, "The 17 stages inside NBTE after your SSQ is accepted (SOP-03)",
               "Five phases: assign the team (stage 1), cost the visit (stages 2 to 7), prepare the visit "
               "(8 to 11), visit and report (12 and 13), decide (14 to 17). The institution is told at stages "
               "10, 12, 16 and 17.", "".join(parts), uid)


DIAGRAMS = {
    "onboarding": lambda: lane_flow(
        "onb", "Onboarding a new institution (SOP-01)",
        "Eight steps alternating between the institution and NBTE: submit the application, departmental "
        "clearance, pay the institutional fee, Finance confirms, choose programmes, programme clearance, pay "
        "the programme fee and complete the SSQ, then resource inspection begins.", ONBOARDING),
    "qa_request": lambda: lane_flow(
        "qar", "A QA request from start to decision (SOP-02)",
        "Start a request, programme clearance for new programmes only, pay the QA fee, Finance confirms, "
        "complete one SSQ per programme, NBTE reviews each SSQ, the visit and recommendation, then the "
        "decision updates the programme record.", QA_REQUEST),
    "sop03": sop03,
    "payment": lambda: lane_flow(
        "pay", "How a payment is confirmed",
        "NBTE issues a bill; you pay online or by bank; bank transfers need an uploaded proof; NBTE Finance "
        "confirms the payment; a receipt is issued and the next step opens.", PAYMENT, width=760),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--png", metavar="DIR", help="also write light-mode PNGs (needs cairosvg)")
    args = parser.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    for name, build in DIAGRAMS.items():
        markup = build()
        with open(os.path.join(OUT, f"{name}.svg.html"), "w", encoding="utf-8") as fh:
            fh.write("{# Generated by scripts/build_howto_diagrams.py — edit the script, not this file. #}\n")
            fh.write(markup + "\n")
        print(f"wrote {name}.svg.html")
        if args.png:
            write_png(name, markup, args.png)
    return 0


LIGHT_CSS = """
.qp-d-box{fill:#fff;stroke:#d2d2d7;stroke-width:1.2}.qp-d-box--soft{fill:#f5f5f7}
.qp-d-box--you{fill:#eaf2ed;stroke:#1f6b3e;stroke-width:1.4}.qp-d-box--end{fill:#1f6b3e}
.qp-d-t{fill:#1d1d1f;font-weight:600;font-size:13px}.qp-d-s{fill:#6e6e73;font-size:11.5px}
.qp-d-n{fill:#86868b;font-weight:600;font-size:11px}.qp-d-t--inv,.qp-d-s--inv{fill:#fff}
.qp-d-lane{fill:#86868b;font-weight:600;font-size:11px}.qp-d-line{fill:none;stroke:#c7c7cc;stroke-width:1.5}
.qp-d-line--you{stroke:#1f6b3e}.qp-d-arrow{fill:#c7c7cc}.qp-d-arrow--you{fill:#1f6b3e}
.qp-d-key{font-size:12px;fill:#6e6e73}
"""


def light_svg(markup):
    """The SVG with the light-mode colours inlined (for PNG export)."""
    return markup.replace("<defs>", f"<defs><style>{LIGHT_CSS}</style>", 1).replace(
        "<svg ", '<svg style="background:#ffffff" ', 1)


def write_png(name, markup, folder):
    try:
        import cairosvg
    except ImportError:
        print("cairosvg is not installed: PNGs skipped", file=sys.stderr)
        return
    os.makedirs(folder, exist_ok=True)
    cairosvg.svg2png(bytestring=light_svg(markup).encode("utf-8"), write_to=os.path.join(folder, f"{name}.png"),
                     output_width=1920, background_color="white")
    print(f"wrote {name}.png")


if __name__ == "__main__":
    sys.exit(main())
