"""Internal Phase 5 screens, namespace "qa_insp", mounted at /qa/inspection/.

    Resource persons directory · Nominations and vetting · QA personnel ·
    Availability finder (Edit QA Mapping) · Mappings · Inspections and the
    visit calendar · Inspection detail (record the visit, correct attendance)
    · Post-visit report editor and endorsement · Endorsement queue · Service
    history (with export).

Every list has a search box, at least three filters, 25 rows per page with
"Show all" (capped by qa.ui.max_show_all_rows) and a bookmarkable URL; every
filter, page and save is an HTMX swap with the loading skeleton (§20).
Institution-bearing rows are read through ``services.visible_*`` (the SOP-03
visibility rule) or ``for_user``; object routes answer 404 out of scope.
The ICT monitor reads everything and writes nothing (R-13).
"""
from __future__ import annotations

import calendar
import datetime
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accreditation import case_services as cs
from qa_core.permissions import require, require_or_404
from qa_core.ui import paginate, render_qa, toast

from . import forms, rules, services
from .models import DUTY_CHOICES, PostVisitReport, QAMapping, QAPersonnel, ResourcePerson, ServiceRecord


def _msg(exc):
    if hasattr(exc, "message_dict"):
        return " ".join(m for msgs in exc.message_dict.values() for m in msgs)
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _attach(form, exc):
    """Put a service ValidationError on the form's fields (inline errors)."""
    if hasattr(exc, "error_dict"):
        for field, errors in exc.message_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc)


def writer(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if services.is_monitor(request.user):
            raise PermissionDenied("The ICT monitor has read-only access (R-13).")
        return view(request, *args, **kwargs)
    return wrapped


def _date(raw):
    try:
        return datetime.date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


def _hx_rows(request, full, ctx, panel, rows_partial):
    return render_qa(request, full, ctx, {"qa-panel": panel, "qa-rows": rows_partial})


# ===========================================================================
# Resource persons
# ===========================================================================
RP_STATUS_FILTER = [("", "Any status")] + ResourcePerson.STATUS_CHOICES
ELIGIBILITY_FILTER = [("", "Any eligibility"), ("ok", "Eligible"), ("lapsed", "No longer eligible")]


def _discipline_list():
    from institutions.models import Discipline

    return Discipline.objects.filter(is_active=True).order_by("sort_order", "name")


def _rp_rows(request, base):
    f = request.GET
    qs = base.prefetch_related("disciplines__discipline").select_related("home_institution", "staff").order_by(
        "full_name")
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(professional_body__icontains=q))
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("source"):
        qs = qs.filter(source=f["source"])
    if f.get("discipline"):
        qs = qs.filter(disciplines__discipline__code=f["discipline"]).distinct()
    rows = list(qs)
    for rp in rows:
        rp.lapsed = rules.no_longer_eligible(rp)
    if f.get("eligibility") == "lapsed":
        rows = [r for r in rows if r.lapsed]
    elif f.get("eligibility") == "ok":
        rows = [r for r in rows if not r.lapsed]
    page = paginate(request, rows)
    from . import workload

    limits_table = workload.table()
    for rp in page["rows"]:
        rp.totals = services.person_totals("resource_person", rp.pk)
        rp.load = workload.load(rp, rows=limits_table)
    return page


@require(services.can_view_people)
@require_GET
def rp_list(request):
    page = _rp_rows(request, ResourcePerson.objects.exclude(status="rejected") if not request.GET.get("status")
                    else ResourcePerson.objects.all())
    ctx = {"page": page, "filters": request.GET, "statuses": RP_STATUS_FILTER, "sources": ResourcePerson.SOURCE_CHOICES,
           "disciplines": _discipline_list(), "eligibility": ELIGIBILITY_FILTER,
           "counts": {"active": ResourcePerson.objects.filter(status="active").count(),
                      "nominated": ResourcePerson.objects.filter(status__in=("nominated", "vetted")).count()},
           "can_nominate": _can_manage(request.user)}
    return _hx_rows(request, "inspection/rp_list.html", ctx, "inspection/_rp_list_panel.html", "inspection/_rp_rows.html")


