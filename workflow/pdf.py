"""Generate the completed-request PDF record.

Produces a professional, letterhead-styled PDF for a completed request:

    • Letterhead with the institutional logo, org name, and department
    • Semi-transparent logo watermark behind the body on every page
    • Metadata block (subject, requester, dates, status, priority)
    • Free-text details body
    • Approvals table (handles synthetic runtime-only steps cleanly)
    • Footer with generated timestamp, reference, and page number

Uses ReportLab (pure Python, portable across Windows/Linux) and Pillow
for the watermark composite. Both are hard dependencies only for this
module — a missing/broken generator never blocks a request from
completing, because `workflow.services._finalise` wraps the call.
"""
import logging
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Brand colours (kept in one place so restyling is a one-file edit)
# ---------------------------------------------------------------------------
GREEN       = "#1F6B3E"   # primary NBTE accent
GREEN_SOFT  = "#E8F0EB"   # very light green for table headers
INK         = "#0F172A"   # main body text
MUTED       = "#64748B"   # secondary text
BORDER      = "#D9DCE0"   # table / rule lines
LETTERHEAD_RULE = "#1F6B3E"


# ---------------------------------------------------------------------------
# Logo discovery + watermark prep
# ---------------------------------------------------------------------------
def _find_logo_path():
    """Return a Path to the logo, or None if we can't find one.

    Checks (in order):
      1. settings.PDF_LOGO_PATH, if defined
      2. Common static/ subdirectories
      3. STATIC_ROOT (if collectstatic has been run)
    """
    # v2.1 A5: the formal brand slot (PNG rendered from the SVG crest).
    from config.branding import pdf_logo_path
    return pdf_logo_path("formal")


def _make_transparent_logo(logo_path, opacity=0.06):
    """Return a BytesIO of the logo with the alpha channel scaled down.

    Used as the page watermark. Returns None if Pillow isn't installed or
    the file can't be read — the caller falls back to no watermark.
    """
    if not logo_path:
        return None
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow is not installed; watermark will be skipped.")
        return None
    try:
        img = Image.open(logo_path).convert("RGBA")
        r, g, b, alpha = img.split()
        alpha = alpha.point(lambda a: int(a * opacity))
        img.putalpha(alpha)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
    except Exception:
        logger.exception("Could not build watermark from %s", logo_path)
        return None


