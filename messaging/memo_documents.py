"""Memo document generation — PDF and Word output with NBTE letterhead.

Both public functions return raw bytes ready to stream from an HttpResponse.

Non-published memos get:
  * A faint diagonal watermark over every page (DRAFT / AWAITING APPROVAL /
    RETURNED FOR REVISION / REJECTED / CANCELLED).
  * An amber banner above the title.

Published memos render clean.

Dependencies:
    reportlab   — for PDF (already installed for attendance)
    python-docx — for Word (pip install python-docx)
    lxml        — for DOCX watermark (comes with python-docx)
"""
from __future__ import annotations

import copy
import io
import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ===========================================================================
# Config helpers
# ===========================================================================
def _cfg(key, default=""):
    try:
        import config as _c
        v = _c.get(key, default)
        return v if v is not None else default
    except Exception:
        return default


def _site_brand():
    return {
        "org":     _cfg("site.organisation_name",
                        "National Board for Technical Education"),
        "dept":    _cfg("site.department_name", "ICT Directorate"),
        "tagline": _cfg("site.tagline",
                        "The directorate's digital workspace."),
        "address": _cfg("site.address", ""),
        "email":   _cfg("site.email", ""),
        "phone":   _cfg("site.phone", ""),
    }


def _find_logo():
    candidates = [
        getattr(settings, "PDF_LOGO_PATH", None),
        Path(settings.BASE_DIR) / "static" / "img"      / "NBTE_logo.png",
        Path(settings.BASE_DIR) / "static" / "images"   / "NBTE_logo.png",
        Path(settings.BASE_DIR) / "static" / "branding" / "NBTE_logo.png",
        Path(settings.BASE_DIR) / "static" / "logo.png",
    ]
    for c in candidates:
        if not c:
            continue
        try:
            p = Path(c)
            if p.is_file():
                return p
        except (TypeError, OSError):
            continue
    return None


def _memo_date(memo):
    dt = memo.published_at or memo.created_at
    return timezone.localtime(dt).strftime("%B %d, %Y")


def _memo_recipients_label(memo):
    """Human-readable 'To:' line for the PDF/Word letterhead.

    Handles every visibility kind we actually store, including
    specific_user, and falls back to recipient rows rather than
    printing an em-dash when the kind is something new.
    """
    vis = (memo.visibility or "").strip()

    if vis == "all_staff":
        return "All Staff"

    if vis == "all_directors":
        return "All Directors"

    if vis == "department":
        return memo.department.name if memo.department_id else "Department"

    if vis == "role":
        names = list(memo.roles.values_list("name", flat=True))
        return ", ".join(names) if names else "Selected Roles"

    if vis in ("specific_user", "individual", "individuals"):
        if memo.target_user_id:
            u = memo.target_user
            return u.get_full_name() or u.email

        qs = memo.memo_recipients.select_related("user")
        total = qs.count()
        names = [(r.user.get_full_name() or r.user.email) for r in qs[:6]]
        if total > len(names):
            names.append(f"+{total - len(names)} more")
        return ", ".join(names) if names else "—"

    # Unknown kind: do NOT print an em-dash if we can do better.
    if memo.target_user_id:
        u = memo.target_user
        return u.get_full_name() or u.email

    qs = memo.memo_recipients.select_related("user")
    total = qs.count()
    names = [(r.user.get_full_name() or r.user.email) for r in qs[:6]]
    if total > len(names):
        names.append(f"+{total - len(names)} more")
    return ", ".join(names) if names else "—"


def _sender_signature_lines(memo):
    sender = memo.sender
    name = sender.get_full_name() or sender.email
    designation = ""
    dept_name = ""
    try:
        sp = getattr(sender, "staff_profile", None)
        if sp is not None:
            designation = getattr(sp, "designation", "") or ""
            dept = getattr(sp, "department", None)
            if dept is not None:
                dept_name = dept.name
    except Exception:
        pass
    if not designation:
        role = getattr(sender, "role", None)
        if role is not None:
            designation = role.name
    if not dept_name:
        dept = getattr(sender, "department", None)
        if dept is not None:
            dept_name = dept.name
    return name, designation, dept_name


