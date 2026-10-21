"""Internal institution management screens (Master Plan §6, §13).

    Institutions list · Institution detail (tabs + timeline) · every §6 action
    form (initiate) · action launcher (pick an institution) · bulk import /
    export · Settings → document rules and lookups.

Confirming is the Phase 1 "Pending confirmations" page filtered by action
(the "Confirm X" menu items link there). Every read is scoped with
``.for_user(request.user)`` (§4); object routes answer 404, never 403.
"""
from __future__ import annotations

import datetime

from django.db import transaction
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core import actions
from qa_core.models import ActionPolicy, PendingAction, TimelineEvent
from qa_core.permissions import can_view_institutions, can_view_qa_settings, require, require_or_404
from qa_core.ui import is_htmx, paginate, render_qa, toast

from . import forms, services
from .handlers import latest_impersonation
from .models import (
    DocumentRequirement,
    Institution,
    InstitutionCategory,
    InstitutionDocument,
    InstitutionImportBatch,
    InstitutionOwnership,
    InstitutionRequest,
    InstitutionType,
    Programme,
)

NI, TS = "New Institution", "TVET Support"


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def _scoped(request):
    return Institution.objects.for_user(request.user).select_related(
        "ownership", "institution_type", "category", "state", "handling_department")


def _institution_or_404(request, code):
    inst = _scoped(request).filter(code=code.upper()).first()
    if inst is None:
        raise Http404("No such institution in your scope.")
    return inst


# ===========================================================================
# Menu data (sidebar + action menus) — driven by ActionPolicy rows (§6)
# ===========================================================================
def action_menu(user, institution=None):
    """[{group, items:[{code,label,url,confirm_url,enabled,reason,can_initiate}]}]"""
    groups = {}
    for policy in ActionPolicy.objects.filter(menu_group__in=(NI, TS)).order_by("menu_group", "label"):
        try:
            handler = actions.get_handler(policy.action_code)
        except ValidationError:
            continue
        can_initiate = bool(user.is_superuser or not policy.initiate_permission
                            or user.has_perm(policy.initiate_permission))
        enabled, reason = policy.is_enabled, ("" if policy.is_enabled else policy.description)
        if enabled and institution is not None and hasattr(handler, "available_for"):
            enabled, reason = handler.available_for(institution)
        if policy.action_code == "institutions.add_tvet":
            url = reverse("qa_inst:add_tvet")
        elif policy.action_code == "institutions.bulk_import":
            url = reverse("qa_inst:import")
        elif institution is not None:
            url = reverse("qa_inst:action", args=[institution.code, policy.action_code])
        else:
            url = reverse("qa_inst:launcher", args=[policy.action_code])
        groups.setdefault(policy.menu_group, []).append({
            "code": policy.action_code, "label": policy.label, "url": url, "enabled": enabled and can_initiate,
            "reason": reason if enabled is False else ("" if can_initiate else "You cannot start this action."),
            "two_step": policy.requires_second_officer, "icon": getattr(handler, "icon", "edit"),
            "confirm_url": (reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}")
            if policy.requires_second_officer else "",
            "summary": getattr(handler, "summary", "") or policy.description,
        })
    order = [NI, TS]
    return [{"group": g, "items": groups[g]} for g in order if g in groups]


# ===========================================================================
# List
# ===========================================================================
STATUS_CHOICES = list(Institution.STATUS_LABELS.items())


