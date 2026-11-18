"""v2.1 Part 5: the NBTE staff "Programmes" section, mounted at /qa/programmes/.

One place for programme set-up work:

* overview — how many programmes NBTE recognises, how many are mapped to a
  professional body, and what the professional bodies are waiting for;
* requests — professional-body "put this programme under us" requests, with
  one approver (the master-list custodian: ``manage_programme_master``);
* requirements — the curriculum requirement lines a programme is measured
  against, filtered to the chosen programme; editing holds
  ``accreditation.manage_catalogue`` and follows the catalogue's
  version-and-retire rule;
* deep links into the existing master-list screens (/qa/professional-bodies/
  master), which stay the source of truth for master data.

Nothing here is writable without a permission; every change is audited.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from qa_core.permissions import require, require_or_404
from qa_core.ui import paginate, render_qa, toast

from . import pb_requests
from .pb_views import _can_view, can_manage_master
from .models import BodyProgrammeRequest


def _nav_ctx(request, tab):
    return {
        "tab": tab,
        "can_manage": can_manage_master(request.user),
        "can_catalogue": (request.user.is_superuser
                          or request.user.has_perm("accreditation.manage_catalogue")),
    }


def _filtered(url, **params):
    """Redirect that keeps the filters in the query string (never the path)."""
    clean = {k: v for k, v in params.items() if v}
    query = ("?" + urlencode(clean)) if clean else ""
    return f"{reverse(url)}{query}"


def _rejected(exc, url, title, **params):
    msg = "; ".join(getattr(exc, "messages", None) or [str(exc)])
    return toast(redirect(_filtered(url, **params)), "error", title, msg)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@require(_can_view)  # type: ignore[arg-type]
@require_GET
def landing(request):
    from accreditation.models import CatalogueItem
    from .models import ProgrammeMaster

    master_rows = ProgrammeMaster.objects.select_related("level", "professional_body")
    pending = BodyProgrammeRequest.objects.filter(status=BodyProgrammeRequest.PENDING)
    decided = BodyProgrammeRequest.objects.exclude(status=BodyProgrammeRequest.PENDING)[:6]
    ctx = {**_nav_ctx(request, "overview"),
           "total": master_rows.count(),
           "mapped": master_rows.filter(professional_body__isnull=False).count(),
           "requirements": CatalogueItem.objects.filter(is_active=True).count(),
           "pending": pending.count(),
           "recent_pending": list(pending[:6]),
           "recent_decided": list(decided),
           "popular": (ProgrammeMaster.objects.filter(is_active=True)
                       .annotate(used=Count("programmes", distinct=True)).order_by("-used")[:8])}
    return render_qa(request, "institutions/programmes/landing.html", ctx)


# ---------------------------------------------------------------------------
# Body programme requests
# ---------------------------------------------------------------------------
def _requests_ctx(request, **extra):
    f = request.GET if request.method == "GET" else request.POST
    qs = (BodyProgrammeRequest.objects
          .select_related("body", "institution", "level", "category", "requested_by", "decided_by", "master"))
    if f.get("status"):
        qs = qs.filter(status=f["status"])
    if f.get("body"):
        qs = qs.filter(body_id=f.get("body") or 0)
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(programme_name__icontains=q) | Q(institution__name__icontains=q)
                       | Q(institution__code__iexact=q) | Q(body__name__icontains=q)
                       | Q(body__acronym__iexact=q))
    ctx = {
        **_nav_ctx(request, "requests"),
        "page": paginate(request, qs),
        "filters": f,
        "pending_count": BodyProgrammeRequest.objects.filter(status=BodyProgrammeRequest.PENDING).count(),
        "statuses": BodyProgrammeRequest.STATUS_CHOICES,
    }
    ctx.update(extra)
    return ctx


@require(_can_view)  # type: ignore[arg-type]
def requests(request):
    ctx = _requests_ctx(request)
    return render_qa(request, "institutions/programmes/requests.html", ctx,
                     {"qa-panel": "institutions/programmes/_requests_panel.html"})


@require_or_404(_can_view)  # type: ignore[arg-type]
@require_GET
def request_detail(request, pk):
    from .models import Programme, ProgrammeMaster

    row = get_object_or_404(BodyProgrammeRequest.objects.select_related(
        "body", "institution", "level", "category", "requested_by", "decided_by"), pk=pk)
    programmes = Programme.unscoped.filter(name__iexact=row.programme_name).exclude(status="dropped")
    if row.institution_id:
        programmes = programmes.filter(institution=row.institution)
    if row.level_id:
        programmes = programmes.filter(level=row.level)
    master = (row.master
              or ProgrammeMaster.objects.filter(name__iexact=row.programme_name)
              .select_related("level", "professional_body").first())
    ctx = _requests_ctx(
        request, row=row,
        programmes=list(programmes.select_related("institution", "level")[:12]),
        master=master,
        requirements=pb_requests.related_requirements(row.body, row.programme_name, row.level))
    return render_qa(request, "institutions/programmes/request_detail.html", ctx)


@require(can_manage_master)  # type: ignore[arg-type]
@require_POST
def request_decide(request, pk):
    row = get_object_or_404(BodyProgrammeRequest.objects.select_related("body", "institution"), pk=pk)
    approve = request.POST.get("decision") == "approve"
    try:
        pb_requests.decide(row, user=request.user, approve=approve, remark=request.POST.get("remark", ""))
    except (ValidationError, PermissionDenied) as exc:
        return _rejected(exc, "qa_programmes:request_detail", "Not decided", )
    title = "Approved" if approve else "Rejected"
    detail = ("The programme is now under the body; its requirements apply from the next SSQ review."
              if approve else "The body has been told why.")
    return toast(redirect(reverse("qa_programmes:request_detail", args=[row.pk])), "success", title, detail)


@require(can_manage_master)  # type: ignore[arg-type]
@require_POST
def request_withdraw(request, pk):
    row = get_object_or_404(BodyProgrammeRequest, pk=pk)
    try:
        pb_requests.withdraw(row, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _rejected(exc, "qa_programmes:requests", "Not withdrawn")
    return toast(redirect(reverse("qa_programmes:requests")), "success", "Withdrawn")


# ---------------------------------------------------------------------------
# Curriculum requirements (a per-programme view over the shared catalogue)
# ---------------------------------------------------------------------------
def _requirements_ctx(request, **extra):
    from accreditation.models import CatalogueItem
    from .models import ProgrammeMaster

    f = request.GET if request.method == "GET" else request.POST
    qs = CatalogueItem.objects.select_related("category").order_by("programme_name", "kind", "sort_order", "name")
    show = f.get("show", "active")
    if show == "active":
        qs = qs.filter(is_active=True)
    if f.get("programme"):
        qs = qs.filter(programme_name=f["programme"])
    if f.get("kind") in ("facility", "equipment"):
        qs = qs.filter(kind=f["kind"])
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    programme_names = sorted(set(CatalogueItem.objects.exclude(programme_name="")
                                 .values_list("programme_name", flat=True)))
    master_names = ProgrammeMaster.objects.filter(is_active=True).values_list("name", flat=True).order_by("name")[:400]
    from core import importdiff

    spec = importdiff.get_spec("curriculum_requirements")
    ctx = {
        **_nav_ctx(request, "requirements"),
        "page": paginate(request, qs),
        "filters": f,
        "programmes": programme_names,
        # The one programme list the add form offers: the master list plus any
        # name already in the catalogue (so the form needs no free-text name).
        "programme_options": sorted(set(master_names) | set(programme_names), key=str.lower),
        "kinds": CatalogueItem.KIND_CHOICES,
        "show": show,
        "can_import": bool(spec is not None and spec.can_use(request.user)),
    }
    ctx.update(extra)
    return ctx


@require(_can_view)  # type: ignore[arg-type]
@require_GET
def requirements(request):
    ctx = _requirements_ctx(request)
    return render_qa(request, "institutions/programmes/requirements.html", ctx,
                     {"qa-panel": "institutions/programmes/_requirements_panel.html",
                      "req-rows": "institutions/programmes/_requirements_rows.html"})


def _requirements_done(request, kind, title, body="", **params):
    """Answer an add / retire. HTMX: a toast, and on success the list is
    reloaded in place (an error leaves the form as typed). No JS: redirect."""
    import json

    from django.http import HttpResponse

    dest = _filtered("qa_programmes:requirements", **params)
    if request.headers.get("HX-Request", "").lower() == "true":
        response = HttpResponse(status=204)
        if kind != "error":
            response["HX-Location"] = json.dumps({"path": dest, "target": "#qa-panel"})
    else:
        response = redirect(dest)
    return toast(response, kind, title, body)


def _catalogue_perm(user):
    return user.is_superuser or user.has_perm("accreditation.manage_catalogue")


@require(_catalogue_perm)  # type: ignore[arg-type]
@require_POST
def requirements_add(request):
    from accreditation.builder import create_item
    from institutions.models import ProgrammeCategory

    p = request.POST
    # One programme control: the add form posts the chosen programme as
    # ``programme_name``; an older form (or a filtered page) may send ``programme``.
    programme_name = (p.get("programme_name") or p.get("programme") or "").strip()
    try:
        category = ProgrammeCategory.objects.filter(pk=p.get("category") or 0).first()
        item = create_item(user=request.user, category=category, programme_name=programme_name,
                           kind=p.get("kind", ""), name=p.get("name", ""),
                           required_quantity=p.get("required_quantity", ""), unit=p.get("unit", "no."),
                           weight=p.get("weight", ""), min_spec=p.get("min_spec", ""),
                           is_critical=p.get("is_critical") == "on")
    except (ValidationError, PermissionDenied) as exc:
        return _requirements_done(request, "error", "Not added",
                                  "; ".join(getattr(exc, "messages", None) or [str(exc)]), programme=programme_name)
    return _requirements_done(request, "success", f"{item.name} added",
                              "SSQs already submitted keep the figures they were opened with.",
                              programme=item.programme_name or "")


@require(_catalogue_perm)  # type: ignore[arg-type]
@require_POST
def requirements_retire(request, pk):
    from accreditation.builder import retire_item
    from accreditation.models import CatalogueItem

    item = get_object_or_404(CatalogueItem.objects, pk=pk)
    name = item.programme_name
    try:
        retire_item(item, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _requirements_done(request, "error", "Not retired",
                                  "; ".join(getattr(exc, "messages", None) or [str(exc)]), programme=name)
    return _requirements_done(request, "success", "Requirement retired", "SSQs already submitted keep it.",
                              programme=name)
