"""Import/Export wizard — views.

Flow:
  GET  /import-export/                     → landing (module list)
  GET  /import-export/<spec>/              → panel for one module (partial, for HTMX)
  GET  /import-export/<spec>/template/     → .xlsx template download
  GET  /import-export/<spec>/export/       → .xlsx export of current data
  POST /import-export/<spec>/preview/      → dry-run, returns preview HTML (partial)
  POST /import-export/<spec>/commit/       → run import, returns result HTML (partial)
  GET  /import-export/<spec>/rejected/     → .xlsx report of rejected rows (from token)

Security:
  - All endpoints require login + `config.change_configentry` (the same
    "administrator" signal used by the settings page).
  - Uploaded files are stashed in PROTECTED_MEDIA_ROOT/import-tmp/<uuid>.xlsx and
    referenced by a session-scoped token. Only the same session can commit
    or download the rejected report for its own upload.
"""
import io
import logging
import os
import uuid
from datetime import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.utils.html import escape
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .importexport import REGISTRY, all_specs

# Modules whose import/export moved to a dedicated page. The wizard lists
# them, but opening one goes to that page (the staff directory import/export
# is the nominal roll: People data in the official HR format).
MERGED_SPECS = {"staff": "staff:nominal_roll"}

logger = logging.getLogger(__name__)

TMP_SUBDIR = "import-tmp"


def _tmp_storage():
    """Review L5: scratch uploads and rejected-row reports hold staff data,
    so they live in the restricted storage (PROTECTED_MEDIA_ROOT, no public
    URL) instead of public MEDIA_ROOT."""
    from core.storage import restricted_storage
    return restricted_storage()
TOKEN_SESSION_KEY = "import_tokens"
TOKEN_MAX_AGE_SECONDS = 3600  # 1 hour

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _spec_or_404(kind, name):
    cls = REGISTRY[kind].get(name)
    if cls is None:
        raise Http404(f"No {kind} spec named '{name}'.")
    return cls()


def _spec_group_or_404(name):
    specs = all_specs()
    if name not in specs:
        raise Http404(f"No module named '{name}'.")
    return specs[name]


def _save_upload(file_obj):
    """Persist an uploaded .xlsx under PROTECTED_MEDIA_ROOT/import-tmp and return its token."""
    token = uuid.uuid4().hex
    ext = os.path.splitext(file_obj.name or "")[1].lower() or ".xlsx"
    if ext not in (".xlsx", ".xlsm"):
        ext = ".xlsx"
    rel_path = f"{TMP_SUBDIR}/{token}{ext}"
    _tmp_storage().save(rel_path, file_obj)
    return token, rel_path


def _stash_token(request, spec_name, token, rel_path):
    store = request.session.get(TOKEN_SESSION_KEY) or {}
    store[spec_name] = {
        "token": token,
        "path": rel_path,
        "at": timezone.now().isoformat(),
    }
    request.session[TOKEN_SESSION_KEY] = store
    request.session.modified = True


def _pop_token(request, spec_name):
    store = request.session.get(TOKEN_SESSION_KEY) or {}
    entry = store.pop(spec_name, None)
    request.session[TOKEN_SESSION_KEY] = store
    request.session.modified = True
    return entry


def _token_age_ok(entry):
    if not entry:
        return False
    try:
        at = datetime.fromisoformat(entry["at"])
        return (timezone.now() - at).total_seconds() < TOKEN_MAX_AGE_SECONDS
    except Exception:
        return False


