"""Backfill a Staff record for every User that doesn't have one.

Safe to run repeatedly — it skips users who already have a linked Staff record.

Usage:
    python manage.py backfill_staff
    python manage.py backfill_staff --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from staff.models import Staff


def _unique_employee_no(seed_pk):
    base = f"USR-{seed_pk:05d}"
    candidate = base
    n = 1
    while Staff.objects.filter(employee_no=candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


class Command(BaseCommand):
    help = "Create a Staff record for every User that doesn't have one."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Show what would be created, without writing.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        missing = (
            User.objects.filter(staff_profile__isnull=True)
            .order_by("date_joined", "pk")
        )
        total = missing.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS(
                "Every user already has a Staff record."
            ))
            return

        self.stdout.write(f"Found {total} user(s) without a Staff record.")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing will be saved."))

        created = 0
        for user in missing:
            employee_no = _unique_employee_no(user.pk)
            if dry_run:
                self.stdout.write(
                    f"  would create: {employee_no}  {user.email}  "
                    f"({user.first_name} {user.last_name})"
                )
                continue
            try:
                with transaction.atomic():
                    Staff.objects.create(
                        user=user,
                        employee_no=employee_no,
                        first_name=user.first_name or "—",
                        last_name=user.last_name or "—",
                        email=user.email or "",
                        department=user.department,
                    )
                created += 1
                self.stdout.write(f"  created: {employee_no}  {user.email}")
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  failed: {user.email} — {exc}")
                )

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"Done. Created {created} of {total} Staff record(s)."
            ))