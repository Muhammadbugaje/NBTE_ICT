"""NBTE-branded PDF reports (Phase 8): the institutional record, timeline,
case summary, payment statement, complaint record and service history
exports all share this one builder.

ReportLab platypus: an optional cover page (logo, title, subject, facts), an
optional table of contents (two passes), and on every page the NBTE header
rule, the report title and "Page N of M". Fonts are the DejaVu faces the
payment slips already bundle (Unicode, the naira sign), with a base-14
fallback.

    rep = Report("Institutional record", subject="Kaduna Polytechnic (KDP)",
                 facts=[("Code", "KDP")], generated_by=user)
    rep.h1("Identity")
    rep.kv([("Name", inst.name), ...])
    rep.table(["Date", "Event"], rows, widths=[0.2, 0.8])
    data = rep.build()
"""
from __future__ import annotations

import io
from xml.sax.saxutils import escape

from django.utils import timezone

GREEN = "#1F6B3E"
GREEN_SOFT = "#F0F7F2"
INK = "#0F172A"
MUTED = "#5B6577"
LINE = "#D9DCE0"


def _fonts():
    from payments.pdfs import _fonts as payment_fonts

    return payment_fonts()


def _logo():
    from payments.pdfs import _logo as payment_logo

    return payment_logo()


def text(value, default="—"):
    """Escape a value for a Paragraph (None / blank → default)."""
    if value is None or value == "":
        return default
    if hasattr(value, "strftime"):
        try:
            if hasattr(value, "hour"):
                value = timezone.localtime(value) if timezone.is_aware(value) else value
                return value.strftime("%d %b %Y, %H:%M")
            return value.strftime("%d %b %Y")
        except (ValueError, TypeError):
            return str(value)
    return escape(str(value))


