"""Scan the media roots for orphaned files.

Every orphan is a file on disk with no database reference — directly or
as a variant of a referenced file. The classification is done by
`core.media_scanner`, which the /settings/files/ page also uses.

Usage:

    # Report only (safe)
    python manage.py scan_media

    # Grouped by top-level folder
    python manage.py scan_media --grouped

    # Delete orphans older than 30 days
    python manage.py scan_media --delete --older-than 30

    # Delete every orphan regardless of age (careful!)
    python manage.py scan_media --delete --older-than 0
"""
from django.core.management.base import BaseCommand

from core.media_scanner import (
    delete_paths,
    human_age,
    human_size,
    scan,
)


class Command(BaseCommand):
    help = "Find files with no database reference."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete", action="store_true",
            help="Delete orphaned files (default: report only).",
        )
        parser.add_argument(
            "--older-than", type=int, default=30,
            help="Only delete orphans older than N days (default: 30).",
        )
        parser.add_argument(
            "--grouped", action="store_true",
            help="Group the report by top-level folder.",
        )
        parser.add_argument(
            "--max-list", type=int, default=200,
            help="Max individual files to print (default: 200).",
        )

    def handle(self, *args, **opts):
        self.stdout.write("Scanning…")
        result = scan()

        self.stdout.write(f"Total files scanned: {result.total_files}")
        self.stdout.write(f"Total size:          {human_size(result.total_bytes)}")

        if result.truncated:
            self.stdout.write(self.style.WARNING(
                "Scan hit the file limit — results may be partial."
            ))

        if not result.orphans:
            self.stdout.write(self.style.SUCCESS("No orphaned files found."))
            return

        self.stdout.write(self.style.WARNING(
            f"Found {len(result.orphans)} orphan(s), "
            f"{human_size(result.orphan_bytes)} total."
        ))

        if opts["grouped"]:
            self._print_grouped(result.orphans)
        else:
            self._print_flat(result.orphans, max_list=opts["max_list"])

        if not opts["delete"]:
            return

        paths = [{"kind": o["kind"], "rel": o["rel"]} for o in result.orphans]
        summary = delete_paths(paths, min_age_days=opts["older_than"])

        parts = []
        if summary.deleted:
            parts.append(f"{summary.deleted} deleted ({human_size(summary.freed)})")
        if summary.skipped_young:
            parts.append(f"{summary.skipped_young} too recent")
        if summary.skipped_missing:
            parts.append(f"{summary.skipped_missing} already gone")
        if summary.errors:
            parts.append(f"{len(summary.errors)} error(s)")

        self.stdout.write(self.style.SUCCESS(", ".join(parts) or "Nothing changed."))

    # ------------------------------------------------------------------
    def _print_flat(self, orphans, max_list):
        ordered = sorted(orphans, key=lambda o: o["mtime"])
        for o in ordered[:max_list]:
            self.stdout.write(
                f"  [{o['kind']}] {o['rel']}  "
                f"{human_size(o['size'])}  ({human_age(o['mtime'])})"
            )
        if len(orphans) > max_list:
            self.stdout.write(f"  … and {len(orphans) - max_list} more")

    def _print_grouped(self, orphans):
        buckets = {}
        for o in orphans:
            top = o["rel"].split("/", 1)[0] if "/" in o["rel"] else "(root)"
            key = f"[{o['kind']}] {top}"
            b = buckets.setdefault(key, {"count": 0, "size": 0})
            b["count"] += 1
            b["size"] += o["size"]
        for key in sorted(buckets, key=lambda k: buckets[k]["size"], reverse=True):
            b = buckets[key]
            self.stdout.write(
                f"  {key}: {b['count']} file(s), {human_size(b['size'])}"
            )