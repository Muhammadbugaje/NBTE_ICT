"""complaints_sla_sweep — reminders, breaches, escalations and auto-close.

Celery beat runs ``complaints.tasks.sla_sweep`` every
``qa.complaints.sla_poll_minutes``; this command does the same by hand (cron
fallback). ``--at "2026-10-21 10:00"`` evaluates as if it were that moment
(testing and demonstrations only).
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Run the complaint SLA sweep once."

    def add_arguments(self, parser):
        parser.add_argument("--at", default="", help='Pretend it is this local time ("YYYY-MM-DD HH:MM").')

    def handle(self, *args, **opts):
        from complaints.services import sla_sweep

        now = None
        if opts["at"]:
            try:
                now = timezone.make_aware(datetime.datetime.strptime(opts["at"], "%Y-%m-%d %H:%M"))
            except ValueError:
                raise CommandError('--at must look like "2026-10-21 10:00".') from None
        report = sla_sweep(now)
        self.stdout.write(self.style.SUCCESS(
            "Complaint SLA sweep: {reminded} reminded, {breached} breached, {escalated} escalated, "
            "{auto_closed} auto-closed.".format(**report)))
