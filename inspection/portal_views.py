"""Phase 5 portal pages.

Resource persons (user type ``resource_person``): My engagements (with the
findings form), My service history, Profile & CV. Institutions: a read-only
list of their scheduled and recorded inspections.

Every query goes through ``inspection.services`` (rp_* helpers filter by the
signed-in person; institution_inspections uses ``objects.for_user``).
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from qa_core.portal_views import _shell_context, institution_required, portal_login_required
from qa_core.ui import is_htmx, paginate, toast

from . import forms
from . import services
from .models import DUTY_CHOICES, PostVisitPersonnel, PostVisitReport, QAMapping, ResourcePersonFile

ENGAGEMENT_STATUS = [("all", "All engagements"), ("live", "Upcoming and current"), ("released", "Completed")]


def rp_required(view):
    @portal_login_required
    def wrapped(request, *args, **kwargs):
        rp = services.rp_of(request.user)
        if rp is None:
            raise Http404("This page is for resource persons.")
        request.resource_person = rp
        return view(request, *args, **kwargs)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _rows_or_page(request, target, rows_template, page_template, ctx):
    if is_htmx(request) and request.headers.get("HX-Target") == target:
        return render(request, rows_template, ctx)
    return render(request, page_template, ctx)


# ---------------------------------------------------------------------------
# Resource person: engagements + findings
# ---------------------------------------------------------------------------
@rp_required
@require_http_methods(["GET", "POST"])
def engagements(request):
    rp = request.resource_person
    errors, error_for, report_error_for, member_form = {}, None, None, None
    if request.method == "POST":
        mapping = next((m for m in services.rp_engagements(rp) if str(m.pk) == request.POST.get("mapping")), None)
        if mapping is None:
            raise Http404
        if request.POST.get("action") == "member_report":
            inspection = services.live_inspection(mapping.case)
            report = (PostVisitReport.unscoped.filter(
                inspection=inspection, qa_request_programme=mapping.qa_request_programme).first()
                if inspection is not None else None)
            member_report = services.member_report_for_user(report, request.user) if report else None
            if member_report is None:
                errors = {"__all__": ["Your individual report is not available until the recorded visit is "
                                      "assigned to your account."]}
                report_error_for = mapping.pk
            else:
                member_form = forms.MemberReportForm(request.POST, member_report=member_report)
                if member_form.is_valid():
                    try:
                        services.save_member_report(
                            member_report, user=request.user, summary=member_form.cleaned_data["summary"],
                            findings=member_form.findings(),
                            recommendation=member_form.cleaned_data["recommendation"],
                            submit=request.POST.get("op") == "submit")
                    except (ValidationError, PermissionDenied) as exc:
                        if hasattr(exc, "error_dict"):
                            for field, messages in exc.message_dict.items():
                                member_form.add_error(field if field in member_form.fields else None, messages)
                        else:
                            member_form.add_error(None, exc)
                        errors = member_form.errors
                        report_error_for = mapping.pk
                else:
                    errors = member_form.errors
                    report_error_for = mapping.pk
        else:
            try:
                services.submit_findings(mapping, user=request.user, text=request.POST.get("findings", ""))
            except (ValidationError, PermissionDenied) as exc:
                errors = getattr(exc, "message_dict", None) or {"findings": exc.messages if hasattr(exc, "messages")
                                                                else [str(exc)]}
                error_for = mapping.pk
    f = request.GET
    qs = services.rp_engagements(rp)
    status = f.get("status") or "all"
    if status == "live":
        qs = qs.filter(status__in=QAMapping.LIVE)
    elif status == "released":
        qs = qs.filter(status="released")
    if f.get("duty"):
        qs = qs.filter(duty=f["duty"])
    today = timezone.localdate()
    if f.get("when") == "upcoming":
        qs = qs.filter(to_date__gte=today)
    elif f.get("when") == "past":
        qs = qs.filter(to_date__lt=today)
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(case__institution__name__icontains=q) | Q(qa_request_programme__programme__name__icontains=q))
    page = paginate(request, list(qs))
    for m in page["rows"]:
        m.can_write, m.write_why = services.findings_open(m)
        inspection = services.live_inspection(m.case)
        m.visit_report = (PostVisitReport.unscoped.filter(
            inspection=inspection, qa_request_programme=m.qa_request_programme).first()
            if inspection is not None else None)
        m.visit_attendance = (PostVisitPersonnel.unscoped.filter(
            inspection=inspection, resource_person=rp).first()
            if inspection is not None and inspection.status == "closed" else None)
        m.member_report = (services.member_report_for_user(m.visit_report, request.user)
                           if m.visit_report is not None else None)
        m.can_write_member_report, m.member_report_why = (
            services.can_write_member_report(m.member_report, request.user)
            if m.member_report is not None else (False, "Your individual report unlocks after attendance is recorded."))
        m.member_form = (member_form if report_error_for == m.pk and member_form is not None else
                         forms.MemberReportForm(member_report=m.member_report) if m.member_report else None)
    ctx = _shell_context(request, "engagements", page=page, filters=f, status=status, statuses=ENGAGEMENT_STATUS,
                         duties=DUTY_CHOICES, rp=rp, errors=errors, error_for=error_for,
                         report_error_for=report_error_for,
                         draft=request.POST.get("findings", "") if errors else "")
    if request.method == "POST":
        response = render(request, "qa_portal/resource_person/engagements.html", ctx, status=422 if errors else 200)
        if errors:
            return toast(response, "error", "Not saved", " ".join(sum(errors.values(), [])))
        if request.POST.get("action") == "member_report":
            submitted = request.POST.get("op") == "submit"
            return toast(response, "success", "Report submitted to the Team Lead" if submitted else "Draft saved",
                         "The Team Lead can now review your programme findings." if submitted else "")
        return toast(response, "success", "Findings saved", "The Team Lead sees them on the report.")
    return _rows_or_page(request, "rp-rows", "qa_portal/resource_person/_engagement_rows.html",
                         "qa_portal/resource_person/engagements.html", ctx)


@rp_required
@require_GET
def history(request):
    rp = request.resource_person
    f = request.GET
    qs = services.rp_service(rp)
    if f.get("duty"):
        qs = qs.filter(duty=f["duty"])
    if f.get("year", "").isdigit():
        qs = qs.filter(served_from__year=int(f["year"]))
    if f.get("work") == "visits":
        qs = qs.filter(visit_count__gt=0)
    elif f.get("work") == "ssq":
        qs = qs.filter(ssq_count__gt=0)
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(institution__name__icontains=q) | Q(programme__name__icontains=q))
    rows = list(qs)
    years = sorted({r.served_from.year for r in services.rp_service(rp)}, reverse=True)
    ctx = _shell_context(request, "service", page=paginate(request, rows), filters=f, duties=DUTY_CHOICES,
                         years=years, rp=rp, visits=sum(r.visit_count for r in rows),
                         ssqs=sum(r.ssq_count for r in rows))
    return _rows_or_page(request, "rp-rows", "qa_portal/resource_person/_history_rows.html",
                         "qa_portal/resource_person/history.html", ctx)


@rp_required
@require_http_methods(["GET", "POST"])
def profile(request):
    rp = request.resource_person
    error = ""
    if request.method == "POST":
        try:
            services.add_file(rp, actor=request.user, upload=request.FILES.get("file"),
                              kind=request.POST.get("kind", "cv"))
        except (ValidationError, PermissionDenied) as exc:
            error = " ".join(getattr(exc, "messages", [str(exc)]))
    ctx = _shell_context(request, "profile", rp=rp, files=services.rp_files(rp), kinds=ResourcePersonFile.KIND_CHOICES,
                         disciplines=list(rp.disciplines.select_related("discipline")), error=error,
                         lapsed=services.rules.nomination_problems(rp) if services.rules.no_longer_eligible(rp) else [])
    response = render(request, "qa_portal/resource_person/profile.html", ctx, status=422 if error else 200)
    if request.method == "POST":
        return toast(response, "error", "Not uploaded", error) if error else toast(
            response, "success", "Uploaded", "NBTE will see the new document on your record.")
    return response


# ---------------------------------------------------------------------------
# Institution: scheduled inspections (read-only)
# ---------------------------------------------------------------------------
@institution_required
@require_GET
def institution_inspections(request):
    f = request.GET
    qs = services.institution_inspections(request.user)
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(case__reference__icontains=q))
    today = timezone.localdate()
    if f.get("when") == "upcoming":
        qs = qs.filter(scheduled_start__gte=today)
    elif f.get("when") == "past":
        qs = qs.filter(scheduled_start__lt=today)
    if f.get("year", "").isdigit():
        qs = qs.filter(scheduled_start__year=int(f["year"]))
    years = sorted({d.year for d in services.institution_inspections(request.user)
                    .values_list("scheduled_start", flat=True)}, reverse=True)
    ctx = _shell_context(request, "inspections", page=paginate(request, list(qs)), filters=f, years=years,
                         statuses=[("scheduled", "Scheduled"), ("completed", "Completed"), ("closed", "Recorded"),
                                   ("cancelled", "Cancelled")])
    return _rows_or_page(request, "insp-rows", "qa_portal/inspections/_rows.html", "qa_portal/inspections/list.html",
                         ctx)
