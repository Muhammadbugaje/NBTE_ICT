"""python manage.py seed_staff_reference [--with-lgas] [--quiet]

(Renamed from `seed_nbte` in v2.1 X51: `seed_nbte` is now the one-command
seed in core/management/commands/seed_nbte.py, whose `org` and `geo` sets
reuse this code.)

Seeds the NBTE reference data the nominal roll and the promotion module need:
departments, designations (with ranks, CONTEDISS ranges and the promotion
ladder), the 36 states + FCT with their zones, and the Confirmed / Probation
staff states.

Idempotent: records are matched by name, code or known alias and only created
when missing. On an existing record it fills fields that are still empty
(a rank of 0, a blank CONTEDISS range, no next designation) and never
changes a value someone has already set. A second run reports no changes.
"""
import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from staff import nbte_reference as ref
from staff.models import Department, Designation, Staff, StaffState, StateOfOrigin

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STAFF_STATES = [("Confirmed", 0), ("Probation", 10)]


def load_designations():
    with open(DATA_DIR / "designations.json", encoding="utf-8") as fh:
        return json.load(fh)["designations"]


def load_lgas():
    with open(DATA_DIR / "lgas.json", encoding="utf-8") as fh:
        return json.load(fh)["states"]


def _is_acting(name):
    words = set(ref.designation_key(name).split())
    return "ACTING" in words


def compute_ladder(designations):
    """{designation: next designation or None} for every ranked designation.

    Within a family (the title with seniority words and grade numerals
    stripped) the next step is the lowest strictly-higher rank; at the top of
    a family it is the lowest strictly-higher rank overall. Ties prefer a
    substantive (not 'Acting') post, then the one more staff hold, then the
    shorter name.
    """
    ranked = [d for d in designations if d.rank > 0]
    counts = {d.pk: getattr(d, "n_staff", 0) for d in ranked}

    def best(cands):
        low = min(c.rank for c in cands)
        tier = [c for c in cands if c.rank == low]
        return sorted(tier, key=lambda c: (_is_acting(c.name), -counts.get(c.pk, 0), len(c.name), c.name))[0]

    families = defaultdict(list)
    for d in ranked:
        families[ref.designation_family(d.name)].append(d)
    ladder = {}
    for d in ranked:
        same = [c for c in families[ref.designation_family(d.name)] if c.rank > d.rank]
        if same:
            ladder[d] = best(same)
            continue
        above = [c for c in ranked if c.rank > d.rank and not _is_acting(c.name)] or \
                [c for c in ranked if c.rank > d.rank]
        ladder[d] = best(above) if above else None
    return ladder


