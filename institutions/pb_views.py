"""Professional bodies and joint QA mapping (Phase 6), namespace "qa_pb",
mounted at /qa/professional-bodies/.

    Bodies list · body detail (its mappings, the cases it was told about) ·
    add / edit a body · programme mappings list · add / edit / remove a
    mapping · Excel import (check, then import) · template · export.

Lists follow the §20 contract: search + three filters, 25 rows with
"Show all", the URL holds the filters, every change is an HTMX swap. Anyone
who may see bodies reads them (need-to-know: full viewers, the TVET Support
groups, ``institutions.view_all_professional_bodies``). Changes are explicit
three-layer permissions (Part 1b/1c, X47): every add, edit, mapping, master-list
change and body account goes through ``qa_core.actions`` (initiate → confirm
→ approve); the ES and super admin apply at once. Rank gives no right here.
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
from .models import BodyLetter, Programme, ProfessionalBody, ProgrammeBodyMapping, ProgrammeLevel

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def can_manage(user):
    """Start professional-body changes (Part 1b): explicit permission, never rank."""
    from .tvet_access import can_initiate
    return bool(getattr(user, "is_authenticated", False) and can_initiate(user, "bodies"))


def _can_view(user):
    from assignments import access

    from .tvet_access import can_view_bodies
    if can_view_bodies(user) or can_manage_master(user):
        return True
    return bool(not access.assignment_mode() and can_view_institutions(user))  # the old department rule


def _initiate(request, code, target, payload, label=""):
    """Start a pending action (X47). Returns (pa, error)."""
    from django.core.exceptions import PermissionDenied

    from qa_core import actions
    try:
        pa = actions.initiate(code, actor=request.user, target=target, payload=payload,
                              reason=label, impersonating=getattr(request, "is_impersonating", False))
    except (ValidationError, PermissionDenied) as exc:
        return None, " ".join(getattr(exc, "messages", None) or [str(exc)])
    return pa, ""


def _sent(response, pa, done_title):
    """Toast for a pending action: applied now (ES / super admin, one layer) or sent on."""
    if pa.status == pa.APPLIED:
        return toast(response, "success", done_title)
    if pa.status == pa.FAILED:
        return toast(response, "error", "Not applied", pa.failure_detail)
    return toast(response, "info", f"Sent for confirmation: {pa.reference}",
                 "A different officer must confirm it before it takes effect.")


def _levels():
    return ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin")


def _programme_names():
    """Distinct programme names offered anywhere, for the datalist."""
    from .services import programme_name_choices
    return programme_name_choices()


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
    from .programme_info import programmes_of_body

    programmes = paginate(request, programmes_of_body(body, request.user))
    ctx = {**_base_ctx(request), "body": body, "mappings": mappings, "notices": notices, "programmes": programmes,
           "map_form": pb_forms.MappingForm(body=body) if can_manage(request.user) else None,
           "programme_names": _programme_names(), "section": "bodies",
           # v2.1 R.5 (X43): letters the body uploaded and its portal accounts
           "letters": BodyLetter.unscoped.filter(body=body).select_related("programme", "programme__institution")[:25],
           "body_users": body.users.select_related("role").order_by("email") if can_manage(request.user) else []}
    return render_qa(request, "institutions/pb/body_detail.html", ctx,
                     {"qa-panel": "institutions/pb/_body_detail_panel.html",
                      "pb-prog-rows": "institutions/pb/_body_programmes.html"})


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
        from .tvet_handlers import form_payload
        pa, error = _initiate(request, "institutions.edit_professional_body", body, form_payload(request))
        if error:
            form.add_error(None, error)
        else:
            verb = "updated" if body else "added"
            saved = body
            if pa.status == pa.APPLIED and body is None:
                saved = ProfessionalBody.objects.filter(name=form.cleaned_data.get("name")).order_by("-pk").first()
            dest = ("qa_pb:body_detail", saved.pk) if saved is not None else ("qa_pb:list",)
            if is_htmx(request):
                response = HttpResponse(status=204)
                response["HX-Location"] = _hx_location(*dest)
                return _sent(response, pa, f"{form.cleaned_data.get('name')} {verb}")
            return _sent(redirect(*dest), pa, f"{form.cleaned_data.get('name')} {verb}")
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


@require(lambda u: can_manage_master(u))
@require_POST
def mapping_new(request):
    return _mapping_save(request, None)


@require_or_404(lambda u: can_manage_master(u))
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
        from .tvet_handlers import form_payload
        pa, error = _initiate(request, "institutions.edit_programme_mapping", mapping,
                              form_payload(request, fixed_body=fixed_body.pk if fixed_body else None))
        if error:
            form.add_error(None, error)
        else:
            body = form.cleaned_data.get("body") or fixed_body
            title = f"{form.cleaned_data.get('programme_name')} mapped to {getattr(body, 'acronym', '') or body}"
            if not is_htmx(request):
                from django.urls import reverse

                from core.urlsafe import safe_next
                return _sent(redirect(safe_next(request, request.POST.get("next"), reverse("qa_pb:mappings"))),
                             pa, title)
            response = HttpResponse(status=204)
            response["HX-Trigger"] = '{"pb:changed": true}'
            return _sent(response, pa, title)
    ctx = {"map_form": form, "fixed_body": fixed_body, "programme_names": _programme_names()}
    return render(request, "institutions/pb/_mapping_form.html", ctx, status=422)


@require_or_404(lambda u: can_manage_master(u))
@require_POST
def mapping_toggle(request, pk):
    mapping = get_object_or_404(ProgrammeBodyMapping, pk=pk)
    pa, error = _initiate(request, "institutions.edit_programme_mapping", mapping, {"op": "toggle"})
    mapping.refresh_from_db()
    response = render(request, "institutions/pb/_mapping_row.html",
                      {"m": mapping, "can_manage": True, "show_body": request.POST.get("show_body") == "1"})
    if error:
        return toast(response, "error", "Not done", error)
    return _sent(response, pa, f"Mapping {'turned on' if mapping.is_active else 'turned off'}")


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
        if not dry_run and not _can_apply_import(request.user, "bodies"):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Applying an import needs the approve permission (an import applies at once).")
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


# ===========================================================================
# v2.1 R.4 (X44): programme master list and the unmapped queue (NTIRD)
# ===========================================================================
def can_manage_master(user):
    """Start master-list / mapping changes (Part 1b): explicit permission, never rank."""
    from .tvet_access import can_initiate
    return bool(getattr(user, "is_authenticated", False) and can_initiate(user, "programmes"))


def _can_apply_import(user, group):
    from .tvet_access import is_top
    perm = {"bodies": "institutions.approve_edit_professional_bodies",
            "programmes": "institutions.approve_edit_programmes"}[group]
    return bool(is_top(user) or user.has_perm(perm))


@require(_can_view)
@require_GET
def master_list(request):
    from .models import ProgrammeMaster

    f = request.GET
    qs = ProgrammeMaster.objects.select_related("level", "professional_body", "programme_category").annotate(
        used=Count("programmes", distinct=True))
    q = (f.get("q") or "").strip()
    if q:
        from .programme_master import normalise

        for w in normalise(q).split():
            qs = qs.filter(Q(search_key__icontains=w) | Q(code__icontains=w)
                           | Q(professional_body__acronym__iexact=w))
    if f.get("level"):
        qs = qs.filter(level__code=f["level"])
    if f.get("body") == "none":
        qs = qs.filter(professional_body__isnull=True)
    elif (f.get("body") or "").isdigit():
        qs = qs.filter(professional_body_id=f["body"])
    if f.get("confirm") == "1":
        qs = qs.filter(to_confirm=True)
    ctx = {**_base_ctx(request), "can_manage_master": can_manage_master(request.user), "page": paginate(request, qs),
           "filters": f, "levels": _levels(), "bodies": ProfessionalBody.objects.order_by("name"),
           "section": "master",
           "unmapped_count": Programme.unscoped.filter(not_in_master=True).exclude(status="dropped").count()}
    return render_qa(request, "institutions/pb/master.html", ctx,
                     {"qa-panel": "institutions/pb/_master_panel.html", "qa-rows": "institutions/pb/_master_rows.html"})


def _unmapped_ctx(request, **extra):
    from .models import ProgrammeMaster

    rows = (Programme.unscoped.filter(not_in_master=True).exclude(status="dropped")
            .select_related("institution", "level", "category", "professional_body").order_by("created_at"))
    ctx = {**_base_ctx(request), "can_manage_master": can_manage_master(request.user), "rows": rows,
           "masters": ProgrammeMaster.objects.filter(is_active=True).select_related("level").order_by("name"),
           "section": "unmapped", "unmapped_count": rows.count()}
    ctx.update(extra)
    return ctx


@require(_can_view)
@require_GET
def master_unmapped(request):
    return render_qa(request, "institutions/pb/unmapped.html", _unmapped_ctx(request),
                     {"qa-panel": "institutions/pb/_unmapped_panel.html"})


@require(can_manage_master)  # Part 1b: no permission → 403; unknown programme → 404
@require_POST
def master_map(request, pk):
    """Map a hand-typed programme to an existing master row, or create one."""
    from .models import ProgrammeMaster
    from .programme_master import map_programme

    programme = get_object_or_404(Programme.unscoped.select_related("level", "category", "professional_body"),
                                  pk=pk, not_in_master=True)
    choice = request.POST.get("master") or ""
    pa, error = _initiate(request, "institutions.edit_programme_master", programme, {"op": "map", "master": choice})
    response = render_qa(request, "institutions/pb/unmapped.html", _unmapped_ctx(request),
                         {"qa-panel": "institutions/pb/_unmapped_panel.html"}, status=422 if error else 200)
    if error:
        return toast(response, "error", "Not done", error)
    return _sent(response, pa, f"{programme} mapped to the master list")


def _diff_export(request, spec_name, filename):
    from core import audit, importdiff

    spec = importdiff.get_spec(spec_name)
    audit.log("other", request.user, actor=request.user, note=f"exported {spec.label.lower()}")
    response = HttpResponse(importdiff.export_workbook(spec), content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{filename}-{timezone.localdate().isoformat()}.xlsx"'
    return response


@require(_can_view)
@require_GET
def bodies_export(request):
    """X42: bodies with their programmes, in the import-diff round-trip format."""
    return _diff_export(request, "professional_bodies", "nbte-professional-bodies")


@require(_can_view)
@require_GET
def master_export(request):
    """X42/X44: the master list; doubles as the mapping template (fill the body column, upload)."""
    return _diff_export(request, "programme_master", "nbte-programme-master")


@require_or_404(can_manage)
@require_POST
def body_user_new(request, pk):
    """v2.1 R.5 (X43): create a portal account for the body (set-password link e-mailed)."""
    from .pb_portal import create_body_user

    body = get_object_or_404(ProfessionalBody, pk=pk)
    from .tvet_handlers import form_payload
    pa, error = _initiate(request, "institutions.edit_professional_body", body, form_payload(request, op="account"))
    response = redirect("qa_pb:body_detail", pk=pk)
    if error:
        return toast(response, "error", "Account not created", error)
    return _sent(response, pa, "Portal account created — a set-password link was e-mailed")
