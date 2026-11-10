"""Task record PDF (Master Plan v2.1 §9.5 step 5, H19).

Reuses the workflow record generator's letterhead, watermark and footer
(``workflow.pdf``). The PDF is rendered on request for someone who may see the
task, so it shows exactly what that person may read: internal notes only for
the people involved (``services.can_see_internal``).
"""
from io import BytesIO

from django.utils import timezone
from django.utils.html import escape

from . import services


def _dt(value):
    if not value:
        return "—"
    if hasattr(value, "hour"):
        return timezone.localtime(value).strftime("%d %b %Y, %H:%M")
    return value.strftime("%d %b %Y")


def _who(u):
    return escape(u.get_full_name() or u.email) if u is not None else "—"


def render_task_record(task, viewer):
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from workflow import pdf as wpdf

    logo = wpdf._find_logo_path()
    watermark = wpdf._make_transparent_logo(logo, opacity=0.06)
    on_page = wpdf._make_page_callbacks(logo, watermark)

    ss = getSampleStyleSheet()
    title = ParagraphStyle("T", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=18, leading=22,
                           textColor=HexColor(wpdf.GREEN), spaceAfter=2)
    ref = ParagraphStyle("R", parent=ss["Normal"], fontName="Helvetica-Bold", fontSize=10,
                         textColor=HexColor(wpdf.MUTED), alignment=1, spaceAfter=14)
    h2 = ParagraphStyle("H", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=11,
                        textColor=HexColor(wpdf.GREEN), spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("B", parent=ss["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13.5,
                          textColor=HexColor(wpdf.INK))
    cell = ParagraphStyle("C", parent=ss["Normal"], fontName="Helvetica", fontSize=8, leading=10.5,
                          textColor=HexColor(wpdf.INK))
    head = ParagraphStyle("CH", parent=cell, fontName="Helvetica-Bold", textColor=white)

    def table(rows, widths):
        data = [[Paragraph(str(c), head) for c in rows[0]]] + [[Paragraph(str(c), cell) for c in r]
                                                               for r in rows[1:]]
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor(wpdf.GREEN)),
            ("GRID", (0, 0), (-1, -1), 0.3, HexColor(wpdf.BORDER)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor(wpdf.GREEN_SOFT)]),
        ]))
        return t

    story = [Paragraph("Task Record", title), Paragraph(escape(task.reference), ref)]
    meta = [
        ["Field", "Value"],
        ["Title", escape(task.title)],
        ["Status", task.get_state_display()],
        ["Priority", task.get_priority_display()],
        ["Department", escape(str(task.owner_department or "—"))],
        ["Division", escape(task.division.name) if task.division_id else "Individual task"],
        ["Created by", f"{_who(task.created_by)} on {_dt(task.created_at)}"],
        ["Reviewer", _who(task.reviewer)],
        ["Due", _dt(task.due_date)],
        ["Completed", _dt(task.completed_at) if task.completed_at else _dt(task.cancelled_at)],
        ["Time logged", f"{task.logged_hours} h"],
    ]
    if task.linked_request_id:
        meta.append(["Handled as request", escape(task.linked_request.reference)])
    story.append(table(meta, [4 * cm, 13 * cm]))

    if task.description:
        story += [Paragraph("What needed doing", h2),
                  Paragraph(escape(task.description).replace("\n", "<br/>"), body)]

    rows = list(task.assignee_rows.select_related("user").order_by("assigned_at"))
    if rows:
        hours = {}
        for e in task.time_entries.all():
            hours[e.user_id] = hours.get(e.user_id, 0) + e.duration_seconds
        data = [["Person", "Part", "Confirmation", "Done", "Time"]]
        for r in rows:
            data.append([_who(r.user), r.get_state_display(), r.get_review_state_display()
                         + (f": {escape(r.review_note)}" if r.review_note else ""),
                         _dt(r.completed_at), f"{round(hours.get(r.user_id, 0) / 3600, 2)} h"])
        story += [Paragraph("People and their parts", h2), table(data, [4.2 * cm, 2.6 * cm, 5 * cm, 3 * cm, 2.2 * cm])]

    subs = list(task.subtasks.order_by("created_at"))
    if subs:
        data = [["Subtask", "Status", "Request"]]
        for s in subs:
            data.append([f"{escape(s.reference)} {escape(s.title)}", s.get_state_display(),
                         escape(s.linked_request.reference) if s.linked_request_id else "—"])
        story += [Paragraph("Subtasks", h2), table(data, [10 * cm, 3.5 * cm, 3.5 * cm])]

    comments = list(services.visible_comments(task, viewer).select_related("author").order_by("created_at"))
    if comments:
        data = [["When", "Who", "Comment"]]
        for c in comments:
            data.append([_dt(c.created_at), _who(c.author),
                         ("[Internal] " if c.is_internal else "") + escape(c.body)[:1500]])
        story += [Paragraph("Comments", h2), table(data, [3.2 * cm, 3.5 * cm, 10.3 * cm])]

    acts = list(services.visible_activities(task, viewer).select_related("actor").order_by("created_at"))
    if acts:
        data = [["When", "Who", "What"]]
        for a in acts[:300]:
            data.append([_dt(a.created_at), _who(a.actor) if a.actor_id else "System", escape(a.message or a.kind)])
        story += [Paragraph("Timeline", h2), table(data, [3.2 * cm, 3.5 * cm, 10.3 * cm])]

    story += [Spacer(1, 10), Paragraph(
        f"Generated for {_who(viewer)} on {_dt(timezone.now())}. This record shows what that person may see.",
        ParagraphStyle("F", parent=body, fontSize=8, textColor=HexColor(wpdf.MUTED)))]

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=3.6 * cm,
                            bottomMargin=2.2 * cm, title=f"{task.reference} task record")
    doc._request_ref = task.reference
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
