"""python manage.py run_backup [--if-due]

Takes a backup now and writes it to every active destination (Master Plan §22).
With --if-due it only runs when qa.backup.interval_days have passed (the same
check the daily Celery Beat entry makes), so it can also be run from cron.
Exit status 1 when the run fails, 2 when it is only partly written."""
import sys

from django.core.management.base import BaseCommand, CommandError

from backups import engine
from backups.destinations import human_size
from backups.models import BackupRun


class Command(BaseCommand):
    help = "Take a backup now (database, media, restricted files, settings snapshot)."

    def add_arguments(self, parser):
        parser.add_argument("--if-due", action="store_true",
                            help="Only run when the schedule says a backup is due.")

    def handle(self, *args, **opts):
        if opts["if_due"]:
            due, reason = engine.schedule_due()
            if not due:
                self.stdout.write(f"Not due: {reason}")
                return
        if not engine.active_destinations():
            raise CommandError(engine.NO_DESTINATION_WARNING)
        try:
            run = engine.run_backup(trigger=BackupRun.SCHEDULE if opts["if_due"] else BackupRun.MANUAL)
        except engine.AlreadyRunning as exc:
            raise CommandError(str(exc))
        style = {"succeeded": self.style.SUCCESS, "partial": self.style.WARNING}.get(run.status, self.style.ERROR)
        self.stdout.write(style(f"{run.label}: {run.status} ({human_size(run.size_bytes)})"))
        for copy in run.copies.select_related("destination"):
            self.stdout.write(f"  {copy.destination.name}: {copy.get_status_display()}"
                              + (f" — {copy.error}" if copy.error else f" — {copy.location}"))
        for warning in run.warnings or []:
            self.stdout.write(self.style.WARNING(f"  ! {warning}"))
        if run.status == BackupRun.FAILED:
            self.stderr.write(run.error)
            sys.exit(1)
        if run.status == BackupRun.PARTIAL:
            sys.exit(2)
