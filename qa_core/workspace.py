"""The workspace switcher (Phase 6).

Staff who can open QA screens choose between two sidebars: the main NBTE
system (HR, assets, tickets …) and the Quality Assurance workspace (only QA
links). The choice is saved on the user, so it follows them across devices.

Any page under ``/qa/`` always shows the QA sidebar, so a deep link never
lands on a QA screen without its navigation.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve, reverse
from django.views.decorators.http import require_POST

QA_PREFIX = "/qa/"
MAIN, QA = "main", "qa"


def can_use_qa(request):
    from .context_processors import qa_nav

    nav = qa_nav(request).get("qa_nav") or {}
    try:
        return bool(nav and nav.get("show"))
    except Exception:
        return False


def current(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return MAIN
    if getattr(request, "is_qa_portal", False) or not can_use_qa(request):
        return MAIN
    if request.path.startswith(QA_PREFIX):
        return QA
    return QA if getattr(user, "workspace", MAIN) == QA else MAIN


def context(request):
    """Template context processor: ``workspace`` = "main" or "qa"."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"workspace": current(request), "workspace_forced": request.path.startswith(QA_PREFIX)}


def _safe_path(raw):
    raw = (raw or "").strip()
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw


@login_required
@require_POST
def switch(request):
    target = request.POST.get("workspace")
    if target not in (MAIN, QA):
        return HttpResponseBadRequest("Unknown workspace.")
    user = request.user
    if getattr(user, "user_type", "staff") != "staff" or (target == QA and not can_use_qa(request)):
        return HttpResponse("You cannot open the QA workspace.", status=403)
    if user.workspace != target:
        from django.contrib.auth import get_user_model

        get_user_model().objects.filter(pk=user.pk).update(workspace=target)
        user.workspace = target
    here = _safe_path(request.POST.get("here"))
    is_htmx = request.headers.get("HX-Request", "").lower() == "true"
    path = urlsplit(here).path or "/"
    # Leaving QA from a QA screen: that page only has a QA sidebar, so go home.
    leaving = target == MAIN and path.startswith(QA_PREFIX)
    # Entering QA from the main system: open the first QA screen the user has.
    entering = target == QA and not path.startswith(QA_PREFIX) and request.POST.get("go") == "1"
    if leaving or entering or not is_htmx:
        destination = reverse("core:home") if leaving else (qa_home(request) if entering else here)
        if is_htmx:
            response = HttpResponse(status=204)
            response["HX-Redirect"] = destination
            return response
        return redirect(destination)
    # Stay on the page: re-render its sidebar for the new workspace.
    try:
        request.resolver_match = resolve(path)
    except Resolver404:
        request.resolver_match = None
    request.path = request.path_info = path
    html = render_to_string("partials/sidebar.html", request=request)
    response = HttpResponse(html)
    from .ui import toast

    title = "Quality Assurance workspace" if target == QA else "Main system"
    return toast(response, "info", f"Switched to the {title.lower() if target == MAIN else title}")


def qa_home(request):
    """Where "switch to QA" lands: the QA home (v2.1 N1) for anyone who can
    use the QA workspace; otherwise the first QA screen the user can open."""
    from .context_processors import qa_nav

    nav = qa_nav(request).get("qa_nav") or {}
    if nav.get("show"):
        return reverse("qa:home")
    for key, name in (("qa_cases", "qa_case:board"), ("qa_requests", "qa_req:list"),
                      ("institutions", "qa_inst:list"), ("applications", "qa_app:list"),
                      ("inspection", "qa_insp:inspections"), ("finance", "qa_fin:queue"),
                      ("complaints", "qa_complaints:queue"),
                      ("pending", "qa:pending_list")):
        try:
            if nav.get(key):
                return reverse(name)
        except Exception:
            continue
    return reverse("core:home")