@require(can_view_institutions)
@require_GET
def institution_list(request):
    qs = _scoped(request)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(lga__icontains=q)
                       | Q(former_names__icontains=q))
    f = request.GET
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("ownership"):
        qs = qs.filter(ownership__code=f["ownership"])
    if f.get("type"):
        qs = qs.filter(institution_type__code=f["type"])
    if f.get("dept"):
        qs = qs.filter(handling_department__code=f["dept"])
    if f.get("zone"):
        qs = qs.filter(zone=f["zone"])
    sort = f.get("sort", "name")
    qs = qs.order_by({"name": "name", "code": "code", "recent": "-created_at", "status": "status"}.get(sort, "name"))
    page = paginate(request, qs)
    base = _scoped(request)
    from staff.models import Department, StateOfOrigin

    ctx = {
        "page": page, "filters": f, "statuses": STATUS_CHOICES,
        "ownerships": InstitutionOwnership.objects.filter(is_active=True),
        "types": InstitutionType.objects.filter(is_active=True),
        "departments": Department.objects.filter(pk__in=base.values_list("handling_department", flat=True)).order_by("name"),
        "zones": StateOfOrigin.ZONE_CHOICES,
        "counts": {"total": base.count(), "proposed": base.filter(status__in=("proposed", "under_review")).count(),
                   "approved": base.filter(status="approved").count(),
                   "pending": PendingAction.objects.filter(status="pending", policy__menu_group__in=(NI, TS))
                   .filter(pk__in=actions.visible_to(request.user).values("pk")).count()},
        "menu": action_menu(request.user),
        "can_export": request.user.has_perm("institutions.export_institutions") or request.user.is_superuser,
        "can_import": request.user.has_perm("institutions.initiate_bulk_import") or request.user.is_superuser,
        "can_add": request.user.has_perm("institutions.initiate_add_tvet") or request.user.is_superuser,
    }
    return render_qa(request, "institutions/list.html", ctx, {"qa-panel": "institutions/_list_panel.html",
                                                               "qa-rows": "institutions/_rows.html"})


# ===========================================================================
# Detail (tabs)
# ===========================================================================
TABS = [("overview", "Overview"), ("programmes", "Programmes"), ("documents", "Documents"), ("users", "Users"),
        ("applications", "Applications"), ("payments", "Payments"), ("changes", "Changes"),
        ("timeline", "Timeline")]


@require(can_view_institutions)
@require_GET
def institution_detail(request, code):
    inst = _institution_or_404(request, code)
    tab = request.GET.get("tab", "overview")
    if tab not in dict(TABS):
        tab = "overview"
    ctx = {"inst": inst, "tab": tab, "tabs": TABS, "menu": action_menu(request.user, inst)}
    ctx.update(_tab_context(request, inst, tab))
    return render_qa(request, "institutions/detail.html", ctx, {"qa-panel": "institutions/_detail_panel.html",
                                                                 "qa-tab": f"institutions/tabs/_{tab}.html"})


def _tab_context(request, inst, tab):
    user = request.user
    ctx = {}
    lifecycle = services.lifecycle(inst)
    ctx["lifecycle_label"] = inst.status_label
    if tab == "overview":
        ctx.update({
            "contacts": services.contacts_of(inst), "addresses": services.addresses_of(inst),
            "primary_user": inst.primary_user(),
            "programme_count": Programme.objects.for_user(user).filter(institution=inst).exclude(status="dropped").count(),
            "doc_summary": services.checklist_summary(services.checklist(inst)),
            "pending_here": _pending_for(request, inst)[:5],
            "recent": list(TimelineEvent.objects.for_user(user).filter(institution=inst)[:6]),
            "impersonation": latest_impersonation(user, inst),
        })
    elif tab == "programmes":
        qs = Programme.objects.for_user(user).filter(institution=inst).select_related("level", "category")
        status = request.GET.get("pstatus", "")
        if status:
            qs = qs.filter(status=status)
        ctx.update({"programmes": list(qs.order_by("name")), "pstatus": status,
                    "programme_statuses": Programme.STATUS_CHOICES})
    elif tab == "documents":
        ctx.update({"rows": services.checklist(inst),
                    "history": list(InstitutionDocument.objects.for_user(user).filter(institution=inst)
                                    .select_related("kind", "uploaded_by", "reviewed_by")
                                    .order_by("kind__name", "-version")[:200])})
    elif tab == "users":
        ctx.update({"members": services.members_of(inst),
                    "contact_history": services.contact_history(inst),
                    "address_history": services.addresses_of(inst, current=False)})
    elif tab == "applications":
        from accreditation.models import Application

        ctx["applications"] = list(Application.objects.for_user(user).filter(institution=inst).order_by("-created_at"))
        ctx["requests"] = list(InstitutionRequest.objects.for_user(user).filter(institution=inst)
                               .select_related("request_type").order_by("-created_at"))
    elif tab == "payments":
        from payments.models import PaymentObligation
        from payments.services import stage_label

        can_see = user.is_superuser or user.has_perm("payments.view_obligation")
        rows = list(PaymentObligation.objects.for_user(user).filter(institution=inst).order_by("-created_at")) \
            if can_see else []
        for ob in rows:
            ob.stage = stage_label(ob)
        ctx.update({"obligations": rows, "can_see_payments": can_see})
    elif tab == "changes":
        ctx["pending"] = _pending_for(request, inst, all_statuses=True)
    elif tab == "timeline":
        qs = TimelineEvent.objects.for_user(user).filter(institution=inst).select_related("actor")
        vis = request.GET.get("vis", "")
        if vis in ("public", "internal"):
            qs = qs.filter(visibility=vis)
        if not (user.is_superuser or user.has_perm("qa_core.view_timeline_internal")
                or user.department_id == inst.handling_department_id):
            qs = qs.filter(visibility="public")
        tq = (request.GET.get("tq") or "").strip()
        if tq:
            qs = qs.filter(Q(public_text__icontains=tq) | Q(internal_text__icontains=tq) | Q(event_code__icontains=tq))
        ctx.update({"page": paginate(request, qs.order_by("-at", "-id")), "vis": vis, "tq": tq})
    return ctx


