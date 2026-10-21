"""Run the QA payments sweep once (cron alternative to Celery beat).

    python manage.py qa_payments_sweep

Marks overdue Finance confirmations, sends SLA reminders and polls Remita /
Paystack for attempts still processing. It never confirms a payment."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run the QA payments sweep (reminders, overdue confirmations, provider polls)."

    def handle(self, *args, **options):
        from payments import services

        report = services.sweep()
        self.stdout.write(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in report.items()))
