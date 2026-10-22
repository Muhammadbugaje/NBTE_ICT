"""Backups (Master Plan §22): runs, manifests, retention, partial runs,
connection tests, restore drills, S3 with a fake client, permissions, seed."""
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from backups import crypto, destinations, engine
from backups.models import BackupCopy, BackupDestination, BackupRun, RestoreDrill
from config.models import ConfigEntry

pytestmark = pytest.mark.django_db


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def setting(key, value, vtype="int"):
    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": str(value), "value_type": vtype})
    cache.clear()


@pytest.fixture(autouse=True)
def _files(settings, monkeypatch):
    """Some uploaded and restricted files, no encryption key, quiet audit."""
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    Path(settings.MEDIA_ROOT, "photos").mkdir(parents=True)
    Path(settings.MEDIA_ROOT, "photos", "a.txt").write_text("public file")
    Path(settings.PROTECTED_MEDIA_ROOT, "evidence").mkdir(parents=True)
    Path(settings.PROTECTED_MEDIA_ROOT, "evidence", "proof.pdf").write_bytes(b"%PDF-1.4 restricted" * 50)


def local_dest(tmp_path, name="Local", active=True):
    return BackupDestination.objects.create(name=name, kind="local", is_active=active,
                                            config={"path": str(tmp_path / f"dest-{name}")})


def staff_user(email, *perms):
    from accounts.models import User

    user = User.objects.create_user(email, "pw-Strong#2026", user_type="staff", must_change_password=False)
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="backups", codename=codename))
    return User.objects.get(pk=user.pk)


# ---------------------------------------------------------------------------
# Runs and manifests
# ---------------------------------------------------------------------------
def test_sqlite_run_writes_a_manifest_whose_hashes_verify(tmp_path):
    dest = local_dest(tmp_path)
    run = engine.run_backup()
    assert run.status == BackupRun.SUCCEEDED, run.error
    folder = Path(dest.config["path"]) / run.label
    manifest = json.loads((folder / "manifest.json").read_text())
    assert sha(folder / "manifest.json") == run.manifest_hash
    kinds = {a["kind"] for a in manifest["artefacts"]}
    assert kinds == {"database", "media", "protected_media", "settings"}
    for art in manifest["artefacts"]:
        assert (folder / art["name"]).stat().st_size == art["size"]
        assert sha(folder / art["name"]) == art["sha256"]
    assert manifest["checksum"] == run.checksum
    assert manifest["database_engine"] == "sqlite"
    assert manifest["table_counts"]["accounts.User"] >= 0
    assert engine.verify_files(manifest, folder, lambda _l: None, run.manifest_hash) == []
    # the dump really is SQL and the settings snapshot lists the rule tables
    assert "CREATE TABLE" in (folder / "database.sqlite.sql").read_text()
    snap = json.loads((folder / "settings.json").read_text())
    assert "config.ConfigEntry" in snap["models"] and "qa_core.FlowDefinition" in snap["models"]
    # the restricted files are in their own archive
    import tarfile
    with tarfile.open(folder / "protected_media.tar.gz") as tar:
        assert "evidence/proof.pdf" in tar.getnames()
    assert run.warnings == ["BACKUP_ENCRYPTION_KEY is not set in the server's .env, so backups are not encrypted."]
    copy = run.copies.get()
    assert copy.status == BackupCopy.OK and copy.destination == dest


def test_run_without_destination_fails_and_notifies_ict(tmp_path):
    from accounts.models import Role, User
    from notifications.models import Notification

    role, _ = Role.objects.get_or_create(code="ict-qa-monitor", defaults={"name": "ICT QA Monitor"})
    monitor = User.objects.create_user("mon@x.local", "pw", user_type="staff", role=role)
    run = engine.run_backup()
    assert run.status == BackupRun.FAILED
    assert engine.NO_DESTINATION_WARNING in run.error
    assert Notification.objects.filter(recipient=monitor, title="Backup failed").exists()


def test_one_run_at_a_time(tmp_path):
    local_dest(tmp_path)
    first = engine.start_run()
    with pytest.raises(engine.AlreadyRunning):
        engine.start_run()
    engine.perform_run(first.pk)
    engine.run_backup()  # the lock is released after a run