@require(services.can_view_people)
@require_GET
def nominations(request):
    base = ResourcePerson.objects.filter(status__in=(request.GET.get("status"),) if request.GET.get("status")
                                         else ("nominated", "vetted"))
    page = _rp_rows(request, base)
    ctx = {"page": page, "filters": request.GET, "statuses": RP_STATUS_FILTER, "sources": ResourcePerson.SOURCE_CHOICES,
           "disciplines": _discipline_list(), "eligibility": ELIGIBILITY_FILTER, "queue": True,
           "can_nominate": _can_manage(request.user)}
    return _hx_rows(request, "inspection/nominations.html", ctx, "inspection/_rp_list_panel.html",
                    "inspection/_rp_rows.html")


def _can_manage(user):
    return not services.is_monitor(user) and (user.is_superuser or user.has_perm("inspection.manage_resource_person"))


def _can_vet(user):
    return not services.is_monitor(user) and (user.is_superuser or user.has_perm("inspection.vet_resource_person"))


@require(services.can_view_people)
@require_http_methods(["GET", "POST"])
def rp_new(request):
    if request.method == "POST":
        if services.is_monitor(request.user) or not _can_manage(request.user):
            raise PermissionDenied("Nominating needs inspection.manage_resource_person.")
        form = forms.NominationForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                rp = services.nominate(form.service_data(), actor=request.user)
            except (ValidationError, PermissionDenied) as exc:
                _attach(form, exc) if isinstance(exc, ValidationError) else form.add_error(None, str(exc))
            else:
                response = render(request, "inspection/_rp_detail_panel.html", _rp_ctx(request, rp))
                response["HX-Push-Url"] = f"/qa/inspection/resource-persons/{rp.pk}/"
                return toast(response, "success", f"{rp.full_name} nominated",
                             "Upload the CV; a vetter approves the nomination.")
        response = render(request, "inspection/_rp_new_panel.html", {"form": form}, status=422)
        return toast(response, "error", "Not saved", "Check the highlighted fields.")
    return render_qa(request, "inspection/rp_new.html", {"form": forms.NominationForm(user=request.user)},
                     {"qa-panel": "inspection/_rp_new_panel.html"})


def _rp_or_404(pk):
    rp = ResourcePerson.objects.filter(pk=pk).select_related("home_institution", "staff__user__role",
                                                               "nominated_by", "vetted_by", "user").first()
    if rp is None:
        raise Http404
    return rp


def _rp_ctx(request, rp, **extra):
    user = request.user
    history = services.visible_service(user).filter(person_type="resource_person", person_id=rp.pk).select_related(
        "institution", "programme")[:50]
    ctx = {"rp": rp, "problems": rules.nomination_problems(rp), "lapsed": rules.no_longer_eligible(rp),
           "disciplines": list(rp.disciplines.select_related("discipline")), "files": list(rp.files.all()),
           "mappings": list(services.visible_mappings(user).filter(resource_person=rp).select_related(
               "case__institution", "qa_request_programme__programme")[:20]),
           "history": list(history), "totals": services.person_totals("resource_person", rp.pk),
           "can_manage": _can_manage(user), "can_vet": _can_vet(user) and rp.nominated_by_id != user.pk,
           "file_form": forms.FileForm(), "vet_form": forms.VetForm(), "status_form": forms.StatusForm(),
           "monitor": services.is_monitor(user)}
    ctx.update(extra)
    return ctx


@require_or_404(services.can_view_people)
@require_GET
def rp_detail(request, pk):
    rp = _rp_or_404(pk)
    return render_qa(request, "inspection/rp_detail.html", _rp_ctx(request, rp),
                     {"qa-panel": "inspection/_rp_detail_panel.html"})


def _rp_respond(request, rp, *, ok=("", ""), error="", status=200, **extra):
    rp.refresh_from_db()
    response = render(request, "inspection/_rp_detail_panel.html", _rp_ctx(request, rp, error=error, **extra),
                      status=status if error or status != 200 else 200)
    if error:
        return toast(response, "error", "Not done", error)
    return toast(response, "success", *ok)


