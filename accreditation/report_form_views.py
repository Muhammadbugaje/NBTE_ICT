"""v2.4 — screens for the template-driven visit reports (SOP-03 stage 12):
the programme evaluation (NBTE/P/4A) per report and the ICT verification
(NBTE/QA/ICT/VVT/001) per visit. Mounted under /qa/cases/<case>/reports/."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from qa_core.permissions import require_or_404
from qa_core.ui import render_qa

from . import case_services as cs
from . import report_forms
from .models import VisitReportForm


def _report_or_404(request, pk, report_id):
    from inspection import services as insp

    report = (insp.visible_reports(request.user).filter(pk=report_id, inspection__case_id=pk)
              .select_related("inspection__case__institution", "inspection__case__qa_request__qa_type",
                              "qa_request_programme__programme__level").first())
    if report is None:
        raise Http404
    return report


def _form_or_404(report, kind):
    if kind not in report_forms.HOLDER_TYPES:
        raise Http404
    evaluation, ict = report_forms.ensure_forms(report)
    form = evaluation if kind == VisitReportForm.EVALUATION else ict
    if form is None:
        raise Http404("The report template is not published (Settings → SSQ templates).")
    return form


def _ctx(request, report, form, *, errors=None, problems=None):
    from inspection import services as insp

    can_edit, why = report_forms.can_edit(form, request.user)
    if insp.is_monitor(request.user):
        can_edit, why = False, "The ICT monitor has read-only access (R-13)."
    assignees = insp.ict_assignees(report.inspection) if form.kind == VisitReportForm.ICT else []
    can_view_data = (form.kind != VisitReportForm.ICT
                     or insp.can_edit_ict(report.inspection, request.user)[0]
                     or insp.can_review_cqac_evidence(report.inspection.case, request.user))
    return {"report": report, "case": report.inspection.case, "inspection": report.inspection, "form": form,
            "ict_assignees": assignees,
            "assigned_ict": ", ".join((u.get_full_name() or u.email) for u in assignees) or "not assigned yet",
            "template": form.template, "schema": form.template.schema or {},
            "sections": report_forms.field_rows(form, errors=errors or problems or {}) if can_view_data else [],
            "score": form.score or {} if can_view_data else {}, "can_edit": can_edit, "can_view_data": can_view_data,
            "edit_why": why, "errors": errors or {},
            "problems": problems or {}, "saved": request.GET.get("saved"),
            "kind": form.kind, "is_evaluation": form.kind == VisitReportForm.EVALUATION,
            "issue_count": len([k for k in (problems or errors or {}) if k != "__all__"]),
            "general_problem": (problems or {}).get("__all__", "")}


@require_or_404(cs.can_view_cases)
@require_http_methods(["GET", "POST"])
def report_form(request, pk, report_id, kind):
    report = _report_or_404(request, pk, report_id)
    form = _form_or_404(report, kind)
    if request.method == "GET":
        from inspection import services as insp

        if form.kind != VisitReportForm.ICT or insp.can_edit_ict(report.inspection, request.user)[0]:
            report_forms.recompute(form)
            form.refresh_from_db()
        return render_qa(request, "accreditation/case/report_form.html", _ctx(request, report, form))
    from inspection import services as insp

    if insp.is_monitor(request.user):
        raise PermissionDenied("The ICT monitor has read-only access (R-13).")
    op = request.POST.get("op", "save")
    try:
        values = report_forms.collect(request.POST, form.template,
                                      report.qa_request_programme.programme if form.report_id else None)
        uploads = {field["key"]: request.FILES.getlist(f"f__{field['key']}")
                   for field in form.template.field_map().values() if field["type"] == "file"}
        errors = report_forms.save(form, values, user=request.user, uploads=uploads)
        form.refresh_from_db()
        if errors:
            return render_qa(request, "accreditation/case/report_form.html",
                             _ctx(request, report, form, errors=errors), status=422)
        if op == "submit":
            report_forms.submit(form, user=request.user)
    except PermissionDenied as exc:
        raise PermissionDenied(str(exc)) from None
    except ValidationError as exc:
        form.refresh_from_db()
        problems = exc.message_dict if hasattr(exc, "error_dict") else {"__all__": " ".join(exc.messages)}
        problems = {k: " ".join(v) if isinstance(v, list) else v for k, v in problems.items()}
        return render_qa(request, "accreditation/case/report_form.html",
                         _ctx(request, report, form, problems=problems), status=422)
    url = reverse("qa_case:report_form", args=[pk, report_id, kind])
    return redirect(f"{url}?saved={'submitted' if op == 'submit' else 'draft'}")


@require_or_404(cs.can_view_cases)
@require_GET
def report_form_pdf(request, pk, report_id, kind):
    from qa_core.pdf_report import pdf_response

    report = _report_or_404(request, pk, report_id)
    form = _form_or_404(report, kind)
    if form.kind == VisitReportForm.ICT:
        from inspection import services as insp

        if not (insp.can_edit_ict(report.inspection, request.user)[0]
                or insp.can_review_cqac_evidence(report.inspection.case, request.user)):
            raise Http404
    report_forms.recompute(form)
    form.refresh_from_db()
    name = "programme-evaluation" if form.kind == VisitReportForm.EVALUATION else "ict-verification"
    response = pdf_response(f"{name}-{report.inspection.reference.replace('/', '-')}.pdf",
                            report_forms.pdf_bytes(form), inline=request.GET.get("download") != "1")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def summary_for(report):
    """Cards on the post-visit report page (never breaks the page)."""
    try:
        evaluation, ict = report_forms.forms_of(report)
        return {"available": report_forms.available(), "evaluation": evaluation, "ict": ict,
                "case_id": report.inspection.case_id}
    except Exception:  # pragma: no cover - defensive
        import logging

        logging.getLogger(__name__).exception("report forms summary failed for %s", report.pk)
        return None
