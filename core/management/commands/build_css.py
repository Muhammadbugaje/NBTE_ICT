"""Rebuild the concatenated CSS bundles (v2.1 V7). See core/css_bundles.py."""
from django.core.management.base import BaseCommand, CommandError

from core import css_bundles


class Command(BaseCommand):
    help = "Concatenate the design-system stylesheets into static/css/nbte-*.bundle.css (cascade layers)."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Fail if a bundle is out of date; write nothing.")

    def handle(self, *args, **opts):
        if opts["check"]:
            stale = css_bundles.stale()
            if stale:
                raise CommandError("Out of date: " + ", ".join(stale) + ". Run python manage.py build_css.")
            self.stdout.write("CSS bundles are up to date.")
            return
        written = css_bundles.write_all()
        for name in css_bundles.BUNDLES:
            self.stdout.write(("wrote   " if css_bundles.bundle_filename(name) in written else "same    ")
                              + css_bundles.bundle_filename(name))
