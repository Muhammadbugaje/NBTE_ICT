"""v2.3: put every ND/HND curriculum from the register on the programme master list.

    python manage.py import_programme_masters

Run after ``import_facilities``. Safe to run again (adds only what is missing).
"""
from django.core.management.base import BaseCommand

from facilities import services


class Command(BaseCommand):
    help = "Add the curriculum register's ND/HND programmes to the national programme master list."

    def handle(self, *args, **opts):
        created, linked = services.import_master_list()
        self.stdout.write(self.style.SUCCESS(f"Programme master list: {created} added, {linked} register "
                                             f"programme(s) linked."))
