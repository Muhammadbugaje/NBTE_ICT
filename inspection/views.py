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
import hashlib
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from tasks.services import visible_comments as task_visible_comments  # Phase 0 (H18)
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accreditation import case_services as cs
from qa_core import flow
from qa_core.permissions import require, require_or_404
from qa_core.ui import paginate, render_qa, toast

from . import forms, rules, services
from .models import (
    DUTY_CHOICES, PostVisitMemberReport, PostVisitPersonnel, PostVisitReport, QAMapping, QAPersonnel,
    ResourcePerson, ServiceRecord,
)


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


def _live_token(parts):
    """A short, stable fingerprint of the values that make a page stale."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


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
          .select_related("case__institution", "case__owning_department", "case__institution__handling_department",
                          "qa_request_programme__programme", "resource_person", "qa_personnel__staff")
          .order_by("-from_date", "-pk"))
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
    for m in ctx["page"]["rows"]:
        m.approval_state, m.approval_label = services.mapping_approval_state(m)
        m.can_director_approve = services.can_approve_mapping(m, request.user, "director")[0]
        m.can_es_approve = services.can_approve_mapping(m, request.user, "es")[0]
    return _hx_rows(request, "inspection/mappings.html", ctx, "inspection/_mappings_panel.html",
                    "inspection/_mappings_rows.html")


@require(cs.can_view_cases)
@require_POST
def mapping_decide(request, pk):
    """v2.7: the Director then the ES approve (or send back) a mapping."""
    from django.http import HttpResponse
    from django.urls import reverse

    mapping = services.visible_mappings(request.user).filter(pk=pk).first()
    if mapping is None:
        raise Http404
    layer = request.POST.get("layer", "director")
    approve = request.POST.get("decision") != "reject"
    try:
        services.approve_mapping(mapping, actor=request.user, layer=layer, approve=approve,
                                 remark=request.POST.get("remark", ""))
    except (PermissionDenied, ValidationError) as exc:
        response = HttpResponse(status=422)
        response["HX-Redirect"] = reverse("qa_insp:mappings")
        return response
    response = HttpResponse(status=204)
    response["HX-Redirect"] = reverse("qa_insp:mappings")
    return response


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
def inspections_live(request):
    """Cheap version stamp for the inspections list (polled)."""
    agg = services.visible_inspections(request.user).aggregate(
        latest=Max("updated_at"), closed=Max("closed_at"), total=Count("pk"))
    token = _live_token([agg["latest"], agg["closed"], agg["total"]])
    if request.GET.get("v") == token:
        return JsonResponse({"v": token, "unchanged": True})
    return JsonResponse({"v": token})


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


@require(cs.can_view_cases)
@require_GET
def calendar_live(request):
    """Cheap version stamp for the visit calendar (polled)."""
    agg = services.visible_inspections(request.user).aggregate(
        latest=Max("updated_at"), closed=Max("closed_at"), total=Count("pk"))
    token = _live_token([agg["latest"], agg["closed"], agg["total"]])
    if request.GET.get("v") == token:
        return JsonResponse({"v": token, "unchanged": True})
    return JsonResponse({"v": token})


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
@require_GET
def inspection_live(request, pk):
    """Cheap version stamp for the inspection panel (polled, never cached).

    Covers the visit itself, its planned team, the recorded attendance, the
    post-visit reports and endorsements, and the service history it wrote.
    The client only swaps the panel when this stamp moves and the viewer is
    not editing.
    """
    from accreditation.models import VisitConflictDeclaration, VisitFinding

    from .models import InspectionTeamMember, PostVisitPersonnel, ReportEndorsement

    inspection = _inspection_or_404(request, pk)
    parts = [inspection.updated_at, inspection.status, inspection.actual_start, inspection.actual_end,
             inspection.closed_at, inspection.completed_by_id]
    for model, lookup, field in (
        (InspectionTeamMember, {"inspection": inspection}, "updated_at"),
        (PostVisitPersonnel, {"inspection": inspection}, "updated_at"),
        (PostVisitReport, {"inspection": inspection}, "updated_at"),
        (ReportEndorsement, {"report__inspection": inspection}, "at"),
        (ServiceRecord, {"inspection": inspection}, "created_at"),
        (VisitConflictDeclaration, {"inspection": inspection}, "updated_at"),
        (VisitFinding, {"report__inspection": inspection}, "updated_at"),
    ):
        parts.append(model.unscoped.filter(**lookup).aggregate(latest=Max(field))["latest"])
    token = _live_token(parts)
    if request.GET.get("v") == token:
        return JsonResponse({"v": token, "unchanged": True})
    return JsonResponse({"v": token, "status": inspection.status})


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
    from accreditation import visit_report

    user = request.user
    can_write, write_why = services.can_write(report, user)
    can_endorse, endorse_why = services.can_endorse(report, user)
    can_review_members = bool(user.is_superuser or report.inspection.team_lead_id == user.pk)
    member_roster = services.member_report_roster(report, user=user)
    bound_member_form = extra.get("member_form")
    for row in member_roster:
        row.can_edit = bool(row.member_report and services.can_write_member_report(row.member_report, user)[0])
        if row.can_edit:
            row.form = (bound_member_form if row.member_report.author_id == user.pk and bound_member_form
                        else forms.MemberReportForm(member_report=row.member_report))
        else:
            row.form = None
    holders, link = services.report_holders(report)
    pinned = list(task_visible_comments(link.task, user).filter(is_pinned=True).select_related("author")
                  .order_by("-pinned_at")[:3]) \
        if link else []
    member_counts = services.member_report_counts(report)
    my_member_row = next((row for row in member_roster
                          if getattr(row.attendee.user, "pk", None) == user.pk), None)
    skipped_rows = [row for row in member_roster
                    if row.member_report and row.member_report.status == "skipped"]
    ctx = {"report": report, "inspection": report.inspection, "case": report.inspection.case,
           "can_write": can_write, "write_why": write_why, "can_endorse": can_endorse, "endorse_why": endorse_why,
           "can_preview_compiled_pdf": bool(
               (report.status == "deputy_verified" and can_endorse)
               or (report.status == "director_endorsed"
                   and services.can_review_cqac_evidence(report.inspection.case, user))),
           "holders": holders, "link": link, "pinned": pinned,
           "member_roster": member_roster, "can_review_members": can_review_members,
           "member_counts": member_counts, "skipped_rows": skipped_rows,
           "is_team_lead": bool(user.is_superuser or report.inspection.team_lead_id == user.pk),
           "my_member_row": my_member_row,
           "member_review_form": extra.get("member_review_form") or forms.MemberReviewForm(),
           "review_member_pk": extra.get("review_member_pk"),
           "review_attendee_pk": extra.get("review_attendee_pk"),
           "compile_pending": services.member_reports_ready(report),
           "layer": services.LAYER_LABEL.get(services.LAYER_FOR_STAGE.get(report.status, ""), ""),
           "form": forms.ReportForm(report=report), "endorse_form": forms.EndorseForm(),
           "sections": forms.REPORT_SECTIONS,
           "endorsements": services.endorsements_of(report),
           "rp_findings": services.rp_findings_for(report),
           "source_ssq": visit_report.submitted_ssq(report.qa_request_programme)[0],
           "chain": _chain(report), "monitor": services.is_monitor(user)}
    ctx.update(_vvt_ctx(report, user))
    from accreditation.report_form_views import summary_for  # v2.4: template-driven report forms

    ctx["report_forms"] = summary_for(report)
    ctx.update(extra)
    return ctx


def _vvt_ctx(report, user):
    """v2.2 X40: the VVT/001 parts of the report page (never breaks the page)."""
    try:
        from accreditation import visit_report as vr
        from accreditation.models import VisitFinding

        can_write, write_why = services.can_write(report, user)
        declared = vr.declarations(report.inspection)
        people = []
        for p in vr.panel_people(report.inspection):
            key = vr._key(staff=p.staff, resource_person=p.resource_person)
            people.append({"p": p, "d": declared.get(key)})
        return {"vvt": {"form": report.FORM_REFERENCE, "prefill": vr.prefill(report), "people": people,
                        "can_write": can_write, "write_why": write_why,
                        "missing": len(vr.missing_declarations(report.inspection)),
                        "conflicts": len(vr.conflicting(report.inspection)),
                        "rows": vr.catalogue_rows(report),
                        "extra_findings": list(VisitFinding.unscoped.filter(report=report, item__isnull=True)),
                        "can_fine": bool(user.is_superuser or user.has_perm("payments.view_obligation")),
                        "fine_rate": vr.config.get("qa.visit.fine_per_falsified_item", 500000),
                        "gps_auto": bool(vr.config.get("qa.visit.gps_auto_capture", True)),
                        "gps_max_km": vr.config.get("qa.visit.gps_max_km", 5),
                        "gps_offset_m": vr.config.get("qa.visit.gps_offset_m", 200),
                        "gps_accuracy_max_m": vr.config.get("qa.visit.gps_accuracy_max_m", 150)}}
    except Exception:
        import logging

        logging.getLogger(__name__).exception("VVT/001 context failed for report %s", report.pk)
        return {"vvt": None}


CHAIN_LABELS = {"submitted": "Division Head", "division_endorsed": "Deputy Director",
                "deputy_verified": "Director", "director_endorsed": "Endorsed"}


def _chain(report):
    stages = services.REPORT_ENDORSEMENT_STAGES
    current = report.status
    if current in ("draft", "returned"):
        first = services._report_submission_stage(report)
        route = ["draft", first]
        route.extend(code for code in stages[stages.index(first) + 1:]
                     if services._report_stage_users(report, code))
    else:
        instance = flow.instance_for(report, "post_visit_report")
        visited = list(instance.events.order_by("at", "pk").values_list("to_stage", flat=True)) \
            if instance is not None else []
        route = ["draft"] if "draft" in visited else []
        route.extend(code for code in stages if code in visited or code == current)
        if current in stages:
            route.extend(code for code in stages[stages.index(current) + 1:]
                         if services._report_stage_users(report, code))
        if current == "director_endorsed":
            route.append(current)
    if not route:
        route = ["draft"]
    labels = {"draft": "Team Lead compilation", "returned": "Returned to Team Lead",
              **CHAIN_LABELS}
    current_code = "draft" if current == "returned" else current
    order = list(dict.fromkeys(route))
    idx = order.index(current_code) if current_code in order else -1
    return [{"code": code, "label": labels.get(code, code), "state":
             "done" if index < idx or current == "director_endorsed" else
             ("current" if code == current_code else "todo")}
            for index, code in enumerate(order)]


@require_or_404(cs.can_view_cases)
@require_GET
def report_detail(request, pk):
    report = _report_or_404(request, pk)
    return render_qa(request, "inspection/report.html", _report_ctx(request, report),
                     {"qa-panel": "inspection/_report_panel.html"})


@require_or_404(cs.can_view_cases)
@require_GET
def report_live(request, pk):
    """Cheap version stamp for the post-visit report panel (polled)."""
    from accreditation.models import VisitConflictDeclaration, VisitFinding

    from .models import PostVisitMemberReport, PostVisitMemberReview, PostVisitPersonnel, ReportEndorsement

    report = _report_or_404(request, pk)
    inspection = report.inspection
    parts = [report.updated_at, report.status, report.version, report.submitted_at, report.recommendation,
             report.recommendation_note, report.fines_total, report.gps_at,
             inspection.updated_at, inspection.status, inspection.actual_start, inspection.actual_end,
             inspection.closed_at]
    for model, lookup, field in (
        (ReportEndorsement, {"report": report}, "at"),
        (PostVisitMemberReport, {"report": report}, "updated_at"),
        (PostVisitMemberReview, {"member_report__report": report}, "at"),
        (VisitFinding, {"report": report}, "updated_at"),
        (VisitConflictDeclaration, {"inspection": inspection}, "updated_at"),
        (PostVisitPersonnel, {"inspection": inspection}, "updated_at"),
    ):
        parts.append(model.unscoped.filter(**lookup).aggregate(latest=Max(field))["latest"])
    token = _live_token(parts)
    if request.GET.get("v") == token:
        return JsonResponse({"v": token, "unchanged": True})
    return JsonResponse({"v": token, "status": report.status})


@require_or_404(cs.can_view_cases)
@require_GET
def report_pdf(request, pk):
    """Preview or download the compiled report at its authorised decision stage."""
    from accreditation.models import VisitFinding
    from core import audit
    from qa_core.pdf_report import Report, pdf_response

    report = _report_or_404(request, pk)
    director_review = (report.status == "deputy_verified"
                       and services.can_endorse(report, request.user)[0])
    decision_review = (report.status == "director_endorsed"
                       and services.can_review_cqac_evidence(report.inspection.case, request.user))
    if not (director_review or decision_review):
        raise Http404

    inspection = report.inspection
    case = inspection.case
    institution = case.institution
    programme = report.qa_request_programme.programme
    rep = Report(f"Post-visit report — {programme}", subject=institution.name, generated_by=request.user,
                 cover=False)
    rep.h1("Compiled post-visit report")
    rep.para("Restricted decision copy. Review access is limited to the assigned Director before endorsement "
             "and active CQAC/Executive Secretary decision holders after Director endorsement.")
    rep.kv([
        ("Institution", institution.name),
        ("Institution code", institution.code),
        ("Programme", programme.name),
        ("Case", case.reference),
        ("Visit", inspection.reference),
        ("Visit dates", f"{inspection.actual_start or inspection.scheduled_start or '—'} – "
                        f"{inspection.actual_end or inspection.scheduled_end or '—'}"),
        ("Status", report.status),
        ("Version", report.version),
        ("Submitted by", report.submitted_by or "—"),
        ("Submitted on", report.submitted_at or "—"),
    ])

    if report.form_reference:
        rep.h2(report.form_reference)
    sections = report.sections if isinstance(report.sections, dict) else {}
    for section, content in sections.items():
        rep.h2(f"Visit report — section {section}")
        if isinstance(content, dict):
            rep.kv([(str(key).replace("_", " ").title(), _pdf_report_value(value))
                    for key, value in content.items()])
        else:
            rep.para(_pdf_report_value(content))

    from .forms import REPORT_SECTIONS

    rep.h2("Team Lead's programme compilation")
    rep.table(["Section", "Findings"],
              [[label, (report.findings or {}).get(key, "") or "—"] for key, label, _help in REPORT_SECTIONS],
              widths=[0.25, 0.75])
    rep.kv([("Summary", report.summary or "—"),
            ("Team recommendation", report.get_recommendation_display() or "—")], cols=1)

    member_rows = services.member_report_roster(report, user=request.user)
    if member_rows:
        rep.h2("Attendee and member contributions")
        rep.table(
            ["Attendee", "Status", "Recommendation", "Summary", "Team Lead review"],
            [[str(row.person), row.member_report.get_status_display() if row.member_report else
              ("Absent" if not row.attendee.attended else "No report"),
              row.member_report.get_recommendation_display() if row.member_report else "—",
              row.member_report.summary if row.member_report else "—",
              row.member_report.review_note if row.member_report else "—"]
             for row in member_rows],
            widths=[0.17, 0.13, 0.16, 0.3, 0.24])
        for row in member_rows:
            member_report = row.member_report
            if member_report is None or not member_report.findings:
                continue
            rep.h2(f"Contribution — {row.person}")
            rep.table(["Section", "Finding"],
                      [[label, member_report.findings.get(key, "") or "—"]
                       for key, label, _help in REPORT_SECTIONS], widths=[0.25, 0.75])

    findings = list(VisitFinding.unscoped.filter(report=report).order_by("kind", "name"))
    if findings:
        rep.h2("Facilities and equipment verified")
        rep.table(
            ["Item", "Type", "Declared", "Found", "Finding", "Remark"],
            [[item.name, item.get_kind_display(), item.declared_quantity, item.found_quantity,
              "Falsified" if item.is_falsified else "Verified", item.remark or "—"] for item in findings],
            widths=[0.27, 0.12, 0.12, 0.12, 0.14, 0.23])

    endorsements = services.endorsements_of(report)
    if endorsements:
        rep.h2("Endorsement history")
        rep.table(["Office", "Action", "Officer", "Date", "Remark"],
                  [[row.get_layer_display(), row.get_action_display(), row.actor or "—", row.at,
                    row.content or "—"] for row in endorsements],
                  widths=[0.15, 0.14, 0.2, 0.2, 0.31])

    data = rep.build()
    audit.log("other", request.user, actor=request.user,
              note=f"generated the pre-approval post-visit report PDF {report.pk} (case {case.reference})")
    response = pdf_response(
        f"compiled-report-{report.pk}.pdf", data, inline=request.GET.get("download") != "1")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _pdf_report_value(value):
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_pdf_report_value(item)}" for key, item in value.items()) or "—"
    if isinstance(value, (list, tuple)):
        return "; ".join(_pdf_report_value(item) for item in value) or "—"
    return "—" if value is None or value == "" else str(value)


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
def member_report_save(request, pk):
    report = _report_or_404(request, pk)
    member_report = next((item for item in services.ensure_member_reports(report)
                          if item.author_id == request.user.pk), None)
    if member_report is None:
        raise Http404("There is no attendee report assigned to your account for this programme.")
    form = forms.MemberReportForm(request.POST, member_report=member_report)
    submit = request.POST.get("op") == "submit"
    if form.is_valid():
        try:
            services.save_member_report(
                member_report, user=request.user, summary=form.cleaned_data["summary"],
                findings=form.findings(), recommendation=form.cleaned_data["recommendation"], submit=submit)
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            _attach(form, exc)
        else:
            report.refresh_from_db()
            response = render(request, "inspection/_report_panel.html", _report_ctx(request, report))
            title = "Your report was sent to the Team Lead" if submit else "Your draft was saved"
            return toast(response, "success", title, "")
    response = render(request, "inspection/_report_panel.html",
                      _report_ctx(request, report, member_form=form), status=422)
    return toast(response, "error", "Not saved", "Check the highlighted fields.")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
def member_report_review(request, pk, member_pk):
    report = _report_or_404(request, pk)
    member_report = PostVisitMemberReport.unscoped.filter(pk=member_pk, report=report).first()
    if member_report is None:
        raise Http404
    form = forms.MemberReviewForm(request.POST)
    if form.is_valid():
        try:
            services.review_member_report(
                member_report, user=request.user, action=form.cleaned_data["action"],
                content=form.cleaned_data.get("content", ""))
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            _attach(form, exc)
        else:
            report.refresh_from_db()
            response = render(request, "inspection/_report_panel.html", _report_ctx(request, report))
            title = "Attendee report accepted" if form.cleaned_data["action"] == "accept" else \
                "Attendee report returned"
            return toast(response, "success", title, "")
    response = render(request, "inspection/_report_panel.html",
                      _report_ctx(request, report, member_review_form=form, review_member_pk=member_report.pk),
                      status=422)
    return toast(response, "error", "Review not saved", "Check the highlighted fields.")


@require_or_404(cs.can_view_cases)
@require_POST
@writer
def member_attendee_skip(request, pk, attendee_pk):
    """Team Lead skips an attendee who has no report row yet (e.g. no portal
    account or never started). The note is kept for the CQAC audit trail."""
    report = _report_or_404(request, pk)
    attendee = PostVisitPersonnel.unscoped.filter(pk=attendee_pk, inspection=report.inspection).first()
    if attendee is None:
        raise Http404
    form = forms.MemberReviewForm(request.POST)
    if form.is_valid():
        try:
            services.skip_attendee_report(report, attendee=attendee, user=request.user,
                                          content=form.cleaned_data.get("content", ""))
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except ValidationError as exc:
            _attach(form, exc)
        else:
            report.refresh_from_db()
            response = render(request, "inspection/_report_panel.html", _report_ctx(request, report))
            return toast(response, "success", "Attendee skipped", "The reason is kept for the CQAC.")
    response = render(request, "inspection/_report_panel.html",
                      _report_ctx(request, report, member_review_form=form, review_attendee_pk=attendee_pk),
                      status=422)
    return toast(response, "error", "Skip not saved", "Add a note explaining the decision.")


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


@require(cs.can_view_cases)
@require_GET
def endorsements_live(request):
    """Cheap version stamp for the endorsement queue (polled)."""
    agg = services.visible_reports(request.user).aggregate(
        latest=Max("updated_at"), submitted=Max("submitted_at"), total=Count("pk"))
    token = _live_token([agg["latest"], agg["submitted"], agg["total"]])
    if request.GET.get("v") == token:
        return JsonResponse({"v": token, "unchanged": True})
    return JsonResponse({"v": token})


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


# ===========================================================================
# v2.2 — structured visit report NBTE/QA/ICT/VVT/001 (X40)
# ===========================================================================
@require_or_404(cs.can_view_cases)
@require_POST
@writer
def report_vvt(request, pk):
    """Declarations, found-vs-declared findings, GPS and the falsification fine."""
    from django.http import Http404 as _404

    from accreditation import visit_report as vr

    report = _report_or_404(request, pk)
    op = request.POST.get("op", "")
    can_write, why = services.can_write(report, request.user)
    try:
        if report.inspection.status != "closed":
            raise PermissionDenied("Record the visit before completing its post-visit report.")
        if op == "declare":
            from inspection.models import ResourcePerson
            from staff.models import Staff

            staff = Staff.objects.filter(pk=request.POST.get("staff") or 0).first()
            rp = ResourcePerson.objects.filter(pk=request.POST.get("rp") or 0).first()
            if not can_write and not (staff and staff.user_id == request.user.pk) \
                    and not (rp and rp.user_id == request.user.pk):
                raise PermissionDenied(why or "You cannot declare for this person.")
            vr.declare(report.inspection, user=request.user, staff=staff, resource_person=rp,
                       has_conflict=request.POST.get("has_conflict") == "yes", details=request.POST.get("details", ""))
            ok = ("Declaration recorded", "")
        elif op == "findings":
            if not can_write:
                raise PermissionDenied(why)
            rows = []
            for key in request.POST:
                if key.startswith("found_"):
                    item_id = key.split("_", 1)[1]
                    rows.append({"item_id": item_id, "name": request.POST.get(f"name_{item_id}", ""),
                                 "declared_quantity": request.POST.get(f"declared_{item_id}") or 0,
                                 "found_quantity": request.POST.get(key),
                                 "is_falsified": request.POST.get(f"falsified_{item_id}") == "on",
                                 "remark": request.POST.get(f"remark_{item_id}", "")})
            vr.save_findings(report, user=request.user, rows=rows)
            ok = ("Findings saved", f"Fine so far: ₦{report.fines_total:,.2f}" if report.fines_total else "")
        elif op == "gps":
            if not can_write:
                raise PermissionDenied(why)
            vr.record_gps(report, user=request.user, lat=request.POST.get("lat"), lng=request.POST.get("lng"),
                          accuracy=request.POST.get("accuracy"), point=request.POST.get("point", ""))
            ok = ("Location recorded", "Flagged: far from the institution's recorded site." if report.gps_flag else "")
        elif op == "fine":
            vr.raise_fine(report, user=request.user)
            ok = ("Fine billed", "The institution sees the bill on its portal.")
        else:
            raise _404
    except (ValidationError, PermissionDenied) as exc:
        report.refresh_from_db()
        response = render(request, "inspection/_report_panel.html", _report_ctx(request, report), status=422)
        return toast(response, "error", "Not saved", " ".join(getattr(exc, "messages", [str(exc)])))
    report.refresh_from_db()
    response = render(request, "inspection/_report_panel.html", _report_ctx(request, report))
    return toast(response, "success", *ok)
