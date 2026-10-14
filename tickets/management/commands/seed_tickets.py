"""Seed the default ticket categories. Idempotent.

Categories are config-editable after seeding — this command only ensures
the standard set exists. It never overwrites edits.

Each category's `handling_department` must exist. If a required
department is missing, the command warns and skips that category rather
than creating a partial row.
"""
from django.core.management.base import BaseCommand

from staff.models import Department
from tickets.models import TicketCategory


# code, name, handling_department_code, minimum_priority, sort_order, description
CATEGORIES = [
    (
        "hardware", "Hardware",
        "ict", "normal", 10,
        "Physical device faults, replacement requests, upgrades.",
    ),
    (
        "software", "Software",
        "ict", "normal", 20,
        "Application errors, installs, licensing, updates.",
    ),
    (
        "network", "Network & Connectivity",
        "ict", "high", 30,
        "Internet, LAN, VPN, Wi-Fi, printer connectivity.",
    ),
    (
        "access", "Access & Accounts",
        "ict", "high", 40,
        "Account creation, password resets, permission changes.",
    ),
    (
        "facilities", "Facilities",
        "admin", "low", 50,
        "Furniture, lighting, plumbing, workspace issues.",
    ),
    (
        "other", "Other",
        "ict", "low", 99,
        "Anything that doesn't fit the categories above.",
    ),
]


class Command(BaseCommand):
    help = "Seed the default ticket categories (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync", action="store_true",
            help="Refresh the handling department and sort order on existing rows.",
        )

    def handle(self, *args, **opts):
        created_count = 0
        skipped_count = 0

        for code, name, dept_code, min_prio, sort_order, description in CATEGORIES:
            dept = Department.objects.filter(code=dept_code).first()
            if dept is None:
                self.stderr.write(
                    self.style.WARNING(
                        f"  Department code '{dept_code}' not found — "
                        f"skipping category '{code}'."
                    )
                )
                skipped_count += 1
                continue

            obj, created = TicketCategory.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "description": description,
                    "handling_department": dept,
                    "minimum_priority": min_prio,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )

            if created:
                created_count += 1
                self.stdout.write(f"  created {code} → {dept.name}")
            elif opts["sync"]:
                # Only re-apply the handling department and sort order.
                # Never touch admin edits to name, description, or SLA.
                obj.handling_department = dept
                obj.sort_order = sort_order
                obj.save(update_fields=["handling_department", "sort_order", "updated_at"])
                self.stdout.write(f"  synced  {code} → {dept.name}")
            else:
                self.stdout.write(f"  exists  {code}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created_count}, skipped: {skipped_count}."
            )
        )