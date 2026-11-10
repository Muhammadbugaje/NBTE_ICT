"""Load the NBTE ND/HND facilities and equipment Word files.

    python manage.py import_facilities path/to/folder_or.zip [--update]

Existing items are kept (``--update`` refreshes their quantities). Nothing is
ever deleted. For reviewed changes use Facilities & equipment → Import."""
import io
import os
import zipfile

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Load the ND/HND facilities and equipment register from the curricula Word files."

    def add_arguments(self, parser):
        parser.add_argument("source", help="A folder of .docx files, a .zip of them, or one .docx")
        parser.add_argument("--update", action="store_true", help="Refresh quantities of existing items.")

    def handle(self, *args, **opts):
        from facilities import docx_parser, services

        src = opts["source"]
        parsed = []
        if os.path.isdir(src):
            for root, _dirs, files in os.walk(src):
                for f in sorted(files):
                    if f.lower().endswith(".docx") and not f.startswith(("~$", ".")):
                        parsed.append(docx_parser.parse(os.path.join(root, f), f))
        elif src.lower().endswith(".zip"):
            with zipfile.ZipFile(src) as zf:
                for m in zf.namelist():
                    if m.lower().endswith(".docx") and "__macosx" not in m.lower():
                        parsed.append(docx_parser.parse(io.BytesIO(zf.read(m)), m))
        elif src.lower().endswith(".docx"):
            parsed.append(docx_parser.parse(src, src))
        else:
            raise CommandError("Give a folder, a .zip or a .docx file.")
        out = services.load_parsed([p for p in parsed if p["programme"]], update_existing=opts["update"])
        self.stdout.write(self.style.SUCCESS(
            f"{len(parsed)} files read: {out['programmes']} new programmes, {out['items']} new items, "
            f"{out['updated']} updated; programme list: {out.get('masters_added', 0)} added, "
            f"{out.get('masters_linked', 0)} linked."))
