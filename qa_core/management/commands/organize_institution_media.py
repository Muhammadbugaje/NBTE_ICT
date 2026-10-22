"""Move existing institution files into per-institution folders.

    python manage.py organize_institution_media              # dry run (default)
    python manage.py organize_institution_media --apply      # needs a fresh backup
    python manage.py organize_institution_media --apply --i-have-a-backup
    python manage.py organize_institution_media --report /path/moves.csv

Every file is moved to ``institutions/<CODE> - <Name>/<kind>/<file>`` (or
``resource-persons/<name>/<file>``) in the same root it lives in now, and the
database field is updated. Files already in place are skipped; nothing is
ever overwritten; every decision is written to a CSV report. Safe to re-run.
See MEDIA_STORAGE.md.
"""
import os
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from qa_core.media_organize import organize, write_report

BACKUP_INSTRUCTIONS = """\
Refusing to move files: no fresh database backup was found.

Take a backup first, then run the command again:

  * SQLite:     cp db.sqlite3 db.sqlite3.bak-$(date +%Y%m%d-%H%M)
  * PostgreSQL: pg_dump -Fc "$DATABASE_URL" > "$DB_BACKUP_DIR/nbte-$(date +%Y%m%d-%H%M).dump"
                (set DB_BACKUP_DIR so this command can see the dump)

Also copy the media folders (MEDIA_ROOT and PROTECTED_MEDIA_ROOT).
If you already have a backup somewhere else, pass --i-have-a-backup.
"""


def find_fresh_backup(max_age_hours=24, db=None):
    """Path of a database backup modified within `max_age_hours`, or None.

    Looks next to a SQLite database (``db.sqlite3.*`` copies) and in the
    folder named by the DB_BACKUP_DIR setting or environment variable."""
    cutoff = time.time() - max_age_hours * 3600
    candidates = []
    db = db if db is not None else settings.DATABASES.get("default", {})
    if "sqlite" in db.get("ENGINE", ""):
        name = Path(str(db.get("NAME", "")))
        if name.parent.is_dir():
            for p in name.parent.glob(name.name + "*"):
                if p == name or p.name.endswith(("-journal", "-wal", "-shm")):
                    continue
                candidates.append(p)
    backup_dir = getattr(settings, "DB_BACKUP_DIR", None) or os.environ.get("DB_BACKUP_DIR")
    if backup_dir and Path(backup_dir).is_dir():
        candidates += [p for p in Path(backup_dir).iterdir() if p.is_file()]
    fresh = [p for p in candidates if p.is_file() and p.stat().st_size > 0 and p.stat().st_mtime >= cutoff]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


class Command(BaseCommand):
    help = "Move institution-linked files into institutions/<CODE> - <Name>/<kind>/ (dry run by default)."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", default=True,
                          help="Only report what would move (the default).")
        mode.add_argument("--apply", action="store_true", help="Move the files and update the database.")
        parser.add_argument("--i-have-a-backup", action="store_true",
                            help="Skip the fresh-backup check (you took a backup elsewhere).")
        parser.add_argument("--backup-max-age-hours", type=int, default=24,
                            help="How recent a backup must be (default 24 hours).")
        parser.add_argument("--report", help="CSV report path (default: PROTECTED_MEDIA_ROOT/.reports/…).")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        if apply and not opts["i_have_a_backup"]:
            backup = find_fresh_backup(opts["backup_max_age_hours"])
            if backup is None:
                self.stderr.write(BACKUP_INSTRUCTIONS)
                raise CommandError("No fresh database backup found.")
            self.stdout.write(f"Backup found: {backup}")

        result = organize(apply=apply)

        stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
        report = opts["report"] or os.path.join(
            str(settings.PROTECTED_MEDIA_ROOT), ".reports",
            f"organize-institution-media-{'apply' if apply else 'dry-run'}-{stamp}.csv")
        write_report(result, report)

        summary = result.summary()
        label = "Applied" if apply else "Dry run"
        self.stdout.write(self.style.SUCCESS(
            f"{label} on {connection.vendor}: "
            + (", ".join(f"{k.replace('_', ' ')} {v}" for k, v in sorted(summary.items())) or "no files")))
        for row in result.rows:
            if row["action"] in ("moved", "would_move", "error", "missing"):
                self.stdout.write(f"  [{row['action']}] {row['model']} #{row['pk']}: {row['old_path']}"
                                  + (f" -> {row['new_path']}" if row["new_path"] else "")
                                  + (f"  ({row['note']})" if row["note"] else ""))
        self.stdout.write(f"Report: {report}")
        if not apply:
            self.stdout.write("Nothing was changed. Run with --apply to move the files.")
