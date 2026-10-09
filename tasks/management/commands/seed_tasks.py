"""Seed default task categories. Idempotent — never overwrites edits."""
from django.core.management.base import BaseCommand

from staff.models import Department
from tasks.models import TaskCategory


# code, name, dept_code, min_rank, due_days, requires_review,
# is_reusable, icon, sort_order, allowed_role_codes, description
CATEGORIES = [
    (
        "general", "General", None, 0, 7, True, False,
        "check-square", 10, [],
        "Everyday work items that don't fit a specific category.",
    ),
    (
        "maintenance", "Maintenance", "ict", 60, 3, True, True,
        "tool", 20,
        ["director", "deputy-director", "office-head-ts",
         "office-head-admin", "officer"],
        "Recurring cleaning, servicing, upkeep.",
    ),
    (
        "installation", "Installation", "ict", 60, 5, True, True,
        "package", 30,
        ["director", "deputy-director", "office-head-ts",
         "office-head-dev", "officer"],
        "Rollout, deployment, setup work.",
    ),
    (
        "review", "Review & Audit", "ict", 100, 7, True, True,
        "shield", 40,
        ["executive-secretary", "director", "deputy-director"],
        "Formal review or audit of a deliverable.",
    ),
    (
        "reporting", "Reporting", "ict", 60, 10, True, False,
        "file-text", 50,
        ["director", "deputy-director", "office-head-ts",
         "office-head-admin", "officer", "admin-staff"],
        "Data collection and report preparation.",
    ),
    (
        "training", "Training", None, 60, 14, True, True,
        "book", 60,
        ["director", "deputy-director", "office-head-ts",
         "office-head-dev", "office-head-admin"],
        "Structured training or onboarding sessions.",
    ),
    (
        "es-directive", "Executive Directive", None, 999, 7, True, False,
        "star", 70,
        ["executive-secretary", "director", "deputy-director"],
        "Task initiated by or on behalf of the Executive Secretary.",
    ),
]


class Command(BaseCommand):
    help = "Seed default task categories (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--sync", action="store_true")

    def handle(self, *args, **opts):
        created = skipped = 0
        for (code, name, dept_code, min_rank, due_days, review,
             reusable, icon, sort_order, roles, description) in CATEGORIES:

            dept = None
            if dept_code:
                dept = Department.objects.filter(code=dept_code).first()
                if dept is None:
                    self.stderr.write(
                        self.style.WARNING(
                            f"  Department '{dept_code}' missing — "
                            f"creating '{code}' without a department."
                        )
                    )

            obj, was_created = TaskCategory.objects.get_or_create(
                code=code,
                defaults={
                    "name": name, "description": description,
                    "handling_department": dept,
                    "min_rank_to_assign": min_rank,
                    "default_due_days": due_days,
                    "requires_review": review,
                    "is_reusable": reusable,
                    "allowed_role_codes": roles,
                    "icon": icon, "sort_order": sort_order,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(f"  created {code}")
            elif opts["sync"]:
                obj.sort_order = sort_order
                obj.is_reusable = reusable
                obj.allowed_role_codes = roles
                obj.save(update_fields=[
                    "sort_order", "is_reusable",
                    "allowed_role_codes", "updated_at",
                ])
                self.stdout.write(f"  synced  {code}")
            else:
                skipped += 1
                self.stdout.write(f"  exists  {code}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Created: {created}, skipped: {skipped}."
        ))