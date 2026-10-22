"""Settings → Backups (/settings/backups/). Master Plan §22.

Every POST falls back to a full-page redirect with a message when it is not
an HTMX request (§20 interaction contract)."""
import os
import shutil

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from config.models import ConfigEntry

from . import crypto, dispatch, engine, tasks
from .destinations import DestinationError, detect_mounts, driver_for
from .forms import DestinationForm, suggested_prefix
from .models import BackupDestination, BackupRun, RestoreDrill

SETTING_KEYS = ["qa.backup.interval_days", "qa.backup.keep_count", "qa.backup.drill_every_n_runs",
                "qa.backup.encrypt_at_rest"]


def _require(request, perm):
    if not request.user.has_perm(f"backups.{perm}"):
        raise PermissionDenied


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _back(anchor=""):
    response = redirect("backups:index")
    if anchor:
        response["Location"] += f"#{anchor}"
    return response


def _env_line(name, kind=""):
    examples = {
        "ACCESS_KEY_ID": "your-access-key-id", "SECRET_ACCESS_KEY": "your-secret-access-key",
        "ENDPOINT_URL": "https://<account-id>.r2.cloudflarestorage.com", "REGION": "auto",
    }
    for suffix, sample in examples.items():
        if name.endswith(suffix):
            return f"{name}={sample}"
    return f"{name}="


def _destination_rows(user):
    can_manage = user.has_perm("backups.manage_destinations")
    rows = []
    for dest in BackupDestination.objects.all():
        env = dest.env_status()
        rows.append({
            "dest": dest,
            "env": env,
            "env_lines": "\n".join(_env_line(row["name"]) for row in env),
            "form": DestinationForm(instance=dest) if can_manage else None,
            "copies": dest.copies.filter(status="ok", deleted_at__isnull=True).count(),
        })
    return rows


def _schedule_rows():
    entries = {e.key: e for e in ConfigEntry.objects.filter(key__in=SETTING_KEYS, scope="global")}
    return [entries[k] for k in SETTING_KEYS if k in entries]


def _history_context():
    runs = list(BackupRun.objects.select_related("triggered_by").prefetch_related(
        "copies__destination", "drills")[:25])
    drills = list(RestoreDrill.objects.select_related("backup_run", "triggered_by")[:10])
    busy = any(r.is_running for r in runs) or any(d.result == RestoreDrill.RUNNING for d in drills)
    return {"runs": runs, "drills": drills, "history_busy": busy}


def _pg_dump_status():
    if engine.db_engine() != "postgresql":
        return None
    bin_dir = os.environ.get("BACKUP_PG_BIN_DIR", "").strip()
    found = shutil.which("pg_dump", path=bin_dir) if bin_dir else shutil.which("pg_dump")
    return found or ""


@login_required
@require_GET
def index(request):
    _require(request, "view_backups")
    user = request.user
    will_encrypt, enc_reason = crypto.status(engine.encrypt_setting())
    destinations = _destination_rows(user)
    active = [r for r in destinations if r["dest"].is_active]
    last_ok = BackupRun.objects.filter(status__in=[BackupRun.SUCCEEDED, BackupRun.PARTIAL]).first()
    last_any = BackupRun.objects.exclude(status=BackupRun.RUNNING).first()
    can_manage = user.has_perm("backups.manage_destinations")
    s3_prefixes = sorted({r["dest"].credential_env_prefix for r in destinations if r["dest"].credential_env_prefix})
    ctx = {
        "destinations": destinations,
        "active_count": len(active),
        "current": engine.current_run(),
        "last_ok": last_ok,
        "last_any": last_any,
        "next_due": engine.next_due_at(),
        "interval_days": engine.interval_days(),
        "keep_count": engine.keep_count(),
        "drill_every": engine.drill_every(),
        "encrypt_setting": engine.encrypt_setting(),
        "will_encrypt": will_encrypt,
        "enc_reason": enc_reason,
        "enc_key_set": crypto.key_is_set(),
        "crypto_available": crypto.library_available(),
        "no_destination_warning": engine.NO_DESTINATION_WARNING if not active else "",
        "schedule_rows": _schedule_rows() if can_manage else [],
        "new_form": DestinationForm(initial={"kind": "local", "is_active": True}) if can_manage else None,
        "mounts": detect_mounts() if can_manage else [],
        "db_engine": engine.db_engine(),
        "pg_dump": _pg_dump_status(),
        "media_roots": engine.media_roots(),
        "can_run": user.has_perm("backups.run_backup"),
        "can_manage": can_manage,
        "can_drill": user.has_perm("backups.run_restore_drill"),
        "suggested_prefix": suggested_prefix("primary"),
        "s3_prefixes": s3_prefixes,
        "now": timezone.now(),
        **_history_context(),
    }
    return render(request, "backups/index.html", ctx)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
