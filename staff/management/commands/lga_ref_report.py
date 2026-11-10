"""python manage.py lga_ref_report [--apply]

v2.1 X49: match the free-text LGA of institutions and their addresses to the
LGA table (per state) and list what does not match. --apply fills lga_ref on
the matched rows (the text is kept). Run after `seed_nbte --only geo`."""
from django.core.management.base import BaseCommand

from staff.geo import backfill_lga_refs


class Command(BaseCommand):
    help = "Report (and with --apply fill) lga_ref on institutions from their LGA text."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, apply=False, **opts):
        report = backfill_lga_refs(apply=apply)
        self.stdout.write(f"Matched: {report['matched']}{' (filled)' if apply else ' (dry run)'}; "
                          f"already linked: {report['already']}; unmatched: {len(report['unmatched'])}")
        for model, pk, state, text in report["unmatched"]:
            self.stdout.write(f"  {model} #{pk}: '{text}' (state: {state or 'none'})")
