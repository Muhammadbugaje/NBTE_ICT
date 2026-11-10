"""Settings -> States & LGAs (v2.1 X49). Permission: staff.manage_geography.

States: add / edit (name, code, zone, active). LGAs per state: add, rename,
headquarters, deactivate; delete only when nothing refers to the LGA.
Every save bumps the LGA cache so the public register dropdown updates at once.
HTMX fragment swaps; every pushed URL (?state=<code>) renders the full page.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from .geo import bump_lga_cache
from .models import LGA, StateOfOrigin, ZonalOffice

PERM = "staff.manage_geography"


def _htmx(request):
    return request.headers.get("HX-Request") == "true"


def _require(request):
    if not request.user.has_perm(PERM):
        raise PermissionDenied


def _states(q=""):
    qs = StateOfOrigin.objects.annotate(n_lgas=Count("lgas", filter=Q(lgas__is_active=True)))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__iexact=q) | Q(lgas__name__icontains=q)).distinct()
    return qs.order_by("name")


def _state_panel_ctx(state, *, error="", notice="", values=None, edit_id=None):
    return {"state": state, "lgas": list(state.lgas.order_by("name")) if state else [],
            "error": error, "notice": notice, "values": values or {}, "edit_id": edit_id,
            "zones": StateOfOrigin.ZONE_CHOICES}


@login_required
def index(request):
    _require(request)
    q = (request.GET.get("q") or "").strip()
    code = request.GET.get("state") or ""
    state = StateOfOrigin.objects.filter(code=code).first() if code else None
    if state is None:
        state = _states().first()
    ctx = {"states": _states(q), "q": q, "zones": StateOfOrigin.ZONE_CHOICES,
           "offices": ZonalOffice.objects.prefetch_related("states").order_by("sort_order", "name"),
           "total_lgas": LGA.objects.filter(is_active=True).count(), **_state_panel_ctx(state)}
    if _htmx(request) and request.GET.get("fragment") == "list":
        return render(request, "staff/geo/_state_list.html", ctx)
    return render(request, "staff/geo/index.html", ctx)


@login_required
def state_panel(request, code):
    _require(request)
    state = get_object_or_404(StateOfOrigin, code=code)
    if not _htmx(request):
        return redirect(reverse("geo:index") + f"?state={state.code}")
    return render(request, "staff/geo/_state_panel.html", _state_panel_ctx(state))


@login_required
@require_POST
def state_save(request):
    _require(request)
    pk = request.POST.get("id")
    state = StateOfOrigin.objects.filter(pk=pk).first() if pk else None
    name = " ".join((request.POST.get("name") or "").split()).upper()
    code = (request.POST.get("code") or "").strip().lower()
    zone = request.POST.get("zone") or ""
    error = ""
    if not name or not code:
        error = "A state needs a name and a short code (e.g. kd)."
    elif StateOfOrigin.objects.filter(code=code).exclude(pk=getattr(state, "pk", None)).exists():
        error = f"The code '{code}' is already used."
    elif StateOfOrigin.objects.filter(name__iexact=name).exclude(pk=getattr(state, "pk", None)).exists():
        error = f"{name} already exists."
    elif zone not in dict(StateOfOrigin.ZONE_CHOICES):
        error = "Choose the geopolitical zone."
    if error:
        messages.error(request, error)
        return redirect(reverse("geo:index") + (f"?state={state.code}" if state else ""))
    if state is None:
        state = StateOfOrigin.objects.create(name=name, code=code, zone=zone)
        messages.success(request, f"{name} added. Add its LGAs below.")
    else:
        state.name, state.code, state.zone = name, code, zone
        state.is_active = request.POST.get("is_active") == "on"
        # v2.2 X38: travel mode and distance for visit costing
        if request.POST.get("travel_mode") in ("road", "air"):
            state.travel_mode = request.POST["travel_mode"]
        km = (request.POST.get("distance_from_hq_km") or "").strip()
        if km.isdigit():
            state.distance_from_hq_km = int(km)
        state.save()
        bump_lga_cache()
        messages.success(request, f"{name} saved.")
    return redirect(reverse("geo:index") + f"?state={state.code}")


@login_required
@require_POST
def lga_save(request, code):
    _require(request)
    state = get_object_or_404(StateOfOrigin, code=code)
    pk = request.POST.get("id")
    lga = LGA.objects.filter(pk=pk, state=state).first() if pk else None
    name = " ".join((request.POST.get("name") or "").split())
    hq = " ".join((request.POST.get("headquarters") or "").split())
    values = {"name": name, "headquarters": hq}
    error = ""
    if not name:
        error = "Enter the LGA name."
    elif LGA.objects.filter(state=state, name__iexact=name).exclude(pk=getattr(lga, "pk", None)).exists():
        error = f"{state.name} already has an LGA called {name}."
    if error:
        ctx = _state_panel_ctx(state, error=error, values=values, edit_id=getattr(lga, "pk", None))
        if _htmx(request):
            return render(request, "staff/geo/_state_panel.html", ctx, status=400)
        messages.error(request, error)
        return redirect(reverse("geo:index") + f"?state={state.code}")
    if lga is None:
        LGA.objects.create(state=state, name=name, headquarters=hq)
        notice = f"{name} added to {state.name}. It shows in the registration dropdown now."
    else:
        lga.name, lga.headquarters = name, hq
        lga.is_active = request.POST.get("is_active") == "on"
        lga.save()
        notice = f"{name} saved."
    if _htmx(request):
        return render(request, "staff/geo/_state_panel.html", _state_panel_ctx(state, notice=notice))
    messages.success(request, notice)
    return redirect(reverse("geo:index") + f"?state={state.code}")


@login_required
@require_POST
def lga_delete(request, code, pk):
    _require(request)
    state = get_object_or_404(StateOfOrigin, code=code)
    lga = get_object_or_404(LGA, pk=pk, state=state)
    from institutions.services import lga_in_use

    used = lga_in_use(lga)
    if used:
        lga.is_active = False
        lga.save(update_fields=["is_active", "updated_at"])
        notice = f"{lga.name} is used by institutions, so it was deactivated instead of deleted."
    else:
        lga.delete()
        notice = f"{lga.name} deleted."
    if _htmx(request):
        return render(request, "staff/geo/_state_panel.html", _state_panel_ctx(state, notice=notice))
    messages.success(request, notice)
    return redirect(reverse("geo:index") + f"?state={state.code}")
