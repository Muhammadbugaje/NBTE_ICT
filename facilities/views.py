"""v2.2 — Facilities & equipment register screens (/qa/facilities/)."""
from __future__ import annotations

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core import importdiff

from . import services
from .diffspecs import FacilityItemDiff
from .models import FacilityItem, FacilityProgramme


def _guard(request):
    if not services.can_view(request.user):
        raise Http404


@login_required
@require_GET
def index(request):
    _guard(request)
    data = services.overview()
    q = (request.GET.get("q") or "").strip()
    level = request.GET.get("level") or ""
    progs = data["programmes"]
    if level:
        progs = [p for p in progs if level in (p.levels or "")]
    pq = (request.GET.get("p") or "").strip().lower()
    if pq:
        progs = [p for p in progs if pq in p.name.lower()]
    return render(request, "facilities/index.html", {
        **data, "rows": progs, "q": q, "level": level, "pq": request.GET.get("p", ""),
        "hits": services.search(q) if q else [], "can_manage": services.can_manage(request.user),
        "can_assign": services.can_assign_handlers(request.user), "handlers": services.handlers(),
        "spec": FacilityItemDiff(),
    })


@login_required
@require_GET
def programme(request, slug):
    _guard(request)
    prog = get_object_or_404(FacilityProgramme.objects.select_related("assigned_to", "programme_master"), slug=slug)
    sections = list(prog.sections.prefetch_related("items").order_by("sort_order"))
    show_all = request.GET.get("inactive") == "1"
    for s in sections:
        s.rows = [i for i in s.items.all() if show_all or i.is_active]
    from accounts.models import User

    staff = []
    if services.can_manage(request.user):
        staff = list(User.objects.filter(is_active=True, user_type="staff").filter(
            Q(groups__name=services.MANAGERS_GROUP) | Q(pk=request.user.pk)).distinct().order_by("first_name"))
    masters = []
    if services.can_manage(request.user) and not prog.programme_master_id:
        from institutions.models import ProgrammeMaster

        words = [w for w in prog.name.split() if len(w) > 3][:2]
        qs = ProgrammeMaster.objects.all()
        for w in words:
            qs = qs.filter(name__icontains=w)
        masters = list(qs.order_by("name")[:20])
    return render(request, "facilities/programme.html", {
        "prog": prog, "sections": sections, "can_manage": services.can_manage(request.user), "staff": staff,
        "masters": masters, "show_all": show_all,
        "n_items": sum(len(s.rows) for s in sections),
    })


@login_required
@require_POST
def item_update(request, pk):
    """Inline edit of one item's quantities (HTMX; optimistic UI on the page)."""
    if not services.can_manage(request.user):
        raise PermissionDenied
    item = get_object_or_404(FacilityItem, pk=pk)
    for f in ("nd_qty", "hnd_qty", "remarks"):
        if f in request.POST:
            setattr(item, f, " ".join(request.POST.get(f, "").split())[:60 if f != "remarks" else 500])
    if "is_critical" in request.POST:
        item.is_critical = request.POST.get("is_critical") == "on"
    if "is_active" in request.POST:
        item.is_active = request.POST.get("is_active") == "on"
    item.updated_by = request.user
    item.save()
    return render(request, "facilities/_item_row.html", {"i": item, "can_manage": True, "saved": True})


@login_required
@require_POST
def programme_update(request, slug):
    prog = get_object_or_404(FacilityProgramme, slug=slug)
    if not services.can_manage(request.user):
        raise PermissionDenied
    verb = request.POST.get("verb")
    if verb == "assign":
        from accounts.models import User

        uid = request.POST.get("user")
        prog.assigned_to = User.objects.filter(pk=uid, is_active=True).first() if uid else None
        prog.save(update_fields=["assigned_to", "updated_at"])
        if prog.assigned_to:
            from notifications.services import notify

            notify(prog.assigned_to, title="Facilities list assigned to you",
                   body=f"You now maintain the facilities and equipment list for {prog.name}.",
                   url=reverse("facilities:programme", args=[prog.slug]))
        messages.success(request, "Officer updated.")
    elif verb == "master":
        from institutions.models import ProgrammeMaster

        prog.programme_master = ProgrammeMaster.objects.filter(pk=request.POST.get("master")).first()
        prog.save(update_fields=["programme_master", "updated_at"])
        messages.success(request, "Linked to the programme master list.")
    elif verb == "sync":
        try:
            out = services.sync_to_catalogue(prog, actor=request.user, level=request.POST.get("level") or "ND")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"SSQ catalogue updated: {out['new']} new, {out['updated']} updated, "
                                      f"{out['retired']} retired.")
    elif verb == "notes":
        prog.notes = request.POST.get("notes", "")[:5000]
        prog.save(update_fields=["notes", "updated_at"])
        messages.success(request, "Notes saved.")
    return redirect("facilities:programme", slug=prog.slug)


