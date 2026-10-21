"""Professional bodies and joint QA mapping (Phase 6), namespace "qa_pb",
mounted at /qa/professional-bodies/.

    Bodies list · body detail (its mappings, the cases it was told about) ·
    add / edit a body · programme mappings list · add / edit / remove a
    mapping · Excel import (check, then import) · template · export.

Lists follow the §20 contract: search + three filters, 25 rows with
"Show all", the URL holds the filters, every change is an HTMX swap. Anyone
who can see institutions may read; editing needs
``institutions.manage_professional_bodies`` (the Curriculum Department).
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core.permissions import can_view_institutions, require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import pb_forms
from .models import Programme, ProfessionalBody, ProgrammeBodyMapping, ProgrammeLevel

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def can_manage(user):
    return user.is_superuser or user.has_perm("institutions.manage_professional_bodies")


def _can_view(user):
    return can_view_institutions(user) or can_manage(user)


def _levels():
    return ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin")


def _programme_names():
    """Distinct programme names offered anywhere, for the datalist."""
    return sorted({n for n in Programme.unscoped.values_list("name", flat=True) if n})[:500]


def _base_ctx(request):
    return {"can_manage": can_manage(request.user)}


# ===========================================================================
# Bodies
# ===========================================================================
@require(_can_view)
@require_GET
def body_list(request):
    f = request.GET
    qs = ProfessionalBody.objects.annotate(
        mapping_count=Count("mappings", filter=Q(mappings__is_active=True), distinct=True),
        case_count=Count("notices__case", distinct=True)).order_by("name")
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(acronym__icontains=q) | Q(contact_person__icontains=q)
                       | Q(email__icontains=q) | Q(mappings__programme_name__icontains=q)).distinct()
    if f.get("status") == "active":
        qs = qs.filter(is_active=True)
    elif f.get("status") == "inactive":
        qs = qs.filter(is_active=False)
    if f.get("level"):
        qs = qs.filter(Q(mappings__level__code=f["level"]) | Q(mappings__level__isnull=True),
                       mappings__is_active=True).distinct()
    if f.get("contact") == "missing":
        qs = qs.filter(email="")
    elif f.get("contact") == "ok":
        qs = qs.exclude(email="")
    ctx = {**_base_ctx(request), "page": paginate(request, qs), "filters": f, "levels": _levels(),
           "section": "bodies"}
    return render_qa(request, "institutions/pb/bodies.html", ctx,
                     {"qa-panel": "institutions/pb/_bodies_panel.html", "qa-rows": "institutions/pb/_bodies_rows.html"})


@require_or_404(_can_view)
@require_GET
def body_detail(request, pk):
    body = get_object_or_404(ProfessionalBody, pk=pk)
    mappings = body.mappings.select_related("level").order_by("programme_name", "level__sort_order")
    from accreditation.models import ProfessionalBodyNotice

    notices = (ProfessionalBodyNotice.objects.for_user(request.user).filter(body=body)
               .select_related("case", "case__institution").order_by("-created_at")[:25])
    ctx = {**_base_ctx(request), "body": body, "mappings": mappings, "notices": notices,
           "map_form": pb_forms.MappingForm(body=body) if can_manage(request.user) else None,
           "programme_names": _programme_names(), "section": "bodies"}
    return render_qa(request, "institutions/pb/body_detail.html", ctx,
                     {"qa-panel": "institutions/pb/_body_detail_panel.html"})


@require(can_manage)
@require_http_methods(["GET", "POST"])
def body_new(request):
    return _body_form(request, None)


@require_or_404(can_manage)
@require_http_methods(["GET", "POST"])
def body_edit(request, pk):
    return _body_form(request, pk)


def _body_form(request, pk=None):
    body = get_object_or_404(ProfessionalBody, pk=pk) if pk else None
    form = pb_forms.ProfessionalBodyForm(request.POST or None, instance=body)
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if body is None:
            saved.created_by = request.user
        saved.save()
        verb = "updated" if body else "added"
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Location"] = _hx_location("qa_pb:body_detail", saved.pk)
            return toast(response, "success", f"{saved.name} {verb}")
        return redirect("qa_pb:body_detail", pk=saved.pk)
    ctx = {**_base_ctx(request), "form": form, "body": body, "section": "bodies"}
    status = 422 if form.is_bound and form.errors else 200
    return render_qa(request, "institutions/pb/body_form.html", ctx,
                     {"qa-panel": "institutions/pb/_body_form_panel.html",
                      "pb-form": "institutions/pb/_body_form.html"}, status=status)


def _hx_location(name, *args):
    import json

    from django.urls import reverse

    return json.dumps({"path": reverse(name, args=args), "target": "#qa-panel"})


# ===========================================================================
# Mappings
# ===========================================================================
@require(_can_view)
@require_GET
def mapping_list(request):
    f = request.GET
    qs = ProgrammeBodyMapping.objects.select_related("body", "level").order_by("programme_name", "body__name")
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(programme_name__icontains=q) | Q(body__name__icontains=q) | Q(body__acronym__iexact=q)
                       | Q(note__icontains=q))
    if f.get("body"):
        qs = qs.filter(body_id=f["body"]) if f["body"].isdigit() else qs.none()
    if f.get("level") == "all":
        qs = qs.filter(level__isnull=True)
    elif f.get("level"):
        qs = qs.filter(level__code=f["level"])
    if f.get("status") == "active":
        qs = qs.filter(is_active=True, body__is_active=True)
    elif f.get("status") == "inactive":
        qs = qs.filter(Q(is_active=False) | Q(body__is_active=False))
    ctx = {**_base_ctx(request), "page": paginate(request, qs), "filters": f, "levels": _levels(),
           "bodies": ProfessionalBody.objects.order_by("name"), "section": "mappings",
           "map_form": pb_forms.MappingForm() if can_manage(request.user) else None,
           "programme_names": _programme_names()}
    return render_qa(request, "institutions/pb/mappings.html", ctx,
                     {"qa-panel": "institutions/pb/_mappings_panel.html",
                      "qa-rows": "institutions/pb/_mappings_rows.html"})


@require(can_manage)
@require_POST
def mapping_new(request):
    return _mapping_save(request, None)


@require_or_404(can_manage)
@require_POST
def mapping_edit(request, pk):
    return _mapping_save(request, pk)


def _mapping_save(request, pk=None):
    mapping = get_object_or_404(ProgrammeBodyMapping, pk=pk) if pk else None
    fixed_body = None
    if request.POST.get("fixed_body"):
        fixed_body = get_object_or_404(ProfessionalBody, pk=request.POST["fixed_body"])
    form = pb_forms.MappingForm(request.POST, instance=mapping, body=fixed_body)
    if form.is_valid():
        saved = form.save(commit=False)
        if mapping is None:
            saved.created_by = request.user
        saved.save()
        response = HttpResponse(status=204)
        response["HX-Trigger"] = '{"pb:changed": true}'
        title = f"{saved.programme_name} mapped to {saved.body.acronym or saved.body.name}"
        if not is_htmx(request):
            return redirect(request.POST.get("next") or "qa_pb:mappings")
        return toast(response, "success", title)
    ctx = {"map_form": form, "fixed_body": fixed_body, "programme_names": _programme_names()}
    return render(request, "institutions/pb/_mapping_form.html", ctx, status=422)


@require_or_404(can_manage)
@require_POST
def mapping_toggle(request, pk):
    mapping = get_object_or_404(ProgrammeBodyMapping, pk=pk)
    mapping.is_active = not mapping.is_active
    mapping.save(update_fields=["is_active", "updated_at"])
    response = render(request, "institutions/pb/_mapping_row.html",
                      {"m": mapping, "can_manage": True, "show_body": request.POST.get("show_body") == "1"})
    return toast(response, "success", f"Mapping {'turned on' if mapping.is_active else 'turned off'}",
                 "Turned-off mappings stop flagging new requests as joint QA.")


# ===========================================================================
# Import / export
# ===========================================================================
@require(can_manage)
@require_http_methods(["GET", "POST"])
def import_page(request):
    from .professional_body_io import import_xlsx

    form = pb_forms.PBImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == "POST" and form.is_valid():
        dry_run = request.POST.get("mode") != "apply"
        try:
            result = import_xlsx(form.cleaned_data["file"], user=request.user, dry_run=dry_run)
        except ValidationError as exc:
            form.add_error("file", exc)
        else:
            if not dry_run and result.ok:
                from core import audit

                audit.log("other", request.user, actor=request.user,
                          note=f"imported professional bodies ({result.changed} change(s))")
    ctx = {**_base_ctx(request), "form": form, "result": result, "section": "import"}
    status = 422 if (form.is_bound and form.errors) or (result and not result.ok) else 200
    if is_htmx(request) and request.method == "POST":
        response = render(request, "institutions/pb/_import_result.html", ctx, status=status)
        if result is not None:
            if not result.ok:
                return toast(response, "error", "The file has errors", f"{len(result.errors)} problem(s) to fix.")
            title = "File checked: nothing saved yet" if result.dry_run else "Import complete"
            return toast(response, "success", title, f"{result.changed} change(s).")
        return response
    return render_qa(request, "institutions/pb/import.html", ctx, {"qa-panel": "institutions/pb/_import_panel.html"},
                     status=status)


@require(can_manage)
@require_GET
def import_template(request):
    from .professional_body_io import template_xlsx

    response = HttpResponse(template_xlsx(), content_type=XLSX)
    response["Content-Disposition"] = 'attachment; filename="nbte-professional-bodies-template.xlsx"'
    return response


@require(_can_view)
@require_GET
def export(request):
    from core import audit

    from .professional_body_io import export_xlsx

    audit.log("other", request.user, actor=request.user, note="exported professional bodies")
    response = HttpResponse(export_xlsx(), content_type=XLSX)
    response["Content-Disposition"] = (f'attachment; filename="nbte-professional-bodies-'
                                       f'{timezone.localdate().isoformat()}.xlsx"')
    return response
