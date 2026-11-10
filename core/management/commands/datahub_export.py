"""Write a Data Hub workbook from the command line.

    python manage.py datahub_export                       # everything -> NBTE_Full_Configuration_Export.xlsx
    python manage.py datahub_export --category org -o org.xlsx
    python manage.py datahub_export --per-category --dir exports/
    python manage.py datahub_export --template            # blank workbook (headers only)
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.datahub import engine, registry


class Command(BaseCommand):
    help = "Export configuration and master data to a Data Hub .xlsx workbook (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--category", action="append", default=[],
                            help=f"Category key (repeatable). One of: {', '.join(registry.category_keys())}.")
        parser.add_argument("-o", "--output", default="NBTE_Full_Configuration_Export.xlsx")
        parser.add_argument("--per-category", action="store_true", help="Also write one workbook per category.")
        parser.add_argument("--dir", default=".", help="Folder for --per-category files.")
        parser.add_argument("--template", action="store_true", help="Headers only, no rows.")

    def handle(self, *args, **opts):
        cats = opts["category"] or [registry.EVERYTHING]
        for c in cats:
            if c != registry.EVERYTHING and not registry.get_category(c):
                raise CommandError(f"Unknown category '{c}'.")
        rows = not opts["template"]
        data = engine.export_workbook(cats, include_rows=rows, source_label="manage.py datahub_export")
        Path(opts["output"]).write_bytes(data)
        self.stdout.write(self.style.SUCCESS(f"Wrote {opts['output']} ({len(data) // 1024} KB)"))
        if opts["per_category"]:
            folder = Path(opts["dir"])
            folder.mkdir(parents=True, exist_ok=True)
            for i, cat in enumerate(registry.CATEGORIES, start=1):
                path = folder / f"{i:02d}_{cat.key}.xlsx"
                path.write_bytes(engine.export_workbook([cat.key], include_rows=rows))
                self.stdout.write(f"  {path}")