@require_or_404(services.can_view_people)
@require_POST
@writer
def rp_action(request, pk, op):
    rp = _rp_or_404(pk)
    try:
        if op == "file":
            form = forms.FileForm(request.POST, request.FILES)
            if not form.is_valid():
                return _rp_respond(request, rp, error="Choose the document and the file.", status=422, file_form=form)
            services.add_file(rp, actor=request.user, upload=form.cleaned_data["file"], kind=form.cleaned_data["kind"])
            return _rp_respond(request, rp, ok=("File uploaded", ""))
        if op == "vet":
            form = forms.VetForm(request.POST)
            if not form.is_valid():
                return _rp_respond(request, rp, error="Choose approve or do not approve.", status=422, vet_form=form)
            approve = form.cleaned_data["decision"] == "approve"
            services.vet(rp, actor=request.user, approve=approve, remark=form.cleaned_data.get("remark", ""))
            return _rp_respond(request, rp, ok=("Approved — now active" if approve else "Not approved", ""))
        if op == "status":
            form = forms.StatusForm(request.POST)
            if not form.is_valid():
                return _rp_respond(request, rp, error="Choose a status.", status=422, status_form=form)
            services.set_status(rp, actor=request.user, status=form.cleaned_data["status"],
                                remark=form.cleaned_data.get("remark", ""))
            return _rp_respond(request, rp, ok=("Status changed", rp.get_status_display()))
        if op == "invite":
            services.invite_to_portal(rp, actor=request.user, request=request)
            return _rp_respond(request, rp, ok=("Portal invitation sent", rp.email))
    except PermissionDenied as exc:
        return _rp_respond(request, rp, error=_msg(exc), status=403)
    except ValidationError as exc:
        return _rp_respond(request, rp, error=_msg(exc), status=422)
    raise Http404


# ===========================================================================
# QA personnel (Edit QA Personnel)
# ===========================================================================
@require(services.can_view_people)
@require_http_methods(["GET", "POST"])
def personnel(request):
    form = forms.QAPersonnelForm()
    if request.method == "POST":
        if services.is_monitor(request.user):
            raise PermissionDenied("The ICT monitor has read-only access (R-13).")
        form = forms.QAPersonnelForm(request.POST)
        if form.is_valid():
            from qa_core import actions

            d = form.cleaned_data
            target = QAPersonnel.objects.filter(staff=d["staff"]).first()
            try:
                pa = actions.initiate("inspection.edit_qa_personnel", actor=request.user, target=target,
                                      payload={"staff_id": d["staff"].pk,
                                               "discipline_ids": [x.pk for x in d["disciplines"]],
                                               "is_verification_officer": d["is_verification_officer"],
                                               "is_team_lead_eligible": d["is_team_lead_eligible"],
                                               "active": d["active"], "reason": d["reason"]},
                                      reason=d["reason"])
            except PermissionDenied as exc:
                form.add_error(None, str(exc))
            except ValidationError as exc:
                form.add_error("staff", exc)
            else:
                response = render(request, "inspection/_personnel_panel.html",
                                  _personnel_ctx(request, forms.QAPersonnelForm()))
                done = pa.status == "applied"
                return toast(response, "success", "Saved" if done else f"Sent for confirmation ({pa.reference})",
                             "" if done else "A second officer confirms in Pending confirmations.")
        response = render(request, "inspection/_personnel_panel.html", _personnel_ctx(request, form), status=422)
        return toast(response, "error", "Not sent", "Check the highlighted fields.")
    return render_qa(request, "inspection/personnel.html", _personnel_ctx(request, form),
                     {"qa-panel": "inspection/_personnel_panel.html", "qa-rows": "inspection/_personnel_rows.html"})


def _personnel_ctx(request, form):
    f = request.GET
    qs = QAPersonnel.objects.select_related("staff__user__role", "staff__designation", "staff__department") \
        .prefetch_related("disciplines").order_by("staff__first_name")
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(staff__first_name__icontains=q) | Q(staff__last_name__icontains=q) | Q(staff__email__icontains=q))
    if f.get("discipline"):
        qs = qs.filter(disciplines__code=f["discipline"]).distinct()
    if f.get("active") in ("1", "0"):
        qs = qs.filter(active=f["active"] == "1")
    if f.get("lead") == "1":
        qs = qs.filter(is_team_lead_eligible=True)
    rows = list(qs)
    for qp in rows:
        qp.lapsed = rules.no_longer_eligible(qp)
    page = paginate(request, rows)
    from . import workload

    limits_table = workload.table()
    for qp in page["rows"]:
        qp.totals = services.person_totals("qa_personnel", qp.pk)
        qp.load = workload.load(qp, rows=limits_table)
    return {"page": page, "filters": f, "form": form, "disciplines": _discipline_list(),
            "can_edit": not services.is_monitor(request.user) and (
                request.user.is_superuser or request.user.has_perm("inspection.initiate_edit_qa_personnel"))}