@login_required
@require_POST
def run_now(request):
    _require(request, "run_backup")
    try:
        run = engine.start_run(BackupRun.MANUAL, request.user)
    except engine.AlreadyRunning as exc:
        if _is_htmx(request):
            return render(request, "backups/_progress.html", {"run": engine.current_run(), "notice": str(exc)})
        messages.warning(request, str(exc))
        return _back("bk-run")
    dispatch.background(tasks.perform_run, run.pk)
    if _is_htmx(request):
        return render(request, "backups/_progress.html", {"run": run})
    messages.success(request, "Backup started. This page shows its progress.")
    return _back("bk-run")


@login_required
@require_GET
def run_progress(request, pk):
    _require(request, "view_backups")
    run = get_object_or_404(BackupRun, pk=pk)
    ctx = {"run": run, "can_drill": request.user.has_perm("backups.run_restore_drill")}
    if not run.is_running:
        ctx.update(_history_context())
        ctx["oob_history"] = True
    return render(request, "backups/_progress.html", ctx)


@login_required
@require_GET
def history(request):
    _require(request, "view_backups")
    return render(request, "backups/_history.html", {**_history_context(), "can_drill":
                  request.user.has_perm("backups.run_restore_drill")})


@login_required
@require_GET
def manifest(request, pk):
    _require(request, "view_backups")
    run = get_object_or_404(BackupRun, pk=pk)
    if not run.manifest:
        raise Http404("This run has no manifest.")
    response = HttpResponse(engine.manifest_bytes(run).getvalue(), content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{run.label}-manifest.json"'
    return response


@login_required
@require_POST
def run_drill(request, pk):
    _require(request, "run_restore_drill")
    run = get_object_or_404(BackupRun, pk=pk)
    try:
        drill = engine.start_drill(run, request.user)
    except (engine.AlreadyRunning, engine.BackupFailed) as exc:
        messages.error(request, str(exc))
    else:
        dispatch.background(tasks.perform_drill, drill.pk)
        messages.success(request, f"Restore drill started for {run.label}. The result appears under Restore drills.")
    if _is_htmx(request):
        return render(request, "backups/_history.html", {**_history_context(), "can_drill": True})
    return _back("bk-drills")


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------
@login_required
@require_POST
def destination_save(request, pk=None):
    _require(request, "manage_destinations")
    instance = get_object_or_404(BackupDestination, pk=pk) if pk else None
    before = {"name": instance.name, "kind": instance.kind, "config": instance.config,
              "credential_env_prefix": instance.credential_env_prefix,
              "is_active": instance.is_active} if instance else {}
    form = DestinationForm(request.POST, instance=instance)
    if not form.is_valid():
        for field, errors in form.errors.items():
            label = form.fields[field].label or field.replace("_", " ").capitalize() if field in form.fields else ""
            messages.error(request, f"{label + ': ' if label else ''}{' '.join(errors)}")
        return _back(f"bk-dest-{pk}" if pk else "bk-add")
    dest = form.save()
    after = {"name": dest.name, "kind": dest.kind, "config": dest.config,
             "credential_env_prefix": dest.credential_env_prefix, "is_active": dest.is_active}
    engine._audit("update" if instance else "create", dest, request.user,
                  f"Backup destination {'updated' if instance else 'added'}",
                  changes={k: [before.get(k), v] for k, v in after.items() if before.get(k) != v})
    messages.success(request, f"Saved “{dest.name}”. Use Test connection to check it.")
    return _back(f"bk-dest-{dest.pk}")


@login_required
@require_POST
def destination_toggle(request, pk):
    _require(request, "manage_destinations")
    dest = get_object_or_404(BackupDestination, pk=pk)
    dest.is_active = not dest.is_active
    dest.save(update_fields=["is_active", "updated_at"])
    engine._audit("update", dest, request.user,
                  f"Backup destination {'turned on' if dest.is_active else 'turned off'}",
                  changes={"is_active": [not dest.is_active, dest.is_active]})
    messages.success(request, f"“{dest.name}” is now {'in use' if dest.is_active else 'turned off'}.")
    return _back(f"bk-dest-{dest.pk}")


@login_required
@require_POST
def destination_delete(request, pk):
    _require(request, "manage_destinations")
    dest = get_object_or_404(BackupDestination, pk=pk)
    if dest.copies.exists():
        messages.error(request, f"“{dest.name}” holds backup history, so it cannot be deleted. Turn it off instead.")
        return _back(f"bk-dest-{dest.pk}")
    engine._audit("delete", dest, request.user, "Backup destination deleted")
    name = dest.name
    dest.delete()
    messages.success(request, f"Deleted “{name}”.")
    return _back("bk-destinations")


@login_required
@require_POST
def destination_test(request, pk):
    if not (request.user.has_perm("backups.manage_destinations") or request.user.has_perm("backups.run_backup")):
        raise PermissionDenied
    dest = get_object_or_404(BackupDestination, pk=pk)
    try:
        message = driver_for(dest).test()
        ok = True
    except DestinationError as exc:
        message, ok = str(exc), False
    except Exception as exc:  # show the exact error, whatever raised it
        message, ok = f"{type(exc).__name__}: {exc}", False
    dest.last_tested_at, dest.last_test_ok, dest.last_test_message = timezone.now(), ok, message
    dest.save(update_fields=["last_tested_at", "last_test_ok", "last_test_message", "updated_at"])
    if _is_htmx(request):
        return render(request, "backups/_test_result.html", {"dest": dest})
    (messages.success if ok else messages.error)(request, f"{dest.name}: {message}")
    return _back(f"bk-dest-{dest.pk}")


@login_required
@require_GET
def mounts(request):
    _require(request, "manage_destinations")
    return render(request, "backups/_mounts.html", {"mounts": detect_mounts(), "refreshed": True})


# ---------------------------------------------------------------------------
# Schedule and retention (the qa.backup.* settings)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def schedule_save(request):
    _require(request, "manage_destinations")
    from qa_core.settings_service import normalise_input, save_setting

    changed = []
    for entry in _schedule_rows():
        field = entry.key.replace(".", "_")
        if entry.value_type == "bool":
            raw = "true" if request.POST.get(field) in ("1", "true", "on") else "false"
        elif field in request.POST:
            raw = request.POST.get(field, "").strip()
        else:
            continue
        if entry.value_type == "int":
            try:
                if int(raw) < 1:
                    raise ValueError
            except ValueError:
                messages.error(request, f"{entry.description.split('.')[0]}: enter a whole number of 1 or more.")
                continue
        new_value = normalise_input(entry, raw)
        if new_value == entry.value:
            continue
        try:
            save_setting(request.user, entry, new_value, reason="Settings → Backups")
        except (PermissionDenied, ValidationError) as exc:
            text = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, f"{entry.key}: {text}")
        else:
            changed.append(entry.key)
    if changed:
        messages.success(request, "Saved the backup schedule.")
    elif not messages.get_messages(request):
        messages.info(request, "Nothing changed.")
    return _back("bk-schedule")

