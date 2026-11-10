"""python manage.py fix_duplicate_directors --dry-run
python manage.py fix_duplicate_directors --apply [--by es@example]

Part 1a: a department with more than one director (or more than one holder of
another single-holder role, ``org.single_holder_roles``) has the role removed
from all of them and its seat left empty. Writes docs/reports/duplicate_directors.md;
``--apply`` audits every removal and notifies the ES and super admin.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

REPORT = Path(settings.BASE_DIR) / "docs" / "reports" / "duplicate_directors.md"


class Command(BaseCommand):
    help = "Find (--dry-run) or remove (--apply) duplicated directors and other single-holder roles."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--by", default="", help="E-mail of the person applying (audit log).")

    def handle(self, *args, **o):
        from accounts.models import User
        from org import single_holder

        actor = None
        if o["by"]:
            actor = User.objects.filter(email__iexact=o["by"]).first()
            if actor is None:
                raise CommandError(f"No account {o['by']}.")
        rows = single_holder.duplicates()
        text = single_holder.report_text(rows, applied=o["apply"])
        if o["apply"] and rows:
            single_holder.apply(actor, rows)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        if rows or not REPORT.exists():  # an empty dry run never overwrites the last real report
            REPORT.write_text(text + "\n", encoding="utf-8")
        self.stdout.write(text)
        self.stdout.write("")
        try:
            shown = REPORT.relative_to(settings.BASE_DIR)
        except ValueError:
            shown = REPORT
        self.stdout.write(f"Report written to {shown}")
        if o["dry_run"] and rows:
            self.stdout.write("Nothing changed. Run again with --apply to vacate these seats.")
