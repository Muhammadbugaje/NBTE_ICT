"""python manage.py import_nominal_roll <path> [--dry-run] [--sheet NAME] [--mode update|skip|fail]

Imports the NBTE HR nominal-roll workbook. Always writes a report next to the
file (<path>.report.txt). See IMPORT_PLAYBOOK.md.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from staff.nominal_roll import MODES, NominalRollImporter


class Command(BaseCommand):
    help = "Import the NBTE nominal roll (.xlsx). Use --dry-run first."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the nominal roll .xlsx file")
        parser.add_argument("--dry-run", action="store_true",
                            help="Check the file and print what would change; save nothing.")
        parser.add_argument("--sheet", action="append", dest="sheets", metavar="NAME",
                            help="Only this sheet (repeat for several). Default: every data sheet, "
                                 "oldest first, later sheets overriding earlier ones.")
        parser.add_argument("--mode", choices=MODES, default="update",
                            help="update (default): update existing staff; skip: leave existing staff "
                                 "alone; fail: stop if any row matches an existing staff member.")
        parser.add_argument("--report", help="Where to write the report (default: <path>.report.txt)")

    def handle(self, *args, path, dry_run, sheets, mode, report, **opts):
        src = Path(path)
        if not src.is_file():
            raise CommandError(f"No such file: {src}")
        if src.suffix.lower() not in (".xlsx", ".xlsm"):
            raise CommandError("Only .xlsx files can be read. Open the file in Excel and "
                               "'Save As' Excel Workbook (.xlsx) first.")
        importer = NominalRollImporter(str(src), filename=src.name, sheets=sheets, mode=mode)
        if sheets:
            known = {name for name, _l, _n in importer.sheet_choices()}
            unknown = [s for s in sheets if s not in known]
            if unknown:
                raise CommandError(
                    f"Unknown or empty sheet(s): {unknown}. Data sheets: {sorted(known)}")
        result = importer.run(dry_run=dry_run)
        text = result.report_text()
        out = Path(report) if report else src.with_name(src.name + ".report.txt")
        out.write_text(text, encoding="utf-8")
        self.stdout.write(text)
        self.stdout.write("")
        self.stdout.write(f"Report written to {out}")
        if result.aborted:
            raise CommandError(f"Import stopped, nothing saved: {result.aborted}")
        msg = (f"{'Dry run' if dry_run else 'Imported'}: {result.new} new, {result.updated} updated, "
               f"{result.unchanged} unchanged, {result.skipped} skipped, "
               f"{len(result.date_warnings)} date warnings.")
        self.stdout.write(self.style.SUCCESS(msg))