def _pending_for(request, inst, all_statuses=False):
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(Institution)
    qs = actions.visible_to(request.user).filter(content_type=ct, object_id=str(inst.pk))
    if not all_statuses:
        qs = qs.filter(status="pending")
    return list(qs.order_by("-initiated_at")[:50])


# ===========================================================================
# Actions (initiate)
# ===========================================================================
def _policy_or_404(code):
    policy = ActionPolicy.objects.filter(action_code=code, menu_group__in=(NI, TS)).first()
    if policy is None:
        raise Http404
    try:
        handler = actions.get_handler(code)
    except ValidationError:
        raise Http404 from None
    return policy, handler


def _form_for(handler, request, institution, data=None):
    cls = getattr(handler, "form_class", forms.InstitutionActionForm)
    if cls is forms.AddTvetForm:
        return cls(data)
    return cls(data, institution=institution, user=request.user)


@require(can_view_institutions)
@require_http_methods(["GET", "POST"])
def action(request, code, action_code):
    inst = _institution_or_404(request, code)
    policy, handler = _policy_or_404(action_code)
    return _action_page(request, policy, handler, inst)


@require(can_view_institutions)
@require_http_methods(["GET", "POST"])
def add_tvet(request):
    policy, handler = _policy_or_404("institutions.add_tvet")
    return _action_page(request, policy, handler, None)


def _action_page(request, policy, handler, inst):
    user = request.user
    allowed = user.is_superuser or not policy.initiate_permission or user.has_perm(policy.initiate_permission)
    available, why = True, ""
    if not policy.is_enabled:
        available, why = False, policy.description
    elif inst is not None and hasattr(handler, "available_for"):
        available, why = handler.available_for(inst)
    form = _form_for(handler, request, inst, request.POST or None)
    error = ""
    if request.method == "POST":
        if not allowed or not available:
            raise PermissionDenied(why or "You cannot start this action.")
        if form.is_valid():
            payload = form.payload()
            reason = (form.cleaned_data.get("reason") or "").strip()
            payload["reason"] = reason
            target = handler.resolve_target(inst, form) if hasattr(handler, "resolve_target") else inst
            try:
                pa = actions.initiate(policy.action_code, actor=user, target=target, payload=payload,
                                      reason=reason, impersonating=_impersonating(request))
            except (ValidationError, PermissionDenied) as exc:
                error = " ".join(getattr(exc, "messages", [str(exc)]))
            else:
                return _after_initiate(request, policy, pa, inst)
    preview = []
    if form.is_bound and form.is_valid() and not error:
        preview = []
    lgas = []
    if inst is not None:
        lgas = services.lgas_for(inst.state)
    if form.is_bound and form.data.get("state"):
        from staff.models import StateOfOrigin

        lgas = services.lgas_for(StateOfOrigin.objects.filter(pk=form.data.get("state")).first())
    ctx = {"inst": inst, "policy": policy, "handler": handler, "form": form, "allowed": allowed,
           "available": available, "why": why, "error": error, "lgas": lgas, "preview": preview,
           "open_pending": _open_pending(request, policy, inst),
           "confirm_url": reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}"}
    response = render_qa(request, "institutions/action.html", ctx, {"qa-panel": "institutions/_action_panel.html"},
                         status=422 if (form.is_bound and (error or form.errors)) else 200)
    if error:
        toast(response, "error", "Not started", error)
    return response


