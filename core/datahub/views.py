"""Data Hub pages. Superusers (site administrators) only: everyone else gets 403.

    GET  /data-hub/                          hub (categories, counts, import order)
    GET  /data-hub/export/<category>.xlsx    export ("everything" or one category);
                                             ?hashes=1 adds the users' password hashes
    GET  /data-hub/template/<category>.xlsx  blank workbook with the same sheets
    POST /data-hub/import/                   upload -> preview (rolled-back run)
    POST /data-hub/import/<token>/apply/     apply the previewed file (one transaction)
    POST /data-hub/import/<token>/discard/   forget the upload
"""
import logging
import os
import uuid
from datetime import datetime
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import engine, registry

logger = logging.getLogger(__name__)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SESSION_KEY = "datahub_uploads"
MAX_UPLOAD_MB = 40
TOKEN_TTL_SECONDS = 6 * 3600
TMP_DIR = "datahub-tmp"


def can_use_hub(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_active and user.is_superuser)


def superuser_only(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_use_hub(request.user):
            raise PermissionDenied("The Data Hub is for site administrators only.")
        return view(request, *args, **kwargs)
    return wrapped


def _storage():
    from core.storage import restricted_storage
    return restricted_storage()


def _category_or_404(key):
    if key == registry.EVERYTHING:
        return None
    cat = registry.get_category(key)
    if cat is None:
        raise Http404("Unknown category")
    return cat


def _filename(key, kind):
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    name = "Full_Configuration" if key == registry.EVERYTHING else key.replace("_", "-").title()
    return f"NBTE_{name}_{kind}_{stamp}.xlsx"


def _download(data, filename):
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Cache-Control"] = "no-store"
    return resp


def _hub_context():
    counts = engine.counts()
    cats, order, total_rows, total_sheets = [], [], 0, 0
    for i, cat in enumerate(registry.CATEGORIES, start=1):
        sheets = counts.get(cat.key, [])
        rows = sum(n for _sd, n in sheets)
        total_rows += rows
        total_sheets += len(sheets)
        links = [sd for sd in engine.plan() if sd.links_only and sd.category == cat.key]
        link_url = ""
        if cat.link_url:
            try:
                from django.urls import reverse
                link_url = reverse(cat.link_url)
            except Exception:
                link_url = ""
        cats.append({
            "n": i, "key": cat.key, "title": cat.title, "summary": cat.summary, "icon": cat.icon,
            "rows": rows, "sheets": [{"title": sd.title, "label": sd.label, "count": n} for sd, n in sheets],
            "links": [sd.title for sd in links], "link_url": link_url, "link_label": cat.link_label,
        })
    return {"categories": cats, "total_rows": total_rows, "total_sheets": total_sheets,
            "total_categories": len(cats), "max_mb": MAX_UPLOAD_MB}


@require_GET
@superuser_only
def hub(request):
    return render(request, "core/datahub/hub.html", _hub_context())


@require_GET
@superuser_only
def export(request, category):
    _category_or_404(category)
    hashes = request.GET.get("hashes") == "1"
    data = engine.export_workbook([category], include_rows=True, source_label=request.get_host(),
                                  include_password_hashes=hashes)
    _audit(request, "datahub_export", {"category": category, "password_hashes": hashes})
    return _download(data, _filename(category, "Export"))


@require_GET
@superuser_only
def template(request, category):
    _category_or_404(category)
    data = engine.export_workbook([category], include_rows=False)
    return _download(data, _filename(category, "Template"))


def _uploads(request):
    store = request.session.get(SESSION_KEY) or {}
    now = timezone.now()
    for tok, entry in list(store.items()):
        try:
            age = (now - datetime.fromisoformat(entry["at"])).total_seconds()
        except Exception:
            age = TOKEN_TTL_SECONDS + 1
        if age > TOKEN_TTL_SECONDS:
            _delete(entry.get("path"))
            store.pop(tok, None)
    return store


def _delete(path):
    if not path:
        return
    try:
        _storage().delete(path)
    except Exception:
        logger.warning("datahub: could not delete %s", path)


def _scope(value):
    if value and value != registry.EVERYTHING and registry.get_category(value):
        return [value]
    return [registry.EVERYTHING]


@require_POST
@superuser_only
def upload(request):
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose an .xlsx workbook to import.")
        return redirect("core:datahub")
    ext = os.path.splitext(f.name or "")[1].lower()
    if ext not in (".xlsx", ".xlsm"):
        messages.error(request, "Only Excel .xlsx workbooks can be imported.")
        return redirect("core:datahub")
    if f.size > MAX_UPLOAD_MB * 1024 * 1024:
        messages.error(request, f"The file is larger than {MAX_UPLOAD_MB} MB.")
        return redirect("core:datahub")
    scope = _scope(request.POST.get("scope"))
    default_password = request.POST.get("default_password") or ""
    pw_hash = None
    if default_password:
        problems = engine.default_password_problems(default_password)
        if problems:
            messages.error(request, "Default password for imported users: " + " ".join(problems))
            return redirect("core:datahub")
        from django.contrib.auth.hashers import make_password
        pw_hash = make_password(default_password)   # only the hash is kept between preview and apply
    token = uuid.uuid4().hex
    path = _storage().save(f"{TMP_DIR}/{token}.xlsx", f)
    with _storage().open(path, "rb") as fh:
        report = engine.import_workbook(fh, apply=False, categories=scope, default_password_hash=pw_hash,
                                        actor=request.user)
    if report.fatal:
        _delete(path)
        messages.error(request, report.fatal)
        return redirect("core:datahub")
    store = _uploads(request)
    store[token] = {"path": path, "name": f.name, "at": timezone.now().isoformat(), "scope": scope,
                    "pw_hash": pw_hash}
    request.session[SESSION_KEY] = store
    request.session.modified = True
    return render(request, "core/datahub/report.html", _report_context(report, token=token, name=f.name,
                                                                         scope=scope, stage="preview"))


@require_POST
@superuser_only
def apply(request, token):
    store = _uploads(request)
    entry = store.get(token)
    if not entry:
        messages.error(request, "That upload has expired. Upload the workbook again.")
        return redirect("core:datahub")
    skip_errors = request.POST.get("skip_errors") == "1"
    with _storage().open(entry["path"], "rb") as fh:
        report = engine.import_workbook(fh, apply=True, categories=entry.get("scope"), skip_errors=skip_errors,
                                        default_password_hash=entry.get("pw_hash"), actor=request.user)
    if report.applied:
        store.pop(token, None)
        _delete(entry["path"])
        request.session[SESSION_KEY] = store
        request.session.modified = True
        _clear_caches()
        _audit(request, "datahub_import", {
            "file": entry.get("name"), "scope": entry.get("scope"), "created": report.created,
            "updated": report.updated, "unchanged": report.unchanged, "errors": report.error_count,
        })
    return render(request, "core/datahub/report.html", _report_context(
        report, token=None if report.applied else token, name=entry.get("name"),
        scope=entry.get("scope"), stage="result"))


@require_POST
@superuser_only
def discard(request, token):
    store = _uploads(request)
    entry = store.pop(token, None)
    if entry:
        _delete(entry.get("path"))
    request.session[SESSION_KEY] = store
    request.session.modified = True
    messages.info(request, "The upload was discarded. Nothing was changed.")
    return redirect("core:datahub")


def _report_context(report, token, name, scope, stage):
    cats = {c.key: c for c in registry.CATEGORIES}
    groups = []
    for s in report.sheets:
        if not groups or groups[-1]["key"] != s.category:
            cat = cats[s.category]
            groups.append({"key": s.category, "title": cat.title, "sheets": []})
        s.status = "error" if s.errors else ("change" if (s.created or s.updated) else "same")
        s.changed_list = sorted(s.changed_fields.items(), key=lambda p: -p[1])[:8]
        groups[-1]["sheets"].append(s)
    scope_label = "Everything" if registry.EVERYTHING in (scope or []) else ", ".join(
        cats[k].title for k in scope or [] if k in cats)
    return {"report": report, "groups": groups, "token": token, "file_name": name, "stage": stage,
            "scope_label": scope_label}


def _clear_caches():
    # Config entries invalidate themselves (post_save fires on raw saves too).
    try:
        from qa_core.departments import clear_cache
        clear_cache()
    except Exception:
        pass


def _audit(request, action, changes):
    try:
        from core import audit
        audit.log(action, request.user, actor=request.user, changes=changes, note="Data Hub")
    except Exception:
        logger.exception("datahub: audit failed")