class Report:
    def __init__(self, title, *, subject="", facts=None, generated_by=None, cover=True, toc=False,
                 landscape=False, note=""):
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.pagesizes import landscape as to_landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

        self.colors = colors
        self.title, self.subject, self.facts = title, subject, list(facts or [])
        self.generated_by, self.cover, self.toc, self.note = generated_by, cover, toc, note
        self.pagesize = to_landscape(A4) if landscape else A4
        self.story = []
        self.f = _fonts()
        base = getSampleStyleSheet()
        reg, bold = self.f["regular"], self.f["bold"]
        self.styles = {
            "body": ParagraphStyle("qa-body", parent=base["BodyText"], fontName=reg, fontSize=9, leading=12.5,
                                   textColor=colors.HexColor(INK), alignment=TA_LEFT),
            "small": ParagraphStyle("qa-small", parent=base["BodyText"], fontName=reg, fontSize=7.8, leading=10,
                                    textColor=colors.HexColor(MUTED)),
            "cell": ParagraphStyle("qa-cell", parent=base["BodyText"], fontName=reg, fontSize=8, leading=10,
                                   textColor=colors.HexColor(INK)),
            "head": ParagraphStyle("qa-head", parent=base["BodyText"], fontName=bold, fontSize=7.4, leading=9,
                                   textColor=colors.white),
            "h1": ParagraphStyle("qa-h1", parent=base["Heading1"], fontName=bold, fontSize=15, leading=19,
                                 spaceBefore=6, spaceAfter=8, textColor=colors.HexColor(GREEN)),
            "h2": ParagraphStyle("qa-h2", parent=base["Heading2"], fontName=bold, fontSize=11.5, leading=15,
                                 spaceBefore=8, spaceAfter=4, textColor=colors.HexColor(INK)),
            "title": ParagraphStyle("qa-title", parent=base["Title"], fontName=bold, fontSize=26, leading=31,
                                    textColor=colors.HexColor(GREEN), alignment=TA_LEFT),
            "subtitle": ParagraphStyle("qa-sub", parent=base["BodyText"], fontName=bold, fontSize=14, leading=18,
                                       textColor=colors.HexColor(INK)),
            "toc1": ParagraphStyle("qa-toc1", fontName=reg, fontSize=10, leading=15, leftIndent=8),
            "toc2": ParagraphStyle("qa-toc2", fontName=reg, fontSize=9, leading=13, leftIndent=24,
                                   textColor=colors.HexColor(MUTED)),
        }
        self._n = 0

    # ------------------------------------------------------------------ content
    def h1(self, value):
        from reportlab.platypus import Paragraph

        self._n += 1
        p = Paragraph(text(value), self.styles["h1"])
        p._qa_toc = (0, str(value), f"s{self._n}")
        self.story.append(p)

    def h2(self, value):
        from reportlab.platypus import Paragraph

        self._n += 1
        p = Paragraph(text(value), self.styles["h2"])
        p._qa_toc = (1, str(value), f"s{self._n}")
        self.story.append(p)

    def para(self, value, style="body"):
        from reportlab.platypus import Paragraph

        self.story.append(Paragraph(text(value, ""), self.styles[style]))

    def spacer(self, h=6):
        from reportlab.platypus import Spacer

        self.story.append(Spacer(1, h))

    def page_break(self):
        from reportlab.platypus import PageBreak

        self.story.append(PageBreak())

    def kv(self, pairs, *, cols=2):
        """Label / value pairs in a compact grid."""
        from reportlab.platypus import Paragraph, Table, TableStyle

        pairs = [(k, v) for k, v in pairs]
        if not pairs:
            return
        width = self._width()
        cells, row = [], []
        for label, value in pairs:
            row.append(Paragraph(f'<font color="{MUTED}" size="7">{text(label).upper()}</font><br/>{text(value)}',
                                 self.styles["cell"]))
            if len(row) == cols:
                cells.append(row)
                row = []
        if row:
            cells.append(row + [""] * (cols - len(row)))
        t = Table(cells, colWidths=[width / cols] * cols)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, self.colors.HexColor(LINE)),
        ]))
        self.story.append(t)
        self.spacer(6)

    def table(self, headers, rows, *, widths=None, empty="None."):
        from reportlab.platypus import Paragraph, Table, TableStyle

        rows = list(rows)
        if not rows:
            self.para(empty, "small")
            self.spacer(4)
            return
        width = self._width()
        if widths:
            total = sum(widths)
            col_widths = [width * w / total for w in widths]
        else:
            col_widths = [width / len(headers)] * len(headers)
        data = [[Paragraph(text(h), self.styles["head"]) for h in headers]]
        for r in rows:
            data.append([c if hasattr(c, "wrap") else Paragraph(text(c), self.styles["cell"]) for c in r])
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self.colors.HexColor(GREEN)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [self.colors.white, self.colors.HexColor(GREEN_SOFT)]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, self.colors.HexColor(LINE)),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        self.story.append(t)
        self.spacer(8)

    # ------------------------------------------------------------------ layout
    def _width(self):
        return self.pagesize[0] - 2 * 42

    def _cover(self):
        from reportlab.platypus import Image, PageBreak, Paragraph, Spacer

        out = []
        logo = _logo()
        if logo:
            try:
                out.append(Image(logo, width=64, height=64, hAlign="LEFT"))
            except Exception:
                pass
        out += [Spacer(1, 14),
                Paragraph("NATIONAL BOARD FOR TECHNICAL EDUCATION", self.styles["small"]),
                Paragraph("Quality Assurance", self.styles["small"]), Spacer(1, 40),
                Paragraph(text(self.title), self.styles["title"]), Spacer(1, 8)]
        if self.subject:
            out.append(Paragraph(text(self.subject), self.styles["subtitle"]))
        out.append(Spacer(1, 22))
        facts = list(self.facts)
        facts.append(("Generated", timezone.localtime().strftime("%d %b %Y, %H:%M")))
        if self.generated_by is not None:
            who = getattr(self.generated_by, "get_full_name", lambda: "")() or getattr(self.generated_by, "email", "")
            facts.append(("Generated by", who))
        self.story_cover_facts = facts
        out.append(_FactsBox(self, facts))
        if self.note:
            out += [Spacer(1, 16), Paragraph(text(self.note), self.styles["small"])]
        out.append(PageBreak())
        return out

    def build(self):
        from reportlab.platypus import Paragraph, Spacer
        from reportlab.platypus.tableofcontents import TableOfContents

        report = self
        story = []
        if self.cover:
            story += self._cover()
        if self.toc:
            toc = TableOfContents()
            toc.levelStyles = [self.styles["toc1"], self.styles["toc2"]]
            story += [Paragraph("Contents", self.styles["h1"]), Spacer(1, 6), toc]
            from reportlab.platypus import PageBreak

            story.append(PageBreak())
        story += self.story
        buf = io.BytesIO()
        doc = _Doc(buf, report=report)
        if self.toc:
            doc.multiBuild(story, canvasmaker=_canvas_maker(report))
        else:
            doc.build(story, canvasmaker=_canvas_maker(report))
        return buf.getvalue()


