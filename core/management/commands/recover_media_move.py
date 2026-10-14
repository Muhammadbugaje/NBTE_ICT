"""Undo move_legacy_media by moving non-media files back to project root.

Media files (images, documents, video, archives) stay under media/.
Everything else — .py, .pyc, migrations, templates, configs — goes
back to where it was.
"""
import shutil
from pathlib import Path

BASE = Path("/Users/macbookair/Desktop/Web/NBTE ICT System/nbte-ict").resolve()
MEDIA = BASE / "media"

PREFIXES = [
    "accounts", "assets", "attendance", "config", "core",
    "dashboards", "leave", "messaging", "notifications",
    "portal", "public", "requests", "staff", "tasks", "tickets", "workflow",
]

# Extensions that legitimately belong under media/.
MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".heic", ".heif", ".tif", ".tiff",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
    ".mp4", ".mov", ".webm", ".avi", ".mkv",
    ".zip", ".tar", ".gz", ".7z",
}

moved_back = 0
kept_in_media = 0
errors = 0

for prefix in PREFIXES:
    src_root = MEDIA / prefix
    if not src_root.is_dir():
        continue

    for src in list(src_root.rglob("*")):
        if not src.is_file():
            continue

        if src.suffix.lower() in MEDIA_EXT:
            kept_in_media += 1
            continue

        # Move back to project root.
        rel = src.relative_to(MEDIA)
        dst = BASE / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                print(f"  SKIP (exists): {rel}")
                continue
            shutil.move(str(src), str(dst))
            moved_back += 1
        except Exception as exc:
            print(f"  ERROR {rel}: {exc}")
            errors += 1

# Clean up empty folders inside media/<prefix>/.
for prefix in PREFIXES:
    src_root = MEDIA / prefix
    if not src_root.is_dir():
        continue
    for path in sorted(src_root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        if not any(src_root.iterdir()):
            src_root.rmdir()
    except OSError:
        pass

print()
print(f"Moved back to project root: {moved_back}")
print(f"Kept under media/:          {kept_in_media}")
print(f"Errors:                     {errors}")