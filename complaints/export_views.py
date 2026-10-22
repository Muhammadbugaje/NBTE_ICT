"""Complaint record PDF (Phase 8), namespace "qa_complaints":
/qa/complaints/<pk>/record/ — the complaint, its stages with SLA, the
messages (internal notes only for staff who may read them) and attachments
listed. Same visibility as the complaint page; audit-logged."""
from __future__ import annotations

from django.http import Http404
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import audit
from qa_core.pdf_report import Report, pdf_response
from qa_core.permissions import require

from . import services


def _staff(user):
    return bool(user.is_authenticated and getattr(user, "user_type", "staff") == "staff")


def _who(user):
    return (user.get_full_name() or user.email) if user is not None else "System"


@require(_staff)
@require_GET
def complaint_record_pdf(request, pk):
    c = services.get_visible(request.user, pk)
    if c is None:
        raise Http404("No such complaint.")
    internal = services.can_handle_view(request.user, c)
    rep = Report("Complaint record", subject=f"{c.reference} — {c.subject}", generated_by=request.user,
                 facts=[("Reference", c.reference), ("Layer", str(c.layer)),
                        ("Institution", f"{c.institution.name} ({c.institution.code})" if c.institution_id else "—"),
                        ("Raised by", f"{_who(c.raised_by)} · {c.get_raiser_type_display()}"),
                        ("Status", (c.status or "").replace("_", " ")), ("Priority", c.get_priority_display())])
    rep.h1("The complaint")
    rep.kv([("Subject", c.subject), ("Raised", c.created_at), ("Related to", c.related_label),
            ("Department now", getattr(c.current_department, "name", "")),
            ("Resolved", c.resolved_at), ("Closed", c.closed_at), ("Reopened", c.reopened_count),
            ("Satisfaction", f"{c.satisfaction}/5" if c.satisfaction else "")])
    rep.para(c.body)
    if c.resolution:
        rep.h2("Resolution")
        rep.para(c.resolution)
    rep.h1("Stages")
    rep.table(["Stage", "Department", "Assigned to", "Entered", "Due", "Left", "Breached"],
              [[s.stage_label or s.stage_code, getattr(s.assigned_department, "name", ""),
                _who(s.assigned_to) if s.assigned_to_id else "", s.entered_at, s.due_at, s.left_at,
                "Yes" if s.breached_at else ""] for s in c.stages.select_related("assigned_department", "assigned_to")
               .order_by("entered_at", "id")], widths=[0.18, 0.15, 0.15, 0.14, 0.14, 0.14, 0.1])
    rep.h1("Messages")
    messages = c.messages.select_related("author").order_by("created_at", "id")
    if not internal:
        messages = messages.filter(visibility="public")
    rep.table(["When", "From", "Visibility", "Message"],
              [[m.created_at, "System" if m.is_system else _who(m.author), m.get_visibility_display(), m.body]
               for m in messages], widths=[0.14, 0.16, 0.14, 0.56], empty="No messages.")
    rep.h2("Attachments")
    rep.table(["File", "Uploaded", "Scan"],
              [[a.display_name, a.created_at, a.scan_status.replace("_", " ")] for a in c.attachments.all()],
              widths=[0.6, 0.2, 0.2], empty="No attachments.")
    audit.log("other", c, actor=request.user, note="downloaded the complaint record (PDF)")
    return pdf_response(f"{c.reference}-record-{timezone.localdate().isoformat()}.pdf", rep.build())