# ===========================================================================
# Availability finder (Edit QA Mapping)
# ===========================================================================
def _finder_need(request, user):
    f = request.GET if request.method == "GET" else request.POST
    case = cs.visible_cases(user).filter(pk=f.get("case") or 0).select_related("institution", "qa_request").first()
    if case is None:
        return None, None, "Choose a case."
    from accreditation.models import QARequestProgramme

    rows = list(QARequestProgramme.objects.for_user(user).filter(qa_request=case.qa_request, status="active")
                .select_related("programme__category__discipline")) or services.case_rows(case)
    row = next((r for r in rows if str(r.pk) == str(f.get("row"))), rows[0] if rows else None)
    duty = f.get("duty") or "resource_inspection"
    try:
        need = services.need_for(case, row, duty, _date(f.get("start")), _date(f.get("end")))
    except ValidationError as exc:
        return case, rows, _msg(exc)
    return case, rows, need


@require(cs.can_view_cases)
@require_GET
def finder(request):
    user = request.user
    case, rows, need = _finder_need(request, user) if request.GET.get("case") else (None, None, None)
    ctx = {"filters": request.GET, "duties": DUTY_CHOICES,
           "cases": cs.visible_cases(user).exclude(status="closed").select_related("institution").order_by("-pk")[:200],
           "case": case, "rows": rows or [], "rules": rules.RULES,
           "can_map": not services.is_monitor(user) and (user.is_superuser or user.has_perm(
               "inspection.initiate_edit_qa_mapping"))}
    if isinstance(need, str):
        ctx["error"] = need
    elif need is not None:
        show_all = request.GET.get("show") == "all"
        cands = rules.find(need, kind=request.GET.get("kind") or "all", search=(request.GET.get("q") or "").strip(),
                           include_ineligible=show_all)
        ctx.update(need=need, page=paginate(request, cands), show_all=show_all)
    return render_qa(request, "inspection/finder.html", ctx,
                     {"qa-panel": "inspection/_finder_panel.html", "qa-rows": "inspection/_finder_rows.html"})


@require(cs.can_view_cases)
@require_POST
@writer
def finder_map(request):
    user = request.user
    case, rows, need = _finder_need(request, user)
    person = services.person_from_key(request.POST.get("person"))
    error = ""
    if isinstance(need, str) or need is None:
        error = need or "Choose a case."
    elif person is None:
        error = "Choose a person."
    else:
        try:
            mapping, pa = services.propose_mapping(case, need.row, duty=need.duty, person=person, actor=user,
                                                   start=need.start, end=need.end,
                                                   expected_version=request.POST.get("version"),
                                                   reason=request.POST.get("reason", ""))
        except (ValidationError, PermissionDenied) as exc:
            error = _msg(exc)
    # Re-render the finder with the same filters (the list now reflects the booking).
    ctx = {"filters": request.POST, "duties": DUTY_CHOICES,
           "cases": cs.visible_cases(user).exclude(status="closed").select_related("institution").order_by("-pk")[:200],
           "case": case, "rows": rows or [], "rules": rules.RULES, "can_map": True, "error": error}
    if not isinstance(need, str) and need is not None:
        cands = rules.find(need, kind=request.POST.get("kind") or "all")
        ctx.update(need=need, page=paginate(request, cands), show_all=False)
    response = render(request, "inspection/_finder_panel.html", ctx, status=422 if error else 200)
    if error:
        return toast(response, "error", "Not mapped", error)
    status = "Mapped" if pa.status == "applied" else f"Booked — waiting for confirmation ({pa.reference})"
    return toast(response, "success", status, f"{person} → {need.row.programme} ({mapping.get_duty_display()}).")


@require(cs.can_view_cases)
@require_GET
def mappings(request):
    f = request.GET
    qs = (services.visible_mappings(request.user)
          .select_related("case__institution", "qa_request_programme__programme", "resource_person",
                          "qa_personnel__staff").order_by("-from_date", "-pk"))
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(resource_person__full_name__icontains=q) | Q(qa_personnel__staff__first_name__icontains=q)
                       | Q(qa_personnel__staff__last_name__icontains=q) | Q(case__reference__icontains=q)
                       | Q(case__institution__name__icontains=q))
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("duty"):
        qs = qs.filter(duty=f["duty"])
    if f.get("kind") == "resource_person":
        qs = qs.filter(resource_person__isnull=False)
    elif f.get("kind") == "qa_personnel":
        qs = qs.filter(qa_personnel__isnull=False)
    ctx = {"page": paginate(request, qs), "filters": f, "statuses": QAMapping.STATUS_CHOICES, "duties": DUTY_CHOICES}
    return _hx_rows(request, "inspection/mappings.html", ctx, "inspection/_mappings_panel.html",
                    "inspection/_mappings_rows.html")


