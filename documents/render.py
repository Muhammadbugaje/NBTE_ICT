"""PDF rendering for generated documents (X24, X25; design 27b).

One layout serves letters, memos and receipts: the NBTE letterhead (crest,
Board name, address), reference and date, addressee, an underlined subject,
the body paragraphs and/or a facts table, the signature block (the stored
signature image of each signatory as it was at the signing time) and, on the
right, the verification QR with the short code. Provisional documents get a
diagonal watermark on every page (``qa.documents.provisional_watermark``,
same shape as ``qa.letters.watermark``).

``render(spec)`` takes a plain dict so the renderer never touches the
database and tests can render without fixtures.
"""
from __future__ import annotations

import io
import json
from xml.sax.saxutils import escape

GREEN, GREEN_DARK, INK, MUTED, LINE = "#1F6B3E", "#134326", "#0F172A", "#5B6577", "#D9DCE0"
DEFAULT_WATERMARK = {"text": "PROVISIONAL — OFFICIAL COPY TO FOLLOW", "opacity": 0.14, "angle": 45}


def watermark_setting():
    import config

    value = config.get("qa.documents.provisional_watermark", DEFAULT_WATERMARK)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = DEFAULT_WATERMARK
    return {**DEFAULT_WATERMARK, **(value if isinstance(value, dict) else {})}


def _fonts():
    from payments.pdfs import _fonts as payment_fonts  # DejaVu (naira sign) with a base-14 fallback

    return payment_fonts()


def qr_png(url):
    import qrcode

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


def _draw_watermark(canvas, w, h, mark):
    canvas.saveState()
    try:
        opacity = float(mark.get("opacity", 0.14))
    except (TypeError, ValueError):
        opacity = 0.14
    from reportlab.lib.colors import HexColor

    canvas.setFillColor(HexColor("#B42318"))
    canvas.setStrokeColor(HexColor("#B42318"))
    try:
        canvas.setFillAlpha(max(0.04, min(opacity, 0.6)))
        canvas.setStrokeAlpha(max(0.04, min(opacity, 0.6)))
    except AttributeError:
        pass
    canvas.translate(w / 2, h / 2)
    try:
        canvas.rotate(float(mark.get("angle", 45)))
    except (TypeError, ValueError):
        canvas.rotate(45)
    text = str(mark.get("text") or "PROVISIONAL")
    head, _sep, tail = text.partition("—")
    head, tail = head.strip(), tail.strip()
    size = 70 if len(head) <= 12 else max(34, int(70 * 12 / len(head)))
    canvas.setFont("Helvetica-Bold", size)  # base-14 font: the word stays searchable in the PDF text
    canvas.drawCentredString(0, 6, head)
    width = canvas.stringWidth(head, "Helvetica-Bold", size)
    canvas.setLineWidth(3)
    canvas.roundRect(-width / 2 - 18, -size * 0.55 - (24 if tail else 0), width + 36,
                     size * 1.35 + (24 if tail else 0), 10, stroke=1, fill=0)
    if tail:
        canvas.setFont("Helvetica-Bold", 20)
        canvas.drawCentredString(0, -size * 0.45, tail)
    canvas.restoreState()


