"""Organisation data sets (grade levels, role minimum grades, ES rights,
Division Head group, Finance seat group, grant allow-list). Idempotent;
`seed_nbte` calls org.seed.run_all() as well.

    python manage.py seed_org --dry-run
    python manage.py seed_org
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from org import seed


class Command(BaseCommand):
    help = "Seed the organisation data sets (idempotent; never overwrites edits)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--with-demo", action="store_true",
                            help="Also create demo divisions, an office and a grant (demo accounts only).")

    def handle(self, *args, **opts):
        with transaction.atomic():
            out = seed.run_all(dry_run=opts["dry_run"], stdout=self.stdout.write)
            if opts["with_demo"] and not opts["dry_run"]:
                from org.demo import seed_org_demo
                seed_org_demo(stdout=self.stdout.write)
        changes = sum(s["created"] + s["updated"] for s in out.values())
        self.stdout.write(("Would change " if opts["dry_run"] else "Changed ") + f"{changes} item(s).")