# ---------------------------------------------------------------------------
# Page furniture (letterhead, watermark, footer)
# ---------------------------------------------------------------------------
def _make_page_callbacks(logo_path, watermark_buf):
    """Return an onPage callable that draws the watermark, letterhead,
    and footer on every page."""
    from reportlab.lib.units import cm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader

    site_name   = _get_setting("site.organisation_name",
                               "National Board for Technical Education")
    dept_name   = _get_setting("site.department_name", "ICT Directorate")
    contact     = _get_setting("site.address", "")
    contact_em  = _get_setting("site.email", "")
    contact_ph  = _get_setting("site.phone", "")

    def on_page(canvas, doc):
        canvas.saveState()
        width, height = doc.pagesize

        # ---- 1. Watermark (behind everything) ---------------------------
        if watermark_buf is not None:
            try:
                watermark_buf.seek(0)
                wm_size = 12 * cm
                canvas.drawImage(
                    ImageReader(watermark_buf),
                    (width - wm_size) / 2,
                    (height - wm_size) / 2,
                    width=wm_size, height=wm_size,
                    mask="auto",
                )
            except Exception:
                logger.exception("Watermark draw failed; continuing without it.")

        # ---- 2. Letterhead ---------------------------------------------
        top_y = height - 1.4 * cm

        # Logo (left)
        if logo_path:
            try:
                logo_h = 1.5 * cm
                logo_w = 1.5 * cm
                canvas.drawImage(
                    ImageReader(str(logo_path)),
                    2 * cm, top_y - logo_h + 0.15 * cm,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, anchor="sw", mask="auto",
                )
            except Exception:
                logger.exception("Letterhead logo draw failed.")

        # Org + department + contact (right)
        canvas.setFillColor(HexColor(GREEN))
        canvas.setFont("Helvetica-Bold", 12)
        canvas.drawRightString(width - 2 * cm, top_y - 0.35 * cm, site_name)

        canvas.setFillColor(HexColor(INK))
        canvas.setFont("Helvetica", 9.5)
        canvas.drawRightString(width - 2 * cm, top_y - 0.85 * cm, dept_name)

        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica", 8)
        contact_line = " · ".join([x for x in (contact, contact_em, contact_ph) if x])
        if contact_line:
            canvas.drawRightString(width - 2 * cm, top_y - 1.25 * cm, contact_line)

        # Rule under the letterhead
        rule_y = top_y - 1.55 * cm
        canvas.setStrokeColor(HexColor(LETTERHEAD_RULE))
        canvas.setLineWidth(1.2)
        canvas.line(2 * cm, rule_y, width - 2 * cm, rule_y)
        canvas.setStrokeColor(HexColor(BORDER))
        canvas.setLineWidth(0.3)
        canvas.line(2 * cm, rule_y - 1.2, width - 2 * cm, rule_y - 1.2)

        # ---- 3. Footer --------------------------------------------------
        canvas.setStrokeColor(HexColor(BORDER))
        canvas.setLineWidth(0.4)
        canvas.line(2 * cm, 1.55 * cm, width - 2 * cm, 1.55 * cm)

        canvas.setFillColor(HexColor(MUTED))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            2 * cm, 1.15 * cm,
            f"Generated {timezone.localtime().strftime('%b %d, %Y · %H:%M')}",
        )
        canvas.drawCentredString(
            width / 2, 1.15 * cm,
            f"{doc._request_ref}",
        )
        canvas.drawRightString(
            width - 2 * cm, 1.15 * cm,
            f"Page {canvas.getPageNumber()}",
        )

        canvas.restoreState()

    return on_page


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------
def _get_setting(key, default=""):
    """Read a value from the config registry, silently falling back."""
    try:
        import config as _cfg
        return _cfg.get(key, default)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_request_record(request):
    """Render a PDF record for a completed request and attach it to
    `request.record_pdf`. Idempotent — regenerating replaces the file."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm, mm
        from reportlab.lib.colors import HexColor, white
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
    except ImportError as exc:
        raise RuntimeError(
            "ReportLab is required for PDF generation. "
            "Install it with: pip install reportlab"
        ) from exc

    logo_path     = _find_logo_path()
    watermark_buf = _make_transparent_logo(logo_path, opacity=0.06)

    # ---- Styles ----------------------------------------------------------
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "RecTitle", parent=styles["Title"],
        fontName="Helvetica-Bold", fontSize=20, leading=24,
        textColor=HexColor(GREEN), spaceAfter=2, alignment=TA_CENTER,
    )
    ref_style = ParagraphStyle(
        "RecRef", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=HexColor(MUTED), spaceAfter=18, alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=HexColor(GREEN), spaceBefore=14, spaceAfter=6,
    )
    label_style = ParagraphStyle(
        "Label", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=9, leading=12,
        textColor=HexColor(MUTED),
    )
    value_style = ParagraphStyle(
        "Value", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
        textColor=HexColor(INK),
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=10, leading=14.5,
        textColor=HexColor(INK), spaceAfter=4,
    )
    cell_style = ParagraphStyle(
        "Cell", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8, leading=10.5,
        textColor=HexColor(INK),
    )
    cell_head_style = ParagraphStyle(
        "CellHead", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8, leading=10.5,
        textColor=white,
    )

    # ---- Flowable story --------------------------------------------------
    story = []

    story.append(Paragraph("Request Record", title_style))
    story.append(Paragraph(request.reference, ref_style))

    # Meta block ----------------------------------------------------------
    def _fmt_dt(dt):
        if not dt:
            return "—"
        return timezone.localtime(dt).strftime("%b %d, %Y · %H:%M")

    def _user(u):
        if not u:
            return "—"
        return u.get_full_name() or u.email

    meta_rows = [
        ["Subject",        request.subject],
        ["Request type",   request.request_type.name],
        ["Requester",      _user(request.requester)],
        ["Department",     request.requester.department.name
                           if request.requester.department_id else "—"],
        ["Receiving office", request.receiving_department.name
                             if request.receiving_department_id else "—"],
        ["Priority",       request.get_priority_display()],
        ["Status",         request.get_status_display()],
        ["Assigned to",    _user(request.assigned_to) if request.assigned_to_id else "—"],
        ["Created",        _fmt_dt(request.created_at)],
        ["Completed",      _fmt_dt(request.completed_at)],
    ]

    meta_data = [
        [Paragraph(label, label_style), Paragraph(value, value_style)]
        for label, value in meta_rows
    ]
    meta_tbl = Table(meta_data, colWidths=[4.2 * cm, 12.8 * cm])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.25, HexColor(BORDER)),
    ]))
    story.append(meta_tbl)

    # Details -------------------------------------------------------------
    story.append(Paragraph("Details", section_style))
    body_text = (request.body or "—").replace("\n", "<br/>")
    story.append(Paragraph(body_text, body_style))

    # v2.1 I8: a funds request carries the trail of the ticket it came from.
    trail = (request.metadata or {}).get("ticket_trail") if isinstance(request.metadata, dict) else None
    if trail:
        from xml.sax.saxutils import escape as _x
        story.append(Paragraph(f"How this came about — ticket {_x(trail.get('reference', ''))}", section_style))
        story.append(Paragraph(_x(f"{trail.get('subject', '')} · opened by {trail.get('raised_by', '')} · "
                                  f"{trail.get('handling_department', '')}"), body_style))
        rows = [[Paragraph("When", cell_head_style), Paragraph("By", cell_head_style),
                 Paragraph("What happened", cell_head_style)]]
        for a in trail.get("activities", [])[:60]:
            rows.append([Paragraph(_x(str(a.get("at", ""))[:16].replace("T", " ")), cell_style),
                         Paragraph(_x(a.get("actor", "")), cell_style),
                         Paragraph(_x(a.get("message", ""))[:300], cell_style)])
        t = Table(rows, colWidths=[3.0 * cm, 3.6 * cm, 10.4 * cm], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HexColor(GREEN)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("GRID", (0, 0), (-1, -1), 0.25, HexColor(BORDER))]))
        story.append(t)

    # Approvals -----------------------------------------------------------
    approvals = list(
        request.approvals.select_related("step", "actor").order_by("decided_at")
    )
    if approvals:
        story.append(Paragraph("Approval trail", section_style))

        headers = [
            Paragraph("Step",     cell_head_style),
            Paragraph("Actor",    cell_head_style),
            Paragraph("Role",     cell_head_style),
            Paragraph("Decision", cell_head_style),
            Paragraph("When",     cell_head_style),
            Paragraph("Comment",  cell_head_style),
        ]
        rows = [headers]

        for a in approvals:
            # Synthetic steps (Finance / Audit / HR / ES / Records) are
            # runtime-only — a.step is None for those. Fall back to the
            # actor's recorded role, then a generic label.
            if a.step_id and a.step is not None:
                step_name = a.step.name
            else:
                step_name = a.actor_role or "(synthetic step)"

            actor_name = _user(a.actor) if a.actor_id else "—"
            when = (
                timezone.localtime(a.decided_at).strftime("%Y-%m-%d %H:%M")
                if a.decided_at else "—"
            )
            comment = (a.comment or "").strip()
            if len(comment) > 220:
                comment = comment[:217] + "…"

            rows.append([
                Paragraph(step_name, cell_style),
                Paragraph(actor_name, cell_style),
                Paragraph(a.actor_role or "—", cell_style),
                Paragraph(a.get_decision_display(), cell_style),
                Paragraph(when, cell_style),
                Paragraph(comment or "—", cell_style),
            ])

        approvals_tbl = Table(
            rows,
            colWidths=[3.0 * cm, 3.0 * cm, 2.2 * cm, 2.0 * cm, 2.6 * cm, 4.2 * cm],
            repeatRows=1,
        )
        approvals_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), HexColor(GREEN)),
            ("TEXTCOLOR",     (0, 0), (-1, 0), white),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID",          (0, 0), (-1, -1), 0.25, HexColor(BORDER)),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [white, HexColor(GREEN_SOFT)]),
        ]))
        story.append(approvals_tbl)

    # ---- Build the document ---------------------------------------------
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=4.2 * cm, bottomMargin=2.2 * cm,
        title=f"{request.reference} — {request.subject}",
        author=dept_name if (dept_name := _get_setting("site.department_name")) else "ICT Directorate",
        subject="Request record",
    )
    # Give the onPage callback access to the reference for the footer.
    doc._request_ref = request.reference

    on_page = _make_page_callbacks(logo_path, watermark_buf)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

    # ---- Persist --------------------------------------------------------
    filename = f"{request.reference}.pdf"
    request.record_pdf.save(filename, ContentFile(buf.getvalue()), save=False)
    if not request.record_original_name:
        request.record_original_name = filename
    request.save(update_fields=[
        "record_pdf", "record_original_name", "updated_at",
    ])
    return request.record_pdf