def render(spec):
    """Return PDF bytes for ``spec`` (see module docstring for the keys)."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from config.branding import pdf_logo_path

    f = _fonts()
    regular, bold = f["regular"], f["bold"]
    PAGE_W, PAGE_H = A4
    MARGIN = 2.0 * cm
    HEAD_H = 3.6 * cm
    content_w = PAGE_W - 2 * MARGIN - 12
    logo = pdf_logo_path("formal")
    mark = spec.get("watermark") if spec.get("provisional") else None
    org = spec.get("organisation") or "NATIONAL BOARD FOR TECHNICAL EDUCATION"
    address = spec.get("address") or "Plot B, Bida Road, P.M.B. 2239, Kaduna · www.nbte.gov.ng"
    footer_left = f"{spec.get('reference', '')} · {spec.get('kind_label', 'Document')}"
    footer_right = spec.get("footer_hint") or ""

    body = ParagraphStyle("DBody", fontName=regular, fontSize=10.5, leading=15.5, textColor=HexColor(INK),
                          alignment=TA_JUSTIFY, spaceAfter=8)
    small = ParagraphStyle("DSmall", parent=body, fontSize=9.5, leading=13, alignment=TA_LEFT, spaceAfter=0)
    right = ParagraphStyle("DRight", parent=small, alignment=TA_RIGHT)
    muted = ParagraphStyle("DMuted", parent=small, fontSize=8.5, leading=11, textColor=HexColor(MUTED))
    centre_muted = ParagraphStyle("DMutedC", parent=muted, alignment=TA_CENTER)
    subject = ParagraphStyle("DSubject", parent=body, fontName=bold, alignment=TA_LEFT, spaceBefore=4,
                             spaceAfter=10)
    label = ParagraphStyle("DLabel", parent=small, textColor=HexColor(MUTED))
    value = ParagraphStyle("DValue", parent=small, fontName=bold)
    banner = ParagraphStyle("DBanner", parent=small, fontName=bold, textColor=HexColor("#7A4B00"),
                            alignment=TA_CENTER)

    def on_page(canvas, doc):
        canvas.saveState()
        w, h = doc.pagesize
        top = h - 1.3 * cm
        size = 1.7 * cm
        x = MARGIN
        if logo:
            try:
                canvas.drawImage(ImageReader(str(logo)), x, top - size, width=size, height=size,
                                 preserveAspectRatio=True, mask="auto")
            except Exception:  # pragma: no cover - a broken logo must not stop the document
                pass
        canvas.setFillColor(HexColor(GREEN_DARK))
        canvas.setFont(bold, 13.5)
        canvas.drawString(x + size + 12, top - 0.75 * cm, org.upper()[:60])
        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont(regular, 8.5)
        canvas.drawString(x + size + 12, top - 1.25 * cm, address[:110])
        canvas.setStrokeColor(HexColor(INK))
        canvas.setLineWidth(1.2)
        canvas.line(MARGIN, top - size - 0.35 * cm, w - MARGIN, top - size - 0.35 * cm)
        canvas.setLineWidth(0.4)
        canvas.line(MARGIN, top - size - 0.45 * cm, w - MARGIN, top - size - 0.45 * cm)
        if mark:
            _draw_watermark(canvas, w, h, mark)
        canvas.setStrokeColor(HexColor(LINE))
        canvas.setLineWidth(0.4)
        canvas.line(MARGIN, 1.5 * cm, w - MARGIN, 1.5 * cm)
        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont(regular, 7.5)
        canvas.drawString(MARGIN, 1.1 * cm, footer_left[:95])
        canvas.drawRightString(w - MARGIN, 1.1 * cm, (footer_right + "  ·  " if footer_right else "")
                               + f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    story = []
    if spec.get("provisional"):
        story.append(Paragraph(escape(spec.get("provisional_banner")
                                      or "PROVISIONAL COPY — generated automatically; the signed official "
                                         "copy will follow."), banner))
        story.append(Spacer(1, 8))
    ref_tbl = Table([[Paragraph(f"Ref: {escape(spec.get('reference', ''))}", small),
                      Paragraph(escape(spec.get("date", "")), right)]], colWidths=[content_w * 0.6, content_w * 0.4])
    ref_tbl.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [ref_tbl, Spacer(1, 12)]
    for line in spec.get("addressee") or []:
        story.append(Paragraph(escape(line), small))
    if spec.get("addressee"):
        story.append(Spacer(1, 12))
    if spec.get("subject"):
        story.append(Paragraph(f"<u>{escape(spec['subject'].upper())}</u>", subject))
    for para in spec.get("paragraphs") or []:
        para = (para or "").strip()
        if para:
            story.append(Paragraph(escape(para).replace("\n", "<br/>"), body))
    facts = spec.get("facts") or []
    if facts:
        rows = [[Paragraph(escape(str(k)), label), Paragraph(escape(str(v if v not in (None, "") else "—")), value)]
                for k, v in facts]
        tbl = Table(rows, colWidths=[content_w * 0.32, content_w * 0.68])
        tbl.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, HexColor(LINE)),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story += [Spacer(1, 4), tbl, Spacer(1, 8)]

    # Signature block (left) and verification QR (right)
    sig_cells = []
    for s in spec.get("signers") or []:
        cell = []
        if s.get("png"):
            img = ImageReader(io.BytesIO(s["png"]))
            iw, ih = img.getSize()
            height = 1.35 * cm
            width = min(5.2 * cm, height * iw / max(ih, 1))
            cell.append(Image(io.BytesIO(s["png"]), width=width, height=width * ih / max(iw, 1)))
        else:
            cell.append(Spacer(1, 1.35 * cm))
        cell.append(Paragraph(f"<b>{escape(s.get('name') or '')}</b>", small))
        if s.get("designation"):
            cell.append(Paragraph(escape(s["designation"]), muted))
        if s.get("caption"):
            cell.append(Paragraph(escape(s["caption"]), muted))
        sig_cells.append(cell)
    left = Table([[c] for c in sig_cells] or [[""]], colWidths=[content_w * 0.66])
    left.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    right_cell = [""]
    if spec.get("qr_url"):
        qr = Image(io.BytesIO(qr_png(spec["qr_url"])), width=2.9 * cm, height=2.9 * cm)
        right_cell = [qr, Paragraph("Scan to verify", centre_muted)]
        if spec.get("short_code"):
            right_cell.append(Paragraph(f"<b>{escape(spec['short_code'])}</b>",
                                        ParagraphStyle("DCode", parent=centre_muted, textColor=HexColor(INK))))
    block = Table([[left, right_cell]], colWidths=[content_w * 0.7, content_w * 0.3])
    block.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (1, 0), (1, 0), "CENTER"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 14), KeepTogether([block])]
    if spec.get("cc"):
        story += [Spacer(1, 10), Paragraph(escape(spec["cc"]), muted)]
    if spec.get("verify_note"):
        story += [Spacer(1, 6), Paragraph(escape(spec["verify_note"]), muted)]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + HEAD_H - 1 * cm,
                            bottomMargin=2.0 * cm, title=spec.get("title") or spec.get("subject") or "NBTE document",
                            author="National Board for Technical Education", creator="NBTE Digital",
                            subject=spec.get("kind_label") or "")
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
