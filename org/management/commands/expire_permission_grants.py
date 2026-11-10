"""X31: mark director grants whose expiry has passed as revoked (run nightly).
Expired grants already stop counting at once; this tidies and audits them."""
from django.core.management.base import BaseCommand

import org


class Command(BaseCommand):
    help = "Expire time-limited permission grants (X31). Run nightly."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        n = org.expire_permission_grants(dry_run=opts["dry_run"])
        self.stdout.write(f"{'Would expire' if opts['dry_run'] else 'Expired'} {n} grant(s).")