def _open_pending(request, policy, inst):
    if inst is None:
        return None
    from django.contrib.contenttypes.models import ContentType

    return (PendingAction.objects.filter(policy=policy, status="pending",
                                         content_type=ContentType.objects.get_for_model(Institution),
                                         object_id=str(inst.pk)).first())


def _after_initiate(request, policy, pa, inst):
    if pa.status == PendingAction.APPLIED and policy.action_code == "institutions.impersonate":
        session = latest_impersonation(request.user, inst)
        from qa_core.hosts import portal_absolute, portal_reverse

        url = portal_absolute(portal_reverse("qa_portal:impersonation_start", token=session.token), request)
        if is_htmx(request):
            response = HttpResponse(status=204)
            response["HX-Redirect"] = url
            return response
        return HttpResponseRedirect(url)
    if pa.status == PendingAction.APPLIED:
        target = reverse("qa_inst:detail", args=[inst.code]) if inst is not None else reverse("qa_inst:list")
        messages.success(request, f"{policy.label}: done.")
    elif pa.status == PendingAction.FAILED:
        messages.error(request, f"{policy.label} could not be applied: {pa.failure_detail}")
        target = reverse("qa:pending_detail", args=[pa.pk])
    else:
        messages.success(request, f"{pa.reference} is waiting for a second officer.")
        target = reverse("qa:pending_detail", args=[pa.pk])
    if is_htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = target
        return response
    return redirect(target)


@require(can_view_institutions)
@require_GET
def launcher(request, action_code):
    """Choose the institution for a TVET Support action (sidebar entry)."""
    policy, handler = _policy_or_404(action_code)
    qs = _scoped(request)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    f = request.GET
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("ownership"):
        qs = qs.filter(ownership__code=f["ownership"])
    if f.get("dept"):
        qs = qs.filter(handling_department__code=f["dept"])
    page = paginate(request, qs.order_by("name"))
    for inst in page["rows"]:
        inst.available, inst.why = handler.available_for(inst) if hasattr(handler, "available_for") else (True, "")
    from staff.models import Department

    base = _scoped(request)
    ctx = {"policy": policy, "handler": handler, "page": page, "filters": f, "statuses": STATUS_CHOICES,
           "ownerships": InstitutionOwnership.objects.filter(is_active=True),
           "departments": Department.objects.filter(pk__in=base.values_list("handling_department", flat=True)),
           "confirm_url": reverse("qa:pending_list") + f"?status=pending&action={policy.action_code}"}
    return render_qa(request, "institutions/launcher.html", ctx, {"qa-panel": "institutions/_launcher_panel.html",
                                                                   "qa-rows": "institutions/_launcher_rows.html"})


@require(can_view_institutions)
@require_GET
def lga_options(request):
    from staff.models import StateOfOrigin

    state = StateOfOrigin.objects.filter(pk=request.GET.get("state") or 0).first()
    return render(request, "qa_core/partials/_lga_options.html", {"lgas": services.lgas_for(state)})


@require(can_view_institutions)
@require_GET
def code_suggestion(request):
    name = request.GET.get("name", "")
    try:
        code = services.allocate_code(name) if len(name.strip()) >= 4 else ""
    except ValidationError:
        code = ""
    return HttpResponse(code, content_type="text/plain")


