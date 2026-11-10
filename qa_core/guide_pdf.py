"""The institution guides as PDF (replaces the Word download on the portal).

    from qa_core.guide_pdf import guide_pdf_path
    path = guide_pdf_path("starter")      # builds (or reuses) the cached PDF

Built with ReportLab from the same block data as the Word guides and the
"Read online" page (qa_core.guides_content), so the three never drift apart.
Layout: a full-bleed cover in the guide's accent colour, an "About this guide"
page with the contents, numbered parts, step cards, framed screenshots with
their numbered call-outs, tip / important / note boxes, tables, and a running
header with "Page N of M".

The PDF is cached under MEDIA_ROOT/qa/guides/ and rebuilt when the guide
content (or this layout code) changes, or once a day so the live document
rules stay current.
"""
from __future__ import annotations

import io
import os
import re
import time
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone

GREEN = "#1F6B3E"
INK = "#1D1D1F"
BODY = "#3A3A3C"
MUTED = "#6E6E73"
LINE = "#E5E5EA"
SOFT = "#F5F5F7"

MAX_AGE = 24 * 3600
BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`([^`]+)`")

BOX_COLOURS = {"tip": ("#1F9D5C", "#EEF8F2"), "important": ("#C98A0B", "#FDF6E7"), "note": ("#2F6FD6", "#EEF3FC")}


def _images_dirs():
    base = settings.BASE_DIR
    return [os.path.join(base, "docs", "guides", "images"), os.path.join(base, "static", "guides")]


def _image_path(name):
    for folder in _images_dirs():
        for ext in ("png", "jpg"):
            path = os.path.join(folder, f"{name}.{ext}")
            if os.path.exists(path):
                return path
    return None


_IMG_CACHE = {}


def _image_data(path, max_px=1500):
    """(BytesIO of a downscaled JPEG, width, height) — big PNG screenshots
    would otherwise make the PDF slow to build and several MB to download."""
    key = (path, os.path.getmtime(path))
    hit = _IMG_CACHE.get(key)
    if hit is None:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            im = im.convert("RGBA")
            bg = PILImage.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            bg.thumbnail((max_px, max_px * 3))
            out = io.BytesIO()
            bg.save(out, "JPEG", quality=84, optimize=True, progressive=False)
            hit = (out.getvalue(), bg.size[0], bg.size[1])
        _IMG_CACHE[key] = hit
    return io.BytesIO(hit[0]), hit[1], hit[2]


def guide_pdf_path(slug):
    from .portal_public import GUIDES_SUBDIR

    return os.path.join(settings.MEDIA_ROOT, GUIDES_SUBDIR, f"nbte-qa-{slug}-guide.pdf")


def _stale(path):
    if not os.path.exists(path):
        return True
    built = os.path.getmtime(path)
    here = os.path.dirname(os.path.abspath(__file__))
    sources = [os.path.join(here, "guides_content.py"), os.path.abspath(__file__)]
    if any(os.path.exists(s) and os.path.getmtime(s) > built for s in sources):
        return True
    return time.time() - built > MAX_AGE


def ensure_pdf(slug):
    """Path of the guide's PDF, building it first when missing or stale."""
    path = guide_pdf_path(slug)
    if _stale(path):
        data = build_pdf(slug)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------- builder
def _inline(text, mono):
    out = escape(text or "")
    out = BOLD.sub(r"<b>\1</b>", out)
    return CODE.sub(rf'<font name="{mono}">\1</font>', out)


def build_pdf(slug):
    """The guide as PDF bytes. ASCII85 image encoding is switched off while
    building: ReportLab's pure-Python encoder (no rl_accel) takes seconds per
    screenshot, and binary streams are smaller anyway."""
    from reportlab import rl_config

    old = rl_config.useA85
    rl_config.useA85 = 0
    try:
        return _build_pdf(slug)
    finally:
        rl_config.useA85 = old


def _build_pdf(slug):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, NextPageTemplate,
                                    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
    from reportlab.platypus.tableofcontents import TableOfContents

    from .guide_render import doc_requirement_rows
    from .guides_content import GUIDES
    from .pdf_report import _fonts, _logo
    from .portal_public import contact_details

    g = GUIDES[slug]
    f = _fonts()
    reg, bold = f["regular"], f["bold"]
    mono = f.get("mono") or bold
    try:  # so <b> inside a Paragraph picks the bold face
        from reportlab.lib.fonts import addMapping

        addMapping(reg, 0, 0, reg)
        addMapping(reg, 1, 0, bold)
        addMapping(reg, 0, 1, reg)
        addMapping(reg, 1, 1, bold)
    except Exception:
        pass
    accent = colors.HexColor("#" + (g.get("accent") or "1F9D5C").lstrip("#"))
    W, H = A4
    M = 52
    width = W - 2 * M

    def S(name, **kw):
        base = dict(fontName=reg, fontSize=10.2, leading=15, textColor=colors.HexColor(BODY), alignment=TA_LEFT)
        base.update(kw)
        return ParagraphStyle(name, **base)

    st = {
        "body": S("body", spaceAfter=7),
        "lead": S("lead", fontSize=11.5, leading=16.5, textColor=colors.HexColor(MUTED), spaceAfter=10),
        "h_part": S("h_part", fontName=bold, fontSize=17, leading=21, textColor=colors.HexColor(INK)),
        "h2": S("h2", fontName=bold, fontSize=13, leading=17, textColor=colors.HexColor(INK), spaceBefore=10,
                spaceAfter=5),
        "h3": S("h3", fontName=bold, fontSize=11, leading=15, textColor=colors.HexColor(INK), spaceBefore=8,
                spaceAfter=4),
        "step_t": S("step_t", fontName=bold, fontSize=10.4, leading=14, textColor=colors.HexColor(INK)),
        "step_d": S("step_d", fontSize=9.6, leading=13.5, textColor=colors.HexColor(MUTED)),
        "num": S("num", fontName=bold, fontSize=10, leading=12, textColor=colors.white, alignment=1),
        "cell": S("cell", fontSize=9, leading=12.5),
        "head": S("head", fontName=bold, fontSize=8.4, leading=11, textColor=colors.white),
        "cap": S("cap", fontSize=8.6, leading=12, textColor=colors.HexColor(MUTED), spaceBefore=4, spaceAfter=8),
        "box_t": S("box_t", fontName=bold, fontSize=10.2, leading=14),
        "box_b": S("box_b", fontSize=9.6, leading=14),
        "toc1": S("toc1", fontSize=11, leading=20, textColor=colors.HexColor(INK)),
        "eyebrow": S("eyebrow", fontName=bold, fontSize=8.6, leading=11, textColor=accent, spaceAfter=6),
        "about_h": S("about_h", fontName=bold, fontSize=20, leading=25, textColor=colors.HexColor(INK),
                     spaceAfter=10),
    }
    I = lambda t: _inline(t, mono)  # noqa: E731

    story = []
    part_no = [0]

    def para(text, style="body"):
        story.append(Paragraph(I(text), st[style]))

    def part(title, lead=""):
        part_no[0] += 1
        n = part_no[0]
        badge = Table([[Paragraph(str(n), st["num"])]], colWidths=[26], rowHeights=[26])
        badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                   ("ROUNDEDCORNERS", [7, 7, 7, 7]), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        title_p = Paragraph(escape(title), st["h_part"])
        head = Table([[badge, title_p]], colWidths=[36, width - 36])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        head._gd_toc = (f"{n}.  {title}", f"part{n}")
        block = [Spacer(1, 8), head, Spacer(1, 6)]
        if lead:
            block.append(Paragraph(I(lead), st["lead"]))
        story.append(CondPageBreak(170))
        story.append(KeepTogether(block))

    def steps(items):
        rows = []
        for i, s in enumerate(items, 1):
            title, _, detail = s.partition("|")
            num = Table([[Paragraph(str(i), st["num"])]], colWidths=[20], rowHeights=[20])
            num.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent), ("ROUNDEDCORNERS", [10, 10, 10, 10]),
                                     ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
            text = [Paragraph(I(title.strip()), st["step_t"])]
            if detail.strip():
                text.append(Paragraph(I(detail.strip()), st["step_d"]))
            rows.append([num, text])
        t = Table(rows, colWidths=[34, width - 34])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(SOFT)),
            ("LINEBELOW", (0, 0), (-1, -2), 0.6, colors.white), ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ]))
        story.extend([Spacer(1, 2), t, Spacer(1, 10)])

    def bullets(items, tick=False):
        from reportlab.platypus import ListFlowable, ListItem

        lst = ListFlowable([ListItem(Paragraph(I(b), st["body"]), leftIndent=14,
                                     value="✓" if tick else "•") for b in items],
                           bulletType="bullet", start="✓" if tick else "•", leftIndent=14,
                           bulletColor=accent, bulletFontName=bold)
        story.extend([lst, Spacer(1, 4)])

    def box(kind, title, text):
        edge, bg = BOX_COLOURS.get(kind, BOX_COLOURS["note"])
        content = [Paragraph(escape(title), ParagraphStyle("bt", parent=st["box_t"], textColor=colors.HexColor(edge)))]
        if isinstance(text, (list, tuple)):
            content += [Paragraph("•  " + I(x), st["box_b"]) for x in text]
        elif text:
            content.append(Paragraph(I(text), st["box_b"]))
        t = Table([[content]], colWidths=[width])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)), ("LINEBEFORE", (0, 0), (0, -1), 3.5,
                                                                    colors.HexColor(edge)),
            ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.extend([Spacer(1, 4), KeepTogether([t]), Spacer(1, 10)])

    def table(headers, rows, widths=None):
        if not widths and len(headers) == 2:
            widths = [1, 2]
        col = [width * w / sum(widths) for w in widths] if widths else [width / len(headers)] * len(headers)
        data = [[Paragraph(escape(h), st["head"]) for h in headers]]
        data += [[Paragraph(I(c), st["cell"]) for c in r] for r in rows]
        t = Table(data, colWidths=col, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), accent), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(SOFT)]),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor(LINE)),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([Spacer(1, 2), t, Spacer(1, 12)])

    def image(name, caption, frac=1.0, callouts=None, max_h=330):
        path = _image_path(name)
        if not path:
            if caption:
                para(f"[Screen: {caption}]", "cap")
            return
        try:
            data, iw, ih = _image_data(path)
        except Exception:
            return
        w = width * frac
        h = w * ih / iw
        if h > max_h:
            h = max_h
            w = h * iw / ih
        img = Image(data, width=w, height=h)
        frame = Table([[img]], colWidths=[w + 2])
        frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor(LINE)),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                                   ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        frame.hAlign = "LEFT"
        block = [Spacer(1, 4), frame]
        if caption:
            block.append(Paragraph(escape(caption), st["cap"]))
        story.append(KeepTogether(block))
        if callouts:
            rows = []
            for n, text in callouts:
                badge = Table([[Paragraph(str(n), ParagraphStyle("cb", parent=st["num"], fontSize=8.4,
                                                                 textColor=colors.HexColor("#2B1D00")))]],
                              colWidths=[16], rowHeights=[16])
                badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2A900")),
                                           ("ROUNDEDCORNERS", [8, 8, 8, 8]), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                           ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                           ("TOPPADDING", (0, 0), (-1, -1), 0),
                                           ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
                rows.append([badge, Paragraph(I(text), st["cell"])])
            t = Table(rows, colWidths=[26, width - 26])
            t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
            story.extend([t, Spacer(1, 10)])

    # ---- "About this guide": intro, before you begin, contents
    story.append(NextPageTemplate("page"))
    story.append(PageBreak())
    story.append(Paragraph("ABOUT THIS GUIDE", st["eyebrow"]))
    story.append(Paragraph(escape(g["title"]), st["about_h"]))
    for p in g.get("intro", []):
        para(p)
    if g.get("before"):
        box("note", "Before you begin", g["before"])
    toc = TableOfContents()
    toc.levelStyles = [st["toc1"]]
    toc.dotsMinLevel = 0
    story.extend([Spacer(1, 6), Paragraph("CONTENTS", st["eyebrow"]), toc, PageBreak()])

    email, phone = contact_details(None)
    for block in g.get("blocks", []):
        kind = block[0]
        if kind == "part":
            part(block[1], block[2] if len(block) > 2 else "")
        elif kind == "h2":
            story.append(Paragraph(escape(block[1]), st["h2"]))
        elif kind == "h3":
            story.append(Paragraph(escape(block[1]), st["h3"]))
        elif kind == "p":
            para(block[1])
        elif kind == "steps":
            steps(block[1])
        elif kind == "bullets":
            bullets(block[1])
        elif kind == "checklist":
            bullets(block[1], tick=True)
        elif kind in ("tip", "important", "note"):
            box(kind, block[1], block[2])
        elif kind == "table":
            table(block[1], block[2])
        elif kind == "shot":
            image(block[1], block[2] if len(block) > 2 else "", 1.0, block[3] if len(block) > 3 else None)
        elif kind == "halfshot":
            image(block[1], block[2] if len(block) > 2 else "", 0.62, block[3] if len(block) > 3 else None)
        elif kind == "diagram":
            image(block[1], block[2] if len(block) > 2 else "", 1.0)
        elif kind == "phone":
            image(block[1], block[2] if len(block) > 2 else "", 0.4, max_h=360)
        elif kind == "phones":
            for name, cap in block[1]:
                image(name, cap, 0.4, max_h=300)
        elif kind == "doc_requirements":
            rows = doc_requirement_rows()
            if rows:
                table(["Document", "Who uploads it", "Required?", "Checked by"], rows, widths=[5.2, 4.6, 2.3, 4.5])
            else:
                para("The **Documents** page lists exactly which documents apply to your institution.")
        elif kind == "help":
            part("Getting help", "NBTE Quality Assurance is here to help you through every step.")
            rows = [["Help on the portal", "Choose the **?** button at the top of any portal page for help with "
                                           "that page, these guides and NBTE's contact details."],
                    ["How it works", "The portal's **How it works & guides** page explains every process with "
                                     "diagrams and links to every guide."]]
            if email:
                rows.append(["E-mail", f"**{email}**. Include your institution's name and the reference you are "
                                       "asking about (for example a request, bill or query number)."])
            if phone:
                rows.append(["Phone", f"**{phone}**, Monday to Friday during office hours."])
            rows.append(["Queries", "Answer queries from NBTE on the **Queries** page so the reply stays on your "
                                    "record."])
            table(["Where", "How"], rows, widths=[1, 3])
            box("note", "About the screens in this guide",
                "The screenshots use demonstration data (fictional institutions and people). Screens may change "
                "slightly as the portal improves; the steps stay the same.")

    # ---- document, cover and running header
    logo = _logo()
    edition = timezone.localdate().strftime("%B %Y")

    def cover(canv, doc):
        canv.saveState()
        canv.setFillColor(accent)
        canv.rect(0, 0, W, H, stroke=0, fill=1)
        canv.setFillColor(colors.Color(0, 0, 0, alpha=0.18))
        canv.rect(0, 0, W, H * 0.34, stroke=0, fill=1)
        canv.setFillColor(colors.Color(1, 1, 1, alpha=0.08))
        canv.circle(W - 40, H - 60, 210, stroke=0, fill=1)
        canv.circle(W - 40, H - 60, 140, stroke=0, fill=1)
        y = H - M - 56
        if logo:
            try:
                canv.setFillColor(colors.white)
                canv.roundRect(M, y - 6, 68, 68, 14, stroke=0, fill=1)
                canv.drawImage(logo, M + 6, y, width=56, height=56, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass
        canv.setFillColor(colors.white)
        canv.setFont(bold, 10)
        canv.drawString(M + 84, y + 34, "NATIONAL BOARD FOR TECHNICAL EDUCATION")
        canv.setFont(reg, 10)
        canv.drawString(M + 84, y + 18, "Quality Assurance Portal")
        canv.setFont(bold, 11)
        canv.drawString(M, H * 0.58 + 70, "INSTITUTION GUIDE")
        canv.setFont(bold, 40)
        canv.drawString(M, H * 0.58 + 22, g["title"])
        sub = ParagraphStyle("csub", fontName=reg, fontSize=15, leading=20, textColor=colors.white)
        p = Paragraph(escape(g.get("subtitle", "")), sub)
        _, ph = p.wrap(width * 0.85, 200)
        p.drawOn(canv, M, H * 0.58 - ph)
        aud = ParagraphStyle("caud", fontName=reg, fontSize=11, leading=15,
                             textColor=colors.Color(1, 1, 1, alpha=0.85))
        p2 = Paragraph(escape(g.get("audience", "")), aud)
        _, ph2 = p2.wrap(width * 0.85, 200)
        p2.drawOn(canv, M, H * 0.58 - ph - 16 - ph2)
        canv.setFont(bold, 11)
        canv.drawString(M, H * 0.34 - 46, "In this guide")
        summ = ParagraphStyle("csum", fontName=reg, fontSize=12, leading=17, textColor=colors.white)
        p3 = Paragraph(escape(g.get("summary", "")), summ)
        _, ph3 = p3.wrap(width * 0.8, 200)
        p3.drawOn(canv, M, H * 0.34 - 54 - ph3)
        canv.setFont(reg, 9)
        canv.setFillColor(colors.Color(1, 1, 1, alpha=0.8))
        canv.drawString(M, 40, f"Edition: {edition}")
        canv.drawRightString(W - M, 40, "qa.nbte.gov.ng")
        canv.restoreState()

    class Doc(BaseDocTemplate):
        def afterFlowable(self, flowable):
            entry = getattr(flowable, "_gd_toc", None)
            if entry:
                label, key = entry
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(label, key, level=0, closed=False)
                self.notify("TOCEntry", (0, label, self.page, key))

    buf = io.BytesIO()
    doc = Doc(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=70, bottomMargin=60,
              title=f"{g['title']} — NBTE Quality Assurance Portal", author="National Board for Technical Education",
              subject=g.get("subtitle", ""), creator="NBTE Quality Assurance Portal")
    body = Frame(M, 60, width, H - 130, id="body")
    doc.addPageTemplates([PageTemplate(id="cover", frames=[Frame(M, M, width, H - 2 * M, id="c")], onPage=cover),
                          PageTemplate(id="page", frames=[body])])

    from reportlab.pdfgen import canvas as rl_canvas

    class Numbered(rl_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                if self._pageNumber > 1:
                    self.saveState()
                    self.setFillColor(accent)
                    self.rect(M, H - 44, 22, 3, stroke=0, fill=1)
                    self.setFont(bold, 8.4)
                    self.setFillColor(colors.HexColor(INK))
                    self.drawString(M + 30, H - 46, "NBTE Quality Assurance Portal")
                    self.setFont(reg, 8.4)
                    self.setFillColor(colors.HexColor(MUTED))
                    self.drawRightString(W - M, H - 46, g["title"])
                    self.setStrokeColor(colors.HexColor(LINE))
                    self.setLineWidth(0.5)
                    self.line(M, 44, W - M, 44)
                    self.drawString(M, 30, f"Edition {edition}")
                    self.drawRightString(W - M, 30, f"Page {self._pageNumber} of {total}")
                    self.restoreState()
                super().showPage()
            super().save()

    doc.multiBuild([Spacer(1, 1)] + story, canvasmaker=Numbered)
    return buf.getvalue()
