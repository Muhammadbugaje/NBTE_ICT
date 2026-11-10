"""Build the facilities workbook from the curricula Word files and/or seed the
register directly.

    # 1. zip (or folder of .docx) → the import workbook, nothing saved
    python manage.py seed_facilities_from_zip "NBTE ND-HND Facilities and Equipment.zip" \\
        --xlsx NBTE_Facilities_and_Equipment_Import.xlsx --no-import

    # 2. seed the register from the zip or from the workbook
    python manage.py seed_facilities_from_zip NBTE_Facilities_and_Equipment_Import.xlsx
    python manage.py seed_facilities_from_zip the.zip --missing=create --dry-run

Programmes not on the national programme master list are created there
(``--missing=create``, default), imported unlinked (``none``) or skipped
(``skip``). Close-but-not-exact matches are linked only with
``--accept-close``. Safe to run again: existing rows are updated, never
duplicated, and nothing is deleted.
"""
import io
import os
import zipfile

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class _DryRun(Exception):
    pass


def parse_docx_source(src):
    from facilities import docx_parser

    parsed = []
    if os.path.isdir(src):
        for root, _dirs, files in os.walk(src):
            if "__MACOSX" in root:
                continue
            for f in sorted(files):
                if f.lower().endswith(".docx") and not f.startswith(("~$", ".")):
                    parsed.append(docx_parser.parse(os.path.join(root, f), f))
    else:
        with zipfile.ZipFile(src) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith(".docx") and "__macosx" not in m.lower()
                       and not m.rsplit("/", 1)[-1].startswith(("~$", "."))]
            if len(members) > 400:
                raise CommandError("The zip holds more than 400 Word files.")
            for m in sorted(members):
                parsed.append(docx_parser.parse(io.BytesIO(zf.read(m)), m.rsplit("/", 1)[-1]))
    return parsed


class Command(BaseCommand):
    help = "Seed the ND/HND facilities register from the curricula .zip / folder of .docx, or from the import .xlsx."

    def add_arguments(self, parser):
        parser.add_argument("source", help="A .zip of .docx files, a folder of them, or the import .xlsx")
        parser.add_argument("--xlsx", help="Also write the import workbook to this path (zip/folder sources).")
        parser.add_argument("--no-import", action="store_true", help="Only write the workbook; save nothing.")
        parser.add_argument("--missing", choices=("create", "none", "skip"), default="create",
                            help="Programmes not on the programme master list: create them (default), "
                                 "import them unlinked, or skip them.")
        parser.add_argument("--accept-close", action="store_true",
                            help="Link programmes to a close (fuzzy) master-list match instead of applying --missing.")
        parser.add_argument("--deactivate-missing", action="store_true",
                            help="Mark items of imported programmes that are not in the source as inactive.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would happen and roll back.")

    def handle(self, *args, **opts):
        from facilities import smart_import

        src = opts["source"]
        if not os.path.exists(src):
            raise CommandError(f"{src} does not exist.")
        if src.lower().endswith(".xlsx"):
            with open(src, "rb") as fh:
                try:
                    data = smart_import.read_workbook(fh)
                except ValidationError as exc:
                    raise CommandError(" ".join(exc.messages))
        elif os.path.isdir(src) or src.lower().endswith(".zip"):
            parsed = parse_docx_source(src)
            data = smart_import.from_parsed(parsed)
            empty = [p["file"] or p["programme"] for p in parsed if not any(s["items"] for s in p["sections"])]
            self.stdout.write(f"{len(parsed)} Word files read: {len(data.programmes)} programmes, "
                              f"{data.facility_count:,} facilities, {data.item_count:,} items.")
            if empty:
                self.stdout.write(self.style.WARNING(f"{len(empty)} file(s) list no equipment: "
                                                     + ", ".join(sorted(empty))))
            if opts["xlsx"]:
                with open(opts["xlsx"], "wb") as fh:
                    fh.write(smart_import.build_workbook(data))
                self.stdout.write(self.style.SUCCESS(f"Workbook written to {opts['xlsx']}"))
        else:
            raise CommandError("Give a .zip, a folder of .docx files or an .xlsx file.")
        if data.merged:
            self.stdout.write(f"{len(data.merged)} duplicate line(s) folded or renamed (see the Instructions sheet).")
        for r, why in data.skipped[:20]:
            self.stdout.write(self.style.WARNING(f"Row {r} skipped: {why}"))
        if opts["no_import"]:
            return

        preview = smart_import.build_preview(data)
        decisions = {}
        for row in preview["programmes"]:
            m = row["match"]
            if m["status"] in ("linked", "exact"):
                decisions[row["key"]] = "link"
            elif m["status"] == "candidate" and opts["accept_close"]:
                decisions[row["key"]] = f"map:{m['candidate_id']}"
            else:
                decisions[row["key"]] = opts["missing"]
        try:
            with transaction.atomic():
                out = smart_import.apply(data, decisions, deactivate_missing=opts["deactivate_missing"])
                if opts["dry_run"]:
                    raise _DryRun
        except _DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing saved."))
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))
        self.stdout.write(self.style.SUCCESS(
            f"Programmes: {out['programmes_created']} created, {out['programmes_updated']} updated, "
            f"{out['programmes_skipped']} skipped · programme master list: {out['masters_created']} created, "
            f"{out['masters_linked']} linked · facilities: {out['facilities_created']} created · items: "
            f"{out['items_created']} created, {out['items_updated']} updated, {out['items_unchanged']} unchanged"
            + (f", {out['items_deactivated']} deactivated" if out["items_deactivated"] else "") + "."))
