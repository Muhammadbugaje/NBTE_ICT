"""The backup engine: build the artefacts, write them to every active
destination, rotate old copies, notify ICT and run restore drills.

Public entry points (used by the page, the Celery tasks and the commands):

    start_run(trigger, user)            -> BackupRun (status running) or raises AlreadyRunning
    perform_run(run_id)                 -> BackupRun (finished)
    run_backup(trigger, user)           -> start_run + perform_run, synchronously
    start_drill(run, user, automatic)   -> RestoreDrill (running)
    perform_drill(drill_id)             -> RestoreDrill (finished)
    restore(...)                        -> the superuser-only real restore (command only)
    schedule_due()                      -> (due, reason)
"""
import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import traceback
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connection, connections, transaction
from django.utils import timezone

import config

from . import crypto
from .destinations import DestinationError, driver_for, human_size
from .models import BackupCopy, BackupDestination, BackupRun, RestoreDrill

logger = logging.getLogger(__name__)

LOCK_KEY = "backups:run-lock"
LOCK_TTL = 6 * 60 * 60
STALE_AFTER = timedelta(hours=12)
MANIFEST_NAME = "manifest.json"
NO_DESTINATION_WARNING = "No backup destination configured. Set one before going live."

# Tables whose row counts go into the manifest and are compared by the drill.
KEY_MODELS = [
    "accounts.User", "accounts.Role", "staff.Staff", "staff.Department", "config.ConfigEntry",
    "tasks.Task", "tickets.Ticket", "attendance.AttendanceRecord", "leave.LeaveRequest",
    "institutions.Institution", "institutions.Programme", "payments.PaymentObligation",
    "accreditation.Application", "qa_core.FlowDefinition", "qa_core.TimelineEvent",
]
SNAPSHOT_MODELS = [
    "config.ConfigEntry", "qa_core.FlowDefinition", "qa_core.FlowStage", "qa_core.FlowTransition",
    "qa_core.ActionPolicy", "qa_core.ReferenceFormat",
]


class AlreadyRunning(Exception):
    pass


class BackupFailed(Exception):
    pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _int_setting(key, default, minimum=1):
    try:
        return max(minimum, int(config.get(key, default)))
    except (TypeError, ValueError):
        return default


def interval_days():
    return _int_setting("qa.backup.interval_days", 30)


def keep_count():
    return _int_setting("qa.backup.keep_count", 8)


def drill_every():
    return _int_setting("qa.backup.drill_every_n_runs", 4)


