"""python manage.py sweep_access_grants [--no-report]

Runs the hourly grant sweep by hand (same as the Celery beat job
``assignments.tasks.sweep_grants``): extends grants of work still open, revokes
grants whose work closed, expires grants past ``expires_at`` and reports
grants older than ``qa.access.long_grant_days`` to the Director of ICT.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Expire, extend and report access grants (X5)."

    def add_arguments(self, parser):
        parser.add_argument("--no-report", action="store_true", help="Do not report long-running grants.")

    def handle(self, *args, **o):
        from assignments.grants import sweep
        out = sweep(report=not o["no_report"])
        self.stdout.write("Access grants: " + ", ".join(f"{k} {v}" for k, v in out.items()))
