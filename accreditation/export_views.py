"""Case summary PDF (Phase 8), namespace "qa_case": /qa/cases/<pk>/summary/.

The SOP-03 case on paper: request and programmes, the current stage, every
stage event, the cost sheet, the personnel list, the visit, fund releases,
post-visit reports and recommendations. Same visibility as the case page
(out of scope → 404); audit-logged.
"""
from __future__ import annotations

from django.http import Http404
from django.utils import timezone
from django.views.decorators.http import require_GET

from core import audit
from qa_core.pdf_report import Report, pdf_response
from qa_core.permissions import require

from . import case_services as cs


def _who(user):
    return (user.get_full_name() or user.email) if user is not None else ""


@require(cs.can_view_cases)
@require_GET
def case_summary_pdf(request, pk):
    case = (cs.visible_cases(request.user).select_related("institution", "owning_department", "qa_request__qa_type",
                                                          "desk_officer").filter(pk=pk).first())
    if case is None:
        raise Http404
    from inspection import services as insp
    from institutions.record import _flow_events

    from .models import QARequestProgramme

    inst = case.institution
    stages = cs.stage_objects(case)
    rows = list(QARequestProgramme.unscoped.filter(qa_request=case.qa_request)
                .select_related("programme", "programme__level"))
    bundle = cs.case_bundle(case)
    rep = Report("QA case summary", subject=f"{case.reference} — {inst.name}", generated_by=request.user,
                 facts=[("Case", case.reference), ("Institution", f"{inst.name} ({inst.code})"),
                        ("QA type", case.qa_request.qa_type.name if case.qa_request.qa_type_id else ""),
                        ("Current stage", "; ".join(f"{s.sop_stage_no or ''} {s.label}".strip() for s in stages)
                         or ("Closed" if case.completed_at else case.status)),
                        ("Owning department", getattr(case.owning_department, "name", ""))])
    rep.h1("The case")
    rep.kv([("Request", case.qa_request.reference), ("Joint QA", "Yes" if case.qa_request.is_joint_qa else "No"),
            ("Started", case.started_at), ("Completed", case.completed_at),
            ("Desk officer", _who(case.desk_officer)), ("Programmes", case.programme_count)])
    rep.table(["Programme", "Level", "In the request", "Programme status"],
              [[r.programme.name, r.programme.level.code.upper() if r.programme.level_id else "",
                r.get_status_display(), r.programme.get_status_display()] for r in rows],
              widths=[0.4, 0.1, 0.25, 0.25])
    rep.h1("Stages")
    rep.table(["When", "From", "To", "Action", "By", "Remark"], _flow_events(case, "qa_case"),
              widths=[0.15, 0.17, 0.17, 0.12, 0.15, 0.24], empty="The case has not started.")
    rep.h1("Cost, personnel and visit")
    sheet = bundle["sheet"]
    if sheet is not None:
        rep.para(f"Cost sheet version {sheet.version} · {sheet.get_status_display()} · total NGN {sheet.total:,.2f}")
        rep.table(["Role", "Staff", "Kind", "Days", "Unit (NGN)", "Amount (NGN)", "Audit note"],
                  [[ln.role_on_exercise, str(ln.staff or ""), ln.get_kind_display(), ln.days, f"{ln.unit_amount:,.2f}",
                    f"{ln.amount:,.2f}", ln.audit_note] for ln in bundle["lines"]],
                  widths=[0.18, 0.18, 0.12, 0.07, 0.13, 0.13, 0.19])
    else:
        rep.para("No cost sheet yet.", "small")
    rep.h2("Personnel list")
    rep.table(["Staff", "Duty", "Status", "Remark"],
              [[str(e.staff), e.get_duty_display(), e.get_status_display(), e.remark] for e in bundle["entries"]],
              widths=[0.35, 0.2, 0.15, 0.3], empty="No personnel yet.")
    visit = bundle["visit"]
    rep.h2("Visit")
    if visit is not None:
        rep.kv([("Planned", f"{visit.planned_start or '—'} – {visit.planned_end or '—'}"),
                ("Team lead", str(visit.team_lead or "")), ("Status", visit.get_status_display()),
                ("Approved", visit.approved_at), ("Logistics", visit.logistics_notes)])
    else:
        rep.para("No visit details yet.", "small")
    rep.h2("Fund releases")
    rep.table(["Amount (NGN)", "Status", "Reference", "Released", "Remark"],
              [[f"{r.amount:,.2f}", r.get_status_display(), r.released_reference, r.released_at, r.remark]
               for r in bundle["releases"]], widths=[0.15, 0.15, 0.2, 0.15, 0.35], empty="No fund releases.")
    rep.h1("Visits and recommendations")
    inspections = list(insp.visible_inspections(request.user).filter(case=case))
    rep.table(["Visit", "Scheduled", "Visited", "Status"],
              [[i.reference, f"{i.scheduled_start:%d %b %Y} – {i.scheduled_end:%d %b %Y}",
                f"{i.actual_start:%d %b %Y} – {i.actual_end:%d %b %Y}" if i.actual_start else "",
                (i.status or "").replace("_", " ")] for i in inspections],
              widths=[0.25, 0.3, 0.3, 0.15], empty="No visits.")
    reports = list(insp.visible_reports(request.user).filter(inspection__case=case)
                   .select_related("qa_request_programme__programme"))
    rep.table(["Programme", "Report status", "Recommendation", "Note"],
              [[str(r.qa_request_programme.programme), (r.status or "").replace("_", " "),
                r.get_recommendation_display(), r.recommendation_note] for r in reports],
              widths=[0.3, 0.18, 0.17, 0.35], empty="No post-visit reports.")
    audit.log("other", case, actor=request.user, note="downloaded the case summary (PDF)")
    return pdf_response(f"{case.reference.replace('/', '-')}-summary-{timezone.localdate().isoformat()}.pdf",
                        rep.build())
