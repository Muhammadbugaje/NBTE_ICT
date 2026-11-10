"""python manage.py backfill_work_assignments [--dry-run] [--write PATH]

P.1 (X1): create WorkAssignment rows from the assignment-like fields already
on QA records (case officers, ICT tracking officers, SSQ reviewers, visit
teams, complaint assignees, clearance deciders). The data migration
``assignments/0004_backfill`` runs the same code once; this command is for a
dry run on another database and for re-running after imports (idempotent).
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

DEFAULT_REPORT = "docs/master_plan_v2_1/reports/work_assignment_backfill.md"


class Command(BaseCommand):
    help = "Back-fill WorkAssignment rows from existing assignment-like fields (P.1)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count only; write nothing to the database.")
        parser.add_argument("--grants", action="store_true",
                            help="Also give the kind's access grants to open back-filled work (P.5).")
        parser.add_argument("--write", nargs="?", const=DEFAULT_REPORT, default=None,
                            help=f"Write the counts report (default {DEFAULT_REPORT}).")

    def handle(self, *args, **o):
        from assignments import backfill
        with transaction.atomic():
            report = backfill.run(dry_run=o["dry_run"])
        text = backfill.render_report(report, dry_run=o["dry_run"])
        if o["grants"] and not o["dry_run"]:
            from assignments import work
            from assignments.models import WorkAssignment
            n = 0
            for wa in WorkAssignment.objects.filter(status__in=WorkAssignment.WORKING, meta__has_key="backfill"):
                n += len(work.open_grants(wa))
            text += f"\nGrants opened or kept for open back-filled work: {n}\n"
        self.stdout.write(text)
        if o["write"]:
            path = Path(settings.BASE_DIR) / o["write"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Report written to {o['write']}"))
