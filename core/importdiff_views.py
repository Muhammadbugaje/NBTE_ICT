"""Import diff preview pages (v2.1 X45): /imports/new/<spec>/ and /imports/<run>/.

Only the person who uploaded a file sees its preview; applying needs the
spec's permission. Tabs, checkboxes and "Select all" are HTMX fragment swaps;
every pushed URL (?tab=...) also renders the full page.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from . import importdiff
from .models import ImportRow, ImportRun

TABS = [("changed", "Changed"), ("new", "New"), ("unchanged", "Unchanged"), ("missing", "Missing from file"),
        ("error", "Errors")]
PAGE = 50


def _htmx(request):
    return request.headers.get("HX-Request") == "true"


def _spec_or_404(name, user):
    spec = importdiff.get_spec(name)
    if spec is None or not spec.can_use(user):
        raise Http404
    return spec


def _run_or_404(request, pk):
    run = get_object_or_404(ImportRun, pk=pk)
    if run.uploaded_by_id != request.user.pk:
        raise Http404
    spec = _spec_or_404(run.spec, request.user)
    return run, spec


@login_required
@require_http_methods(["GET", "POST"])
def upload(request, spec_name):
    spec = _spec_or_404(spec_name, request.user)
    error = ""
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            error = "Choose an Excel file (.xlsx)."
        elif not (f.name or "").lower().endswith(".xlsx"):
            error = "Only .xlsx files are accepted."
        else:
            try:
                run = importdiff.preview(spec, f, user=request.user)
            except importdiff.ImportRejected as exc:
                error = " ".join(exc.messages)
            else:
                return redirect(reverse("core:import_preview", args=[run.pk]))
    recent = ImportRun.objects.filter(uploaded_by=request.user, spec=spec.name)[:5]
    return render(request, "core/importdiff/upload.html", {"spec": spec, "error": error, "recent": recent},
                  status=400 if error else 200)


def _tab_rows(run, tab, page):
    actions = ("error", "conflict") if tab == "error" else (tab,)
    qs = run.rows.filter(action__in=actions).order_by("row_number", "id")
    total = qs.count()
    start = (page - 1) * PAGE
    return list(qs[start:start + PAGE]), total


def _context(request, run, spec, tab=None):
    counts = run.counts or {}
    if tab is None:
        tab = request.GET.get("tab") or next((t for t, _l in TABS if counts.get(t)), "changed")
    if tab not in dict(TABS):
        tab = "changed"
    try:
        page = max(1, int(request.GET.get("page") or 1))
    except ValueError:
        page = 1
    rows, total = _tab_rows(run, tab, page)
    for r in rows:
        chosen = r.accepted_fields if r.accepted_fields is not None else list(r.diff or {})
        r.field_rows = [{"field": f, "label": d.get("label", f), "old": d.get("old", ""), "new": d.get("new", ""),
                         "ticked": f in chosen, "warning": (r.warnings or {}).get(f, "")}
                        for f, d in (r.diff or {}).items()]
    tabs = []
    for key, label in TABS:
        n = counts.get(key, 0) + (counts.get("conflict", 0) if key == "error" else 0)
        tabs.append({"key": key, "label": label, "count": n, "active": key == tab})
    return {"run": run, "spec": spec, "tab": tab, "tabs": tabs, "rows": rows, "total": total, "page": page,
            "has_next": page * PAGE < total, "summary": importdiff.summary(run), "open": importdiff.is_open(run)}


@login_required
def preview(request, pk):
    run, spec = _run_or_404(request, pk)
    ctx = _context(request, run, spec)
    if _htmx(request) and request.GET.get("fragment") == "tab":
        ctx["fragment"] = True
        return render(request, "core/importdiff/_tab.html", ctx)
    return render(request, "core/importdiff/preview.html", ctx)


@login_required
def preview_tab(request, pk, tab):
    run, spec = _run_or_404(request, pk)
    ctx = _context(request, run, spec, tab=tab)
    if not _htmx(request):
        return redirect(reverse("core:import_preview", args=[run.pk]) + f"?tab={ctx['tab']}")
    ctx["fragment"] = True
    return render(request, "core/importdiff/_tab.html", ctx)


@login_required
@require_POST
def toggle(request, pk):
    """A checkbox changed: row, single field, or "select all in tab"."""
    run, spec = _run_or_404(request, pk)
    if not importdiff.is_open(run):
        return HttpResponse(status=409)
    on = request.POST.get("on") in ("1", "true", "on")
    if request.POST.get("all"):
        importdiff.set_all(run, request.POST["all"], on)
        ctx = _context(request, run, spec, tab=request.POST["all"])
        ctx["fragment"] = True
        return render(request, "core/importdiff/_tab.html", ctx)
    row_id = request.POST.get("row") or 0
    field = request.POST.get("field")
    if field:
        importdiff.set_field(run, row_id, field, on)
    else:
        importdiff.set_row(run, row_id, on)
    return render(request, "core/importdiff/_footer.html", {"run": run, "summary": importdiff.summary(run),
                                                            "open": True, "oob": True})


@login_required
@require_POST
def apply(request, pk):
    run, spec = _run_or_404(request, pk)
    try:
        result = importdiff.apply(run, user=request.user, spec=spec)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        msg = f"Applied: {result['applied']} row(s)."
        if result["conflicts"]:
            msg += f" {result['conflicts']} changed by someone else since the preview were not overwritten."
        if result["failed"]:
            msg += f" {result['failed']} could not be saved (see Errors)."
        (messages.success if not (result["conflicts"] or result["failed"]) else messages.warning)(request, msg)
    url = reverse("core:import_preview", args=[run.pk])
    if _htmx(request):
        response = HttpResponse(status=204)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


@login_required
@require_POST
def discard(request, pk):
    run, _spec = _run_or_404(request, pk)
    importdiff.discard(run, user=request.user)
    messages.info(request, "Discarded. Nothing was changed.")
    return redirect(reverse("core:import_upload", args=[run.spec]))


@login_required
def errors_xlsx(request, pk):
    run, spec = _run_or_404(request, pk)
    data = importdiff.errors_workbook(run, spec)
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="import-{run.pk}-rows-to-fix.xlsx"'
    return response


@login_required
def export_xlsx(request, spec_name):
    spec = _spec_or_404(spec_name, request.user)
    data = importdiff.export_workbook(spec)
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{spec.name}-export.xlsx"'
    return response
