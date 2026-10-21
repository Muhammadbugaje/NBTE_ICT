"""python manage.py audit_designations

Read-only: prints every Designation with its rank, CONTEDISS range, next step
and the number of staff holding it — the table HR uses to tune ranks after
the migration or an import. Safe to run at any time.
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from staff.models import Designation, Staff


class Command(BaseCommand):
    help = "List designations with rank, CONTEDISS range, next step and staff count (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--unranked", action="store_true", help="Only designations with rank 0.")

    def handle(self, *args, unranked=False, **opts):
        qs = (Designation.objects.select_related("next_designation")
              .annotate(n=Count("staff", filter=Q(staff__is_deleted=False)))
              .order_by("-rank", "name"))
        if unranked:
            qs = qs.filter(rank=0)
        rows = list(qs)
        width = max([len(d.name) for d in rows] + [11])
        head = f"{'Rank':>4}  {'Designation':<{width}}  {'CONTEDISS':>9}  {'Staff':>5}  {'Active':<6}  Next"
        self.stdout.write(head)
        self.stdout.write("-" * (len(head) + 20))
        for d in rows:
            grades = f"{d.contediss_min}-{d.contediss_max}" if d.contediss_min or d.contediss_max else "—"
            nxt = d.next_designation.name if d.next_designation else "—"
            self.stdout.write(f"{d.rank:>4}  {d.name:<{width}}  {grades:>9}  {d.n:>5}  "
                              f"{'yes' if d.is_active else 'no':<6}  {nxt}")
        without = Staff.objects.filter(designation__isnull=True).count()
        self.stdout.write("")
        self.stdout.write(f"{len(rows)} designation(s); {sum(d.n for d in rows)} staff assigned; "
                          f"{without} staff without a designation; "
                          f"{sum(1 for d in rows if d.rank == 0)} unranked (rank 0).")
