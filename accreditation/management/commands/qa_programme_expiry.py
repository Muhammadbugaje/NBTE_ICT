"""python manage.py qa_programme_expiry [--date YYYY-MM-DD] [--dry-run]

Daily job (also run by Celery beat as ``accreditation.programme_expiry``):
marks programmes past their expiry date as expired and sends the renewal
reminders at ``qa.programme.renewal_reminder_days`` (each once per expiry
date). See accreditation/expiry.py.
"""
import datetime

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Mark expired programmes and send renewal reminders (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Run as if today were this date (YYYY-MM-DD).")
        parser.add_argument("--dry-run", action="store_true", help="Report only; change nothing.")

    def handle(self, *args, **opts):
        from accreditation import expiry

        today = None
        if opts.get("date"):
            try:
                today = datetime.date.fromisoformat(opts["date"])
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD.") from None
        result = expiry.run(today, dry_run=opts["dry_run"])
        verb = "would be" if opts["dry_run"] else "were"
        self.stdout.write(f"Checked {result['checked']} programme(s).")
        for p in result["expired"]:
            self.stdout.write(f"  expired: {p.institution.code} · {p} (expiry {p.expiry_date:%d %b %Y})")
        for p, days in result["reminded"]:
            self.stdout.write(f"  reminder ({days}-day): {p.institution.code} · {p} (expiry {p.expiry_date:%d %b %Y})")
        self.stdout.write(self.style.SUCCESS(f"{len(result['expired'])} expired and {len(result['reminded'])} "
                                             f"reminder(s) {verb} sent."))