# ===========================================================================
# Inspections, calendar, detail
# ===========================================================================
INSPECTION_STATUS = [("", "Any status"), ("scheduled", "Scheduled"), ("closed", "Visit recorded"),
                     ("cancelled", "Cancelled")]


@require(cs.can_view_cases)
@require_GET
def inspections(request):
    f = request.GET
    qs = services.visible_inspections(request.user).select_related("case__institution", "case__owning_department",
                                                                   "team_lead").order_by("-scheduled_start")
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(case__institution__name__icontains=q)
                       | Q(case__institution__code__iexact=q) | Q(case__reference__icontains=q))
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("department"):
        qs = qs.filter(case__owning_department__code=f["department"])
    if f.get("when") == "upcoming":
        qs = qs.filter(scheduled_end__gte=timezone.localdate())
    elif f.get("when") == "past":
        qs = qs.filter(scheduled_end__lt=timezone.localdate())
    if f.get("mine") == "1":
        qs = qs.filter(team_lead=request.user)
    from staff.models import Department

    ctx = {"page": paginate(request, qs), "filters": f, "statuses": INSPECTION_STATUS,
           "departments": Department.objects.filter(pk__in=services.visible_inspections(request.user)
                                                    .values("case__owning_department")).order_by("name")}
    return _hx_rows(request, "inspection/inspections.html", ctx, "inspection/_inspections_panel.html",
                    "inspection/_inspections_rows.html")


@require(cs.can_view_cases)
@require_GET
def visit_calendar(request):
    today = timezone.localdate()
    try:
        year, month = (int(x) for x in (request.GET.get("month") or f"{today:%Y-%m}").split("-"))
        first = datetime.date(year, month, 1)
    except (TypeError, ValueError):
        first = today.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    # A recorded visit shows on its actual dates; a scheduled one on its planned dates.
    qs = (services.visible_inspections(request.user)
          .filter(Q(actual_start__isnull=True, scheduled_start__lte=last, scheduled_end__gte=first)
                  | Q(actual_start__isnull=False, actual_start__lte=last, actual_end__gte=first))
          .exclude(status="cancelled").select_related("case__institution", "team_lead"))
    if request.GET.get("department"):
        qs = qs.filter(case__owning_department__code=request.GET["department"])
    visits = list(qs)
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(first.year, first.month):
        days = []
        for day in week:
            days.append({"date": day, "in_month": day.month == first.month, "today": day == today,
                         "visits": [v for v in visits if (v.actual_start or v.scheduled_start) <= day
                                    <= (v.actual_end or v.scheduled_end)]})
        weeks.append(days)
    prev_month = (first - datetime.timedelta(days=1)).replace(day=1)
    next_month = last + datetime.timedelta(days=1)
    ctx = {"weeks": weeks, "first": first, "prev": f"{prev_month:%Y-%m}", "next": f"{next_month:%Y-%m}",
           "visits": visits, "filters": request.GET, "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
    return render_qa(request, "inspection/calendar.html", ctx, {"qa-panel": "inspection/_calendar_panel.html"})


def _inspection_or_404(request, pk):
    inspection = (services.visible_inspections(request.user).filter(pk=pk)
                  .select_related("case__institution", "case__owning_department", "team_lead", "completed_by").first())
    if inspection is None:
        raise Http404
    return inspection


def _inspection_ctx(request, inspection, **extra):
    user = request.user
    team = services.team(inspection)
    attendance = services.attendance(inspection)
    ok, why = services.can_complete(inspection, user)
    reports = list(services.visible_reports(user).filter(inspection=inspection).select_related(
        "qa_request_programme__programme", "submitted_by"))
    can_override = (not services.is_monitor(user) and inspection.status in ("completed", "closed")
                    and (user.is_superuser or inspection.team_lead_id == user.pk))
    ctx = {"inspection": inspection, "case": inspection.case, "team": team, "attendance": attendance,
           "can_complete": ok, "complete_why": why, "reports": reports, "can_override": can_override,
           "complete_form": forms.CompleteVisitForm(team=team, initial={
               "actual_start": inspection.scheduled_start, "actual_end": min(inspection.scheduled_end,
                                                                             timezone.localdate())}),
           "attendance_form": forms.AttendanceForm(attendance=attendance) if can_override else None,
           "service": list(services.visible_service(user).filter(inspection=inspection).order_by("pk"))
           if services.can_view_service(user) else [],
           "monitor": services.is_monitor(user)}
    ctx.update(extra)
    return ctx


@require_or_404(cs.can_view_cases)
@require_GET
def inspection_detail(request, pk):
    inspection = _inspection_or_404(request, pk)
    return render_qa(request, "inspection/inspection_detail.html", _inspection_ctx(request, inspection),
                     {"qa-panel": "inspection/_inspection_panel.html"})


@require_or_404(cs.can_view_cases)
@require_POST
@writer
def inspection_complete(request, pk):
    inspection = _inspection_or_404(request, pk)
    team = services.team(inspection)
    form = forms.CompleteVisitForm(request.POST, team=team)
    if form.is_valid():
        d = form.cleaned_data
        try:
            services.complete(inspection, actor=request.user, actual_start=d["actual_start"],
                              actual_end=d["actual_end"], absent=d.get("absent") or [])
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            _attach(form, exc)
        else:
            inspection.refresh_from_db()
            response = render(request, "inspection/_inspection_panel.html", _inspection_ctx(request, inspection))
            return toast(response, "success", "Visit recorded",
                         "Attendance saved and the service history written. The report can now be submitted.")
    response = render(request, "inspection/_inspection_panel.html",
                      _inspection_ctx(request, inspection, complete_form=form), status=422)
    return toast(response, "error", "Not saved", "Check the highlighted fields.")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
def inspection_attendance(request, pk):
    from qa_core import actions

    inspection = _inspection_or_404(request, pk)
    form = forms.AttendanceForm(request.POST, attendance=services.attendance(inspection))
    if form.is_valid():
        try:
            pa = actions.initiate("inspection.override_post_visit_personnel", actor=request.user, target=inspection,
                                  payload={"changes": form.changes(), "reason": form.cleaned_data["reason"]},
                                  reason=form.cleaned_data["reason"])
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, _msg(exc))
        else:
            inspection.refresh_from_db()
            response = render(request, "inspection/_inspection_panel.html", _inspection_ctx(request, inspection))
            done = pa.status == "applied"
            return toast(response, "success", "Attendance corrected" if done
                         else f"Sent for confirmation ({pa.reference})",
                         "" if done else "A second officer confirms in Pending confirmations.")
    response = render(request, "inspection/_inspection_panel.html",
                      _inspection_ctx(request, inspection, attendance_form=form), status=422)
    return toast(response, "error", "Not sent", "Check the highlighted fields.")


