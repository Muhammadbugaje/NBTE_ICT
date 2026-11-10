"""v2.2: reconcile payments now (also runs nightly through Celery beat).

    python manage.py qa_payments_reconcile            # full run, asks the providers
    python manage.py qa_payments_reconcile --offline  # ledger only, no provider calls
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reconcile QA payments: provider records, NBTE records and the double-entry ledger."

    def add_arguments(self, parser):
        parser.add_argument("--offline", action="store_true", help="Do not call the payment providers.")
        parser.add_argument("--first-run", action="store_true", help="Opening balances: do not flag drift.")

    def handle(self, *args, **opts):
        from payments import reconcile

        run = reconcile.run(trigger="first_run" if opts["first_run"] else "manual",
                            verify_with_provider=not opts["offline"])
        self.stdout.write(self.style.SUCCESS(
            f"Run {run.pk}: {run.checked_obligations} bills, {run.checked_attempts} online attempts, "
            f"{run.issues_found} issue(s), {run.auto_fixed} fixed automatically."))
        for issue in run.issues.all()[:50]:
            self.stdout.write(f"  [{issue.severity}] {issue.get_kind_display()}: {issue.message}")
