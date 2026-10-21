"""python manage.py seed_memo_routes [--dry-run] [--show] [--remove]

Installs the DIRECTORATE MEMO ROUTING policy (18 Oct 2026) as ordinary,
editable routes and rules (Messages → Manage → Routes / Rules):

  A director writing …
    • to anyone in their own department (one person, a list, the whole
      department)                                   → delivered directly
    • to another director                           → delivered directly
    • to staff in another directorate (person, list,
      or that department as a circular)             → that directorate's director
                                                      approves, then delivered
  Anyone else writing …
    • to a colleague in their own department        → line head (if there is one
                                                      and they are senior), then
                                                      delivered
    • to their own director                         → line head → deputy director
                                                      → delivered to the director
    • to another directorate's director             → line head → deputy → own
                                                      director → delivered
    • to staff in another directorate               → line head → deputy → own
                                                      director → that directorate's
                                                      director → delivered
  The Executive Secretary's memos are delivered directly.

Skip rules (the engine, messaging/routing.py — not this command):
  a step is skipped when nobody holds it (line head, deputy), when the sender
  is already at that level or above (a line head skips the line-head step, a
  deputy the deputy step, a director every "own department" step), when the
  approver would be the sender, or when the approver is the recipient. The
  directors' steps are required: if a department has no director the memo
  waits, the admins are alerted, and it moves on by itself as soon as a
  director is set (department head, Director role or DIRECTOR designation).

Idempotent: routes are found by code, rules by name; a second run changes
nothing. Rules you edit afterwards (priority, active …) are left alone.
Existing rules from seed_memo_demo or the admin keep working: these rules sit
at priorities that make the directorate policy win, while the ES gates
("Staff memo to ES", priority 100) are kept above the staff rules.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from messaging.models import MemoRoute, MemoRouteRule, MemoRouteStep

MARK = "Added by seed_memo_routes (directorate routing)."
ES, DIR = "executive-secretary", "director"

# code, name, description, same_rank, steps [(name, kind, optional, hierarchical, instructions)]
ROUTES = [
    ("dr-direct", "Directorate: delivered directly",
     "No approval: the memo goes straight to its recipients.", "skip", []),
    ("dr-within-department", "Directorate: within the department",
     "A colleague in the same department: the sender's line head sees it first, if there is one.", "skip",
     [("Line Head", "division_head", True, True,
       "Check the memo before it goes to your colleague.")]),
    ("dr-up-and-across", "Directorate: up the line, then across",
     "Line head → deputy director → the sender's director → the recipient's director. "
     "Each step is skipped when nobody holds it, when the sender is at that level or above, "
     "or when the approver is the recipient.", "skip",
     [("Line Head", "division_head", True, True, "First check by the sender's line head."),
      ("Deputy Director", "deputy", True, True, "The sender's deputy director."),
      ("Director", "director", False, True, "The sender's director approves before it leaves the department."),
      ("Receiving Director", "receiving_director", False, True,
       "The director of the recipient's department approves before it reaches their staff.")]),
    ("dr-to-other-directorate", "Directorate: through the receiving director",
     "A director writing to staff in another directorate: that directorate's director approves first.",
     "skip",
     [("Receiving Director", "receiving_director", False, True,
       "The director of the recipient's department approves before it reaches their staff.")]),
]

# name, route, target kind, sender roles, target roles, department relation, priority
RULES = [
    # The ES writes to anyone, directly.
    ("Directorate: ES to one person", "dr-direct", "specific_user", [ES], [], "any", 310),
    ("Directorate: ES to a list", "dr-direct", "individuals", [ES], [], "any", 310),
    ("Directorate: ES to a department", "dr-direct", "department", [ES], [], "any", 310),
    # Directors.
    ("Directorate: director to own department (one person)", "dr-direct", "specific_user", [DIR], [], "same", 260),
    ("Directorate: director to own department (a list)", "dr-direct", "individuals", [DIR], [], "same", 260),
    ("Directorate: director to own department (circular)", "dr-direct", "department", [DIR], [], "same", 260),
    ("Directorate: director to another director", "dr-direct", "specific_user", [DIR], [DIR], "other", 255),
    ("Directorate: director to staff in another directorate", "dr-to-other-directorate",
     "specific_user", [DIR], [], "other", 250),
    ("Directorate: director to a list in another directorate", "dr-to-other-directorate",
     "individuals", [DIR], [], "other", 250),
    ("Directorate: director to another directorate (circular)", "dr-to-other-directorate",
     "department", [DIR], [], "other", 250),
    # Everyone else (below the ES gate rules at 100 and the Director/Deputy → ES rule at 200).
    ("Directorate: staff to a director", "dr-up-and-across", "specific_user", [], [DIR], "any", 95),
    ("Directorate: staff to another directorate (one person)", "dr-up-and-across",
     "specific_user", [], [], "other", 94),
    ("Directorate: staff to a list in another directorate", "dr-up-and-across",
     "individuals", [], [], "other", 94),
    ("Directorate: staff to another directorate (circular)", "dr-up-and-across",
     "department", [], [], "other", 94),
    ("Directorate: staff to a colleague in own department", "dr-within-department",
     "specific_user", [], [], "same", 85),
    ("Directorate: staff to a list in own department", "dr-within-department",
     "individuals", [], [], "same", 85),
]


class Command(BaseCommand):
    help = "Install the directorate memo-routing policy (routes + rules). Idempotent."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, then roll back.")
        parser.add_argument("--show", action="store_true", help="Only print the active rules, highest first.")
        parser.add_argument("--remove", action="store_true",
                            help="Delete the routes and rules this command added (memos keep their history).")

    def handle(self, *args, dry_run=False, show=False, remove=False, **opts):
        if show:
            return self.show()
        changes = 0
        with transaction.atomic():
            changes = self.remove() if remove else self.install()
            if dry_run:
                transaction.set_rollback(True)
        verb = "would change" if dry_run else "changed"
        self.stdout.write(self.style.SUCCESS(f"Directorate memo routing: {changes} row(s) {verb}."))
        if not remove:
            self.show()

    # ------------------------------------------------------------------
    def install(self):
        changes = 0
        routes = {}
        for code, name, desc, same, steps in ROUTES:
            route = MemoRoute.objects.filter(code=code).first()
            if route is None:
                route = MemoRoute.objects.create(code=code, name=name, description=f"{desc} {MARK}",
                                                 same_rank_behaviour=same, is_active=True)
                for pos, (sname, kind, optional, hier, instr) in enumerate(steps, start=1):
                    MemoRouteStep.objects.create(route=route, position=pos, name=sname, approver_kind=kind,
                                                 is_optional=optional, is_hierarchical=hier,
                                                 instructions=instr)
                changes += 1 + len(steps)
                self.stdout.write(f"  + route {name} ({len(steps)} step{'s' if len(steps) != 1 else ''})")
            routes[code] = route
        for name, code, kind, senders, targets, relation, priority in RULES:
            if MemoRouteRule.objects.filter(name=name).exists():
                continue
            MemoRouteRule.objects.create(name=name, route=routes[code], target_kind=kind,
                                         sender_role_codes=senders, target_role_codes=targets,
                                         department_relation=relation, priority=priority, is_active=True)
            changes += 1
            self.stdout.write(f"  + rule  {name} (priority {priority})")
        if not changes:
            self.stdout.write("  Already installed — nothing to change.")
        return changes

    def remove(self):
        names = [r[0] for r in RULES]
        n_rules, _ = MemoRouteRule.objects.filter(name__in=names).delete()
        n_routes = 0
        for code, *_ in ROUTES:
            route = MemoRoute.objects.filter(code=code).first()
            if route is None:
                continue
            if route.rules.exists():
                self.stdout.write(f"  ! kept route {route.name}: other rules still use it")
                continue
            route.delete()  # memos on it keep their decisions (steps are SET_NULL there)
            n_routes += 1
        self.stdout.write(f"  - removed {n_rules} rule(s) and {n_routes} route(s)")
        return n_rules + n_routes

    def show(self):
        self.stdout.write("Active memo rules (highest priority wins):")
        for rule in MemoRouteRule.objects.filter(is_active=True).select_related("route").order_by("-priority", "name"):
            senders = ", ".join(rule.sender_role_codes or []) or "anyone"
            targets = ", ".join(rule.target_role_codes or []) or "anyone"
            rel = getattr(rule, "department_relation", "any")
            self.stdout.write(f"  {rule.priority:>4}  {rule.name:<58} {rule.target_kind:<13} "
                              f"from {senders:<22} to {targets:<20} dept={rel:<5} → {rule.route.name}")
