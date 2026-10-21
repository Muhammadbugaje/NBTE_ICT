"""Nightly task/QA reconciliation (Phase 4, §8 step 5).

Compares every open QA stage task with its case: closes tasks whose stage
has moved on (orphans) and opens a task for any current stage without one.
Each run writes a TaskLinkDriftReport (QA cases → Task-link drift).

    python manage.py qa_reconcile_tasks            # repair
    python manage.py qa_reconcile_tasks --dry-run  # report only
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reconcile QA stage tasks with their workflow (close orphans, open missing tasks)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report the drift without repairing it.")

    def handle(self, *args, **opts):
        from qa_core.task_bridge import reconcile

        report = reconcile(dry_run=opts["dry_run"])
        verb = "found" if report.dry_run else "repaired"
        self.stdout.write(f"QA task links: {report.checked} checked; {report.closed} orphaned task(s) and "
                          f"{report.opened} missing task(s) {verb}.")
        for d in report.details:
            self.stdout.write(f"  - {d.get('kind')}: {d.get('subject')} {d.get('stage')} — {d.get('why')}")
