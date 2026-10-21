"""Onboarding application pages on the portal (SOP-01): tracker, document
checklist summary, clearance status, submit, and programme selection."""
from __future__ import annotations

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from institutions import services as inst_services
from institutions.forms import QAForm
from institutions.models import Institution, Programme, ProgrammeCategory, ProgrammeLevel
from qa_core.portal_views import _shell_context, institution_required
from qa_core.ui import is_htmx, toast

from . import services
from .models import Application, DepartmentalClearance


def _inst(request):
    return Institution.objects.for_user(request.user).select_related("ownership", "institution_type",
                                                                      "category", "state").first()


def _app(request, inst):
    return (Application.objects.for_user(request.user).filter(institution=inst).exclude(status="withdrawn")
            .order_by("-created_at").first())


def _context(request, inst, app, **extra):
    rows = inst_services.checklist(inst)
    clearances = []
    if app is not None and app.round:
        clearances = list(DepartmentalClearance.objects.for_user(request.user)
                          .filter(application=app, round=app.round).select_related("department")
                          .order_by("branch_code"))
    ctx = {
        "inst": inst, "app": app, "summary": inst_services.checklist_summary(rows), "rows": rows,
        "gaps": services.profile_gaps(inst) if app is None or app.status == "draft" else [],
        "blockers": services.submission_blockers(app) if app is not None and app.status == "draft" else [],
        "board": services.stage_board(app) if app is not None else [],
        "public_status": services.public_status(app) if app is not None else "Not started",
        "clearances": clearances, "hold": inst_services.active_hold(app) if app is not None else None,
        "programmes": list(Programme.objects.for_user(request.user).filter(institution=inst)
                           .select_related("level", "category").order_by("name")),
        "obligations": _obligations(request, app),
    }
    ctx.update(extra)
    return _shell_context(request, "application", **ctx)


def _obligations(request, app):
    if app is None:
        return []
    from django.contrib.contenttypes.models import ContentType

    from payments.models import PaymentObligation

    ct = ContentType.objects.get_for_model(Application)
    return list(PaymentObligation.objects.for_user(request.user).filter(source_type=ct, source_id=str(app.pk))
                .order_by("-created_at"))


@institution_required
def application(request):
    inst = _inst(request)
    app = _app(request, inst)
    return render(request, "qa_portal/application/page.html", _context(request, inst, app))


@institution_required
@require_POST
def application_start(request):
    inst = _inst(request)
    if getattr(request, "is_impersonating", False):
        raise PermissionDenied
    try:
        services.start_application(inst, user=request.user)
    except ValidationError as exc:
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, _app(request, inst), error=" ".join(exc.messages)))
        return toast(response, "error", "Could not start", " ".join(exc.messages))
    response = redirect("qa_portal:application")
    if is_htmx(request):
        response = render(request, "qa_portal/application/page.html", _context(request, inst, _app(request, inst)))
        toast(response, "success", "Application opened", "Upload your documents, then submit.")
    return response


@institution_required
@require_POST
def application_submit(request):
    inst = _inst(request)
    app = _app(request, inst)
    if app is None:
        raise Http404
    try:
        services.submit(app, user=request.user, declaration=request.POST.get("declaration") == "on")
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", [str(exc)])
        response = render(request, "qa_portal/application/page.html",
                          _context(request, inst, app, error=" ".join(msgs)), status=422)
        return toast(response, "error", "Not submitted", " ".join(msgs))
    app.refresh_from_db()
    response = render(request, "qa_portal/application/page.html", _context(request, inst, app, submitted=True))
    return toast(response, "success", "Application submitted",
                 "Legal, Finance, Academic Planning and Physical Planning will review it at the same time.")


class ProgrammeForm(QAForm):
    name = forms.CharField(max_length=200, label="Programme")
    level = forms.ModelChoiceField(ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin"))
    category = forms.ModelChoiceField(ProgrammeCategory.objects.filter(is_active=True), required=False)


@institution_required
@require_http_methods(["GET", "POST"])
def programmes(request):
    """Programme selection (onboarding stage `programme_selection`). Phase 2
    lets the institution list its programmes; programme clearance and fees
    open in Phase 3."""
    inst = _inst(request)
    app = _app(request, inst)
    selecting = app is not None and app.status == "programme_selection" and not getattr(
        request, "is_impersonating", False)
    form = ProgrammeForm(request.POST or None)
    if request.method == "POST":
        if not selecting:
            raise Http404
        if form.is_valid():
            d = form.cleaned_data
            exists = Programme.objects.for_user(request.user).filter(institution=inst, name__iexact=d["name"].strip(),
                                                                     level=d["level"]).exists()
            if exists:
                form.add_error("name", "This programme is already on your list.")
            else:
                inst_services.add_proposed_programme(inst, d["name"].strip(), d["level"], d.get("category"),
                                                     application=app, user=request.user)
                response = render(request, "qa_portal/application/_programmes.html",
                                  _context(request, inst, app, form=ProgrammeForm(), selecting=selecting))
                return toast(response, "success", "Programme added")
        return render(request, "qa_portal/application/_programmes.html",
                      _context(request, inst, app, form=form, selecting=selecting), status=422)
    return render(request, "qa_portal/application/_programmes.html",
                  _context(request, inst, app, form=form, selecting=selecting))


@institution_required
@require_POST
def programme_remove(request, pk):
    inst = _inst(request)
    app = _app(request, inst)
    programme = Programme.objects.for_user(request.user).filter(pk=pk, institution=inst).first()
    if programme is None or app is None or app.status != "programme_selection" or programme.status != "proposed" \
            or getattr(request, "is_impersonating", False):
        raise Http404
    inst_services.remove_proposed_programme(programme, user=request.user)
    response = render(request, "qa_portal/application/_programmes.html",
                      _context(request, inst, app, form=ProgrammeForm(), selecting=True))
    return toast(response, "info", "Programme removed")
