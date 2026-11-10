"""C7: report how the free-text Staff.grade_level maps onto GradeLevel rows.

    python manage.py plan_grade_levels            # dry run: counts + unmapped values
    python manage.py plan_grade_levels --apply    # set Staff.grade where it is empty and mappable
    python manage.py plan_grade_levels --write    # write docs/master_plan_v2_1/reports/grade_level_mapping.md

Only values (never names) appear in the report.
"""
import os
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand

from org.services import parse_grade_number

DEFAULT_REPORT = os.path.join("docs", "master_plan_v2_1", "reports", "grade_level_mapping.md")


class Command(BaseCommand):
    help = "Map Staff.grade_level text to GradeLevel; report unmapped values."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--write", action="store_true")
        parser.add_argument("--report", default=DEFAULT_REPORT)

    def handle(self, *args, **opts):
        from org.models import GradeLevel
        from staff.models import Staff

        levels = {g.number: g for g in GradeLevel.objects.all()}
        mapped, unmapped, empty, already = Counter(), Counter(), 0, 0
        for s in Staff.objects.all().only("pk", "grade_level", "grade_id"):
            text = (s.grade_level or "").strip()
            if not text:
                empty += 1
                continue
            n = parse_grade_number(text)
            if n in levels:
                mapped[levels[n].display] += 1
                if s.grade_id:
                    already += 1
                elif opts["apply"]:
                    Staff.objects.filter(pk=s.pk).update(grade=levels[n])
            else:
                unmapped[text] += 1
        lines = ["# Grade level mapping (C7)", "",
                 f"Mode: {'APPLIED' if opts['apply'] else 'dry run (nothing changed)'}", "",
                 f"- Staff with a grade text: {sum(mapped.values()) + sum(unmapped.values())}",
                 f"- Mapped: {sum(mapped.values())} (already linked: {already})",
                 f"- Unmapped: {sum(unmapped.values())}", f"- No grade text: {empty}", "",
                 "## Mapped", "", "| Grade level | Staff |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(mapped.items())] or ["| — | 0 |"]
        lines += ["", "## Unmapped values (fix the text or add a grade level)", "", "| Text | Staff |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(unmapped.items())] or ["| — | 0 |"]
        text = "\n".join(lines) + "\n"
        self.stdout.write(text)
        if opts["write"]:
            path = opts["report"] if os.path.isabs(opts["report"]) else os.path.join(settings.BASE_DIR, opts["report"])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.stdout.write(self.style.SUCCESS(f"Report written to {path}"))