# ===========================================================================
# Bulk import / export
# ===========================================================================
def _can_import(user):
    return user.is_superuser or user.has_perm("institutions.initiate_bulk_import")


@require(lambda u: can_view_institutions(u) and _can_import(u))
@require_http_methods(["GET", "POST"])
def import_page(request):
    from .importexport import COLUMNS, stage_batch

    form = forms.ImportUploadForm(request.POST or None, request.FILES or None)
    batch = None
    if request.method == "POST" and form.is_valid():
        try:
            batch = stage_batch(form.cleaned_data["file"], form.cleaned_data["kind"], user=request.user)
        except ValidationError as exc:
            form.add_error("file", exc)
        else:
            if is_htmx(request):
                response = render(request, "institutions/import/_batch.html", _batch_context(request, batch))
                kind = "success" if batch.status == "validated" else "error"
                return toast(response, kind, "Sheet checked" if kind == "success" else "The sheet has errors",
                             f"{batch.summary.get('total', 0)} row(s) read.")
            return redirect("qa_inst:import_batch", pk=batch.pk)
    recent = InstitutionImportBatch.objects.filter(created_by=request.user).order_by("-created_at")[:8]
    ctx = {"form": form, "batch": batch, "recent": recent, "columns": COLUMNS,
           "can_export": request.user.has_perm("institutions.export_institutions") or request.user.is_superuser}
    status = 422 if form.is_bound and form.errors else 200
    if is_htmx(request) and request.method == "POST":
        return render(request, "institutions/import/_upload.html", ctx, status=status)
    return render_qa(request, "institutions/import/page.html", ctx, {"qa-panel": "institutions/import/_panel.html"},
                     status=status)


def _batch_context(request, batch):
    pa = (PendingAction.objects.filter(policy_id="institutions.bulk_import", object_id=str(batch.pk))
          .order_by("-initiated_at").first())
    return {"batch": batch, "pa": pa, "rows": batch.rows[:500], "errors": batch.errors[:500]}


@require_or_404(lambda u: can_view_institutions(u) and _can_import(u))
@require_http_methods(["GET", "POST"])
def import_batch(request, pk):
    batch = InstitutionImportBatch.objects.filter(pk=pk).first()
    if batch is None or not (batch.created_by_id == request.user.pk or request.user.is_superuser
                             or request.user.has_perm("institutions.confirm_bulk_import")):
        raise Http404
    if request.method == "POST":
        if batch.created_by_id != request.user.pk or batch.status != "validated":
            raise Http404
        reason = (request.POST.get("reason") or "").strip()
        try:
            if len(reason) < 5:
                raise ValidationError("Say what this import is for (at least 5 characters).")
            # Mark the batch pending *before* initiating, in one transaction: the
            # pending action records the batch's version, and saving the batch
            # afterwards would make every confirmation fail as stale.
            with transaction.atomic():
                batch.status = "pending"
                batch.save(update_fields=["status", "updated_at"])
                pa = actions.initiate("institutions.bulk_import", actor=request.user, target=batch,
                                      payload={"reason": reason}, reason=reason,
                                      impersonating=_impersonating(request))
                if pa.status != PendingAction.PENDING:
                    batch.refresh_from_db()
        except (ValidationError, PermissionDenied) as exc:
            batch.refresh_from_db()
            response = render(request, "institutions/import/_batch.html",
                              {**_batch_context(request, batch), "error": " ".join(getattr(exc, "messages", [str(exc)]))})
            return toast(response, "error", "Not sent", " ".join(getattr(exc, "messages", [str(exc)])))
        response = render(request, "institutions/import/_batch.html", _batch_context(request, batch))
        return toast(response, "success", f"{pa.reference} sent for confirmation",
                     "A second officer must confirm before anything is created or changed.")
    return render_qa(request, "institutions/import/batch.html", _batch_context(request, batch),
                     {"qa-panel": "institutions/import/_batch.html"})