class _OneProgramme(FacilityItemDiff):
    def __init__(self, prog=None):
        self._prog = prog

    def export_queryset(self):
        qs = self.queryset()
        return qs.filter(programme=self._prog) if self._prog is not None else qs


@login_required
@require_GET
def export(request, slug=None):
    _guard(request)
    prog = get_object_or_404(FacilityProgramme, slug=slug) if slug else None
    data = importdiff.export_workbook(_OneProgramme(prog))
    name = f"NBTE_facilities_{prog.slug if prog else 'all'}_{datetime.date.today():%Y%m%d}.xlsx"
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@login_required
def import_word(request):
    """Upload the curricula Word files (.docx or a .zip of them); they are
    turned into the Excel layout and shown in the usual change preview."""
    if not services.can_manage(request.user):
        raise PermissionDenied
    error = ""
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            error = "Choose a .docx or .zip file."
        else:
            try:
                parsed = services.parse_upload(f)
                if not parsed:
                    raise ValidationError("No programme list was found in that file.")
                xlsx = services.rows_to_workbook(services.parsed_rows(parsed))
                upload = SimpleUploadedFile("facilities.xlsx", xlsx)
                run = importdiff.preview(FacilityItemDiff(), upload, user=request.user)
            except (ValidationError, importdiff.ImportRejected) as exc:
                error = " ".join(getattr(exc, "messages", [str(exc)]))
            except Exception as exc:  # a corrupt Word file
                error = f"That file could not be read ({type(exc).__name__})."
            else:
                messages.success(request, f"{len(parsed)} programme list(s) read. Check the changes, then apply.")
                return redirect(reverse("core:import_preview", args=[run.pk]))
    return render(request, "facilities/import_word.html", {"error": error}, status=400 if error else 200)


@login_required
@require_POST
def handlers_update(request):
    from accounts.models import User

    target = get_object_or_404(User, pk=request.POST.get("user"), is_active=True, user_type="staff")
    try:
        services.set_handler(request.user, target, add=request.POST.get("verb") != "remove", actor=request.user)
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Handlers updated.")
    return redirect(reverse("facilities:index") + "#handlers")


@login_required
@require_GET
def staff_search(request):
    """Small HTMX lookup for the handlers picker."""
    from accounts.models import User

    if not services.can_assign_handlers(request.user):
        raise Http404
    q = (request.GET.get("q") or "").strip()
    users = []
    if len(q) >= 2:
        users = list(User.objects.filter(is_active=True, user_type="staff").filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))[:10])
    return render(request, "facilities/_staff_pick.html", {"users": users})


# ---------------------------------------------------------------------------
# Smart Excel import / export (one workbook layout: facilities_smart_import)
# ---------------------------------------------------------------------------
_SMART_SESSION = "facilities_smart_imports"


def _smart_dir():
    import tempfile
    from pathlib import Path

    d = Path(tempfile.gettempdir()) / "nbte_facilities_imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _smart_path(request, token):
    import re

    if not re.fullmatch(r"[0-9a-f]{32}", token or "") or token not in request.session.get(_SMART_SESSION, {}):
        raise Http404
    path = _smart_dir() / f"{token}.xlsx"
    if not path.exists():
        raise Http404
    return path