# ===========================================================================
# Post-visit report editor and endorsement
# ===========================================================================
def _report_or_404(request, pk):
    report = (services.visible_reports(request.user).filter(pk=pk)
              .select_related("inspection__case__institution", "inspection__team_lead", "submitted_by",
                              "qa_request_programme__programme__level").first())
    if report is None:
        raise Http404
    return report


def _report_ctx(request, report, **extra):
    user = request.user
    can_write, write_why = services.can_write(report, user)
    can_endorse, endorse_why = services.can_endorse(report, user)
    holders, link = services.report_holders(report)
    pinned = list(link.task.comments.filter(is_pinned=True).select_related("author").order_by("-pinned_at")[:3]) \
        if link else []
    ctx = {"report": report, "inspection": report.inspection, "case": report.inspection.case,
           "can_write": can_write, "write_why": write_why, "can_endorse": can_endorse, "endorse_why": endorse_why,
           "holders": holders, "link": link, "pinned": pinned,
           "layer": services.LAYER_LABEL.get(services.LAYER_FOR_STAGE.get(report.status, ""), ""),
           "form": forms.ReportForm(report=report), "endorse_form": forms.EndorseForm(),
           "sections": forms.REPORT_SECTIONS,
           "endorsements": services.endorsements_of(report),
           "rp_findings": services.rp_findings_for(report),
           "chain": _chain(report), "monitor": services.is_monitor(user)}
    ctx.update(extra)
    return ctx


CHAIN = [("draft", "Team (attendees)"), ("submitted", "Division Head"), ("division_endorsed", "Deputy Director"),
         ("deputy_verified", "Director"), ("director_endorsed", "Endorsed")]