def test_failing_destination_makes_the_run_partial_and_notifies(tmp_path):
    from notifications.models import Notification
    from accounts.models import User

    User.objects.create_superuser("root@x.local", "pw")  # fallback recipient when no ICT holders
    good = local_dest(tmp_path, "Good")
    bad = BackupDestination.objects.create(name="Unplugged USB", kind="mounted", is_active=True,
                                           config={"mount_path": str(tmp_path / "no-such-drive")})
    run = engine.run_backup()
    assert run.status == BackupRun.PARTIAL
    assert run.copies.get(destination=good).status == BackupCopy.OK
    failed = run.copies.get(destination=bad)
    assert failed.status == BackupCopy.FAILED and "does not exist" in failed.error
    bad.refresh_from_db()
    assert bad.last_error and not bad.last_success_at
    assert Notification.objects.filter(title="Backup only partly written").exists()
    assert not (tmp_path / "no-such-drive").exists()  # never written to the server's own disk


def test_success_is_silent(tmp_path):
    from notifications.models import Notification

    local_dest(tmp_path)
    assert engine.run_backup().status == BackupRun.SUCCEEDED
    assert not Notification.objects.exists()


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def test_retention_rotates_only_after_a_newer_success(tmp_path, monkeypatch):
    setting("qa.backup.keep_count", 2)
    setting("qa.backup.drill_every_n_runs", 100)
    dest = local_dest(tmp_path)
    base = Path(dest.config["path"])
    r1, r2 = engine.run_backup(), engine.run_backup()
    assert (base / r1.label).exists() and (base / r2.label).exists()

    # a failed run deletes nothing
    def boom(*a, **k):
        raise destinations.DestinationError("disk full")
    monkeypatch.setattr(destinations.FolderDriver, "put", boom)
    r3 = engine.run_backup()
    assert r3.status == BackupRun.FAILED
    assert (base / r1.label).exists() and (base / r2.label).exists()
    monkeypatch.undo()

    # a newer success rotates the oldest copy off
    r4 = engine.run_backup()
    assert r4.status == BackupRun.SUCCEEDED
    assert not (base / r1.label).exists()
    assert (base / r2.label).exists() and (base / r4.label).exists()
    assert BackupCopy.objects.get(run=r1, destination=dest).deleted_at is not None
    assert BackupCopy.objects.get(run=r2, destination=dest).deleted_at is None


# ---------------------------------------------------------------------------
# Connection tests and the page
# ---------------------------------------------------------------------------
def test_test_connection_for_local_ok_and_error(tmp_path, client):
    dest = local_dest(tmp_path)
    user = staff_user("dir@x.local", "view_backups", "manage_destinations")
    client.force_login(user)
    resp = client.post(f"/settings/backups/destinations/{dest.pk}/test/", HTTP_HX_REQUEST="true")
    assert resp.status_code == 200 and b"Connection OK" in resp.content
    assert not any(p.name.startswith(".nbte-backup-test") for p in Path(dest.config["path"]).iterdir())
    dest.refresh_from_db()
    assert dest.last_test_ok is True

    dest.config = {"path": "relative/folder"}
    dest.save()
    resp = client.post(f"/settings/backups/destinations/{dest.pk}/test/", HTTP_HX_REQUEST="true")
    assert b"Connection failed" in resp.content and b"full path" in resp.content


def test_page_permissions_and_back_up_now(tmp_path, client, monkeypatch):
    from backups import dispatch

    local_dest(tmp_path)
    viewer = staff_user("view@x.local", "view_backups")
    client.force_login(viewer)
    page = client.get("/settings/backups/")
    assert page.status_code == 200 and b"Backup history" in page.content
    assert b"Add a destination</h2>" not in page.content
    assert client.post("/settings/backups/run/").status_code == 403
    assert client.post("/settings/backups/destinations/new/", {"name": "x", "kind": "local"}).status_code == 403

    # run the job inline instead of in a thread
    monkeypatch.setattr(dispatch, "background", lambda task, *args: task.run(*args))
    runner = staff_user("run@x.local", "view_backups", "run_backup")
    client.force_login(runner)
    resp = client.post("/settings/backups/run/", HTTP_HX_REQUEST="true")
    assert resp.status_code == 200 and b'id="bk-progress"' in resp.content
    run = BackupRun.objects.get()
    assert run.status == BackupRun.SUCCEEDED and run.triggered_by == runner
    progress = client.get(f"/settings/backups/runs/{run.pk}/progress/")
    assert b"Backup finished" in progress.content and b'hx-swap-oob="outerHTML"' in progress.content
    manifest = client.get(f"/settings/backups/runs/{run.pk}/manifest/")
    assert hashlib.sha256(manifest.content).hexdigest() == run.manifest_hash
    assert client.get("/settings/").status_code == 403  # the generic settings page is still admin-only