class Command(BaseCommand):
    help = "Seed NBTE departments, designations, states of origin and staff states (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--with-lgas", action="store_true",
                            help="Also check the bundled list of 774 LGAs (staff/data/lgas.json) "
                                 "and report staff LGA spellings that are not on it.")
        parser.add_argument("--quiet", action="store_true", help="Only print the summary.")

    @transaction.atomic
    def handle(self, *args, with_lgas=False, quiet=False, **opts):
        self.quiet = quiet
        summary = []
        summary.append(("Departments",) + self.seed_departments())
        summary.append(("States of origin",) + self.seed_states())
        summary.append(("Staff states",) + self.seed_staff_states())
        summary.append(("Designations",) + self.seed_designations())
        summary.append(("Promotion ladder",) + self.seed_ladder())

        self.stdout.write("")
        self.stdout.write(f"{'Data':<20}{'Created':>9}{'Filled in':>11}{'Already set':>13}")
        self.stdout.write("-" * 53)
        for label, created, filled, existing in summary:
            self.stdout.write(f"{label:<20}{created:>9}{filled:>11}{existing:>13}")
        if with_lgas:
            self.check_lgas()
        if not quiet:
            self.print_ladder()
        changed = sum(c + f for _l, c, f, _e in summary)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "Seed complete — no changes (already up to date)." if not changed
            else f"Seed complete — {changed} change(s)."))
        self.stdout.write("Next: python manage.py import_nominal_roll <file.xlsx> --dry-run "
                          "(or People → Nominal roll), then audit_designations to review ranks.")

    # -- departments -----------------------------------------------------------
    def seed_departments(self):
        created = existing = 0
        for order, (name, code, title, aliases) in enumerate(ref.DEPARTMENTS):
            obj = (Department.objects.filter(name__iexact=name).first()
                   or Department.objects.filter(code=code).first())
            if obj is None:
                for alt in [title, *aliases]:
                    obj = Department.objects.filter(name__iexact=alt).first()
                    if obj:
                        break
            if obj is None:
                Department.objects.create(name=name, code=code, sort_order=(order + 1) * 10)
                created += 1
                self._say(f"  + department {name} ({code})")
            else:
                existing += 1
        return created, 0, existing

    # -- states -------------------------------------------------------------------
    def seed_states(self):
        created = filled = existing = 0
        for name, code, zone in ref.STATES:
            obj = (StateOfOrigin.objects.filter(name__iexact=name).first()
                   or StateOfOrigin.objects.filter(code=code).first())
            if obj is None:
                StateOfOrigin.objects.create(name=name, code=code, zone=zone)
                created += 1
                continue
            changed = []
            if not obj.zone:
                obj.zone = zone
                changed.append("zone")
            if not obj.code and not StateOfOrigin.objects.filter(code=code).exists():
                obj.code = code
                changed.append("code")
            if changed:
                obj.save(update_fields=changed + ["updated_at"])
                filled += 1
            else:
                existing += 1
        return created, filled, existing

    def seed_staff_states(self):
        created = existing = 0
        for name, order in STAFF_STATES:
            if StaffState.objects.filter(name__iexact=name).exists():
                existing += 1
            else:
                StaffState.objects.create(name=name, sort_order=order)
                created += 1
        return created, 0, existing

    # -- designations -------------------------------------------------------------
    def seed_designations(self, rows=None):
        index = {}
        for d in Designation.objects.all():
            for spelling in [d.name, *d.alias_list]:
                index.setdefault(ref.designation_key(spelling), d)
        created = filled = existing = 0
        rows = load_designations() if rows is None else rows
        for pos, row in enumerate(sorted(rows, key=lambda r: (-r["rank"], r["name"]))):
            spellings = [row["name"], *row["aliases"]]
            obj = next((index[ref.designation_key(s)] for s in spellings
                        if ref.designation_key(s) in index), None)
            if obj is None:
                obj = Designation.objects.filter(name__iexact=row["name"]).first()
            if obj is None:
                obj = Designation.objects.create(
                    name=row["name"], rank=row["rank"], aliases="\n".join(row["aliases"]),
                    contediss_min=row["contediss_min"], contediss_max=row["contediss_max"],
                    sort_order=pos + 1)
                created += 1
                for s in spellings:
                    index.setdefault(ref.designation_key(s), obj)
                continue
            changed = []
            if not obj.rank and row["rank"]:
                obj.rank = row["rank"]
                changed.append("rank")
            if not obj.contediss_min and row["contediss_min"]:
                obj.contediss_min = row["contediss_min"]
                changed.append("contediss_min")
            if not obj.contediss_max and row["contediss_max"]:
                obj.contediss_max = row["contediss_max"]
                changed.append("contediss_max")
            known = {ref.clean_text(s).upper() for s in [obj.name, *obj.alias_list]}
            extra = [s for s in spellings if ref.clean_text(s).upper() not in known]
            if extra:
                obj.aliases = "\n".join([*obj.alias_list, *extra])
                changed.append("aliases")
            if not obj.sort_order:
                obj.sort_order = pos + 1
                changed.append("sort_order")
            if changed:
                obj.save(update_fields=changed + ["updated_at"])
                filled += 1
            else:
                existing += 1
        # Import-created designations that the bundle doesn't know: rank by title.
        for obj in Designation.objects.filter(rank=0):
            rank = ref.designation_rank(obj.name)
            if rank:
                obj.rank = rank
                obj.save(update_fields=["rank", "updated_at"])
                filled += 1
        return created, filled, existing

    def seed_ladder(self):
        from django.db.models import Count
        designations = list(Designation.objects.annotate(n_staff=Count("staff")))
        ladder = compute_ladder(designations)
        filled = existing = 0
        for d, nxt in ladder.items():
            if d.next_designation_id:
                existing += 1
            elif nxt is not None:
                d.next_designation = nxt
                d.save(update_fields=["next_designation", "updated_at"])
                filled += 1
        return 0, filled, existing

    # -- output ---------------------------------------------------------------
    def print_ladder(self):
        self.stdout.write("")
        self.stdout.write("Designation ladder (rank · CONTEDISS · designation → next):")
        for d in Designation.objects.select_related("next_designation").order_by("-rank", "name"):
            grades = f"{d.contediss_min}-{d.contediss_max}" if d.contediss_min else "—"
            nxt = d.next_designation.name if d.next_designation else "—"
            self.stdout.write(f"  {d.rank:>3}  {grades:>6}  {d.name} → {nxt}")

    def check_lgas(self):
        states = load_lgas()
        total = sum(len(v) for v in states.values())
        self.stdout.write("")
        self.stdout.write(f"LGAs: {total} in {len(states)} states/FCT (staff/data/lgas.json).")
        known = {ref.compact_key(l) for v in states.values() for l in v}
        used = Staff.objects.exclude(lga="").values_list("lga", flat=True).distinct()
        unknown = sorted({l for l in used if ref.compact_key(l) not in known})
        self.stdout.write(f"Staff LGA spellings not on the list: {len(unknown)}"
                          + (f" (e.g. {', '.join(unknown[:12])})" if unknown else ""))
        self.stdout.write("LGA stays free text on the staff record; the list powers the "
                          "autocomplete on the staff form.")

    def _say(self, msg):
        if not self.quiet:
            self.stdout.write(msg)
