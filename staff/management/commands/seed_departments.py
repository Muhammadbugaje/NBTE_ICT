from django.core.management.base import BaseCommand

from staff.models import Department

# Codes are stable handles used by workflow routing; names can change.
DEPARTMENTS = [
    ("ICT", "ict", 0),
    ("Human Resources", "hr", 10),
]


class Command(BaseCommand):
    help = "Create the base departments (idempotent)."

    def handle(self, *args, **opts):
        for name, code, order in DEPARTMENTS:
            _, created = Department.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})
            self.stdout.write(f"{'created' if created else 'exists '} {code}")