def _smart_cleanup():
    import time

    cutoff = time.time() - 2 * 86400
    for f in _smart_dir().glob("*.xlsx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


@login_required
def smart_import(request):
    """Step 1: upload the facilities workbook (.xlsx)."""
    import uuid

    from . import smart_import as si

    if not services.can_manage(request.user):
        raise PermissionDenied
    error = ""
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f:
            error = "Choose the Excel file (.xlsx) to import."
        elif not f.name.lower().endswith(".xlsx"):
            error = "Upload an Excel workbook (.xlsx). To start from the curricula Word files use Import Word files."
        elif f.size > 25 * 1024 * 1024:
            error = "The file is larger than 25 MB."
        else:
            data = f.read()
            try:
                import io

                si.read_workbook(io.BytesIO(data))
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                _smart_cleanup()
                token = uuid.uuid4().hex
                (_smart_dir() / f"{token}.xlsx").write_bytes(data)
                store = request.session.get(_SMART_SESSION, {})
                store = dict(list(store.items())[-4:])     # keep the last few uploads only
                store[token] = {"name": f.name[:120]}
                request.session[_SMART_SESSION] = store
                return redirect("facilities:smart_import_preview", token=token)
    return render(request, "facilities/smart_import.html", {"error": error, "stats": services.overview()},
                  status=400 if error else 200)


@login_required
def smart_import_preview(request, token):
    """Step 2: what will happen, per programme, and the decisions for programmes
    that are not on the national programme master list. POST applies it."""
    from . import smart_import as si

    if not services.can_manage(request.user):
        raise PermissionDenied
    path = _smart_path(request, token)
    with open(path, "rb") as fh:
        try:
            data = si.read_workbook(fh)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("facilities:smart_import")
    error = ""
    if request.method == "POST":
        decisions = {}
        preview = si.build_preview(data)
        for i, row in enumerate(preview["programmes"]):
            if not row["needs_decision"]:
                decisions[row["key"]] = "skip" if request.POST.get(f"skip-{i}") == "1" else "link"
                continue
            choice = request.POST.get(f"d-{i}") or "create"
            if choice == "map":
                master = request.POST.get(f"m-{i}") or ""
                if not master.isdigit():
                    error = f"Choose the existing programme to map “{row['name']}” to, or pick another option."
                    break
                choice = f"map:{master}"
            elif choice not in ("create", "none", "skip"):
                choice = "create"
            decisions[row["key"]] = choice
        if not error:
            try:
                out = si.apply(data, decisions, actor=request.user,
                               deactivate_missing=request.POST.get("deactivate_missing") == "1")
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                out.pop("created_ids", None)
                request.session["facilities_smart_result"] = {**out, "file": request.session.get(
                    _SMART_SESSION, {}).get(token, {}).get("name", "")}
                store = request.session.get(_SMART_SESSION, {})
                store.pop(token, None)
                request.session[_SMART_SESSION] = store
                try:
                    path.unlink()
                except OSError:
                    pass
                messages.success(request, f"Import finished: {out['items_created']:,} items added, "
                                          f"{out['items_updated']:,} updated.")
                return redirect("facilities:smart_import_done")
    preview = si.build_preview(data)
    for i, row in enumerate(preview["programmes"]):
        row["idx"] = i
        row["default"] = si.default_decision(row["match"])
        row["default_map"] = row["default"].startswith("map:")
    return render(request, "facilities/smart_import_preview.html", {
        "p": preview, "token": token, "error": error, "masters": si.master_choices(),
        "file_name": request.session.get(_SMART_SESSION, {}).get(token, {}).get("name", "facilities.xlsx"),
        "decide": [r for r in preview["programmes"] if r["needs_decision"]],
    }, status=400 if error else 200)


@login_required
@require_GET
def smart_import_done(request):
    if not services.can_manage(request.user):
        raise PermissionDenied
    result = request.session.get("facilities_smart_result")
    if not result:
        return redirect("facilities:smart_import")
    return render(request, "facilities/smart_import_done.html", {"r": result})


@login_required
@require_GET
def smart_export(request):
    """The register in the smart-import workbook layout (all, or ?programme=<slug>)."""
    from . import smart_import as si

    _guard(request)
    prog = None
    if request.GET.get("programme"):
        prog = get_object_or_404(FacilityProgramme, slug=request.GET["programme"])
    data = si.export_bytes([prog] if prog else None)
    response = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{si.export_filename(prog)}"'
    return response
