"""Phase 0 (U3): move workflow request record PDFs into restricted storage.

Migration workflow 0007 already does this once; run the command again after
restoring an old backup or copying media from another server. Idempotent:
files already in restricted storage are left alone (a stale public copy is
deleted).

    python manage.py move_record_pdfs            # move
    python manage.py move_record_pdfs --dry-run  # report only
"""
from django.core.files.storage import storages
from django.core.management.base import BaseCommand

from core.migration_utils import _move

FOLDER = "workflow/records"  # workflow.Request.MEDIA_SUBDIR


class Command(BaseCommand):
    help = "Move workflow.Request.record_pdf files from public /media/ to restricted storage."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List what would move; change nothing.")

    def handle(self, *args, dry_run=False, **options):
        from workflow.models import Request

        public, restricted = storages["default"], storages["restricted"]
        names = (Request._base_manager.exclude(record_pdf="").exclude(record_pdf__isnull=True)
                 .values_list("record_pdf", flat=True))
        moved = already = missing = stale = 0
        for name in names:
            in_public, in_restricted = public.exists(name), restricted.exists(name)
            if in_restricted:
                already += 1
                if in_public:
                    stale += 1
                    if not dry_run:
                        public.delete(name)
                continue
            if not in_public:
                missing += 1
                self.stderr.write(f"missing: {name}")
                continue
            if dry_run or _move(public, restricted, name):
                moved += 1
        # Unreferenced leftovers in the public records folder (old versions)
        # are private too: move them as well, so nothing stays under /media/.
        orphans = 0
        referenced = set(names)
        try:
            _dirs, files = public.listdir(FOLDER)
        except FileNotFoundError:
            files = []
        for fname in files:
            name = f"{FOLDER}/{fname}"
            if name in referenced:
                continue
            orphans += 1
            if not dry_run:
                _move(public, restricted, name)
        verb = "would move" if dry_run else "moved"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {moved}; already restricted {already} (stale public copies "
            f"{'found' if dry_run else 'removed'}: {stale}); missing {missing}; "
            f"unreferenced files {verb.split()[-1]} too: {orphans}"))