# ===========================================================================
# Status → watermark / banner
# ===========================================================================
def _memo_watermark_label(memo):
    """Faint diagonal watermark for non-published states. Returns None
    when the memo is published (published prints clean)."""
    status = (memo.status or "").lower()
    if status == "published":
        return None
    return {
        "draft":     "DRAFT",
        "awaiting":  "AWAITING APPROVAL",
        "returned":  "RETURNED FOR REVISION",
        "rejected":  "REJECTED",
        "cancelled": "CANCELLED",
    }.get(status, "NOT PUBLISHED")


def _memo_status_banner(memo):
    """Amber inline banner shown at the top of the document body when
    the memo isn't published. Returns None when published."""
    return {
        "draft":     "— DRAFT — NOT YET PUBLISHED —",
        "awaiting":  "— AWAITING APPROVAL — NOT YET DELIVERED —",
        "returned":  "— RETURNED FOR REVISION —",
        "rejected":  "— REJECTED —",
        "cancelled": "— CANCELLED —",
    }.get((memo.status or "").lower())


# ===========================================================================
# PDF
# ===========================================================================
def render_memo_pdf(memo, request=None):
    """Return the memo as a PDF (bytes).

    Signature block is drawn directly on the canvas and pinned to the
    bottom-left of the *last* page. Two-pass build: pass 1 counts total
    pages, pass 2 draws the signature only on the last page.
    """
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.colors import HexColor
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable,
        )
        from reportlab.lib.utils import ImageReader
    except ImportError as exc:
        raise RuntimeError("reportlab is required for PDF output.") from exc

    brand = _site_brand()
    logo_path = _find_logo()

    watermark_label = _memo_watermark_label(memo)

    GREEN = "#1F6B3E"
    INK   = "#0F172A"
    MUTED = "#64748B"
    LINE  = "#D9DCE0"

    # Page geometry
    PAGE_W, PAGE_H = A4
    MARGIN_L = MARGIN_R = 2.2 * cm
    TOP_MARGIN = 3.6 * cm
    BOTTOM_MARGIN = 3.2 * cm
    CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

    FOOTER_RULE_Y = 1.5 * cm
    SIG_BASE_Y    = 3.0 * cm

    # --- Styles ------------------------------------------------------
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "MemoTitle", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=15, leading=18,
        textColor=HexColor(GREEN), alignment=TA_CENTER,
        spaceBefore=4, spaceAfter=14,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9.5, leading=12,
        textColor=HexColor(MUTED), spaceAfter=1,
    )
    field_label_style = ParagraphStyle(
        "FieldLabel", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=10, leading=13,
        textColor=HexColor(INK),
    )
    field_value_style = ParagraphStyle(
        "FieldValue", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
        textColor=HexColor(INK),
    )
    body_style = ParagraphStyle(
        "MemoBody", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10.5, leading=15,
        textColor=HexColor(INK), alignment=TA_JUSTIFY,
        spaceAfter=8,
    )
    draft_style = ParagraphStyle(
        "DraftBanner", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=9, leading=12,
        textColor=HexColor("#B45309"), alignment=TA_CENTER,
        spaceAfter=10,
    )

    # --- Canvas decoration ------------------------------------------
    sender_name, sender_desig, sender_dept = _sender_signature_lines(memo)

    def _draw_page(canvas, doc, *, draw_signature=False):
        canvas.saveState()
        width, height = doc.pagesize

        # ---- Watermark (every page, behind everything) -------------
        if watermark_label:
            canvas.saveState()
            try:
                canvas.setFillAlpha(0.10)
            except Exception:
                pass
            canvas.setFillColor(HexColor(GREEN))
            canvas.setFont("Helvetica-Bold", 72)
            canvas.translate(width / 2.0, height / 2.0)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, watermark_label)
            canvas.restoreState()

        # ---- Letterhead --------------------------------------------
        top_y = height - 0.9 * cm
        logo_size = 1.8 * cm

        if logo_path:
            try:
                canvas.drawImage(
                    ImageReader(str(logo_path)),
                    MARGIN_L, top_y - logo_size,
                    width=logo_size, height=logo_size,
                    preserveAspectRatio=True, anchor="sw", mask="auto",
                )
            except Exception:
                logger.exception("Could not render memo logo on PDF")

        text_x = MARGIN_L + logo_size + 0.6 * cm
        canvas.setFillColor(HexColor(GREEN))
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(text_x, top_y - 0.35 * cm, brand["org"])

        canvas.setFont("Helvetica", 10.5)
        canvas.drawString(text_x, top_y - 0.85 * cm, brand["dept"])

        if brand["tagline"]:
            canvas.setFont("Helvetica-Oblique", 8.5)
            canvas.drawString(text_x, top_y - 1.2 * cm, brand["tagline"])

        rule_y = top_y - 2.5 * cm
        canvas.setStrokeColor(HexColor(GREEN))
        canvas.setLineWidth(1.4)
        canvas.line(MARGIN_L, rule_y, width - MARGIN_R, rule_y)

        # ---- Signature (last page only) ----------------------------
        if draw_signature:
            sig_x = MARGIN_L
            line_w = 6.5 * cm

            canvas.setStrokeColor(HexColor(INK))
            canvas.setLineWidth(0.7)
            canvas.line(
                sig_x, SIG_BASE_Y + 1.35 * cm,
                sig_x + line_w, SIG_BASE_Y + 1.35 * cm,
            )

            canvas.setFillColor(HexColor(INK))
            canvas.setFont("Helvetica-Bold", 11)
            canvas.drawString(sig_x, SIG_BASE_Y + 0.85 * cm,
                              sender_name.upper())

            if sender_desig:
                canvas.setFillColor(HexColor(MUTED))
                canvas.setFont("Helvetica", 9.5)
                canvas.drawString(sig_x, SIG_BASE_Y + 0.45 * cm,
                                  sender_desig)

            if sender_dept and \
               sender_dept.lower() not in (sender_desig or "").lower():
                canvas.setFont("Helvetica", 9.5)
                canvas.drawString(sig_x, SIG_BASE_Y + 0.1 * cm,
                                  sender_dept)

        # ---- Footer ------------------------------------------------
        canvas.setStrokeColor(HexColor(LINE))
        canvas.setLineWidth(0.3)
        canvas.line(MARGIN_L, FOOTER_RULE_Y,
                    width - MARGIN_R, FOOTER_RULE_Y)

        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            MARGIN_L, 1.1 * cm,
            f"{memo.reference} - Internal Memorandum",
        )
        canvas.drawRightString(
            width - MARGIN_R, 1.1 * cm,
            f"Page {canvas.getPageNumber()}",
        )
        canvas.restoreState()

    # --- Story ------------------------------------------------------
    def _build_story():
        story = []

        # Status banner (draft / awaiting / returned / rejected / cancelled)
        status_banner = _memo_status_banner(memo)
        if status_banner:
            story.append(Paragraph(status_banner, draft_style))

        story.append(Paragraph("INTERNAL MEMORANDUM", title_style))

        date_str = _memo_date(memo)
        story.append(Paragraph(f"<b>Ref:</b> {memo.reference}", meta_style))
        story.append(Paragraph(f"<b>Date:</b> {date_str}", meta_style))
        story.append(Spacer(1, 14))

        from_line = sender_name
        if sender_desig:
            from_line += f", {sender_desig}"
        if sender_dept:
            from_line += f" ({sender_dept})"

        LABEL_W = 2.2 * cm
        VALUE_W = CONTENT_W - LABEL_W

        fields_data = [
            [Paragraph("To:", field_label_style),
             Paragraph(_memo_recipients_label(memo), field_value_style)],
            [Paragraph("From:", field_label_style),
             Paragraph(from_line, field_value_style)],
            [Paragraph("Subject:", field_label_style),
             Paragraph(memo.subject, field_value_style)],
        ]
        fields_table = Table(
            fields_data,
            colWidths=[LABEL_W, VALUE_W],
            hAlign="LEFT",
        )
        fields_table.setStyle(TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(fields_table)
        story.append(Spacer(1, 4))
        story.append(HRFlowable(
            width="100%", thickness=0.4,
            color=HexColor(LINE), spaceBefore=6, spaceAfter=14,
        ))

        body_text = (memo.body or "").strip()
        if body_text:
            for block in body_text.split("\n\n"):
                block = block.strip()
                if not block:
                    continue
                html = (block
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace("\n", "<br/>"))
                story.append(Paragraph(html, body_style))

        return story

    def _make_doc(buffer):
        return SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=MARGIN_L, rightMargin=MARGIN_R,
            topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
            title=f"{memo.reference} — {memo.subject}",
            author=brand["org"],
            subject="Internal Memorandum",
        )

    # ---- Pass 1: count pages ---------------------------------------
    story = _build_story()
    buf_1 = io.BytesIO()
    doc_1 = _make_doc(buf_1)
    doc_1.build(
        story,
        onFirstPage=lambda c, d: _draw_page(c, d, draw_signature=False),
        onLaterPages=lambda c, d: _draw_page(c, d, draw_signature=False),
    )
    total_pages = doc_1.page or 1

    # ---- Pass 2: draw the signature on the last page ---------------
    story = _build_story()
    buf_2 = io.BytesIO()
    doc_2 = _make_doc(buf_2)

    def _on_page(canvas, doc):
        is_last = (canvas.getPageNumber() == total_pages)
        _draw_page(canvas, doc, draw_signature=is_last)

    doc_2.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buf_2.getvalue()


