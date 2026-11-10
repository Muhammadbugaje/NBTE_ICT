"""Import a Data Hub workbook from the command line (preview by default).

    python manage.py datahub_import NBTE_Full_Configuration_Export.xlsx            # dry run
    python manage.py datahub_import NBTE_Full_Configuration_Export.xlsx --apply    # save
    python manage.py datahub_import file.xlsx --apply --category users --strict
"""
from django.core.management.base import BaseCommand, CommandError

from core.datahub import engine, registry


class Command(BaseCommand):
    help = "Import a Data Hub workbook. Without --apply nothing is saved (preview)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--apply", action="store_true", help="Save the changes (default: preview only).")
        parser.add_argument("--category", action="append", default=[], help="Only these categories' sheets.")
        parser.add_argument("--strict", action="store_true", help="Roll everything back if any row has an error.")
        parser.add_argument("--show", type=int, default=10, help="Errors/warnings shown per sheet.")

    def handle(self, *args, **opts):
        cats = opts["category"] or [registry.EVERYTHING]
        with open(opts["path"], "rb") as fh:
            report = engine.import_workbook(fh, apply=opts["apply"], categories=cats,
                                            skip_errors=not opts["strict"])
        if report.fatal:
            raise CommandError(report.fatal)
        show = opts["show"]
        for s in report.sheets:
            flag = "!" if s.errors else " "
            self.stdout.write(f"{flag} {s.title:34} rows={s.rows:<5} new={s.created:<5} changed={s.updated:<5} "
                              f"same={s.unchanged:<5} errors={len(s.errors):<4} warnings={len(s.warnings)}")
            if s.changed_fields:
                self.stdout.write("      changed: " + ", ".join(f"{k} ({v})" for k, v in s.changed_fields.items()))
            for row, msg in s.errors[:show]:
                self.stdout.write(self.style.ERROR(f"      row {row}: {msg}"))
            for row, msg in s.warnings[:show]:
                self.stdout.write(self.style.WARNING(f"      row {row}: {msg}"))
        if report.ignored:
            self.stdout.write(f"Ignored sheets: {', '.join(report.ignored)}")
        summary = (f"{report.created} new, {report.updated} changed, {report.unchanged} unchanged, "
                   f"{report.error_count} errors, {report.warning_count} warnings")
        if report.applied:
            self.stdout.write(self.style.SUCCESS(f"APPLIED: {summary}"))
        elif report.rolled_back:
            self.stdout.write(self.style.ERROR(f"ROLLED BACK (errors with --strict): {summary}"))
        else:
            self.stdout.write(f"PREVIEW ONLY (use --apply to save): {summary}")
