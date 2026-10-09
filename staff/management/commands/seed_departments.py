from django.core.management.base import BaseCommand

from staff.models import Department

# ICT is the owning department. Add the other NBTE departments in /admin/
# (or extend this list) - codes are stable handles, names can change.
DEPARTMENTS = [("ICT", "ict", 0)]


class Command(BaseCommand):
    help = "Create the base departments (idempotent)."

    def handle(self, *args, **opts):
        for name, code, order in DEPARTMENTS:
            _, created = Department.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})
            self.stdout.write(f"{'created' if created else 'exists '} {code}")