@require(lambda u: can_view_institutions(u) and _can_import(u))
@require_GET
def import_template(request, kind):
    from .importexport import template_xlsx

    if kind not in ("institutions", "programmes"):
        raise Http404
    response = HttpResponse(template_xlsx(kind),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="nbte-qa-{kind}-template.xlsx"'
    return response


@require(lambda u: can_view_institutions(u) and (u.is_superuser or u.has_perm("institutions.export_institutions")))
@require_GET
def export(request):
    from .importexport import export_institutions_xlsx, export_programmes_xlsx

    kind = request.GET.get("kind", "institutions")
    stamp = timezone.localdate().isoformat()
    if kind == "programmes":
        rows = (Programme.objects.for_user(request.user).select_related("institution", "level", "category")
                .order_by("institution__code", "name"))
        data = export_programmes_xlsx(rows)
    else:
        data = export_institutions_xlsx(_scoped(request).order_by("code"))
    from core import audit

    audit.log("other", request.user, actor=request.user, note=f"exported QA {kind}")
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="nbte-qa-{kind}-{stamp}.xlsx"'
    return response


# ===========================================================================
# Settings → QA → Document rules and lookups (§14 tables)
# ===========================================================================
def _can_edit_docs(user):
    return user.is_superuser or user.has_perm("institutions.manage_document_requirements")


def _can_edit_lookups(user):
    return user.is_superuser or user.has_perm("institutions.manage_institution_lookups")


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def settings_documents(request):
    from qa_core.views import _settings_context

    editable = _can_edit_docs(request.user)
    edit_id = request.GET.get("edit") or request.POST.get("id")
    instance = DocumentRequirement.objects.filter(pk=edit_id).first() if edit_id else None
    form = forms.DocumentRequirementForm(request.POST or None, instance=instance)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            response = render(request, "institutions/settings/_documents_panel.html",
                              _settings_context(request, "documents", **_docs_ctx(request, editable)))
            return toast(response, "success", "Document rule saved")
    ctx = _settings_context(request, "documents", **_docs_ctx(request, editable, form=form, editing=instance))
    return render_qa(request, "institutions/settings/documents.html", ctx,
                     {"qa-panel": "institutions/settings/_documents_panel.html"},
                     status=422 if form.is_bound and form.errors else 200)


def _docs_ctx(request, editable, form=None, editing=None):
    rules = DocumentRequirement.objects.select_related("ownership", "institution_type", "document_kind",
                                                       "reviewing_department")
    process = request.GET.get("process", "")
    if process:
        rules = rules.filter(process=process)
    return {"rules": list(rules), "editable": editable, "form": form or forms.DocumentRequirementForm(),
            "editing": editing, "process": process,
            "processes": sorted(set(DocumentRequirement.objects.values_list("process", flat=True)))}


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def settings_lookups(request, kind="ownership"):
    from qa_core.views import _settings_context

    if kind not in forms.LOOKUP_FORMS:
        raise Http404
    model, form_cls, title = forms.LOOKUP_FORMS[kind]
    editable = _can_edit_lookups(request.user)
    edit_id = request.GET.get("edit") or request.POST.get("id")
    instance = model.objects.filter(pk=edit_id).first() if edit_id else None
    form = form_cls(request.POST or None, instance=instance)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        if form.is_valid():
            form.save()
            response = render(request, "institutions/settings/_lookups_panel.html",
                              _settings_context(request, "lookups", **_lookup_ctx(kind, editable)))
            return toast(response, "success", f"{title}: saved")
    ctx = _settings_context(request, "lookups", **_lookup_ctx(kind, editable, form=form, editing=instance))
    return render_qa(request, "institutions/settings/lookups.html", ctx,
                     {"qa-panel": "institutions/settings/_lookups_panel.html"},
                     status=422 if form.is_bound and form.errors else 200)


def _lookup_ctx(kind, editable, form=None, editing=None):
    model, form_cls, title = forms.LOOKUP_FORMS[kind]
    return {"kind": kind, "title": title, "rows": list(model.objects.all()), "editable": editable,
            "form": form or form_cls(), "editing": editing,
            "kinds": [(k, v[2]) for k, v in forms.LOOKUP_FORMS.items()],
            "fields": [f for f in form_cls._meta.fields]}