def encrypt_setting():
    value = config.get("qa.backup.encrypt_at_rest", True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def active_destinations():
    return list(BackupDestination.objects.filter(is_active=True).order_by("name"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _progress(run, pct, label):
    BackupRun.objects.filter(pk=run.pk).update(progress=pct, progress_label=label[:200])
    run.progress, run.progress_label = pct, label


def _staging_dir(prefix):
    base = os.environ.get("BACKUP_STAGING_DIR", "").strip() or None
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def db_engine(alias="default"):
    engine = settings.DATABASES[alias]["ENGINE"]
    if "sqlite" in engine:
        return "sqlite"
    if "postgres" in engine:
        return "postgresql"
    return engine.rsplit(".", 1)[-1]


def _model(label):
    from django.apps import apps

    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def key_tables():
    tables = []
    for label in KEY_MODELS:
        model = _model(label)
        if model is not None:
            tables.append((label, model._meta.db_table))
    return tables


def _audit(action, obj, actor, note, changes=None):
    try:
        from core import audit
        audit.log(action, obj, actor=actor, changes=changes or {}, note=note)
    except Exception:  # never let the audit trail break a backup
        logger.exception("backups: audit entry failed")


def ict_recipients():
    """Director of ICT role holders and ict-qa-monitor holders (fallback: superusers)."""
    from accounts.models import User
    from django.db.models import Q

    from qa_core.departments import qa_department

    ict = qa_department("ict")
    q = Q(role__code="ict-qa-monitor")
    if ict is not None:
        q |= Q(role__code="director", department=ict)
    users = list(User.objects.filter(q, is_active=True).distinct())
    if not users:
        users = list(User.objects.filter(is_superuser=True, is_active=True))
    return users


def _notify(title, body, level):
    try:
        from notifications.models import Notification
        from notifications.services import notify_many

        notify_many(ict_recipients(), title=title, body=body, url="/settings/backups/",
                    level=getattr(Notification, level.upper()), category=Notification.SYSTEM)
    except Exception:
        logger.exception("backups: notification failed")


# ---------------------------------------------------------------------------
# Database dump
# ---------------------------------------------------------------------------
def _sqlite_dump(out_path):
    """Consistent snapshot with the online-backup API, then an SQL text dump.
    Returns {table: count} for the key tables, read from the same snapshot."""
    connection.ensure_connection()
    raw = connection.connection
    if raw.in_transaction:
        # Inside an open transaction (tests, or a caller's atomic block) the
        # online-backup API would wait for our own lock forever: dump from
        # this connection, which already sees one consistent state.
        return _sqlite_iterdump(raw, out_path)
    snap_path = Path(str(out_path) + ".snapshot")
    snap = sqlite3.connect(snap_path)
    try:
        raw.backup(snap)
        return _sqlite_iterdump(snap, out_path)
    finally:
        snap.close()
        snap_path.unlink(missing_ok=True)


def _sqlite_iterdump(conn, out_path):
    counts = {}
    for label, table in key_tables():
        try:
            counts[label] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.Error:
            continue
    with open(out_path, "w", encoding="utf-8") as fh:
        for line in conn.iterdump():
            fh.write(line + "\n")
    return counts


def _pg_env(db):
    env = os.environ.copy()
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = str(db["PASSWORD"])
    if db.get("HOST"):
        env["PGHOST"] = str(db["HOST"])
    if db.get("PORT"):
        env["PGPORT"] = str(db["PORT"])
    if db.get("USER"):
        env["PGUSER"] = str(db["USER"])
    sslmode = (db.get("OPTIONS") or {}).get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    return env


def _pg_tool(name):
    bin_dir = os.environ.get("BACKUP_PG_BIN_DIR", "").strip()
    path = shutil.which(name, path=bin_dir) if bin_dir else shutil.which(name)
    if not path:
        raise BackupFailed(f"{name} is not installed on this server (or not on PATH). Install the PostgreSQL "
                           f"client tools (e.g. apt install postgresql-client) or set BACKUP_PG_BIN_DIR.")
    return path


def _pg_dump(out_path):
    """pg_dump --format=custom from an exported snapshot, so the key-table
    counts in the manifest come from exactly the same data as the dump."""
    pg_dump = _pg_tool("pg_dump")
    db = settings.DATABASES["default"]
    snap_conn = connections.create_connection("default")
    try:
        snap_conn.set_autocommit(False)
        with snap_conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cur.execute("SELECT pg_export_snapshot()")
            snapshot = cur.fetchone()[0]
            counts = {}
            for label, table in key_tables():
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[label] = cur.fetchone()[0]
        cmd = [pg_dump, "--format=custom", "--no-owner", "--no-privileges", f"--snapshot={snapshot}",
               "--file", str(out_path), str(db["NAME"])]
        proc = subprocess.run(cmd, env=_pg_env(db), capture_output=True, text=True, timeout=6 * 3600)
        if proc.returncode != 0:
            raise BackupFailed(f"pg_dump failed: {proc.stderr.strip() or proc.returncode}")
    finally:
        try:
            snap_conn.rollback()
        except Exception:
            pass
        snap_conn.close()
    return counts


def dump_database(stage):
    engine = db_engine()
    if engine == "sqlite":
        path = stage / "database.sqlite.sql"
        counts = _sqlite_dump(path)
    elif engine == "postgresql":
        path = stage / "database.pgdump"
        counts = _pg_dump(path)
    else:
        raise BackupFailed(f"Backups support SQLite and PostgreSQL only (this server uses {engine}).")
    return path, engine, counts


# ---------------------------------------------------------------------------
# Files and settings snapshot
# ---------------------------------------------------------------------------
def media_roots():
    """(artefact stem, setting name, path) for every file root that is backed up."""
    roots = [("media", "MEDIA_ROOT", Path(settings.MEDIA_ROOT))]
    protected = getattr(settings, "PROTECTED_MEDIA_ROOT", None)
    if protected:
        roots.append(("protected_media", "PROTECTED_MEDIA_ROOT", Path(protected)))
    return roots


def tar_folder(folder, out_path):
    files = 0
    with tarfile.open(out_path, "w:gz", compresslevel=6) as tar:
        if folder.is_dir():
            for root, dirs, names in os.walk(folder):
                dirs.sort()
                for name in sorted(names):
                    full = Path(root) / name
                    if full.is_symlink() or not full.is_file():
                        continue
                    tar.add(full, arcname=str(full.relative_to(folder)), recursive=False)
                    files += 1
    return files


def settings_snapshot(out_path):
    from django.core import serializers

    data = {"generated_at": timezone.now().isoformat(), "models": {}}
    for label in SNAPSHOT_MODELS:
        model = _model(label)
        if model is None:
            continue
        manager = getattr(model, "unscoped", None) or model._base_manager
        rows = json.loads(serializers.serialize("json", manager.all().order_by("pk")))
        data["models"][label] = rows
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
    return {label: len(rows) for label, rows in data["models"].items()}


# ---------------------------------------------------------------------------
# Running a backup
# ---------------------------------------------------------------------------
def current_run():
    return BackupRun.objects.filter(status=BackupRun.RUNNING,
                                    started_at__gte=timezone.now() - STALE_AFTER).first()


def _fail_stale_runs():
    stale = BackupRun.objects.filter(status=BackupRun.RUNNING, started_at__lt=timezone.now() - STALE_AFTER)
    for run in stale:
        run.status, run.finished_at = BackupRun.FAILED, timezone.now()
        run.error = run.error or "The run stopped without finishing (the server or worker restarted)."
        run.save(update_fields=["status", "finished_at", "error"])


def start_run(trigger=BackupRun.MANUAL, user=None):
    """Create the run row (status running). One run at a time: the shared
    cache lock (Redis in production) plus a check for a running row."""
    _fail_stale_runs()
    if not cache.add(LOCK_KEY, "starting", LOCK_TTL):
        if current_run() is not None:
            raise AlreadyRunning("A backup is already running.")
        cache.delete(LOCK_KEY)  # the lock outlived its run
        if not cache.add(LOCK_KEY, "starting", LOCK_TTL):
            raise AlreadyRunning("A backup is already running.")
    try:
        with transaction.atomic():
            if current_run() is not None:
                raise AlreadyRunning("A backup is already running.")
            run = BackupRun.objects.create(trigger=trigger, triggered_by=user if getattr(user, "pk", None) else None,
                                           progress_label="Waiting to start")
            run.label = f"nbte-backup-{timezone.localtime(run.started_at):%Y%m%d-%H%M%S}-r{run.pk}"
            run.save(update_fields=["label"])
    except Exception:
        cache.delete(LOCK_KEY)
        raise
    cache.set(LOCK_KEY, run.pk, LOCK_TTL)
    _audit("create", run, user, f"Backup started ({run.get_trigger_display().lower()})")
    return run


def run_backup(trigger=BackupRun.MANUAL, user=None):
    run = start_run(trigger, user)
    return perform_run(run.pk)


def build_artefacts(run, stage):
    """Create the artefacts in `stage`. Returns (manifest, files_to_upload)."""
    will_encrypt, enc_reason = crypto.status(encrypt_setting())
    if encrypt_setting() and not will_encrypt:
        run.warnings = list(run.warnings or []) + [enc_reason]
    plain = []

    _progress(run, 5, "Dumping the database")
    db_path, engine, counts = dump_database(stage)
    plain.append(("database", db_path, {"engine": engine}))

    pct = 25
    for stem, setting_name, root in media_roots():
        _progress(run, pct, f"Archiving {setting_name} ({root})")
        out = stage / f"{stem}.tar.gz"
        n = tar_folder(root, out)
        plain.append((stem, out, {"source_setting": setting_name, "files": n, "exists": root.is_dir()}))
        pct += 20

    _progress(run, 65, "Saving the settings snapshot")
    snap = stage / "settings.json"
    snap_counts = settings_snapshot(snap)
    plain.append(("settings", snap, {"rows": snap_counts}))

    artefacts, files = [], []
    for kind, path, extra in plain:
        entry = {"kind": kind, "name": path.name, "plain_size": path.stat().st_size,
                 "plain_sha256": sha256_file(path), **extra}
        if will_encrypt:
            _progress(run, 70, f"Encrypting {path.name}")
            enc = path.with_name(path.name + ".enc")
            crypto.encrypt_file(path, enc)
            path.unlink()
            path = enc
        entry.update({"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path),
                      "encrypted": will_encrypt})
        artefacts.append(entry)
        files.append(path)

    checksum = hashlib.sha256("".join(a["sha256"] for a in artefacts).encode()).hexdigest()
    manifest = {
        "format": "nbte-backup/1",
        "label": run.label,
        "run_id": run.pk,
        "created_at": timezone.now().isoformat(),
        "site": getattr(settings, "SITE_NAME", ""),
        "database_engine": engine,
        "encrypted": will_encrypt,
        "encryption": "AES-256-GCM (BACKUP_ENCRYPTION_KEY)" if will_encrypt else "",
        "artefacts": artefacts,
        "table_counts": counts,
        "checksum": checksum,
        "total_size": sum(a["size"] for a in artefacts),
    }
    return manifest, files


def write_manifest(manifest, stage):
    path = stage / MANIFEST_NAME
    data = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def perform_run(run_id):
    run = BackupRun.objects.get(pk=run_id)
    stage = None
    try:
        destinations = active_destinations()
        if not destinations:
            raise BackupFailed(NO_DESTINATION_WARNING)
        stage = _staging_dir(f"{run.label}-")
        manifest, files = build_artefacts(run, stage)
        manifest_path, manifest_hash = write_manifest(manifest, stage)
        run.manifest, run.manifest_hash = manifest, manifest_hash
        run.checksum, run.encrypted = manifest["checksum"], manifest["encrypted"]
        run.size_bytes = manifest["total_size"] + manifest_path.stat().st_size
        run.save(update_fields=["manifest", "manifest_hash", "checksum", "encrypted", "size_bytes", "warnings"])

        ok = []
        for i, dest in enumerate(destinations):
            _progress(run, 75 + int(20 * i / len(destinations)), f"Writing to {dest.name}")
            try:
                driver = driver_for(dest)
                location = ""
                for path in files + [manifest_path]:  # the manifest last: its presence marks a complete copy
                    location = driver.put(path, run.label, path.name)
                BackupCopy.objects.update_or_create(run=run, destination=dest, defaults={
                    "status": BackupCopy.OK, "location": location, "error": "", "written_at": timezone.now()})
                dest.last_success_at, dest.last_error = timezone.now(), ""
                dest.save(update_fields=["last_success_at", "last_error", "updated_at"])
                ok.append(dest)
            except Exception as exc:
                message = str(exc) if isinstance(exc, DestinationError) else f"{type(exc).__name__}: {exc}"
                logger.warning("backups: destination %s failed: %s", dest.name, message)
                BackupCopy.objects.update_or_create(run=run, destination=dest, defaults={
                    "status": BackupCopy.FAILED, "error": message, "written_at": timezone.now()})
                dest.last_error = message
                dest.save(update_fields=["last_error", "updated_at"])

        if len(ok) == len(destinations):
            run.status = BackupRun.SUCCEEDED
        elif ok:
            run.status = BackupRun.PARTIAL
            failed = [c for c in run.copies.filter(status=BackupCopy.FAILED).select_related("destination")]
            run.error = "\n".join(f"{c.destination.name}: {c.error}" for c in failed)
        else:
            run.status = BackupRun.FAILED
            run.error = "\n".join(f"{c.destination.name}: {c.error}"
                                  for c in run.copies.select_related("destination"))
        _progress(run, 97, "Removing old copies")
        for dest in ok:
            apply_retention(dest, run)
    except Exception as exc:
        logger.exception("backups: run %s failed", run.pk)
        run.status = BackupRun.FAILED
        run.error = str(exc) if isinstance(exc, (BackupFailed, DestinationError)) else (
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc(limit=6)}")
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        run.finished_at = timezone.now()
        run.progress = 100
        run.progress_label = {"succeeded": "Finished", "partial": "Finished with problems",
                              "failed": "Failed"}.get(run.status, "Finished")
        run.save(update_fields=["status", "error", "finished_at", "progress", "progress_label", "warnings"])
        if cache.get(LOCK_KEY) in (run.pk, "starting"):
            cache.delete(LOCK_KEY)

    _after_run(run)
    return run


def _after_run(run):
    duration = run.duration
    note = f"Backup {run.status}: {human_size(run.size_bytes)}"
    _audit("state_change", run, run.triggered_by, note,
           changes={"status": run.status, "size_bytes": run.size_bytes,
                    "duration_seconds": int(duration.total_seconds()) if duration else None})
    if run.status == BackupRun.FAILED:
        _notify("Backup failed", f"{run.label}: {(run.error or '').splitlines()[0] if run.error else ''}", "error")
    elif run.status == BackupRun.PARTIAL:
        _notify("Backup only partly written",
                f"{run.label} reached some destinations but not all. {run.error.splitlines()[0] if run.error else ''}",
                "warning")
    if run.status in (BackupRun.SUCCEEDED, BackupRun.PARTIAL) and drill_due():
        try:
            drill = start_drill(run, None, automatic=True)
            perform_drill(drill.pk)
        except Exception:
            logger.exception("backups: automatic drill failed to start")


def apply_retention(destination, newest_run):
    """Keep the newest `keep_count` copies at this destination. Called only
    after `newest_run` succeeded there, so a failure never deletes anything."""
    keep = keep_count()
    copies = list(BackupCopy.objects.filter(destination=destination, status=BackupCopy.OK,
                                            deleted_at__isnull=True)
                  .select_related("run").order_by("-run__started_at", "-run_id"))
    if not copies or copies[0].run_id != newest_run.pk:
        return []
    removed = []
    driver = driver_for(destination)
    for copy in copies[keep:]:
        try:
            driver.delete_run(copy.run.label)
        except Exception as exc:
            logger.warning("backups: retention could not delete %s at %s: %s",
                           copy.run.label, destination.name, exc)
            continue
        copy.deleted_at = timezone.now()
        copy.save(update_fields=["deleted_at"])
        removed.append(copy)
    return removed


def drill_due():
    every = drill_every()
    last = RestoreDrill.objects.exclude(result=RestoreDrill.RUNNING).order_by("-started_at").first()
    runs = BackupRun.objects.filter(status__in=[BackupRun.SUCCEEDED, BackupRun.PARTIAL])
    if last is not None:
        runs = runs.filter(started_at__gt=last.backup_run.started_at)
    return runs.count() >= every


def schedule_due(now=None):
    """(due, reason) — used by the daily Celery Beat check."""
    now = now or timezone.now()
    if not active_destinations():
        return False, NO_DESTINATION_WARNING
    if current_run() is not None:
        return False, "A backup is already running."
    last = BackupRun.objects.filter(status__in=[BackupRun.SUCCEEDED, BackupRun.PARTIAL]).order_by(
        "-started_at").first()
    if last is None:
        return True, "No backup has been taken yet."
    due_at = last.started_at + timedelta(days=interval_days())
    if now >= due_at:
        return True, f"The last backup was taken {(now - last.started_at).days} days ago."
    return False, f"Next backup due {timezone.localtime(due_at):%d %b %Y}."


def next_due_at():
    last = BackupRun.objects.filter(status__in=[BackupRun.SUCCEEDED, BackupRun.PARTIAL]).order_by(
        "-started_at").first()
    if last is None:
        return None
    return last.started_at + timedelta(days=interval_days())


# ---------------------------------------------------------------------------
# Fetching a run back
# ---------------------------------------------------------------------------
def _copy_sources(run):
    copies = list(run.copies.filter(status=BackupCopy.OK, deleted_at__isnull=True).select_related("destination"))
    order = {BackupDestination.LOCAL: 0, BackupDestination.MOUNTED: 1, BackupDestination.S3: 2}
    copies.sort(key=lambda c: order.get(c.destination.kind, 9))
    return copies


def fetch_run(run, workdir, log):
    """Download the run's files into `workdir` from the first destination
    that has them. Returns the manifest read from the destination."""
    errors = []
    for copy in _copy_sources(run):
        driver = driver_for(copy.destination)
        try:
            driver.get(run.label, MANIFEST_NAME, workdir / MANIFEST_NAME)
            manifest = json.loads((workdir / MANIFEST_NAME).read_text(encoding="utf-8"))
            for art in manifest["artefacts"]:
                driver.get(run.label, art["name"], workdir / art["name"])
            log(f"Fetched {len(manifest['artefacts'])} artefacts and the manifest from {copy.destination.name}.")
            return manifest
        except Exception as exc:
            errors.append(f"{copy.destination.name}: {exc}")
            log(f"Could not fetch from {copy.destination.name}: {exc}")
    raise BackupFailed("No destination still holds this backup. " + " | ".join(errors))


def verify_files(manifest, workdir, log, expected_manifest_hash=None):
    problems = []
    if expected_manifest_hash:
        actual = sha256_file(workdir / MANIFEST_NAME)
        if actual != expected_manifest_hash:
            problems.append(f"manifest.json: hash {actual[:12]}… does not match the recorded {expected_manifest_hash[:12]}…")
        else:
            log("manifest.json: hash matches the recorded manifest hash.")
    for art in manifest["artefacts"]:
        path = workdir / art["name"]
        if not path.exists():
            problems.append(f"{art['name']}: missing")
            continue
        size, digest = path.stat().st_size, sha256_file(path)
        if size != art["size"] or digest != art["sha256"]:
            problems.append(f"{art['name']}: size/hash mismatch")
        else:
            log(f"{art['name']}: {human_size(size)}, SHA-256 verified.")
    combined = hashlib.sha256("".join(a["sha256"] for a in manifest["artefacts"]).encode()).hexdigest()
    if combined != manifest.get("checksum"):
        problems.append("The combined checksum does not match the manifest.")
    return problems


def decrypt_all(manifest, workdir, log):
    """Returns {kind: plain path}."""
    out = {}
    for art in manifest["artefacts"]:
        path = workdir / art["name"]
        if art.get("encrypted"):
            if not crypto.library_available():
                raise BackupFailed("This backup is encrypted but the cryptography package is not installed.")
            plain = path.with_name(art["name"][:-4] if art["name"].endswith(".enc") else art["name"] + ".plain")
            try:
                crypto.decrypt_file(path, plain)
            except crypto.EncryptionUnavailable as exc:
                raise BackupFailed(f"This backup is encrypted: {exc}") from exc
            except Exception as exc:
                raise BackupFailed(f"Could not decrypt {art['name']} (wrong BACKUP_ENCRYPTION_KEY?): {exc}") from exc
            if sha256_file(plain) != art.get("plain_sha256"):
                raise BackupFailed(f"{art['name']}: the decrypted file does not match its recorded hash.")
            log(f"{art['name']}: decrypted and verified.")
            path = plain
        out[art["kind"]] = path
    return out


# ---------------------------------------------------------------------------
# Restore drill
# ---------------------------------------------------------------------------
def start_drill(run, user=None, automatic=False):
    if not run.can_drill:
        raise BackupFailed("This backup has no complete copy left at any destination.")
    if RestoreDrill.objects.filter(result=RestoreDrill.RUNNING,
                                   started_at__gte=timezone.now() - STALE_AFTER).exists():
        raise AlreadyRunning("A restore drill is already running.")
    drill = RestoreDrill.objects.create(backup_run=run, triggered_by=user if getattr(user, "pk", None) else None,
                                        automatic=automatic)
    return drill


def _sqlite_scratch(dump_path, workdir):
    scratch = workdir / "scratch.sqlite3"
    conn = sqlite3.connect(scratch)
    try:
        conn.executescript(dump_path.read_text(encoding="utf-8"))
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return scratch, integrity


def _sqlite_counts(scratch):
    conn = sqlite3.connect(scratch)
    try:
        counts = {}
        for label, table in key_tables():
            try:
                counts[label] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                counts[label] = None
        inst = _model("institutions.Institution")
        tl = _model("qa_core.TimelineEvent")
        sample = None
        if inst is not None and tl is not None:
            row = conn.execute(f'SELECT id FROM "{inst._meta.db_table}" ORDER BY id LIMIT 1').fetchone()
            if row:
                n = conn.execute(f'SELECT COUNT(*) FROM "{tl._meta.db_table}" WHERE institution_id = ?',
                                 (row[0],)).fetchone()[0]
                sample = (row[0], n)
        return counts, sample
    finally:
        conn.close()


def _scratch_pg_name(drill):
    base = str(settings.DATABASES["default"]["NAME"])[:40]
    return f"{base}_drill_{drill.pk}_{timezone.now():%H%M%S}"


def _pg_admin_execute(sql):
    conn = connections.create_connection("default")
    try:
        conn.ensure_connection()
        conn.set_autocommit(True)
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


def _pg_scratch(dump_path, drill):
    name = _scratch_pg_name(drill)
    _pg_admin_execute(f'CREATE DATABASE "{name}"')
    db = dict(settings.DATABASES["default"])
    proc = subprocess.run([_pg_tool("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error",
                           "--dbname", name, str(dump_path)],
                          env=_pg_env(db), capture_output=True, text=True, timeout=6 * 3600)
    if proc.returncode != 0:
        raise BackupFailed(f"pg_restore failed: {proc.stderr.strip()[:2000]}")
    return name


def _pg_counts(name):
    from django.db.backends.postgresql.base import DatabaseWrapper

    params = dict(settings.DATABASES["default"])
    params["NAME"] = name
    conn = DatabaseWrapper(params, alias="backups_drill")
    try:
        counts = {}
        with conn.cursor() as cur:
            for label, table in key_tables():
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                counts[label] = cur.fetchone()[0]
            sample = None
            inst, tl = _model("institutions.Institution"), _model("qa_core.TimelineEvent")
            if inst is not None and tl is not None:
                cur.execute(f'SELECT id FROM "{inst._meta.db_table}" ORDER BY id LIMIT 1')
                row = cur.fetchone()
                if row:
                    cur.execute(f'SELECT COUNT(*) FROM "{tl._meta.db_table}" WHERE institution_id = %s', [row[0]])
                    sample = (row[0], cur.fetchone()[0])
        return counts, sample
    finally:
        conn.close()


def _database_url(engine, scratch):
    if engine == "sqlite":
        return f"sqlite:///{scratch}"
    from urllib.parse import quote

    db = settings.DATABASES["default"]
    auth = quote(str(db.get("USER") or ""), safe="")
    if db.get("PASSWORD"):
        auth += ":" + quote(str(db["PASSWORD"]), safe="")
    host = db.get("HOST") or "localhost"
    port = f":{db['PORT']}" if db.get("PORT") else ""
    return f"postgres://{auth}@{host}{port}/{quote(scratch, safe='')}"


def django_check(engine, scratch):
    """`manage.py check --database default` against the scratch database."""
    env = os.environ.copy()
    env["DATABASE_URL"] = _database_url(engine, scratch)
    env.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "nbte_ict.settings.dev"))
    manage = Path(settings.BASE_DIR) / "manage.py"
    proc = subprocess.run([sys.executable, str(manage), "check", "--database", "default"], env=env,
                          capture_output=True, text=True, timeout=600, cwd=str(settings.BASE_DIR))
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines()
             if ln.strip() and " INFO [" not in ln and "objects imported" not in ln]
    return proc.returncode == 0, "\n".join(lines[-15:])


def _open_one_file(tar_path):
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar:
            if member.isfile():
                fh = tar.extractfile(member)
                data = fh.read()
                return member.name, len(data) == member.size, member.size
    return None, True, 0


def perform_drill(drill_id, *, run_django_check=True):
    drill = RestoreDrill.objects.select_related("backup_run").get(pk=drill_id)
    run = drill.backup_run
    lines = []

    def log(text):
        lines.append(text)

    workdir = _staging_dir(f"drill-{run.pk}-")
    passed, scratch_pg = False, None
    try:
        log(f"Restore drill of {run.label} (taken {timezone.localtime(run.started_at):%d %b %Y %H:%M}).")
        manifest = fetch_run(run, workdir, log)
        problems = verify_files(manifest, workdir, log, expected_manifest_hash=run.manifest_hash)
        if problems:
            raise BackupFailed("Checksum verification failed: " + "; ".join(problems))
        drill.verified_at = timezone.now()
        drill.save(update_fields=["verified_at"])
        plain = decrypt_all(manifest, workdir, log)

        engine = manifest.get("database_engine")
        if engine == "sqlite":
            scratch, integrity = _sqlite_scratch(plain["database"], workdir)
            drill.scratch_db = str(scratch)
            log(f"Restored the dump into a scratch SQLite database. Integrity check: {integrity}.")
            if integrity != "ok":
                raise BackupFailed(f"SQLite integrity check failed: {integrity}")
            counts, sample = _sqlite_counts(scratch)
            target = str(scratch)
        elif engine == "postgresql":
            scratch_pg = _pg_scratch(plain["database"], drill)
            drill.scratch_db = scratch_pg
            log(f"Restored the dump into the scratch PostgreSQL database {scratch_pg}.")
            counts, sample = _pg_counts(scratch_pg)
            target = scratch_pg
        else:
            raise BackupFailed(f"Unknown database engine in the manifest: {engine}")
        drill.save(update_fields=["scratch_db"])

        if run_django_check:
            ok, output = django_check(engine, target)
            log("manage.py check against the scratch database: " + ("passed." if ok else "FAILED."))
            if output:
                log("  " + output.replace("\n", "\n  "))
            if not ok:
                raise BackupFailed("manage.py check failed against the restored database.")

        expected = manifest.get("table_counts", {})
        mismatches = []
        log("Key table counts (restored / at backup time):")
        for label, want in expected.items():
            got = counts.get(label)
            flag = "ok" if got == want else "MISMATCH"
            log(f"  {label}: {got} / {want} {flag}")
            if got != want:
                mismatches.append(label)
        nonzero = [label for label, n in counts.items() if n]
        if expected and not nonzero:
            mismatches.append("every key table is empty")
        if sample is not None:
            log(f"Institution #{sample[0]} loads, with {sample[1]} timeline event(s).")
        if mismatches:
            raise BackupFailed("Smoke test failed: " + ", ".join(mismatches))

        for stem, setting_name, _root in media_roots():
            tar_path = plain.get(stem)
            if tar_path is None:
                continue
            name, ok, size = _open_one_file(tar_path)
            if name is None:
                log(f"{setting_name}: the archive is empty (no files to check).")
            elif not ok:
                raise BackupFailed(f"{setting_name}: {name} could not be read from the archive.")
            else:
                log(f"{setting_name}: opened {name} ({human_size(size)}) from the archive.")
        passed = True
        log("Result: PASSED.")
    except Exception as exc:
        log(f"Result: FAILED — {exc}")
        if not isinstance(exc, (BackupFailed, DestinationError)):
            log(traceback.format_exc(limit=6))
    finally:
        if scratch_pg:
            try:
                _pg_admin_execute(f'DROP DATABASE IF EXISTS "{scratch_pg}"')
                log(f"Dropped the scratch database {scratch_pg}.")
            except Exception as exc:
                log(f"Could not drop the scratch database {scratch_pg}: {exc}")
        shutil.rmtree(workdir, ignore_errors=True)
        if not scratch_pg and drill.scratch_db:
            log("Removed the scratch SQLite database and the downloaded files.")
        drill.result = RestoreDrill.PASSED if passed else RestoreDrill.FAILED
        drill.finished_at = timezone.now()
        drill.report = "\n".join(lines)
        drill.save(update_fields=["result", "finished_at", "report", "scratch_db"])

    _audit("other", drill, drill.triggered_by, f"Restore drill {drill.result} for {run.label}",
           changes={"result": drill.result, "backup_run": run.pk})
    if drill.result == RestoreDrill.FAILED:
        last = next((ln for ln in reversed(lines) if ln.startswith("Result:")), "")
        _notify("Restore drill failed", f"{run.label}. {last}", "error")
    return drill


# ---------------------------------------------------------------------------
# Real restore (management command only; superusers; documented runbook)
# ---------------------------------------------------------------------------
def restore(*, run=None, from_dir=None, include_db=True, include_media=True, log=print):
    """Overwrite the live database and/or file roots from a backup.

    The current database file (SQLite) or the file roots are moved aside to
    `*.before-restore-<timestamp>` first, never deleted."""
    workdir = _staging_dir("restore-")
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    try:
        if from_dir:
            src = Path(from_dir)
            manifest = json.loads((src / MANIFEST_NAME).read_text(encoding="utf-8"))
            shutil.copyfile(src / MANIFEST_NAME, workdir / MANIFEST_NAME)
            for art in manifest["artefacts"]:
                shutil.copyfile(src / art["name"], workdir / art["name"])
            expected = None
        else:
            manifest = fetch_run(run, workdir, log)
            expected = run.manifest_hash
        problems = verify_files(manifest, workdir, log, expected_manifest_hash=expected)
        if problems:
            raise BackupFailed("Checksum verification failed: " + "; ".join(problems))
        plain = decrypt_all(manifest, workdir, log)
        engine = manifest.get("database_engine")

        if include_db:
            if engine != db_engine():
                raise BackupFailed(f"The backup is a {engine} dump but this server uses {db_engine()}.")
            if engine == "sqlite":
                live = Path(settings.DATABASES["default"]["NAME"])
                new_db, integrity = _sqlite_scratch(plain["database"], workdir)
                if integrity != "ok":
                    raise BackupFailed(f"The restored database failed its integrity check: {integrity}")
                connection.close()
                if live.exists():
                    aside = live.with_name(f"{live.name}.before-restore-{stamp}")
                    shutil.copyfile(live, aside)
                    log(f"Saved the current database as {aside}.")
                tmp = live.with_name(f".{live.name}.restoring")
                shutil.copyfile(new_db, tmp)
                os.replace(tmp, live)
                log(f"Database restored into {live}.")
            else:
                db = settings.DATABASES["default"]
                proc = subprocess.run([_pg_tool("pg_restore"), "--clean", "--if-exists", "--no-owner",
                                       "--no-privileges", "--single-transaction", "--dbname", str(db["NAME"]),
                                       str(plain["database"])], env=_pg_env(db), capture_output=True, text=True,
                                      timeout=6 * 3600)
                if proc.returncode != 0:
                    raise BackupFailed(f"pg_restore failed: {proc.stderr.strip()[:3000]}")
                log(f"Database restored into {db['NAME']}.")

        if include_media:
            for stem, setting_name, root in media_roots():
                tar_path = plain.get(stem)
                if tar_path is None:
                    continue
                if root.exists():
                    aside = root.with_name(f"{root.name}.before-restore-{stamp}")
                    os.replace(root, aside)
                    log(f"Moved the current {setting_name} to {aside}.")
                root.mkdir(parents=True, exist_ok=True)
                with tarfile.open(tar_path, "r:gz") as tar:
                    tar.extractall(root, filter="data")
                log(f"{setting_name} restored into {root}.")
        return manifest
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def manifest_bytes(run):
    return io.BytesIO(json.dumps(run.manifest, indent=2, sort_keys=True).encode("utf-8"))