def test_destination_form_keeps_credentials_out_of_the_database(tmp_path, client):
    user = staff_user("dir2@x.local", "view_backups", "manage_destinations")
    client.force_login(user)
    resp = client.post("/settings/backups/destinations/new/", {
        "name": "Cloudflare R2", "kind": "s3", "bucket": "nbte-backups", "prefix": "nbte", "region": "auto",
        "endpoint_url": "https://abc.r2.cloudflarestorage.com", "addressing_style": "auto", "is_active": "1"})
    assert resp.status_code == 302
    dest = BackupDestination.objects.get(name="Cloudflare R2")
    assert dest.credential_env_prefix == "BACKUP_S3_CLOUDFLARE_R2_"
    assert [r["name"] for r in dest.env_status()][:2] == ["BACKUP_S3_CLOUDFLARE_R2_ACCESS_KEY_ID",
                                                          "BACKUP_S3_CLOUDFLARE_R2_SECRET_ACCESS_KEY"]
    page = client.get("/settings/backups/")
    assert b"BACKUP_S3_CLOUDFLARE_R2_SECRET_ACCESS_KEY=" in page.content and b"Missing" in page.content
    # a secret pasted into a config field is refused
    client.post("/settings/backups/destinations/new/", {"name": "Bad", "kind": "s3", "bucket": "b",
                                                        "prefix": "secret=abc123", "addressing_style": "auto"})
    assert not BackupDestination.objects.filter(name="Bad").exists()


def test_schedule_settings_save(client):
    from qa_core.settings_catalogue import SETTINGS

    for key, value, vtype, group, desc, editable_by, dual in SETTINGS:
        if key.startswith("qa.backup."):
            ConfigEntry.objects.create(key=key, value=value, value_type=vtype, group=group, description=desc,
                                       editable_by=editable_by, dual_control=dual)
    assert engine.interval_days() == 30 and engine.keep_count() == 8 and engine.drill_every() == 4
    user = staff_user("sched@x.local", "view_backups", "manage_destinations")
    client.force_login(user)
    client.post("/settings/backups/schedule/", {"qa_backup_interval_days": "7", "qa_backup_keep_count": "8",
                                                "qa_backup_drill_every_n_runs": "4"})
    cache.clear()
    assert engine.interval_days() == 7
    assert engine.encrypt_setting() is False  # the switch was not ticked


def test_schedule_due(tmp_path):
    setting("qa.backup.interval_days", 30)
    assert engine.schedule_due()[0] is False  # nothing to write to
    local_dest(tmp_path)
    assert engine.schedule_due()[0] is True  # never backed up
    run = engine.run_backup(trigger=BackupRun.SCHEDULE)
    assert engine.schedule_due()[0] is False
    BackupRun.objects.filter(pk=run.pk).update(started_at=timezone.now() - timedelta(days=31))
    assert engine.schedule_due()[0] is True
    from backups.tasks import scheduled_check
    setting("qa.backup.drill_every_n_runs", 100)
    assert "succeeded" in scheduled_check.run()


# ---------------------------------------------------------------------------
# Restore drill and encryption
# ---------------------------------------------------------------------------
def test_restore_drill_passes_and_cleans_up(tmp_path):
    setting("qa.backup.drill_every_n_runs", 100)
    local_dest(tmp_path)
    run = engine.run_backup()
    out = io.StringIO()
    call_command("restore_backup", "--drill", str(run.pk), stdout=out)
    drill = RestoreDrill.objects.get()
    assert drill.result == RestoreDrill.PASSED, drill.report
    assert drill.verified_at is not None
    assert "manage.py check against the scratch database: passed." in drill.report
    assert "SHA-256 verified" in drill.report and "PROTECTED_MEDIA_ROOT: opened evidence/proof.pdf" in drill.report
    assert not Path(drill.scratch_db).exists()


def test_drill_fails_when_a_file_is_tampered(tmp_path):
    from notifications.models import Notification
    from accounts.models import User

    User.objects.create_superuser("root2@x.local", "pw")
    dest = local_dest(tmp_path)
    run = engine.run_backup()
    target = Path(dest.config["path"]) / run.label / "settings.json"
    target.write_text(target.read_text() + " ")
    drill = engine.perform_drill(engine.start_drill(run).pk, run_django_check=False)
    assert drill.result == RestoreDrill.FAILED and "size/hash mismatch" in drill.report
    assert Notification.objects.filter(title="Restore drill failed").exists()