def _FactsBox(report, facts):
    from reportlab.platypus import Paragraph, Table, TableStyle

    rows = [[Paragraph(f'<font color="{MUTED}">{text(k)}</font>', report.styles["body"]),
             Paragraph(f"<b>{text(v)}</b>", report.styles["body"])] for k, v in facts]
    t = Table(rows, colWidths=[report._width() * 0.3, report._width() * 0.7])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), report.colors.HexColor(GREEN_SOFT)),
        ("LINEABOVE", (0, 0), (-1, 0), 2, report.colors.HexColor(GREEN)),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _doc_class():
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    class Doc(BaseDocTemplate):
        def __init__(self, buf, *, report):
            self.report = report
            super().__init__(buf, pagesize=report.pagesize, leftMargin=42, rightMargin=42, topMargin=64,
                             bottomMargin=54, title=f"{report.title} — {report.subject}".strip(" —"),
                             author="National Board for Technical Education", creator="NBTE Quality Assurance")
            frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body")
            self.addPageTemplates([PageTemplate(id="page", frames=[frame])])

        def afterFlowable(self, flowable):
            entry = getattr(flowable, "_qa_toc", None)
            if entry:
                level, label, key = entry
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(label, key, level=level, closed=level > 0)
                self.notify("TOCEntry", (level, label, self.page, key))

    return Doc


def _Doc(buf, *, report):
    return _doc_class()(buf, report=report)


def _canvas_maker(report):
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    class NumberedCanvas(canvas.Canvas):
        """Two-pass page numbering: "Page N of M" on every page."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            w, h = report.pagesize
            f = report.f
            first = self._pageNumber == 1 and report.cover
            self.saveState()
            self.setStrokeColor(HexColor(GREEN))
            self.setLineWidth(2)
            self.line(42, h - 40, w - 42, h - 40)
            if not first:
                self.setFont(f["bold"], 8)
                self.setFillColor(HexColor(GREEN))
                self.drawString(42, h - 34, "NBTE · Quality Assurance")
                self.setFont(f["regular"], 8)
                self.setFillColor(HexColor(MUTED))
                label = f"{report.title} — {report.subject}" if report.subject else report.title
                self.drawRightString(w - 42, h - 34, label[:110])
            self.setStrokeColor(HexColor(LINE))
            self.setLineWidth(0.5)
            self.line(42, 40, w - 42, 40)
            self.setFont(f["regular"], 7.5)
            self.setFillColor(HexColor(MUTED))
            self.drawString(42, 28, f"Generated {timezone.localtime():%d %b %Y, %H:%M} · Restricted: for NBTE use")
            self.drawRightString(w - 42, 28, f"Page {self._pageNumber} of {total}")
            self.restoreState()

    return NumberedCanvas


def csv_response(filename, headers, rows):
    """A UTF-8 (with BOM, for Excel) CSV download."""
    import csv

    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")
    writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    return response


def pdf_response(filename, data, *, inline=False):
    from django.http import HttpResponse

    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    return response