def _chain(report):
    order = [c for c, _l in CHAIN]
    current = "draft" if report.status == "returned" else report.status
    idx = order.index(current) if current in order else 0
    return [{"code": c, "label": label, "state": "done" if i < idx else ("current" if i == idx else "todo")}
            for i, (c, label) in enumerate(CHAIN)]


@require_or_404(cs.can_view_cases)
@require_GET
def report_detail(request, pk):
    report = _report_or_404(request, pk)
    return render_qa(request, "inspection/report.html", _report_ctx(request, report),
                     {"qa-panel": "inspection/_report_panel.html"})


@require_or_404(cs.can_view_cases)
@require_POST
@writer
def report_save(request, pk):
    report = _report_or_404(request, pk)
    form = forms.ReportForm(request.POST, report=report)
    submit = request.POST.get("op") == "submit"
    if form.is_valid():
        try:
            services.save_report(report, user=request.user, summary=form.cleaned_data["summary"],
                                 findings=form.findings(), recommendation=form.cleaned_data["recommendation"])
            if submit:
                services.submit_report(report, user=request.user)
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            _attach(form, exc)
        else:
            report.refresh_from_db()
            response = render(request, "inspection/_report_panel.html", _report_ctx(request, report))
            return toast(response, "success", "Submitted to the Division Head" if submit else "Draft saved",
                         "Your task closed; the Division Head has a new one." if submit else "")
    response = render(request, "inspection/_report_panel.html", _report_ctx(request, report, form=form), status=422)
    return toast(response, "error", "Not saved", "Check the highlighted fields.")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
def report_endorse(request, pk):
    report = _report_or_404(request, pk)
    form = forms.EndorseForm(request.POST)
    if form.is_valid():
        action = form.cleaned_data["action"]
        try:
            services.endorse(report, user=request.user, action=action, content=form.cleaned_data.get("content", ""))
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            _attach(form, exc)
        else:
            report.refresh_from_db()
            response = render(request, "inspection/_report_panel.html", _report_ctx(request, report))
            title = {"endorse": "Endorsed", "add_content": "Content added", "return": "Returned"}[action]
            return toast(response, "success", title, "")
    response = render(request, "inspection/_report_panel.html",
                      _report_ctx(request, report, endorse_form=form), status=422)
    return toast(response, "error", "Not done", "Check the highlighted fields.")


ENDORSE_STATUS = [("", "Awaiting endorsement"), ("submitted", "With the Division Head"),
                  ("division_endorsed", "With the Deputy Director"), ("deputy_verified", "With the Director"),
                  ("returned", "Returned"), ("draft", "Being written"), ("director_endorsed", "Endorsed"),
                  ("all", "Every report")]


@require(cs.can_view_cases)
@require_GET
def endorsements(request):
    f = request.GET
    qs = (services.visible_reports(request.user).select_related(
        "inspection__case__institution", "qa_request_programme__programme", "submitted_by").order_by("-updated_at"))
    status = f.get("status", "")
    if status == "":
        qs = qs.filter(status__in=("submitted", "division_endorsed", "deputy_verified"))
    elif status != "all":
        qs = qs.filter(status=status)
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(inspection__case__institution__name__icontains=q) | Q(inspection__reference__icontains=q)
                       | Q(qa_request_programme__programme__name__icontains=q))
    if f.get("recommendation"):
        qs = qs.filter(recommendation=f["recommendation"])
    rows = list(qs)
    if f.get("mine") == "1":
        rows = [r for r in rows if request.user in services.report_holders(r)[0]]
    page = paginate(request, rows)
    for r in page["rows"]:
        r.layer = services.LAYER_LABEL.get(services.LAYER_FOR_STAGE.get(r.status, ""), "")
        r.holders = services.report_holders(r)[0]
    ctx = {"page": page, "filters": f, "statuses": ENDORSE_STATUS,
           "recommendations": PostVisitReport.RECOMMENDATION_CHOICES}
    return _hx_rows(request, "inspection/endorsements.html", ctx, "inspection/_endorsements_panel.html",
                    "inspection/_endorsements_rows.html")


