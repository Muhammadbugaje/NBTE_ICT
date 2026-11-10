"""python manage.py access_report [--mode assignment|department] [--write [PATH]]

P.11: who can see which institution and why, with counts per role in both
privacy modes. Only demo accounts are named.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

DEFAULT = "docs/master_plan_v2_1/reports/privacy_access_report.md"


class Command(BaseCommand):
    help = "Write the need-to-know access report (P.11)."

    def add_arguments(self, parser):
        parser.add_argument("--mode", default="assignment", choices=["assignment", "department"])
        parser.add_argument("--write", nargs="?", const=DEFAULT, default=None)

    def handle(self, *args, **o):
        from assignments.report import full_report
        text = full_report(o["mode"])
        if o["write"]:
            path = Path(settings.BASE_DIR) / o["write"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Report written to {o['write']}"))
        else:
            self.stdout.write(text)