@pytest.mark.skipif(not crypto.library_available(), reason="cryptography is not installed")
def test_encrypted_run_round_trips(tmp_path, monkeypatch):
    import base64, os

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    setting("qa.backup.drill_every_n_runs", 100)
    dest = local_dest(tmp_path)
    run = engine.run_backup()
    assert run.encrypted and not run.warnings
    folder = Path(dest.config["path"]) / run.label
    assert all(a["name"].endswith(".enc") for a in run.manifest["artefacts"])
    assert b"CREATE TABLE" not in (folder / "database.sqlite.sql.enc").read_bytes()
    drill = engine.perform_drill(engine.start_drill(run).pk, run_django_check=False)
    assert drill.result == RestoreDrill.PASSED, drill.report
    # the wrong key cannot restore it
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
    drill = engine.perform_drill(engine.start_drill(run).pk, run_django_check=False)
    assert drill.result == RestoreDrill.FAILED and "decrypt" in drill.report


def test_automatic_drill_after_n_runs(tmp_path):
    setting("qa.backup.drill_every_n_runs", 2)
    local_dest(tmp_path)
    engine.run_backup()
    assert not RestoreDrill.objects.exists()
    engine.run_backup()
    drill = RestoreDrill.objects.get()
    assert drill.automatic


# ---------------------------------------------------------------------------
# S3-compatible storage with a fake boto3 client (no network)
# ---------------------------------------------------------------------------
class FakeS3:
    def __init__(self):
        self.objects = {}

    def upload_file(self, filename, bucket, key):
        self.objects[(bucket, key)] = Path(filename).read_bytes()

    def download_file(self, bucket, key, filename):
        if (bucket, key) not in self.objects:
            raise RuntimeError(f"NoSuchKey: {key}")
        Path(filename).write_bytes(self.objects[(bucket, key)])

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {"Contents": [{"Key": k} for (b, k) in self.objects if b == Bucket and k.startswith(Prefix)],
                "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.pop((Bucket, obj["Key"]), None)


def test_s3_destination_with_fake_client(tmp_path, monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(destinations, "_S3_CLIENT_FACTORY", lambda dest: fake)
    setting("qa.backup.keep_count", 1)
    setting("qa.backup.drill_every_n_runs", 100)
    dest = BackupDestination.objects.create(name="R2", kind="s3", is_active=True,
                                            credential_env_prefix="BACKUP_S3_R2_",
                                            config={"bucket": "nbte", "prefix": "prod", "region": "auto"})
    assert "Wrote, read back and deleted" in destinations.driver_for(dest).test()
    assert fake.objects == {}  # the test object is gone

    r1 = engine.run_backup()
    assert r1.status == BackupRun.SUCCEEDED, r1.error
    keys = {k for (_b, k) in fake.objects}
    assert f"prod/{r1.label}/manifest.json" in keys and f"prod/{r1.label}/database.sqlite.sql" in keys
    drill = engine.perform_drill(engine.start_drill(r1).pk, run_django_check=False)
    assert drill.result == RestoreDrill.PASSED, drill.report

    r2 = engine.run_backup()
    keys = {k for (_b, k) in fake.objects}
    assert not any(k.startswith(f"prod/{r1.label}/") for k in keys)  # keep_count=1 rotated r1 off
    assert f"prod/{r2.label}/manifest.json" in keys


def test_s3_without_env_credentials_reports_the_missing_variables(monkeypatch):
    for suffix in ("ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.delenv(f"BACKUP_S3_X_{suffix}", raising=False)
    dest = BackupDestination.objects.create(name="X", kind="s3", credential_env_prefix="BACKUP_S3_X_",
                                            config={"bucket": "b"})
    with pytest.raises(destinations.DestinationError, match="BACKUP_S3_X_ACCESS_KEY_ID"):
        destinations.driver_for(dest).test()


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
def test_seed_is_idempotent_and_warns_without_destination():
    from accounts.models import Role
    from backups import seed

    Role.objects.get_or_create(code="ict-qa-monitor", defaults={"name": "ICT QA Monitor"})
    first = seed.seed()
    assert first["destinations"] == 1
    placeholder = BackupDestination.objects.get()
    assert placeholder.is_placeholder and not placeholder.is_active and placeholder.kind == "local"
    assert seed.warning() == "No backup destination configured. Set one before going live."
    monitor = Role.objects.get(code="ict-qa-monitor")
    assert monitor.permissions.filter(codename="view_backups").exists()
    assert not monitor.permissions.filter(codename="run_backup").exists()
    second = seed.seed()
    assert second == {"grants": 0, "groups": 0, "destinations": 0, "directors": 0}
