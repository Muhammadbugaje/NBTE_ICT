"""Fire due reminders right now, optionally pretending it is another moment.

    python manage.py fire_reminders                      # real time
    python manage.py fire_reminders --now "2026-10-17 14:00"   # Lagos time
    python manage.py fire_reminders --tasks              # also the task sweep

Lets you test reminders without waiting for Celery Beat: create a reminder,
run this with a --now after its due time, then look at the notification bell.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Fire due portal reminders (and optionally the task reminder sweep)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--now",
            help='Pretend it is this local (Africa/Lagos) time, "YYYY-MM-DD HH:MM".',
        )
        parser.add_argument("--tasks", action="store_true",
                            help="Also run the task due/overdue reminder sweep.")

    def handle(self, *args, **opts):
        now = None
        if opts["now"]:
            try:
                naive = datetime.strptime(opts["now"], "%Y-%m-%d %H:%M")
            except ValueError:
                raise CommandError('--now must look like "2026-10-17 14:00"')
            now = timezone.make_aware(naive)

        from portal.services import fire_due_reminders
        sent = fire_due_reminders(now=now)
        self.stdout.write(f"Portal reminders: {sent} new delivery(ies).")

        if opts["tasks"]:
            from tasks.services import reminder_sweep
            self.stdout.write(f"Task sweep: {reminder_sweep(now=now)}")
