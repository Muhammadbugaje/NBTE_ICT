"""Scan MEDIA_ROOT and PROTECTED_MEDIA_ROOT for orphaned files.

An orphan is a file on disk whose name does not appear in any
registered FileField row — directly, or as a variant of a name that does.

Public API:

    build_reference_index() -> (refs_public, refs_protected)
        Sets of storage-relative names considered "in use".

    walk_storage() -> iterable of FileEntry
        Every file under both roots. Single filesystem walk.

    scan(max_files=None) -> ScanResult
        Synchronous scan — used by the CLI management command and the
        no-JS POST fallback.

    scan_stream(max_files=None) -> generator of dict events
        Streaming scan — used by the SSE endpoint. Yields progress
        and results as they're computed.

    storage_stats() -> Stats
        Per-root and per-top-folder file count + byte totals.

    delete_paths(paths, min_age_days=None, actor=None) -> DeleteResult
        Delete the given storage-relative paths. Refuses files younger
        than min_age_days. Records each deletion in the audit log.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

KIND_PUBLIC = "public"
KIND_PROTECTED = "protected"

_DEFAULT_SIZES = (300, 64)


# ===========================================================================
# Config
# ===========================================================================
def _sizes():
    try:
        import config
        raw = config.get("media.thumbnail_sizes", list(_DEFAULT_SIZES))
    except Exception:
        return list(_DEFAULT_SIZES)
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
    if isinstance(parsed, int):
        parsed = [parsed]
    if not isinstance(parsed, (list, tuple)):
        return list(_DEFAULT_SIZES)
    out = []
    for item in parsed:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out or list(_DEFAULT_SIZES)


def max_files():
    try:
        import config
        return int(config.get("files.orphan_scan_max_files", 5000))
    except Exception:
        return 5000


def purge_min_age_days():
    try:
        import config
        return int(config.get("files.orphan_purge_min_age_days", 30))
    except Exception:
        return 30


# ===========================================================================
# Roots — with a BASE_DIR safety guard
# ===========================================================================
def _roots():
    """Return [(kind, path), ...] for valid media roots only.

    Refuses any root that resolves to BASE_DIR — the exact bug that
    once caused the scanner to walk source code instead of media.
    """
    base = Path(settings.BASE_DIR).resolve()
    candidates = [
        (KIND_PUBLIC,    Path(settings.MEDIA_ROOT).resolve()),
        (KIND_PROTECTED, Path(settings.PROTECTED_MEDIA_ROOT).resolve()),
    ]
    out = []
    for kind, root in candidates:
        if root == base:
            logger.error(
                "media_scanner: refusing %s root == BASE_DIR (%s). "
                "Set MEDIA_ROOT in .env to a subfolder such as 'media'.",
                kind, root,
            )
            continue
        out.append((kind, root))
    return out


def _kind_for_storage(storage):
    try:
        loc = str(getattr(storage, "location", "") or "")
    except Exception:
        return KIND_PUBLIC
    protected_root = str(getattr(settings, "PROTECTED_MEDIA_ROOT", "") or "")
    if protected_root and protected_root in loc:
        return KIND_PROTECTED
    return KIND_PUBLIC


# ===========================================================================
# Reference index
# ===========================================================================
def build_reference_index():
    """Return (refs_public, refs_protected) — sets of storage-relative
    names considered in-use. Includes every variant of every original."""
    try:
        from core.files import registered_models, registered_fields
    except Exception:
        logger.exception("media_scanner: could not import file registry")
        return set(), set()

    try:
        from core.images import variant_name
    except Exception:
        variant_name = None

    sizes = _sizes()
    refs_public: set[str] = set()
    refs_protected: set[str] = set()

    for model in registered_models():
        for fname in registered_fields(model):
            try:
                field = model._meta.get_field(fname)
            except Exception:
                continue

            kind = _kind_for_storage(field.storage)
            target = refs_public if kind == KIND_PUBLIC else refs_protected

            try:
                rows = (
                    getattr(model, "_base_manager", model._default_manager)
                    .exclude(**{fname: ""})
                    .exclude(**{f"{fname}__isnull": True})
                    .values_list(fname, flat=True)
                    .iterator()
                )
            except Exception:
                logger.exception(
                    "media_scanner: query failed for %s.%s",
                    model._meta.label, fname,
                )
                continue

            for name in rows:
                if not name:
                    continue
                target.add(name)

                if variant_name is None:
                    continue

                for size in sizes:
                    try:
                        target.add(variant_name(name, size))
                    except Exception:
                        pass

    return refs_public, refs_protected


# ===========================================================================
# Filesystem walk
# ===========================================================================
@dataclass
class FileEntry:
    kind: str
    rel: str
    abs: Path
    size: int
    mtime: float


def walk_storage(limit=None):
    """Yield FileEntry for every file under both roots."""
    count = 0
    for kind, root in _roots():
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fname in files:
                if fname.startswith("."):
                    continue
                abs_path = Path(dirpath) / fname
                try:
                    st = abs_path.stat()
                except OSError:
                    continue
                try:
                    rel = abs_path.relative_to(root).as_posix()
                except ValueError:
                    continue
                yield FileEntry(
                    kind=kind,
                    rel=rel,
                    abs=abs_path,
                    size=st.st_size,
                    mtime=st.st_mtime,
                )
                count += 1
                if limit and count >= limit:
                    return


def _quick_count(limit=None):
    """Fast count of files under both roots. No DB access."""
    count = 0
    for _kind, root in _roots():
        if not root.is_dir():
            continue
        for _dirpath, _dirs, files in os.walk(root):
            for fname in files:
                if fname.startswith("."):
                    continue
                count += 1
                if limit and count >= limit:
                    return count
    return count


# ===========================================================================
# Scan — synchronous
# ===========================================================================
@dataclass
class ScanResult:
    orphans: list = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    orphan_bytes: int = 0
    truncated: bool = False
    scanned_at: datetime = None

    def to_dict(self):
        return {
            "orphans": [
                {
                    "kind": o["kind"],
                    "rel": o["rel"],
                    "size": o["size"],
                    "mtime": o["mtime"],
                    "age_days": o["age_days"],
                    "is_variant": o["is_variant"],
                }
                for o in self.orphans
            ],
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "orphan_bytes": self.orphan_bytes,
            "truncated": self.truncated,
            "scanned_at": (self.scanned_at or timezone.now()).isoformat(),
        }


def _looks_like_variant(rel):
    """True if the file name ends with _w<digits>.jpg — a variant produced
    by core.images. UI label only; the reference index is authoritative."""
    stem = Path(rel).stem
    if "_w" not in stem:
        return False
    tail = stem.rsplit("_w", 1)[-1]
    return tail.isdigit()


def scan(limit=None):
    refs_public, refs_protected = build_reference_index()
    refs = {KIND_PUBLIC: refs_public, KIND_PROTECTED: refs_protected}

    result = ScanResult(scanned_at=timezone.now())
    now = timezone.now().timestamp()
    limit = limit or max_files()

    for entry in walk_storage(limit=limit):
        result.total_files += 1
        result.total_bytes += entry.size

        if entry.rel in refs[entry.kind]:
            continue

        age_days = max(0, int((now - entry.mtime) // 86400))
        result.orphans.append({
            "kind": entry.kind,
            "rel": entry.rel,
            "size": entry.size,
            "mtime": entry.mtime,
            "age_days": age_days,
            "is_variant": _looks_like_variant(entry.rel),
        })
        result.orphan_bytes += entry.size

    if result.total_files >= limit:
        result.truncated = True

    result.orphans.sort(key=lambda o: o["mtime"], reverse=True)
    return result


# ===========================================================================
# Scan — streaming (SSE)
# ===========================================================================
def scan_stream(limit=None):
    """Generator yielding dict events during a scan.

    Event shapes:

        {"type": "phase",    "phase": "counting"}
        {"type": "counted",  "total": 1234}
        {"type": "phase",    "phase": "indexing"}
        {"type": "indexed",  "refs": 42}
        {"type": "phase",    "phase": "scanning"}
        {"type": "progress", "scanned": 100, "total": 1234, "current": "..."}
        {"type": "found",    "orphan": {...}, "scanned": 12, "total": 1234}
        {"type": "done",     "summary": {...}}
        {"type": "error",    "message": "..."}
    """
    limit = limit or max_files()

    try:
        # ---- Phase 1: count files ----------------------------------------
        yield {"type": "phase", "phase": "counting"}
        total = _quick_count(limit=limit)
        yield {"type": "counted", "total": total}

        # ---- Phase 2: build reference index ------------------------------
        yield {"type": "phase", "phase": "indexing"}
        refs_public, refs_protected = build_reference_index()
        refs = {KIND_PUBLIC: refs_public, KIND_PROTECTED: refs_protected}
        yield {
            "type": "indexed",
            "refs": len(refs_public) + len(refs_protected),
        }

        # ---- Phase 3: scan -----------------------------------------------
        yield {"type": "phase", "phase": "scanning"}

        result = ScanResult(scanned_at=timezone.now())
        now = timezone.now().timestamp()
        scanned = 0
        EMIT_EVERY = 25

        for entry in walk_storage(limit=limit):
            scanned += 1
            result.total_files = scanned
            result.total_bytes += entry.size

            is_orphan = entry.rel not in refs[entry.kind]

            if is_orphan:
                age_days = max(0, int((now - entry.mtime) // 86400))
                orphan = {
                    "kind": entry.kind,
                    "rel": entry.rel,
                    "size": entry.size,
                    "mtime": entry.mtime,
                    "age_days": age_days,
                    "is_variant": _looks_like_variant(entry.rel),
                    "size_h": human_size(entry.size),
                    "age_h": human_age(entry.mtime),
                }
                result.orphans.append(orphan)
                result.orphan_bytes += entry.size
                yield {
                    "type": "found",
                    "orphan": orphan,
                    "scanned": scanned,
                    "total": total,
                }
            elif scanned % EMIT_EVERY == 0:
                yield {
                    "type": "progress",
                    "scanned": scanned,
                    "total": total,
                    "current": entry.rel,
                }

        if scanned >= limit:
            result.truncated = True

        result.orphans.sort(key=lambda o: o["mtime"], reverse=True)

        yield {
            "type": "done",
            "summary": {
                "total_files": result.total_files,
                "total_bytes": result.total_bytes,
                "total_bytes_h": human_size(result.total_bytes),
                "orphan_count": len(result.orphans),
                "orphan_bytes": result.orphan_bytes,
                "orphan_bytes_h": human_size(result.orphan_bytes),
                "truncated": result.truncated,
            },
        }

    except Exception as exc:
        logger.exception("scan_stream failed")
        yield {
            "type": "error",
            "message": str(exc) or "Scan failed. Check the server log.",
        }


# ===========================================================================
# Stats
# ===========================================================================
@dataclass
class Stats:
    total_files: int = 0
    total_bytes: int = 0
    public_files: int = 0
    public_bytes: int = 0
    protected_files: int = 0
    protected_bytes: int = 0
    folders: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "public_files": self.public_files,
            "public_bytes": self.public_bytes,
            "protected_files": self.protected_files,
            "protected_bytes": self.protected_bytes,
            "folders": self.folders,
        }


def storage_stats(limit=None):
    stats = Stats()
    limit = limit or max_files()
    for entry in walk_storage(limit=limit):
        stats.total_files += 1
        stats.total_bytes += entry.size

        if entry.kind == KIND_PUBLIC:
            stats.public_files += 1
            stats.public_bytes += entry.size
        else:
            stats.protected_files += 1
            stats.protected_bytes += entry.size

        top = entry.rel.split("/", 1)[0] if "/" in entry.rel else "(root)"
        key = f"{entry.kind}/{top}"
        bucket = stats.folders.setdefault(key, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += entry.size

    return stats


# ===========================================================================
# Delete
# ===========================================================================
@dataclass
class DeleteResult:
    deleted: int = 0
    freed: int = 0
    skipped_young: int = 0
    skipped_missing: int = 0
    errors: list = field(default_factory=list)


def delete_paths(paths, min_age_days=None, actor=None):
    """Delete the given (kind, rel) pairs from disk.

    Refuses files whose mtime is newer than `min_age_days`. Records each
    deletion in the audit log.
    """
    if min_age_days is None:
        min_age_days = purge_min_age_days()

    cutoff = timezone.now().timestamp() - (min_age_days * 86400)
    roots = dict(_roots())
    result = DeleteResult()

    for path in paths:
        kind = path.get("kind")
        rel = path.get("rel")
        if kind not in roots or not rel:
            continue

        abs_path = roots[kind] / rel
        try:
            abs_path.resolve().relative_to(roots[kind].resolve())
        except Exception:
            result.errors.append(f"refused: {kind}/{rel}")
            continue

        if not abs_path.is_file():
            result.skipped_missing += 1
            continue

        try:
            st = abs_path.stat()
        except OSError as exc:
            result.errors.append(f"{kind}/{rel}: {exc}")
            continue

        if min_age_days > 0 and st.st_mtime > cutoff:
            result.skipped_young += 1
            continue

        try:
            abs_path.unlink()
            result.deleted += 1
            result.freed += st.st_size
            logger.warning(
                "media_scanner: DELETED %s/%s (%s, %d bytes)",
                kind, rel,
                f"orphan, {min_age_days}d threshold", st.st_size,
            )
        except OSError as exc:
            result.errors.append(f"{kind}/{rel}: {exc}")

    if actor is not None and result.deleted:
        try:
            from core import audit
            audit.log(
                "other", None,
                actor=actor,
                note=(
                    f"Purged {result.deleted} orphaned file(s), "
                    f"freed {result.freed} bytes"
                ),
            )
        except Exception:
            logger.exception("media_scanner: audit log failed")

    return result


# ===========================================================================
# Human formatting helpers
# ===========================================================================
def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def human_age(mtime):
    delta = timezone.now().timestamp() - mtime
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    days = int(delta // 86400)
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"