# ===========================================================================
# DOCX (Word)
# ===========================================================================
def _add_docx_watermark(doc, text):
    """Insert a diagonal VML watermark into every section's header.

    Word has no native 'watermark' flag in .docx — Word itself does it
    via a VML shape anchored in the header, behind the body, rotated
    315°. We reproduce that here.
    """
    try:
        from lxml import etree
    except ImportError:
        logger.exception("lxml is required for DOCX watermark")
        return

    safe = (
        (text or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    xml = (
        '<w:r '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word">'
        '<w:pict>'
        '<v:shape id="PowerPlusWaterMarkObject" type="#_x0000_t136" '
        'style="position:absolute;margin-left:0;margin-top:0;'
        'width:480pt;height:120pt;z-index:-251658752;'
        'mso-position-horizontal:center;'
        'mso-position-horizontal-relative:margin;'
        'mso-position-vertical:center;'
        'mso-position-vertical-relative:margin;'
        'rotation:315" '
        'fillcolor="#c8c8c8" stroked="f">'
        '<v:textpath '
        'style="font-family:&quot;Calibri&quot;;font-size:1pt" '
        'string="' + safe + '"/>'
        '</v:shape>'
        '</w:pict>'
        '</w:r>'
    )

    try:
        run_elem = etree.fromstring(xml)
    except Exception:
        logger.exception("Could not build DOCX watermark XML")
        return

    for section in doc.sections:
        header = section.header
        try:
            header.is_linked_to_previous = False
        except Exception:
            pass
        if not header.paragraphs:
            header.add_paragraph()
        p = header.paragraphs[0]
        try:
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception:
            pass
        p._p.append(copy.deepcopy(run_elem))


def render_memo_docx(memo, request=None):
    """Return the memo as a .docx Word document (bytes).

    Word has no canvas, so the signature is placed as a bottom-anchored
    block via an invisible spacer table with vertical alignment.
    """
    try:
        from docx import Document
        from docx.shared import Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.enum.table import WD_ALIGN_VERTICAL
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required for Word output. "
            "Install with: pip install python-docx"
        ) from exc

    brand = _site_brand()
    logo_path = _find_logo()

    GREEN = RGBColor(0x1F, 0x6B, 0x3E)
    INK   = RGBColor(0x0F, 0x17, 0x2A)
    MUTED = RGBColor(0x64, 0x74, 0x8B)
    AMBER = RGBColor(0xB4, 0x53, 0x09)

    doc = Document()

    for section in doc.sections:
        section.top_margin = Cm(1.4)
        section.bottom_margin = Cm(2)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)

    # ---- Watermark (header of every section) ------------------------
    watermark_label = _memo_watermark_label(memo)
    if watermark_label:
        _add_docx_watermark(doc, watermark_label)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    # ---- Letterhead: 2-column table (logo | text) -------------------
    header_table = doc.add_table(rows=1, cols=2)
    header_table.autofit = False
    header_table.columns[0].width = Cm(3.2)
    header_table.columns[1].width = Cm(13)

    left_cell = header_table.cell(0, 0)
    left_cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    if logo_path:
        try:
            p = left_cell.paragraphs[0]
            run = p.add_run()
            run.add_picture(str(logo_path), width=Cm(2.2))
        except Exception:
            logger.exception("Could not embed memo logo in DOCX")

    right_cell = header_table.cell(0, 1)
    right_cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
    right_cell.paragraphs[0].text = ""

    p_org = right_cell.add_paragraph()
    r = p_org.add_run(brand["org"])
    r.bold = True
    r.font.size = Pt(14)
    r.font.color.rgb = GREEN

    p_dept = right_cell.add_paragraph()
    r = p_dept.add_run(brand["dept"])
    r.font.size = Pt(10.5)
    r.font.color.rgb = GREEN

    p_tag = None
    if brand["tagline"]:
        p_tag = right_cell.add_paragraph()
        r = p_tag.add_run(brand["tagline"])
        r.italic = True
        r.font.size = Pt(8)
        r.font.color.rgb = GREEN

    for para in (p_org, p_dept):
        para.paragraph_format.space_before = Pt(0)
        para.paragraph_format.space_after = Pt(0)
    if p_tag is not None:
        p_tag.paragraph_format.space_before = Pt(0)
        p_tag.paragraph_format.space_after = Pt(0)

    # ---- Green rule under header ------------------------------------
    rule_para = doc.add_paragraph()
    pPr = rule_para._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "18")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "1F6B3E")
    pBdr.append(bottom)
    pPr.append(pBdr)
    rule_para.paragraph_format.space_after = Pt(16)

    # ---- Status banner (non-published only) -------------------------
    status_banner = _memo_status_banner(memo)
    if status_banner:
        banner = doc.add_paragraph()
        banner.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = banner.add_run(status_banner)
        r.bold = True
        r.font.size = Pt(10)
        r.font.color.rgb = AMBER
        banner.paragraph_format.space_after = Pt(8)

    # ---- Title ------------------------------------------------------
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = title.add_run("INTERNAL MEMORANDUM")
    r.bold = True
    r.font.size = Pt(15)
    r.font.color.rgb = GREEN
    title.paragraph_format.space_after = Pt(14)

    # ---- Ref / Date -------------------------------------------------
    def _meta_line(label, value):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(1)
        rb = p.add_run(f"{label} ")
        rb.bold = True
        rb.font.size = Pt(9.5)
        rb.font.color.rgb = MUTED
        rv = p.add_run(value)
        rv.font.size = Pt(9.5)
        rv.font.color.rgb = MUTED

    _meta_line("Ref:", memo.reference)
    _meta_line("Date:", _memo_date(memo))

    doc.add_paragraph().paragraph_format.space_after = Pt(8)

    # ---- To / From / Subject ----------------------------------------
    sender_name, sender_desig, sender_dept = _sender_signature_lines(memo)
    from_line = sender_name
    if sender_desig:
        from_line += f", {sender_desig}"
    if sender_dept:
        from_line += f" ({sender_dept})"

    def _field(label, value):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        rl = p.add_run(f"{label} ")
        rl.bold = True
        rl.font.size = Pt(10)
        rl.font.color.rgb = INK
        rv = p.add_run(value)
        rv.font.size = Pt(10)
        rv.font.color.rgb = INK

    _field("To:", _memo_recipients_label(memo))
    _field("From:", from_line)
    _field("Subject:", memo.subject)

    thin_rule = doc.add_paragraph()
    pPr = thin_rule._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "6")
    bottom.set(qn("w:color"), "D9DCE0")
    pBdr.append(bottom)
    pPr.append(pBdr)
    thin_rule.paragraph_format.space_before = Pt(6)
    thin_rule.paragraph_format.space_after = Pt(14)

    # ---- Body -------------------------------------------------------
    body_text = (memo.body or "").strip()
    if body_text:
        for block in body_text.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            p = doc.add_paragraph(block)
            p.paragraph_format.space_after = Pt(8)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    # ---- Signature block, bottom-anchored ---------------------------
    sig_table = doc.add_table(rows=1, cols=1)
    sig_table.autofit = False
    sig_table.columns[0].width = Cm(16.6)

    tr = sig_table.rows[0]._tr
    trPr = tr.get_or_add_trPr()
    trHeight = OxmlElement("w:trHeight")
    trHeight.set(qn("w:val"), "2800")
    trHeight.set(qn("w:hRule"), "atLeast")
    trPr.append(trHeight)

    cell = sig_table.cell(0, 0)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.BOTTOM

    cell.paragraphs[0].text = ""

    def _cell_line(text, *, bold=False, muted=False, size=9):
        p = cell.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(size)
        r.font.color.rgb = MUTED if muted else INK
        return p

    _cell_line("____________________________", size=10)
    _cell_line(sender_name.upper(), bold=True, size=10.5)
    if sender_desig:
        _cell_line(sender_desig, muted=True, size=9)
    if sender_dept and sender_dept.lower() not in (sender_desig or "").lower():
        _cell_line(sender_dept, muted=True, size=9)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()