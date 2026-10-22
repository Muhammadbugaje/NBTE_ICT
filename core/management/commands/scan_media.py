"""Scan the media roots for orphaned files — safely.

A file is an orphan only when nothing refers to it (any file field of any
model, including soft-deleted, archived and institution-scoped rows; any
text or JSON value naming it), it is not in a protected folder, and it is
older than the age threshold. See `core.media_scanner` and MEDIA_STORAGE.md.

Usage:

    # Dry-run report (default): every orphan with the reason
    python manage.py scan_media
    python manage.py scan_media --grouped
    python manage.py scan_media --report /tmp/orphans.csv
    python manage.py scan_media --show-kept      # also list unreferenced-but-new files

    # Move orphans to MEDIA_ROOT/.quarantine/<date>/ (and the protected twin)
    python manage.py scan_media --quarantine [--older-than 30]
    # (--delete is kept as an alias of --quarantine; nothing is unlinked)

    # Permanently remove quarantine days older than the retention period
    python manage.py scan_media --purge-quarantine [--retention 30] [--dry-run]

    # Put a quarantined day back
    python manage.py scan_media --restore 2026-10-20
"""
import csv

from django.core.management.base import BaseCommand, CommandError

from core import media_scanner as scanner
from core.media_scanner import human_age, human_size


class Command(BaseCommand):
    help = "Find files with no reference (dry run by default); quarantine, purge or restore them."

    def add_arguments(self, parser):
        parser.add_argument("--quarantine", action="store_true",
                            help="Move orphans to the quarantine folder (default: report only).")
        parser.add_argument("--delete", action="store_true",
                            help="Alias of --quarantine (files are never deleted straight away).")
        parser.add_argument("--older-than", type=int, default=None,
                            help="Age threshold in days (default: files.orphan_purge_min_age_days).")
        parser.add_argument("--grouped", action="store_true", help="Group the report by top-level folder.")
        parser.add_argument("--max-list", type=int, default=200, help="Max files to print (default: 200).")
        parser.add_argument("--report", help="Write every orphan, with its reason, to this CSV file.")
        parser.add_argument("--show-kept", action="store_true",
                            help="Also list unreferenced files kept because they are too new.")
        parser.add_argument("--purge-quarantine", action="store_true",
                            help="Remove quarantine days older than the retention period.")
        parser.add_argument("--retention", type=int, default=None,
                            help="Retention in days for --purge-quarantine (default: files.quarantine_retention_days).")
        parser.add_argument("--dry-run", action="store_true", help="With --purge-quarantine: only report.")
        parser.add_argument("--restore", metavar="YYYY-MM-DD", help="Restore a quarantined day.")

    def handle(self, *args, **opts):
        if opts["restore"]:
            restored, skipped = scanner.restore_quarantine(opts["restore"])
            self.stdout.write(self.style.SUCCESS(f"Restored {restored} file(s); {skipped} skipped "
                                                 "(missing, or a file already exists at the original path)."))
            return
        if opts["purge_quarantine"]:
            res = scanner.purge_quarantine(opts["retention"], dry_run=opts["dry_run"])
            verb = "Would remove" if opts["dry_run"] else "Removed"
            self.stdout.write(self.style.SUCCESS(
                f"{verb} {len(res.days_removed)} quarantine day(s), {res.files_removed} file(s), "
                f"{human_size(res.bytes_freed)}: {', '.join(res.days_removed) or 'none'}."))
            for err in res.errors:
                self.stderr.write(err)
            return

        min_age = opts["older_than"] if opts["older_than"] is not None else scanner.purge_min_age_days()
        if min_age < 0:
            raise CommandError("--older-than must be 0 or more.")
        self.stdout.write("Scanning… (dry run)" if not (opts["quarantine"] or opts["delete"]) else "Scanning…")
        result = scanner.scan(min_age_days=min_age)

        self.stdout.write(f"Total files scanned: {result.total_files}")
        self.stdout.write(f"Total size:          {human_size(result.total_bytes)}")
        self.stdout.write(f"Age threshold:       {min_age} day(s)")
        self.stdout.write("Protected folders:   " + ", ".join(
            (f"{k}:{p}" if k else p) for k, p in scanner.protected_folders()))
        if result.recent_unreferenced:
            self.stdout.write(f"Kept (too new):      {len(result.recent_unreferenced)} unreferenced file(s)")
            if opts["show_kept"]:
                for o in result.recent_unreferenced[: opts["max_list"]]:
                    self.stdout.write(f"  [{o['kind']}] {o['rel']}  — {o['reason']}")
        if result.truncated:
            self.stdout.write(self.style.WARNING("Scan hit the file limit — results may be partial."))

        if opts["report"]:
            self._write_report(opts["report"], result.orphans)
            self.stdout.write(f"Report written to {opts['report']}")

        if not result.orphans:
            self.stdout.write(self.style.SUCCESS("No orphaned files found."))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {len(result.orphans)} orphan(s), {human_size(result.orphan_bytes)} total."))
        if opts["grouped"]:
            self._print_grouped(result.orphans)
        else:
            self._print_flat(result.orphans, max_list=opts["max_list"])

        if not (opts["quarantine"] or opts["delete"]):
            self.stdout.write("Dry run: nothing moved. Use --quarantine to move these to the quarantine folder.")
            return

        paths = [{"kind": o["kind"], "rel": o["rel"]} for o in result.orphans]
        summary = scanner.quarantine_paths(paths, min_age_days=min_age)
        parts = []
        if summary.deleted:
            parts.append(f"{summary.deleted} moved to .quarantine/{summary.quarantine_day} "
                         f"({human_size(summary.freed)})")
        if summary.skipped_in_use:
            parts.append(f"{summary.skipped_in_use} now in use")
        if summary.skipped_young:
            parts.append(f"{summary.skipped_young} too recent")
        if summary.skipped_missing:
            parts.append(f"{summary.skipped_missing} already gone")
        if summary.errors:
            parts.append(f"{len(summary.errors)} error(s)")
        self.stdout.write(self.style.SUCCESS(", ".join(parts) or "Nothing changed."))

    # ------------------------------------------------------------------
    def _write_report(self, path, orphans):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["root", "path", "size_bytes", "age_days", "reason"])
            for o in orphans:
                writer.writerow([o["kind"], o["rel"], o["size"], o["age_days"], o["reason"]])

    def _print_flat(self, orphans, max_list):
        for o in sorted(orphans, key=lambda o: o["mtime"])[:max_list]:
            self.stdout.write(f"  [{o['kind']}] {o['rel']}  {human_size(o['size'])}  ({human_age(o['mtime'])})")
            self.stdout.write(f"      why: {o['reason']}")
        if len(orphans) > max_list:
            self.stdout.write(f"  … and {len(orphans) - max_list} more")

    def _print_grouped(self, orphans):
        buckets = {}
        for o in orphans:
            top = o["rel"].split("/", 1)[0] if "/" in o["rel"] else "(root)"
            b = buckets.setdefault(f"[{o['kind']}] {top}", {"count": 0, "size": 0})
            b["count"] += 1
            b["size"] += o["size"]
        for key in sorted(buckets, key=lambda k: buckets[k]["size"], reverse=True):
            b = buckets[key]
            self.stdout.write(f"  {key}: {b['count']} file(s), {human_size(b['size'])}")