# ===========================================================================
# Service history (§9)
# ===========================================================================
def _service_qs(request):
    f = request.GET
    qs = services.visible_service(request.user).select_related("institution", "programme", "discipline")
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(person_name__icontains=q)
    if f.get("person_type"):
        qs = qs.filter(person_type=f["person_type"])
    if f.get("institution"):
        qs = qs.filter(Q(institution__name__icontains=f["institution"]) | Q(institution__code__iexact=f["institution"]))
    if f.get("programme"):
        qs = qs.filter(programme__name__icontains=f["programme"])
    if f.get("discipline"):
        qs = qs.filter(discipline__code=f["discipline"])
    if f.get("duty"):
        qs = qs.filter(duty=f["duty"])
    start, end = _date(f.get("from")), _date(f.get("to"))
    if start:
        qs = qs.filter(served_to__gte=start)
    if end:
        qs = qs.filter(served_from__lte=end)
    return qs.order_by("-served_to", "-pk")


@require(services.can_view_service)
@require_GET
def service_history(request):
    qs = _service_qs(request)
    totals = list(qs.values("person_type", "person_id", "person_name").annotate(
        visits=Sum("visit_count"), ssqs=Sum("ssq_count"), last=Max("served_to")).order_by("-visits", "person_name")[:12])
    ctx = {"page": paginate(request, qs), "filters": request.GET, "duties": DUTY_CHOICES,
           "person_types": [("", "Everyone")] + list(ServiceRecord.PERSON_TYPES), "disciplines": _discipline_list(),
           "totals": totals, "can_export": request.user.is_superuser or request.user.has_perm(
               "inspection.export_service_history")}
    return _hx_rows(request, "inspection/service_history.html", ctx, "inspection/_service_panel.html",
                    "inspection/_service_rows.html")


@require(services.can_view_service)
@require_GET
def service_export(request):
    from core.excel import write_export

    if not (request.user.is_superuser or request.user.has_perm("inspection.export_service_history")):
        raise PermissionDenied("Exporting needs inspection.export_service_history.")
    rows = [[r.person_name, r.get_person_type_display(), r.get_duty_display(), r.role_on_team,
             getattr(r.institution, "name", ""), getattr(r.programme, "name", ""), getattr(r.discipline, "name", ""),
             r.served_from, r.served_to, r.visit_count, r.ssq_count, r.source, r.reason]
            for r in _service_qs(request)[:5000]]
    data = write_export(["Person", "Type", "Duty", "Role", "Institution", "Programme", "Discipline", "From", "To",
                         "Visits", "SSQs", "Source", "Note"], rows, sheet_title="Service history")
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="service-history.xlsx"'
    return response


@require(services.can_view_service)
@require_GET
def service_export_pdf(request):
    """Phase 8: the service history as a PDF (same filters as the screen), with
    each person's totals first."""
    from core import audit
    from qa_core.pdf_report import Report, pdf_response

    if not (request.user.is_superuser or request.user.has_perm("inspection.export_service_history")):
        raise PermissionDenied("Exporting needs inspection.export_service_history.")
    qs = _service_qs(request)
    totals = list(qs.values("person_type", "person_name").annotate(
        visits=Sum("visit_count"), ssqs=Sum("ssq_count"), last=Max("served_to")).order_by("-visits", "person_name"))
    f = request.GET
    filters = ", ".join(f"{k} {v}" for k, v in f.items() if v and k not in ("page", "all"))
    rep = Report("Service history", subject="Resource persons and QA personnel", generated_by=request.user,
                 cover=False, landscape=True)
    rep.h1("Service history")
    if filters:
        rep.para(f"Filters: {filters}", "small")
    rep.h2("Totals per person")
    rep.table(["Person", "Type", "Visits", "SSQs reviewed", "Last served"],
              [[t["person_name"], dict(ServiceRecord.PERSON_TYPES).get(t["person_type"], t["person_type"]),
                t["visits"] or 0, t["ssqs"] or 0, t["last"]] for t in totals],
              widths=[0.35, 0.2, 0.12, 0.15, 0.18], empty="No service recorded.")
    rep.h2("Records")
    rep.table(["Person", "Duty", "Institution", "Programme", "From", "To", "Visits", "SSQs", "Note"],
              [[r.person_name, r.get_duty_display(), getattr(r.institution, "name", ""),
                getattr(r.programme, "name", ""), r.served_from, r.served_to, r.visit_count, r.ssq_count,
                r.reason] for r in qs[:5000]],
              widths=[0.15, 0.11, 0.17, 0.14, 0.08, 0.08, 0.06, 0.06, 0.15])
    audit.log("other", request.user, actor=request.user, note="exported the service history (PDF)")
    return pdf_response(f"service-history-{timezone.localdate().isoformat()}.pdf", rep.build())
