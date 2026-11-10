"""Ticket record PDF (v2.1 Phase C, I13) — same letterhead, watermark and
footer as the workflow request record (workflow/pdf.py).

Rendered on demand for anyone who may see the ticket. Internal notes are
never included (the raiser can download it too).
"""
from io import BytesIO

from django.utils import timezone


def _dt(dt):
    return timezone.localtime(dt).strftime("%b %d, %Y · %H:%M") if dt else "—"


def _user(u):
    return (u.get_full_name() or u.email) if u else "—"


def _esc(text):
    from xml.sax.saxutils import escape
    return escape(str(text or "")).replace("\n", "<br/>")


def render_ticket_pdf(ticket) -> bytes:
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    from workflow.pdf import (BORDER, GREEN, GREEN_SOFT, INK, MUTED, _find_logo_path, _get_setting,
                              _make_page_callbacks, _make_transparent_logo)

    from .bridge import path_events

    styles = getSampleStyleSheet()
    title = ParagraphStyle("TkTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24,
                           textColor=HexColor(GREEN), spaceAfter=2, alignment=TA_CENTER)
    ref = ParagraphStyle("TkRef", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=14,
                         textColor=HexColor(MUTED), spaceAfter=18, alignment=TA_CENTER)
    section = ParagraphStyle("TkSec", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11,
                             leading=14, textColor=HexColor(GREEN), spaceBefore=14, spaceAfter=6)
    label = ParagraphStyle("TkLabel", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12,
                           textColor=HexColor(MUTED))
    value = ParagraphStyle("TkValue", parent=styles["Normal"], fontName="Helvetica", fontSize=10, leading=13,
                           textColor=HexColor(INK))
    body = ParagraphStyle("TkBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=14.5,
                          textColor=HexColor(INK), spaceAfter=4)
    cell = ParagraphStyle("TkCell", parent=styles["Normal"], fontName="Helvetica", fontSize=8, leading=10.5,
                          textColor=HexColor(INK))
    head = ParagraphStyle("TkHead", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10.5,
                          textColor=white)

    def kv_table(rows):
        t = Table([[Paragraph(_esc(k), label), Paragraph(_esc(v), value)] for k, v in rows],
                  colWidths=[4.2 * cm, 12.8 * cm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                               ("LINEBELOW", (0, 0), (-1, -2), 0.25, HexColor(BORDER))]))
        return t

    def grid(headers, rows, widths):
        data = [[Paragraph(h, head) for h in headers]] + [[Paragraph(_esc(c), cell) for c in r] for r in rows]
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HexColor(GREEN)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("GRID", (0, 0), (-1, -1), 0.25, HexColor(BORDER)),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor(GREEN_SOFT)]),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        return t

    story = [Paragraph("Ticket Record", title), Paragraph(_esc(ticket.reference), ref)]
    rating = f"{ticket.satisfaction_rating}/5" if ticket.satisfaction_rating else "Not rated"
    story.append(kv_table([
        ("Subject", ticket.subject), ("Category", ticket.category.name), ("Priority", ticket.get_priority_display()),
        ("State", ticket.get_state_display()), ("Opened by", _user(ticket.raised_by)),
        ("Handling department", ticket.handling_department.name),
        ("Division", ticket.division.name if ticket.division_id else "—"),
        ("Assignee", _user(ticket.assigned_to)), ("Opened", _dt(ticket.created_at)),
        ("Resolved", _dt(ticket.resolved_at)), ("Closed", _dt(ticket.closed_at)), ("Rating", rating),
    ]))
    story.append(Paragraph("Details", section))
    story.append(Paragraph(_esc(ticket.body) or "—", body))
    if ticket.resolution_notes:
        story.append(Paragraph("Resolution", section))
        story.append(Paragraph(_esc(ticket.resolution_notes), body))
    if ticket.satisfaction_comment:
        story.append(Paragraph(f"Opener's feedback: {_esc(ticket.satisfaction_comment)}", body))
    if ticket.asset_id:
        a = ticket.asset
        story.append(Paragraph("Asset", section))
        story.append(kv_table([("Tag", a.asset_tag), ("Item", a.display_name), ("State", a.get_state_display()),
                               ("Location", a.current_location or "—")]))
    if ticket.funds_state:
        story.append(Paragraph("Funds", section))
        story.append(kv_table([
            ("Request", ticket.funds_request.reference if ticket.funds_request_id else "—"),
            ("Amount", f"NGN {ticket.funds_amount:,.2f}" if ticket.funds_amount is not None else "—"),
            ("Decision", ticket.get_funds_state_display()), ("Note", ticket.funds_decision_note or "—")]))
    if ticket.source_request_id:
        story.append(Paragraph(f"Converted from request {_esc(ticket.source_request.reference)}.", body))

    events = path_events(ticket)
    if events:
        story.append(Paragraph("Path taken", section))
        story.append(grid(["When", "Step", "By", "Note"],
                          [[_dt(e.created_at), e.label, _user(e.actor),
                            (e.message or "")[:220]] for e in events],
                          [3.2 * cm, 3.2 * cm, 3.4 * cm, 7.2 * cm]))
    comments = list(ticket.comments.filter(is_internal=False).select_related("author"))
    if comments:
        story.append(Paragraph("Comments", section))
        story.append(grid(["When", "By", "Comment"],
                          [[_dt(c.created_at), _user(c.author), c.body[:600]] for c in comments],
                          [3.2 * cm, 3.6 * cm, 10.2 * cm]))
    atts = list(ticket.attachments.select_related("uploaded_by"))
    if atts:
        story.append(Paragraph("Attachments", section))
        story.append(grid(["File", "By", "When"],
                          [[a.original_name, _user(a.uploaded_by), _dt(a.created_at)] for a in atts],
                          [9 * cm, 4.4 * cm, 3.6 * cm]))
    story.append(Paragraph(f"Generated {_dt(timezone.now())}. Internal staff notes are not included.", label))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=4.2 * cm,
                            bottomMargin=2.2 * cm, title=f"{ticket.reference} — {ticket.subject}",
                            author=_get_setting("site.department_name") or "ICT Directorate", subject="Ticket record")
    doc._request_ref = ticket.reference
    logo = _find_logo_path()
    on_page = _make_page_callbacks(logo, _make_transparent_logo(logo, opacity=0.06))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