def _download(bytes_data, filename, content_type):
    resp = HttpResponse(bytes_data, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["Cache-Control"] = "no-store"
    return resp


def _notify_import_result(request, spec, result, rejected_url=""):
    """Create an in-app notification for the user who ran the import.
    Defensive: a notification failure must never break the import result page."""
    try:
        from notifications.models import Notification
        from notifications.services import notify

        summary = f"{result.created} created · {result.updated} updated"
        rejected_count = len(result.rejected)
        if rejected_count:
            summary += f" · {rejected_count} skipped"
        level = Notification.WARNING if rejected_count else Notification.SUCCESS
        notify(
            recipient=request.user,
            title=f"Import complete — {spec.label or spec.name}",
            body=summary,
            url=rejected_url or reverse("core:import_export_spec", args=[spec.name]),
            category=Notification.IMPORT,
            level=level,
            metadata={
                "spec": spec.name,
                "created": result.created,
                "updated": result.updated,
                "rejected": rejected_count,
            },
        )
    except Exception:
        logger.exception("Could not create import-complete notification")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@login_required
@permission_required("config.change_configentry", raise_exception=True)
def wizard_landing(request, spec_name=None):
    # Nominal-roll release: staff import/export is one page now.
    if spec_name in MERGED_SPECS:
        return redirect(MERGED_SPECS[spec_name])
    specs = all_specs()
    selected = specs.get(spec_name) if spec_name else None
    return render(request, "core/import_export.html", {
        "specs": specs,
        "selected_name": spec_name,
        "selected": selected,
    })


@login_required
@permission_required("config.change_configentry", raise_exception=True)
def wizard_panel(request, spec_name):
    """HTMX partial: the wizard panel for one module."""
    if spec_name in MERGED_SPECS:
        resp = HttpResponse(status=204)
        resp["HX-Redirect"] = reverse(MERGED_SPECS[spec_name])
        return resp
    spec = _spec_group_or_404(spec_name)
    return render(request, "core/_wizard_panel.html", {"spec": spec})


@login_required
@permission_required("config.change_configentry", raise_exception=True)
def download_template(request, spec_name):
    if spec_name in MERGED_SPECS:
        return redirect("staff:nominal_roll_template")
    spec = _spec_or_404("import", spec_name)
    data = spec.template_xlsx()
    return _download(data, f"{spec_name}-template.xlsx", XLSX_CONTENT_TYPE)


@login_required
@permission_required("config.change_configentry", raise_exception=True)
def download_export(request, spec_name):
    if spec_name in MERGED_SPECS:
        return redirect("staff:nominal_roll_export")
    spec = _spec_or_404("export", spec_name)
    data = spec.export_xlsx()
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return _download(data, f"{spec_name}-{stamp}.xlsx", XLSX_CONTENT_TYPE)


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def preview(request, spec_name):
    spec = _spec_or_404("import", spec_name)
    upload = request.FILES.get("file")
    if not upload:
        return HttpResponse(
            '<div class="wizard-error">No file was uploaded. Please choose an .xlsx file.</div>',
            status=400,
        )
    if not (upload.name or "").lower().endswith((".xlsx", ".xlsm")):
        return HttpResponse(
            '<div class="wizard-error">Only .xlsx files are accepted. '
            'Please download the template and fill it in.</div>',
            status=400,
        )

    # Stash the file, run a dry run, show results.
    token, rel_path = _save_upload(upload)
    _stash_token(request, spec_name, token, rel_path)

    try:
        with _tmp_storage().open(rel_path, "rb") as fh:
            result = spec.run_from_file(fh, dry_run=True)
    except Exception as exc:
        # Clean up the file if we couldn't read it.
        try:
            _tmp_storage().delete(rel_path)
        except Exception:
            pass
        _pop_token(request, spec_name)
        return HttpResponse(
            f'<div class="wizard-error">Could not read the file: {escape(exc)}</div>',  # review L4
            status=400,
        )

    return render(request, "core/_wizard_preview.html", {
        "spec": spec,
        "result": result,
        "token": token,
    })


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def commit(request, spec_name):
    spec = _spec_or_404("import", spec_name)
    entry = _pop_token(request, spec_name)
    if not _token_age_ok(entry):
        return HttpResponse(
            '<div class="wizard-error">Your upload session expired. '
            'Please upload the file again.</div>',
            status=400,
        )

    rel_path = entry["path"]
    try:
        with _tmp_storage().open(rel_path, "rb") as fh:
            result = spec.run_from_file(fh, dry_run=False)
    except Exception as exc:
        logger.exception("Import failed for %s", spec_name)
        return HttpResponse(
            f'<div class="wizard-error">Import failed: {escape(exc)}</div>',  # review L4
            status=400,
        )
    finally:
        try:
            _tmp_storage().delete(rel_path)
        except Exception:
            pass

    # Build the rejected-rows download URL for this session.
    rejected_url = ""
    if result.rejected:
        from .importexport import excel
        header, rows = result.rejected_table(spec.columns)
        xlsx = excel.write_rejected(header, rows, sheet_title="Rejected rows")
        rep_token = uuid.uuid4().hex
        rep_path = f"{TMP_SUBDIR}/{rep_token}-rejected.xlsx"
        _tmp_storage().save(rep_path, io.BytesIO(xlsx))
        store = request.session.get(TOKEN_SESSION_KEY) or {}
        store[f"{spec_name}:rejected"] = {
            "token": rep_token,
            "path": rep_path,
            "at": timezone.now().isoformat(),
        }
        request.session[TOKEN_SESSION_KEY] = store
        request.session.modified = True
        rejected_url = reverse("core:ie_rejected", args=[spec_name])

    # Let the operator know the import finished (in-app notification).
    _notify_import_result(request, spec, result, rejected_url=rejected_url)

    return render(request, "core/_wizard_result.html", {
        "spec": spec,
        "result": result,
        "rejected_url": rejected_url,
    })


@login_required
@permission_required("config.change_configentry", raise_exception=True)
def download_rejected(request, spec_name):
    store = request.session.get(TOKEN_SESSION_KEY) or {}
    entry = store.pop(f"{spec_name}:rejected", None)
    request.session[TOKEN_SESSION_KEY] = store
    request.session.modified = True
    if not _token_age_ok(entry):
        raise Http404("Rejected-rows report is no longer available.")
    try:
        with _tmp_storage().open(entry["path"], "rb") as fh:
            data = fh.read()
    finally:
        try:
            _tmp_storage().delete(entry["path"])
        except Exception:
            pass
    return _download(data, f"{spec_name}-rejected-rows.xlsx", XLSX_CONTENT_TYPE)