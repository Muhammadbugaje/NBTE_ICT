"""Scan MEDIA_ROOT and PROTECTED_MEDIA_ROOT for orphaned files — safely.

A file is an *orphan* only when ALL of these hold:

1. No FileField / ImageField of ANY model refers to it. Every concrete model
   with a file field is walked (not just the cleanup registry), through a
   plain QuerySet that bypasses every manager — so soft-deleted, archived and
   institution-scoped rows (whose default managers refuse unscoped reads)
   all count — plus the ``_base_manager`` / ``unscoped`` / ``all_objects``
   managers as a belt-and-braces second read.
2. No text, char or JSON value anywhere in the database mentions its file
   name (paths stored as strings, e.g. in JSON snapshots, HTML bodies or
   import sessions). Matching is by file name, so it errs on the side of
   keeping a file.
3. It is not a generated image variant (``<name>_w<size>.jpg``) of a
   referenced file.
4. It is not inside a protected folder (``MEDIA_SCAN_PROTECTED_FOLDERS`` in
   settings plus the ``files.orphan_protected_folders`` setting): generated
   or served assets such as the institution guides (``qa/guides``),
   showcase images and import scratch folders.
5. It is older than the age threshold (``files.orphan_purge_min_age_days``).

Nothing is ever deleted straight away: ``quarantine_paths`` re-checks every
file against a FRESH reference index and then moves it to
``<root>/.quarantine/<YYYY-MM-DD>/<original path>`` with a ``manifest.csv``.
``purge_quarantine`` removes quarantine days older than the retention period
(``files.quarantine_retention_days``) and ``restore_quarantine`` puts a
day's files back.

Public API (kept for the CLI and the /settings/files/ page):

    build_reference_index() -> (refs_public, refs_protected)
    build_reference_map() -> ReferenceIndex   (with the source of each ref)
    walk_storage() / scan() / scan_stream() / storage_stats()
    classify(entry, index, now, min_age_days) -> (is_orphan, reason)
    quarantine_paths(paths, min_age_days=None, actor=None) -> DeleteResult
    delete_paths(...)  — alias of quarantine_paths (never unlinks)
    purge_quarantine(older_than_days=None) / restore_quarantine(day)
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

KIND_PUBLIC = "public"
KIND_PROTECTED = "protected"
QUARANTINE_DIR = ".quarantine"

_DEFAULT_SIZES = (300, 64)
DEFAULT_PROTECTED_FOLDERS = (
    "qa/guides",            # institution guides built by generate_institution_guides
    "guides",
    "docs",
    "user-guides",
    "showcase",             # login showcase / gallery images
    "import-tmp",           # import previews (own hourly sweep)
    "staff/imports",        # nominal-roll uploads kept in the session
)


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


def _config_int(key, default):
    try:
        import config
        return int(config.get(key, default))
    except Exception:
        return default


def max_files():
    return _config_int("files.orphan_scan_max_files", 5000)


def purge_min_age_days():
    return max(0, _config_int("files.orphan_purge_min_age_days", 30))


def quarantine_retention_days():
    return max(1, _config_int("files.quarantine_retention_days", 30))


def protected_folders():
    """[(kind or None, prefix), ...] — folders never treated as orphans.

    Entries are ``"prefix"`` (both roots) or ``"public:prefix"`` /
    ``"protected:prefix"``. Settings and the config entry are combined."""
    raw = list(getattr(settings, "MEDIA_SCAN_PROTECTED_FOLDERS", DEFAULT_PROTECTED_FOLDERS) or [])
    try:
        import config
        extra = config.get("files.orphan_protected_folders", "") or ""
    except Exception:
        extra = ""
    if isinstance(extra, str):
        raw += [p for p in re.split(r"[,\n]", extra)]
    elif isinstance(extra, (list, tuple)):
        raw += list(extra)
    out = []
    for item in raw:
        item = str(item or "").strip()
        if not item:
            continue
        kind = None
        if ":" in item:
            head, tail = item.split(":", 1)
            if head in (KIND_PUBLIC, KIND_PROTECTED):
                kind, item = head, tail
        prefix = item.strip().strip("/")
        if prefix and (kind, prefix) not in out:
            out.append((kind, prefix))
    return out


def protected_folder_of(kind, rel, folders=None):
    for fkind, prefix in (folders if folders is not None else protected_folders()):
        if fkind and fkind != kind:
            continue
        if rel == prefix or rel.startswith(prefix + "/"):
            return prefix
    return None


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
        loc = Path(str(getattr(storage, "location", "") or "")).resolve()
    except Exception:
        return KIND_PUBLIC
    protected_root = getattr(settings, "PROTECTED_MEDIA_ROOT", "") or ""
    if protected_root:
        try:
            if loc == Path(protected_root).resolve():
                return KIND_PROTECTED
        except Exception:
            pass
        if str(protected_root) in str(loc):
            return KIND_PROTECTED
    return KIND_PUBLIC


# ===========================================================================
# Reference index
# ===========================================================================
@dataclass
class ReferenceIndex:
    """Where each in-use name comes from.

    ``exact[kind][rel]`` — a file field value (or its variant);
    ``names[basename]`` — a file name mentioned in a text/JSON value.
    """
    exact: dict = field(default_factory=lambda: {KIND_PUBLIC: {}, KIND_PROTECTED: {}})
    names: dict = field(default_factory=dict)
    folders: list = field(default_factory=list)
    file_rows: int = 0
    text_values: int = 0

    def total(self):
        return len(self.exact[KIND_PUBLIC]) + len(self.exact[KIND_PROTECTED]) + len(self.names)

    def reference_for(self, kind, rel):
        """A human reason why (kind, rel) is in use, or None."""
        source = self.exact.get(kind, {}).get(rel)
        if source:
            return source
        other = KIND_PROTECTED if kind == KIND_PUBLIC else KIND_PUBLIC
        source = self.exact.get(other, {}).get(rel)
        if source:
            return f"{source} (stored in the other root)"
        base = PurePosixPath(rel).name
        source = self.names.get(base)
        if source:
            return source
        folder = protected_folder_of(kind, rel, self.folders)
        if folder:
            return f"inside protected folder '{folder}'"
        return None


def file_field_models():
    """[(model, [field names])] for every concrete model with file fields —
    the cleanup registry plus anything it misses."""
    from django.apps import apps
    from django.db import models as dj_models

    found = {}
    for model in apps.get_models():
        meta = model._meta
        if meta.proxy or not meta.managed:
            continue
        names = [f.name for f in meta.concrete_fields if isinstance(f, dj_models.FileField)]
        if names:
            found[model] = set(names)
    try:
        from core.files import registered_fields, registered_models
        for model in registered_models():
            if model._meta.proxy:
                continue
            found.setdefault(model, set()).update(registered_fields(model))
    except Exception:
        logger.exception("media_scanner: could not read the file registry")
    return [(m, sorted(f)) for m, f in found.items()]


def all_row_querysets(model):
    """Querysets that together cover EVERY row of `model`.

    A plain ``QuerySet`` bypasses all manager filtering (soft delete,
    archive, institution scoping); the named managers are read as well in
    case a model routes rows somewhere unusual."""
    from django.db import models as dj_models

    yield dj_models.QuerySet(model=model)
    seen = set()
    for name in ("_base_manager", "unscoped", "all_objects", "_default_manager"):
        manager = getattr(model, name, None)
        if manager is None or id(manager) in seen:
            continue
        seen.add(id(manager))
        try:
            qs = manager.get_queryset()
            mark = getattr(qs, "_mark_scoped", None)
            if callable(mark):
                qs = mark()
            yield qs
        except Exception:
            continue


_NAME_RE = re.compile(r"[A-Za-z0-9_\-.%+]+\.[A-Za-z0-9]{1,10}")


def _strings_in(value):
    if value is None:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _strings_in(v)
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _strings_in(v)


def file_names_in(text):
    """File names (basenames) mentioned in a free-text or JSON string."""
    if not text or "." not in text:
        return set()
    out = set()
    for token in _NAME_RE.findall(unquote(text)):
        name = token.rsplit("/", 1)[-1]
        if name and not name.startswith("."):
            out.add(name)
    return out


def _text_fields(model):
    from django.db import models as dj_models

    out = []
    for f in model._meta.concrete_fields:
        if isinstance(f, dj_models.FileField):
            continue
        if isinstance(f, dj_models.JSONField):
            out.append(f.name)
        elif isinstance(f, (dj_models.TextField, dj_models.CharField)) and not f.choices:
            if isinstance(f, dj_models.CharField) and (f.max_length or 0) < 16:
                continue
            out.append(f.name)
    return out


def _variant_names(name, sizes):
    try:
        from core.images import variant_name
    except Exception:
        return []
    out = []
    for size in sizes:
        try:
            out.append(variant_name(name, size))
        except Exception:
            pass
    return out


def build_reference_map(include_text=True):
    """Collect every reference to a stored file, with its source."""
    from django.apps import apps

    sizes = _sizes()
    index = ReferenceIndex(folders=protected_folders())

    for model, fnames in file_field_models():
        label = model._meta.label
        for fname in fnames:
            try:
                fld = model._meta.get_field(fname)
            except Exception:
                continue
            kind = _kind_for_storage(fld.storage)
            target = index.exact[kind]
            for qs in all_row_querysets(model):
                try:
                    rows = (qs.exclude(**{fname: ""}).exclude(**{f"{fname}__isnull": True})
                            .values_list("pk", fname).iterator(chunk_size=2000))
                    for pk, name in rows:
                        if not name or name in target:
                            continue
                        index.file_rows += 1
                        source = f"referenced by {label} #{pk} ({fname})"
                        target[name] = source
                        for variant in _variant_names(name, sizes):
                            target.setdefault(variant, f"image variant of a file {source}")
                except Exception:
                    logger.exception("media_scanner: query failed for %s.%s", label, fname)

    if include_text:
        from django.db import models as dj_models

        for model in apps.get_models():
            meta = model._meta
            if meta.proxy or not meta.managed:
                continue
            fields = _text_fields(model)
            if not fields:
                continue
            try:
                rows = dj_models.QuerySet(model=model).values_list("pk", *fields).iterator(chunk_size=2000)
                for row in rows:
                    pk = row[0]
                    for fname, value in zip(fields, row[1:]):
                        for text in _strings_in(value):
                            index.text_values += 1
                            for name in file_names_in(text):
                                if name not in index.names:
                                    index.names[name] = f"named in {meta.label} #{pk} ({fname})"
                                    for variant in _variant_names(name, sizes):
                                        index.names.setdefault(
                                            PurePosixPath(variant).name,
                                            f"image variant of a file named in {meta.label} #{pk} ({fname})")
            except Exception:
                logger.exception("media_scanner: text scan failed for %s", meta.label)

    return index


def build_reference_index():
    """Backwards-compatible: (refs_public, refs_protected) name sets."""
    index = build_reference_map()
    return set(index.exact[KIND_PUBLIC]), set(index.exact[KIND_PROTECTED])


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
    """Yield FileEntry for every file under both roots (skips dot-folders
    such as the quarantine)."""
    count = 0
    for kind, root in _roots():
        if not root.is_dir():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
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
                yield FileEntry(kind=kind, rel=rel, abs=abs_path, size=st.st_size, mtime=st.st_mtime)
                count += 1
                if limit and count >= limit:
                    return


def _quick_count(limit=None):
    count = 0
    for _entry in walk_storage(limit=limit):
        count += 1
    return count


# ===========================================================================
# Classification
# ===========================================================================
def classify(entry, index, now=None, min_age_days=None):
    """(is_orphan, reason) for one file on disk."""
    now = now if now is not None else timezone.now().timestamp()
    min_age_days = purge_min_age_days() if min_age_days is None else min_age_days
    in_use = index.reference_for(entry.kind, entry.rel)
    if in_use:
        return False, f"In use: {in_use}."
    age_days = max(0, int((now - entry.mtime) // 86400))
    if age_days < min_age_days:
        return False, (f"Not referenced, but only {age_days} day(s) old — kept until it is "
                       f"{min_age_days} days old.")
    return True, ("No database record, text or JSON value refers to it, it is not in a protected "
                  f"folder, and it is {age_days} day(s) old (threshold {min_age_days}).")


# ===========================================================================
# Scan — synchronous
# ===========================================================================
@dataclass
class ScanResult:
    orphans: list = field(default_factory=list)
    recent_unreferenced: list = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0
    orphan_bytes: int = 0
    truncated: bool = False
    min_age_days: int = 0
    scanned_at: datetime = None

    def to_dict(self):
        keys = ("kind", "rel", "size", "mtime", "age_days", "is_variant", "reason")
        return {
            "orphans": [{k: o.get(k) for k in keys} for o in self.orphans],
            "recent_unreferenced": len(self.recent_unreferenced),
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "orphan_bytes": self.orphan_bytes,
            "truncated": self.truncated,
            "min_age_days": self.min_age_days,
            "scanned_at": (self.scanned_at or timezone.now()).isoformat(),
        }


def _looks_like_variant(rel):
    stem = Path(rel).stem
    if "_w" not in stem:
        return False
    return stem.rsplit("_w", 1)[-1].isdigit()


def _orphan_dict(entry, now, reason):
    return {
        "kind": entry.kind,
        "rel": entry.rel,
        "size": entry.size,
        "mtime": entry.mtime,
        "age_days": max(0, int((now - entry.mtime) // 86400)),
        "is_variant": _looks_like_variant(entry.rel),
        "reason": reason,
    }


def scan(limit=None, min_age_days=None):
    index = build_reference_map()
    min_age_days = purge_min_age_days() if min_age_days is None else min_age_days
    result = ScanResult(scanned_at=timezone.now(), min_age_days=min_age_days)
    now = timezone.now().timestamp()
    limit = limit or max_files()

    for entry in walk_storage(limit=limit):
        result.total_files += 1
        result.total_bytes += entry.size
        is_orphan, reason = classify(entry, index, now, min_age_days)
        if is_orphan:
            result.orphans.append(_orphan_dict(entry, now, reason))
            result.orphan_bytes += entry.size
        elif reason.startswith("Not referenced"):
            result.recent_unreferenced.append(_orphan_dict(entry, now, reason))

    if result.total_files >= limit:
        result.truncated = True
    result.orphans.sort(key=lambda o: o["mtime"], reverse=True)
    return result


# ===========================================================================
# Scan — streaming (SSE)
# ===========================================================================
def scan_stream(limit=None):
    """Generator yielding dict events during a scan (see views_files)."""
    limit = limit or max_files()
    try:
        yield {"type": "phase", "phase": "counting"}
        total = _quick_count(limit=limit)
        yield {"type": "counted", "total": total}

        yield {"type": "phase", "phase": "indexing"}
        index = build_reference_map()
        yield {"type": "indexed", "refs": index.total()}

        yield {"type": "phase", "phase": "scanning"}
        min_age_days = purge_min_age_days()
        result = ScanResult(scanned_at=timezone.now(), min_age_days=min_age_days)
        now = timezone.now().timestamp()
        scanned = 0
        emit_every = 25

        for entry in walk_storage(limit=limit):
            scanned += 1
            result.total_files = scanned
            result.total_bytes += entry.size
            is_orphan, reason = classify(entry, index, now, min_age_days)
            if is_orphan:
                orphan = _orphan_dict(entry, now, reason)
                orphan.update({"size_h": human_size(entry.size), "age_h": human_age(entry.mtime)})
                result.orphans.append(orphan)
                result.orphan_bytes += entry.size
                yield {"type": "found", "orphan": orphan, "scanned": scanned, "total": total}
            elif scanned % emit_every == 0:
                yield {"type": "progress", "scanned": scanned, "total": total, "current": entry.rel}

        if scanned >= limit:
            result.truncated = True

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
        yield {"type": "error", "message": str(exc) or "Scan failed. Check the server log."}


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
        bucket = stats.folders.setdefault(f"{entry.kind}/{top}", {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += entry.size
    return stats


# ===========================================================================
# Quarantine (never delete straight away)
# ===========================================================================
@dataclass
class DeleteResult:
    deleted: int = 0          # = moved to quarantine (name kept for callers)
    freed: int = 0
    skipped_young: int = 0
    skipped_missing: int = 0
    skipped_in_use: int = 0
    errors: list = field(default_factory=list)
    quarantine_day: str = ""

    @property
    def quarantined(self):
        return self.deleted


def quarantine_root(kind):
    roots = dict(_roots())
    return roots[kind] / QUARANTINE_DIR if kind in roots else None


def _unique_target(path):
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 1
    while True:
        candidate = path.with_name(f"{stem}~{n}{suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def quarantine_paths(paths, min_age_days=None, actor=None, index=None):
    """Move the given {"kind", "rel"} files to the quarantine.

    Every file is re-checked against a fresh reference index (a cached scan
    may be stale) and the age threshold; anything in use or too new is left
    alone. A ``manifest.csv`` in the day folder records each move so
    ``restore_quarantine`` can put files back.
    """
    if min_age_days is None:
        min_age_days = purge_min_age_days()
    roots = dict(_roots())
    index = index if index is not None else build_reference_map()
    now = timezone.now().timestamp()
    day = timezone.localdate().isoformat()
    result = DeleteResult(quarantine_day=day)

    for path in paths:
        kind = path.get("kind")
        rel = (path.get("rel") or "").strip("/")
        if kind not in roots or not rel or rel.split("/", 1)[0] == QUARANTINE_DIR:
            continue
        root = roots[kind]
        abs_path = root / rel
        try:
            abs_path.resolve().relative_to(root.resolve())
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
        entry = FileEntry(kind=kind, rel=rel, abs=abs_path, size=st.st_size, mtime=st.st_mtime)
        is_orphan, reason = classify(entry, index, now, min_age_days)
        if not is_orphan:
            if reason.startswith("In use"):
                result.skipped_in_use += 1
            else:
                result.skipped_young += 1
            continue

        day_dir = root / QUARANTINE_DIR / day
        target = _unique_target(day_dir / rel)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(abs_path), str(target))
        except OSError as exc:
            result.errors.append(f"{kind}/{rel}: {exc}")
            continue
        _append_manifest(day_dir, rel, target.relative_to(day_dir).as_posix(), st.st_size, reason, actor)
        result.deleted += 1
        result.freed += st.st_size
        logger.warning("media_scanner: QUARANTINED %s/%s -> %s (%d bytes)", kind, rel, target, st.st_size)

    if actor is not None and result.deleted:
        try:
            from core import audit
            audit.log("other", None, actor=actor,
                      note=(f"Moved {result.deleted} orphaned file(s) ({result.freed} bytes) "
                            f"to the quarantine ({day})"))
        except Exception:
            logger.exception("media_scanner: audit log failed")
    return result


def delete_paths(paths, min_age_days=None, actor=None):
    """Kept for existing callers: now moves files to the quarantine."""
    return quarantine_paths(paths, min_age_days=min_age_days, actor=actor)


MANIFEST_FIELDS = ["moved_at", "original", "stored_as", "size", "reason", "actor"]


def _append_manifest(day_dir, original, stored_as, size, reason, actor):
    manifest = day_dir / "manifest.csv"
    new = not manifest.exists()
    try:
        with manifest.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow({"moved_at": timezone.now().isoformat(timespec="seconds"), "original": original,
                             "stored_as": stored_as, "size": size, "reason": reason,
                             "actor": getattr(actor, "username", "") or ("system" if actor is None else str(actor))})
    except OSError:
        logger.exception("media_scanner: could not write the quarantine manifest")


@dataclass
class PurgeResult:
    days_removed: list = field(default_factory=list)
    files_removed: int = 0
    bytes_freed: int = 0
    errors: list = field(default_factory=list)


def quarantine_days():
    """[(kind, day, path)] for every quarantine day folder."""
    out = []
    for kind, root in _roots():
        qroot = root / QUARANTINE_DIR
        if not qroot.is_dir():
            continue
        for child in sorted(qroot.iterdir()):
            if child.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", child.name):
                out.append((kind, child.name, child))
    return out


def purge_quarantine(older_than_days=None, dry_run=False):
    """Permanently remove quarantine day folders older than the retention."""
    older_than_days = quarantine_retention_days() if older_than_days is None else older_than_days
    today = timezone.localdate()
    result = PurgeResult()
    for kind, day, path in quarantine_days():
        try:
            age = (today - datetime.strptime(day, "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if age < older_than_days:
            continue
        for dirpath, _dirs, files in os.walk(path):
            for fname in files:
                if fname == "manifest.csv":
                    continue
                result.files_removed += 1
                try:
                    result.bytes_freed += (Path(dirpath) / fname).stat().st_size
                except OSError:
                    pass
        if not dry_run:
            try:
                shutil.rmtree(path)
            except OSError as exc:
                result.errors.append(f"{kind}/{day}: {exc}")
                continue
            logger.warning("media_scanner: PURGED quarantine %s/%s", kind, day)
        result.days_removed.append(f"{kind}/{day}")
    return result


def restore_quarantine(day):
    """Move every file of quarantine `day` back to its original path (never
    overwriting). Returns (restored, skipped)."""
    restored = skipped = 0
    for kind, root in _roots():
        day_dir = root / QUARANTINE_DIR / day
        manifest = day_dir / "manifest.csv"
        if not manifest.is_file():
            continue
        with manifest.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            src = day_dir / row["stored_as"]
            dst = root / row["original"]
            if not src.is_file() or dst.exists():
                skipped += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            restored += 1
    return restored, skipped


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
