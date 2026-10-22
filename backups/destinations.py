"""Destination drivers: a local folder, a mounted drive, or S3-compatible storage.

Every driver offers the same five operations, all keyed by the run's folder
name (``run_label``) and a file name inside it:

    put(local_path, run_label, name)   -> location string
    get(run_label, name, local_path)
    delete_run(run_label)
    test()                             -> message (raises DestinationError)
    describe()                         -> human text

SFTP is not offered: neither the ``paramiko`` package nor the ``sftp``/``scp``
programs are installed on this server (see BACKUPS.md).
"""
import os
import secrets
import shutil
from pathlib import Path

MOUNT_ROOTS = ("/media", "/mnt", "/run/media", "/Volumes")
DEFAULT_SUBFOLDER = "nbte-backups"


class DestinationError(Exception):
    pass


def _clean_name(value):
    value = str(value or "")
    if not value or "/" in value or "\\" in value or value in (".", ".."):
        raise DestinationError(f"Invalid file or folder name: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Folder-based destinations
# ---------------------------------------------------------------------------
class FolderDriver:
    def __init__(self, destination):
        self.destination = destination
        self.config = destination.config or {}

    def base(self):
        path = (self.config.get("path") or "").strip()
        if not path:
            raise DestinationError("No folder is set for this destination.")
        base = Path(path).expanduser()
        if not base.is_absolute():
            raise DestinationError(f"The folder must be a full path (it starts with /): {path}")
        return base

    def _ready_base(self):
        base = self.base()
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DestinationError(f"Cannot create the folder {base}: {exc}") from exc
        if not os.access(base, os.W_OK):
            raise DestinationError(f"The server cannot write to {base} (permission denied).")
        return base

    def put(self, local_path, run_label, name):
        folder = self._ready_base() / _clean_name(run_label)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / _clean_name(name)
            tmp = folder / f".{name}.partial"
            shutil.copyfile(local_path, tmp)
            os.replace(tmp, target)
        except OSError as exc:
            raise DestinationError(f"Could not write {name} to {folder}: {exc}") from exc
        return str(folder)

    def get(self, run_label, name, local_path):
        source = self.base() / _clean_name(run_label) / _clean_name(name)
        if not source.exists():
            raise DestinationError(f"{source} does not exist.")
        shutil.copyfile(source, local_path)

    def delete_run(self, run_label):
        folder = self.base() / _clean_name(run_label)
        if folder.exists():
            shutil.rmtree(folder)

    def test(self):
        base = self._ready_base()
        name = f".nbte-backup-test-{secrets.token_hex(6)}"
        payload = secrets.token_bytes(64)
        path = base / name
        try:
            path.write_bytes(payload)
            if path.read_bytes() != payload:
                raise DestinationError("The test file read back differently from what was written.")
        except OSError as exc:
            raise DestinationError(f"Could not write a test file in {base}: {exc}") from exc
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        free = shutil.disk_usage(base).free
        return f"Wrote, read back and deleted a test file in {base}. Free space: {human_size(free)}."

    def describe(self):
        return str(self.config.get("path") or "")


class MountedDriver(FolderDriver):
    """A USB/external drive or a network mount. Refuses to write when the
    drive is not mounted, so an unplugged disk never fills the server's own
    disk instead."""

    def mount_path(self):
        mount = (self.config.get("mount_path") or "").strip()
        if not mount:
            raise DestinationError("No drive is chosen for this destination.")
        return Path(mount)

    def base(self):
        mount = self.mount_path()
        if not mount.is_absolute():
            raise DestinationError(f"The mount point must be a full path: {mount}")
        if not mount.exists():
            raise DestinationError(f"{mount} does not exist. Is the drive plugged in and mounted?")
        if not os.path.ismount(mount) and not self.config.get("allow_unmounted"):
            raise DestinationError(f"{mount} is not a mounted drive right now. Plug the drive in and mount it.")
        sub = (self.config.get("subfolder") or DEFAULT_SUBFOLDER).strip().strip("/")
        return mount / sub if sub else mount

    def describe(self):
        return str(self.config.get("mount_path") or "")


# ---------------------------------------------------------------------------
# S3-compatible storage (AWS S3, Cloudflare R2, Supabase Storage, Backblaze B2)
# ---------------------------------------------------------------------------
class S3Driver:
    def __init__(self, destination, client=None):
        self.destination = destination
        self.config = destination.config or {}
        self._client = client

    def _env(self, suffix):
        prefix = (self.destination.credential_env_prefix or "").strip()
        return os.environ.get(f"{prefix}{suffix}", "").strip() if prefix else ""

    def bucket(self):
        bucket = (self.config.get("bucket") or "").strip()
        if not bucket:
            raise DestinationError("No bucket is set for this destination.")
        return bucket

    def key(self, run_label, name=""):
        prefix = (self.config.get("prefix") or "").strip().strip("/")
        parts = [p for p in (prefix, _clean_name(run_label), name and _clean_name(name)) if p]
        return "/".join(parts)

    def client(self):
        if self._client is not None:
            return self._client
        missing = self.destination.missing_env
        if not (self.destination.credential_env_prefix or "").strip():
            raise DestinationError("Set a credential prefix (for example BACKUP_S3_PRIMARY_) for this destination.")
        if missing:
            raise DestinationError("Missing in the server's .env: " + ", ".join(missing))
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - boto3 is in requirements
            raise DestinationError("The boto3 package is not installed on this server.") from exc
        endpoint = self._env("ENDPOINT_URL") or (self.config.get("endpoint_url") or "").strip() or None
        region = self._env("REGION") or (self.config.get("region") or "").strip() or None
        style = (self.config.get("addressing_style") or "auto").strip() or "auto"
        cfg = Config(
            s3={"addressing_style": style},
            retries={"max_attempts": 4, "mode": "standard"},
            connect_timeout=15, read_timeout=120,
            # R2, Supabase and B2 do not all accept the newer default checksums.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=self._env("ACCESS_KEY_ID"),
            aws_secret_access_key=self._env("SECRET_ACCESS_KEY"),
            config=cfg,
        )
        return self._client

    @staticmethod
    def _explain(exc):
        text = str(exc)
        code = getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
        return f"{code}: {text}" if code and code not in text else text

    def put(self, local_path, run_label, name):
        key = self.key(run_label, name)
        try:
            self.client().upload_file(str(local_path), self.bucket(), key)
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(f"Upload of {name} failed: {self._explain(exc)}") from exc
        return f"s3://{self.bucket()}/{self.key(run_label)}"

    def get(self, run_label, name, local_path):
        try:
            self.client().download_file(self.bucket(), self.key(run_label, name), str(local_path))
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(f"Download of {name} failed: {self._explain(exc)}") from exc

    def delete_run(self, run_label):
        client, bucket = self.client(), self.bucket()
        prefix = self.key(run_label) + "/"
        try:
            token = None
            while True:
                kwargs = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                page = client.list_objects_v2(**kwargs)
                keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                for i in range(0, len(keys), 1000):
                    client.delete_objects(Bucket=bucket, Delete={"Objects": keys[i:i + 1000], "Quiet": True})
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
        except Exception as exc:
            raise DestinationError(f"Could not delete {prefix}: {self._explain(exc)}") from exc

    def test(self):
        client, bucket = self.client(), self.bucket()
        prefix = (self.config.get("prefix") or "").strip().strip("/")
        key = "/".join(p for p in (prefix, f".nbte-backup-test-{secrets.token_hex(6)}") if p)
        payload = secrets.token_bytes(64)
        try:
            client.put_object(Bucket=bucket, Key=key, Body=payload)
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            if body != payload:
                raise DestinationError("The test object read back differently from what was written.")
        except DestinationError:
            raise
        except Exception as exc:
            raise DestinationError(self._explain(exc)) from exc
        finally:
            try:
                client.delete_object(Bucket=bucket, Key=key)
            except Exception:
                pass
        return f"Wrote, read back and deleted a test object in s3://{bucket}/{prefix}."

    def describe(self):
        return self.destination.target_display


def driver_for(destination, *, s3_client=None):
    from .models import BackupDestination

    if destination.kind == BackupDestination.LOCAL:
        return FolderDriver(destination)
    if destination.kind == BackupDestination.MOUNTED:
        return MountedDriver(destination)
    if destination.kind == BackupDestination.S3:
        return S3Driver(destination, client=s3_client or _S3_CLIENT_FACTORY(destination))
    raise DestinationError(f"Unsupported destination kind: {destination.kind}")


def _no_client(destination):
    return None


# Tests replace this with a factory that returns a fake boto3 client.
_S3_CLIENT_FACTORY = _no_client


# ---------------------------------------------------------------------------
# Detecting mounted drives
# ---------------------------------------------------------------------------
def _proc_mounts():
    points = set()
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    points.add(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return points


def detect_mounts(roots=MOUNT_ROOTS):
    """Writable mount points under /media, /mnt, /run/media and /Volumes.

    Returns [{path, free, total, writable}] (writable ones first)."""
    candidates = set()
    for point in _proc_mounts():
        if any(point == r or point.startswith(r.rstrip("/") + "/") for r in roots):
            if point not in roots:
                candidates.add(point)
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        try:
            level1 = [p for p in root_path.iterdir() if p.is_dir()]
        except OSError:
            continue
        for p in level1:
            if os.path.ismount(p) or root == "/Volumes":
                candidates.add(str(p))
            try:  # /media/<user>/<label>, /run/media/<user>/<label>
                for q in p.iterdir():
                    if q.is_dir() and os.path.ismount(q):
                        candidates.add(str(q))
            except OSError:
                continue
    result = []
    for path in sorted(candidates):
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        result.append({"path": path, "free": usage.free, "total": usage.total,
                       "writable": os.access(path, os.W_OK)})
    result.sort(key=lambda m: (not m["writable"], m["path"]))
    return result


def human_size(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"
