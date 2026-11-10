"""B1 dry run: who would sit in each department's director seat.

    python manage.py plan_single_director                 # print the plan (nothing changes)
    python manage.py plan_single_director --write         # also write the report file
    python manage.py plan_single_director --apply         # seat the chosen directors (empty seats only)

The rule is the one the data migration org/0005 applies (org/director_plan.py).
Non-demo people are shown as "Staff #<id> (<designation>)" in the written
report unless --show-names is given, so the committed report holds no real names.
"""
import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from org import director_plan

DEMO = re.compile(r"@(demo\.nbte\.local|demo-poly\.local|demo-health\.local|p4demo\.local|demo\.nbte\.test)$", re.I)
DEFAULT_REPORT = os.path.join("docs", "master_plan_v2_1", "reports", "single_director_plan.md")


def _redact(rows):
    for r in rows:
        for key in ("current", "chosen"):
            if r.get(key):
                r[key] = _person(r[key])
        r["others"] = [_person(o) for o in r["others"]]
        r["hints"] = [_person(h) for h in r["hints"]]
    return rows


def _person(p):
    if DEMO.search(p.get("email") or ""):
        return p
    q = dict(p)
    q["name"] = f"Staff #{p['user_id']}" + (f" ({p['designation']})" if p.get("designation") else "")
    q["email"] = "hidden"
    return q


class Command(BaseCommand):
    help = "Dry run (default) or apply the one-director-per-department rule; write a report."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Seat the chosen directors (empty seats only).")
        parser.add_argument("--write", action="store_true", help="Write the Markdown report.")
        parser.add_argument("--report", default=DEFAULT_REPORT)
        parser.add_argument("--show-names", action="store_true", help="Keep real names in the written report.")

    def handle(self, *args, **opts):
        from accounts.models import User
        from staff.models import Department, Staff

        if opts["apply"]:
            with transaction.atomic():
                rows = director_plan.apply(Department, User, Staff)
        else:
            rows = director_plan.plan(Department, User, Staff)
        shown = rows if opts["show_names"] else _redact(rows)
        text = director_plan.to_markdown(shown, applied=opts["apply"])
        self.stdout.write(text)
        if opts["write"] or opts["apply"]:
            path = opts["report"]
            if not os.path.isabs(path):
                path = os.path.join(settings.BASE_DIR, path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.stdout.write(self.style.SUCCESS(f"Report written to {path}"))
