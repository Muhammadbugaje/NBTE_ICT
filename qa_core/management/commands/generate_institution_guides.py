"""Build the institution guides as Word documents (Phase 6).

    python manage.py generate_institution_guides [--only starter] [--output-dir DIR] [--images-dir DIR]

Writes one .docx per guide in qa_core.guides_content (starter, onboarding,
QA requests, payments) to MEDIA_ROOT/qa/guides/, where the portal offers them
for download. Screenshots, diagrams and the cover artwork are read from
docs/guides/images (refresh them with scripts/capture_guide_screenshots.py); a
missing image becomes a short note so the build never fails.

Layout: a full-bleed NBTE-green cover, an "About this guide" page with the
contents, numbered parts, step cards, framed screenshots with numbered
call-outs that match the gold badges on the screenshots, tip / important /
note boxes, and a running header and "Page X of Y" footer.
"""
from __future__ import annotations

import datetime
import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_ROW_HEIGHT_RULE, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Emu, Pt, RGBColor

from qa_core import guides_content
from qa_core.portal_public import GUIDES_SUBDIR

INK = RGBColor(0x1D, 0x1D, 0x1F)
BODY = RGBColor(0x3A, 0x3A, 0x3C)
MUTED = RGBColor(0x6E, 0x6E, 0x73)
GREEN = RGBColor(0x1F, 0x6B, 0x3E)
FOREST = "04261A"
MINT = RGBColor(0x8E, 0xE8, 0xB4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GOLD = "F2A900"
FONT = "Calibri"
PAGE_W, PAGE_H = 21.0, 29.7
MARGIN = 2.2
CONTENT_WIDTH_CM = PAGE_W - 2 * MARGIN  # 16.6 cm
BOXES = {  # kind: (fill, accent, label colour)
    "tip": ("EAF5EE", "1F9D5C", RGBColor(0x14, 0x6B, 0x3D)),
    "important": ("FFF5DB", "D99A00", RGBColor(0x8A, 0x5A, 0x00)),
    "note": ("EAF2FD", "2F7FE0", RGBColor(0x1F, 0x5F, 0xB0)),
}


class Command(BaseCommand):
    help = "Build the institution guides (.docx) offered on the portal's how-to page."

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=guides_content.ORDER, help="Build one guide only.")
        parser.add_argument("--output-dir", help="Folder for the .docx files (default MEDIA_ROOT/qa/guides).")
        parser.add_argument("--images-dir", help="Screenshot folder (default docs/guides/images).")

    def handle(self, *args, **opts):
        out_dir = opts.get("output_dir") or os.path.join(settings.MEDIA_ROOT, GUIDES_SUBDIR)
        images = opts.get("images_dir") or os.path.join(settings.BASE_DIR, "docs", "guides", "images")
        os.makedirs(out_dir, exist_ok=True)
        slugs = [opts["only"]] if opts.get("only") else guides_content.ORDER
        missing = set()
        for slug in slugs:
            spec = guides_content.GUIDES.get(slug)
            if spec is None:
                raise CommandError(f"Unknown guide {slug}")
            builder = GuideBuilder(spec, images, contact(), number=guides_content.ORDER.index(slug) + 1)
            builder.build()
            missing |= builder.missing
            path = os.path.join(out_dir, f"nbte-qa-{slug}-guide.docx")
            builder.doc.save(path)
            self.stdout.write(f"wrote {path} ({os.path.getsize(path) // 1024} KB)")
        if missing:
            self.stdout.write(self.style.WARNING(
                f"{len(missing)} image(s) not found in {images}: {', '.join(sorted(missing))}. "
                "Run scripts/capture_guide_screenshots.py to create them."))


def contact():
    import config

    email = (config.get("qa.portal.registration_contact_email", "") or "").strip() or config.get("site.email", "")
    phone = (config.get("qa.portal.registration_contact_phone", "") or "").strip() or config.get("site.phone", "")
    return (email or "").strip(), (phone or "").strip()


def _hex(rgb):
    return str(rgb)


class GuideBuilder:
    def __init__(self, spec, images_dir, contact_details, number=1):
        self.spec = spec
        self.images_dir = images_dir
        self.email, self.phone = contact_details
        self.number = number
        self.missing = set()
        self.figure = 0
        self.part = 0
        self.accent = spec.get("accent", "1F9D5C")
        self.doc = Document()

    # ================================================================== setup
    def _styles(self):
        styles = self.doc.styles
        normal = styles["Normal"]
        normal.font.name = FONT
        normal.font.size = Pt(10.5)
        normal.font.color.rgb = BODY
        normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.22
        for name, size, before, after, colour in (("Heading 1", 22, 6, 6, INK), ("Heading 2", 14, 16, 5, INK),
                                                   ("Heading 3", 11.5, 12, 3, GREEN), ("Title", 34, 0, 6, INK)):
            style = styles[name]
            style.font.name = FONT
            style.font.size = Pt(size)
            style.font.bold = True
            style.font.italic = False
            style.font.color.rgb = colour
            style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
            style.element.rPr.rFonts.set(qn("w:ascii"), FONT)
            style.element.rPr.rFonts.set(qn("w:hAnsi"), FONT)
            style.paragraph_format.space_before = Pt(before)
            style.paragraph_format.space_after = Pt(after)
            style.paragraph_format.keep_with_next = True
            ppr = style.element.get_or_add_pPr()
            for border in ppr.findall(qn("w:pBdr")):
                ppr.remove(border)
        for name in ("List Bullet", "List Number"):
            styles[name].font.name = FONT
            styles[name].paragraph_format.space_after = Pt(3)
        caption = styles["Caption"]
        caption.font.name = FONT
        caption.font.size = Pt(8.5)
        caption.font.italic = False
        caption.font.bold = False
        caption.font.color.rgb = MUTED
        caption.paragraph_format.space_after = Pt(12)

    def _section_margins(self, section, margin):
        section.page_width, section.page_height = Cm(PAGE_W), Cm(PAGE_H)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(section, side, Cm(margin))
        section.header_distance = Cm(0.9 if margin else 0)
        section.footer_distance = Cm(0.9 if margin else 0)

    # ================================================================== build
    def build(self):
        self._styles()
        cover = self.doc.sections[0]
        self._section_margins(cover, 0)
        self._cover()
        body = self.doc.add_section(WD_SECTION.NEW_PAGE)
        # The paragraph that ends the cover section must not spill onto a page of its own.
        for paragraph in self.doc.element.body.iterchildren(qn("w:p")):
            if paragraph.find(qn("w:pPr")) is not None and paragraph.find(qn("w:pPr")).find(qn("w:sectPr")) is not None:
                ppr = paragraph.find(qn("w:pPr"))
                spacing = OxmlElement("w:spacing")
                spacing.set(qn("w:before"), "0")
                spacing.set(qn("w:after"), "0")
                spacing.set(qn("w:line"), "20")
                spacing.set(qn("w:lineRule"), "exact")
                ppr.append(spacing)
                break
        self._section_margins(body, MARGIN)
        body.top_margin = Cm(2.4)
        self._header_footer(body)
        self._about()
        for block in self.spec["blocks"]:
            getattr(self, f"_b_{block[0]}")(*block[1:])
        return self.doc

    # ================================================================== cover
    def _cover(self):
        """A full-page NBTE-green table cell: logo on a white disc, the title in
        white, the map of NBTE and its institutions, and the guide's number."""
        doc = self.doc
        table = doc.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        self._no_borders(table)
        self._table_width(table, PAGE_W)
        row = table.rows[0]
        row.height = Cm(PAGE_H - 0.35)
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        cell = row.cells[0]
        cell.width = Cm(PAGE_W)
        self._shade(cell, FOREST)
        self._cell_margins(cell, top=1.9, bottom=1.0, left=2.2, right=2.2)
        p = cell.paragraphs[0]
        logo = self._path("logo_disc") or os.path.join(settings.BASE_DIR, "static", "img", "NBTE_logo.png")
        if logo and os.path.exists(logo):
            p.add_run().add_picture(logo, width=Cm(3.0))
        p.paragraph_format.space_after = Pt(26)
        self._cover_text(cell, "National Board for Technical Education", 11, MINT, bold=True, after=2)
        self._cover_text(cell, "Quality Assurance Portal", 11, RGBColor(0xC9, 0xE9, 0xD6), after=34)
        tag = cell.add_paragraph()
        run = tag.add_run(f"  Guide {self.number} of {len(guides_content.ORDER)}  ")
        run.font.size, run.font.bold, run.font.color.rgb = Pt(9.5), True, RGBColor(0x2B, 0x1D, 0x00)
        self._run_shade(run, "F5C04A")
        tag.paragraph_format.space_after = Pt(14)
        self._cover_text(cell, self.spec["title"], 40, WHITE, bold=True, after=8, line=1.0)
        self._cover_text(cell, self.spec["subtitle"], 16, RGBColor(0xDD, 0xF3, 0xE6), after=6, line=1.15)
        if self.spec.get("audience"):
            self._cover_text(cell, self.spec["audience"], 11, RGBColor(0xA9, 0xD8, 0xBD), after=0)
        art = self._path("cover_map")
        pic = cell.add_paragraph()
        pic.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        pic.paragraph_format.space_before = Pt(26)
        if art:
            pic.add_run().add_picture(art, width=Cm(12.2))
        foot = cell.add_paragraph()
        foot.paragraph_format.space_before = Pt(20)
        run = foot.add_run(f"Version {datetime.date.today():%B %Y}  ·  Screens shown use demonstration data.")
        run.font.size, run.font.color.rgb = Pt(9), RGBColor(0xA9, 0xD8, 0xBD)

    def _cover_text(self, cell, text, size, colour, *, bold=False, after=6, line=None):
        p = cell.add_paragraph()
        run = p.add_run(text)
        run.font.size, run.font.bold, run.font.color.rgb = Pt(size), bold, colour
        p.paragraph_format.space_after = Pt(after)
        if line:
            p.paragraph_format.line_spacing = line
        return p

    # ================================================================== header / footer
    def _header_footer(self, section):
        section.header.is_linked_to_previous = False
        section.footer.is_linked_to_previous = False
        header = section.header
        table = header.add_table(rows=1, cols=2, width=Cm(CONTENT_WIDTH_CM))
        self._no_borders(table)
        self._table_width(table, CONTENT_WIDTH_CM)
        left, right = table.rows[0].cells
        left.width, right.width = Cm(10.6), Cm(6.0)
        lp = left.paragraphs[0]
        logo = os.path.join(settings.BASE_DIR, "static", "img", "NBTE_logo.png")
        if os.path.exists(logo):
            lp.add_run().add_picture(logo, height=Cm(0.75))
        run = lp.add_run("   NBTE Quality Assurance Portal")
        run.font.size, run.font.bold, run.font.color.rgb = Pt(9), True, GREEN
        rp = right.paragraphs[0]
        rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = rp.add_run(self.spec["title"])
        run.font.size, run.font.color.rgb = Pt(9), MUTED
        for c in (left, right):
            c.vertical_alignment = 1  # center
        rule = header.paragraphs[0]
        rule.paragraph_format.space_after = Pt(0)
        header._element.remove(rule._p)
        header._element.append(rule._p)
        self._bottom_border(rule, "D2D2D7", 4)
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for text in ("Page ",):
            run = footer.add_run(text)
            run.font.size, run.font.color.rgb = Pt(8.5), MUTED
        self._field(footer, "PAGE")
        run = footer.add_run(" of ")
        run.font.size, run.font.color.rgb = Pt(8.5), MUTED
        self._field(footer, "NUMPAGES")

    @staticmethod
    def _field(paragraph, code):
        for tag, text in (("begin", None), (None, code), ("separate", None), (None, "1"), ("end", None)):
            run = paragraph.add_run()
            run.font.size, run.font.color.rgb = Pt(8.5), MUTED
            if tag:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), tag)
                run._r.append(el)
            elif text == code:
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = f" {code} "
                run._r.append(el)
            else:
                run.text = text

    # ================================================================== about page
    def _about(self):
        spec = self.spec
        self.doc.add_paragraph(f"About the {spec['title']}", style="Heading 1")
        self._accent_rule()
        for text in spec.get("intro", []):
            self._rich(self.doc.add_paragraph(), text)
        parts = [b[1] for b in spec["blocks"] if b[0] == "part"]
        if parts:
            self.doc.add_paragraph("In this guide", style="Heading 2")
            table = self.doc.add_table(rows=0, cols=2)
            self._no_borders(table)
            self._table_width(table, CONTENT_WIDTH_CM)
            for i, title in enumerate(parts, 1):
                a, b = table.add_row().cells
                a.width, b.width = Cm(1.2), Cm(CONTENT_WIDTH_CM - 1.2)
                run = a.paragraphs[0].add_run(f"{i:02d}")
                run.font.bold, run.font.size = True, Pt(12)
                run.font.color.rgb = RGBColor.from_string(self.accent)
                run = b.paragraphs[0].add_run(title)
                run.font.size, run.font.color.rgb = Pt(11.5), INK
                for c in (a, b):
                    self._cell_margins(c, top=0.12, bottom=0.12, left=0.1, right=0.1)
                    self._cell_bottom_border(c, "E5E5EA")
            self._spacer(6)
        if spec.get("before"):
            self._box("important", "Before you begin", None, items=spec["before"], checklist=True)
        self._page_break()

    # ================================================================== text blocks
    def _rich(self, paragraph, text, size=None, colour=None):
        """Text with **bold** spans."""
        for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            if part:
                run = paragraph.add_run(part)
                run.bold = i % 2 == 1
                if size:
                    run.font.size = Pt(size)
                if colour is not None:
                    run.font.color.rgb = colour
        return paragraph

    def _b_part(self, title, lead=""):
        self.part += 1
        label = self.doc.add_paragraph()
        label.paragraph_format.space_before = Pt(26 if self.part > 1 else 0)
        run = label.add_run(f"PART {self.part}")
        run.font.size, run.font.bold = Pt(9.5), True
        run.font.color.rgb = RGBColor.from_string(self.accent)
        self._spacing(run, 40)
        label.paragraph_format.space_after = Pt(0)
        label.paragraph_format.keep_with_next = True
        self.doc.add_paragraph(title, style="Heading 1")
        self._accent_rule()
        if lead:
            p = self._rich(self.doc.add_paragraph(), lead, size=12, colour=INK)
            p.paragraph_format.space_after = Pt(10)
            p.paragraph_format.keep_with_next = True

    def _b_h2(self, text):
        self.doc.add_paragraph(text, style="Heading 2")

    def _b_h3(self, text):
        self.doc.add_paragraph(text, style="Heading 3")

    def _b_p(self, text):
        self._rich(self.doc.add_paragraph(), text)

    def _b_bullets(self, items):
        for item in items:
            self._rich(self.doc.add_paragraph(style="List Bullet"), item)
        self._spacer(2)

    def _b_checklist(self, items):
        for item in items:
            p = self.doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.7)
            p.paragraph_format.first_line_indent = Cm(-0.7)
            p.paragraph_format.space_after = Pt(3)
            run = p.add_run("☐  ")
            run.font.color.rgb = GREEN
            run.font.size = Pt(12)
            self._rich(p, item)
        self._spacer(2)

    def _b_steps(self, items, start=1):
        """Numbered step cards: a green number, the instruction beside it."""
        table = self.doc.add_table(rows=0, cols=2)
        self._no_borders(table)
        self._table_width(table, CONTENT_WIDTH_CM)
        for i, item in enumerate(items, start):
            step_row = table.add_row()
            self._cant_split(step_row)
            a, b = step_row.cells
            a.width, b.width = Cm(1.1), Cm(CONTENT_WIDTH_CM - 1.1)
            self._cell_margins(a, top=0.14, bottom=0.14, left=0.0, right=0.1)
            self._cell_margins(b, top=0.14, bottom=0.14, left=0.15, right=0.1)
            num = a.paragraphs[0]
            num.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = num.add_run(f" {i} ")
            run.font.bold, run.font.size, run.font.color.rgb = True, Pt(10.5), WHITE
            self._run_shade(run, self.accent)
            title, _, rest = item.partition("|")
            p = b.paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            if rest:
                self._rich(p, title.strip(), colour=INK).runs[0].bold = True
                self._rich(b.add_paragraph(), rest.strip())
            else:
                self._rich(p, title.strip())
            for c in (a, b):
                self._cell_bottom_border(c, "EEEEF0")
        self._spacer(4)

    def _b_tip(self, title, text):
        self._box("tip", title, text)

    def _b_important(self, title, text):
        self._box("important", title, text)

    def _b_note(self, title, text):
        self._box("note", title, text)

    def _box(self, kind, title, text, items=None, checklist=False):
        fill, accent, label = BOXES[kind]
        table = self.doc.add_table(rows=1, cols=1)
        self._no_borders(table)
        self._table_width(table, CONTENT_WIDTH_CM)
        self._cant_split(table.rows[0])
        cell = table.cell(0, 0)
        cell.width = Cm(CONTENT_WIDTH_CM)
        self._shade(cell, fill)
        self._cell_margins(cell, top=0.3, bottom=0.25, left=0.45, right=0.4)
        borders = OxmlElement("w:tcBorders")
        left = OxmlElement("w:left")
        for k, v in (("w:val", "single"), ("w:sz", "24"), ("w:color", accent), ("w:space", "0")):
            left.set(qn(k), v)
        borders.append(left)
        cell._tc.get_or_add_tcPr().append(borders)
        head = cell.paragraphs[0]
        run = head.add_run(title)
        run.bold, run.font.color.rgb, run.font.size = True, label, Pt(10.5)
        head.paragraph_format.space_after = Pt(3)
        if text:
            self._rich(cell.add_paragraph(), text).paragraph_format.space_after = Pt(2)
        for item in items or []:
            p = cell.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.first_line_indent = Cm(-0.6)
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run("☐  " if checklist else "•  ")
            r.font.color.rgb = label
            self._rich(p, item)
        self._spacer(6)

    def _b_table(self, header, rows, widths=None):
        n = len(header)
        if widths is None:
            first = 4.6 if n > 2 else 5.0
            widths = [first] + [(CONTENT_WIDTH_CM - first) / (n - 1)] * (n - 1)
        table = self.doc.add_table(rows=1, cols=n)
        self._no_borders(table)
        self._table_width(table, CONTENT_WIDTH_CM)
        for i, text in enumerate(header):
            cell = table.rows[0].cells[i]
            cell.width = Cm(widths[i])
            self._shade(cell, "1F6B3E")
            self._cell_margins(cell, top=0.12, bottom=0.12, left=0.18, right=0.18)
            run = cell.paragraphs[0].add_run(text)
            run.bold, run.font.color.rgb, run.font.size = True, WHITE, Pt(9.5)
        self._repeat_header(table.rows[0])
        for cell in table.rows[0].cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.keep_with_next = True
        for r, row in enumerate(rows):
            new_row = table.add_row()
            self._cant_split(new_row)
            cells = new_row.cells
            for i, text in enumerate(row):
                cells[i].width = Cm(widths[i])
                self._cell_margins(cells[i], top=0.11, bottom=0.11, left=0.18, right=0.18)
                if r % 2:
                    self._shade(cells[i], "F7F7F9")
                p = cells[i].paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                self._rich(p, text, size=9.5)
                if i == 0:
                    for run in p.runs:
                        run.bold = True
                        run.font.color.rgb = INK
                self._cell_bottom_border(cells[i], "E5E5EA")
        self._spacer(8)

    def _b_doc_requirements(self):
        """The documents each kind of institution uploads, from the live rules."""
        rows = []
        try:
            from institutions.models import DocumentRequirement

            qs = (DocumentRequirement.objects.filter(process=DocumentRequirement.PROCESS_REGISTRATION, is_active=True,
                                                     document_kind__is_active=True)
                  .select_related("document_kind", "ownership", "reviewing_department")
                  .order_by("document_kind__sort_order", "document_kind__name"))
            seen = {}
            for req in qs:
                key = req.document_kind_id
                who = req.ownership.name if req.ownership_id else "Every institution"
                entry = seen.setdefault(key, {"name": req.document_kind.name, "who": [], "dept": "", "need": set()})
                if who not in entry["who"]:
                    entry["who"].append(who)
                entry["need"].add("Required" if req.is_mandatory else "Optional")
                if req.reviewing_department_id and not entry["dept"]:
                    entry["dept"] = req.reviewing_department.name
            for e in seen.values():
                need = "Required" if "Required" in e["need"] else "Optional"
                rows.append([e["name"], ", ".join(e["who"]), need, e["dept"] or "NBTE"])
        except Exception:
            rows = []
        if rows:
            self._b_table(["Document", "Who uploads it", "Required?", "Checked by"], rows,
                          widths=[5.2, 4.6, 2.3, 4.5])
        else:
            self._b_p("The Documents page lists exactly which documents apply to your institution.")

    def _b_pagebreak(self):
        self._page_break()

    # ================================================================== images
    def _path(self, name):
        path = os.path.join(self.images_dir, f"{name}.png")
        return path if os.path.exists(path) else None

    def _image(self, name, caption, width_cm, *, border=True):
        path = self._path(name)
        if path is None:
            self.missing.add(name)
            note = self.doc.add_paragraph()
            run = note.add_run(f"[Screenshot: {caption}]")
            run.font.color.rgb, run.italic = MUTED, True
            return
        try:
            from PIL import Image

            with Image.open(path) as im:
                ratio = im.height / im.width
        except Exception:
            ratio = 0.6
        # Keep very tall images on one page.
        max_h = 17.5
        if width_cm * ratio > max_h:
            width_cm = max_h / ratio
        picture = self.doc.add_paragraph()
        picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture.paragraph_format.keep_with_next = True
        picture.paragraph_format.space_before = Pt(6)
        picture.paragraph_format.space_after = Pt(4)
        shape = picture.add_run().add_picture(path, width=Cm(width_cm))
        if border:
            self._picture_border(shape)
        self.figure += 1
        cap = self.doc.add_paragraph(style="Caption")
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = cap.add_run(f"Figure {self.figure}  ")
        run.bold = True
        run.font.color.rgb = RGBColor.from_string(self.accent)
        cap.add_run(caption)

    @staticmethod
    def _picture_border(shape):
        sp_pr = shape._inline.graphic.graphicData.pic.spPr
        ln = OxmlElement("a:ln")
        ln.set("w", str(int(Emu(9525))))
        fill = OxmlElement("a:solidFill")
        clr = OxmlElement("a:srgbClr")
        clr.set("val", "D2D2D7")
        fill.append(clr)
        ln.append(fill)
        sp_pr.append(ln)

    def _b_shot(self, name, caption, callouts=None):
        self._image(name, caption, CONTENT_WIDTH_CM)
        if callouts:
            self._callouts(callouts)

    def _b_halfshot(self, name, caption, callouts=None):
        self._image(name, caption, CONTENT_WIDTH_CM * 0.72)
        if callouts:
            self._callouts(callouts)

    def _b_diagram(self, name, caption):
        self._image(name, caption, CONTENT_WIDTH_CM, border=False)

    def _b_phone(self, name, caption, callouts=None):
        self._image(name, caption, 6.4)
        if callouts:
            self._callouts(callouts)

    def _b_phones(self, items):
        """Two phone screenshots side by side."""
        table = self.doc.add_table(rows=1, cols=len(items))
        self._no_borders(table)
        self._table_width(table, CONTENT_WIDTH_CM)
        for cell, (name, caption) in zip(table.rows[0].cells, items):
            cell.width = Cm(CONTENT_WIDTH_CM / len(items))
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            path = self._path(name)
            if path:
                self._picture_border(p.add_run().add_picture(path, width=Cm(5.6)))
                self.figure += 1
                cap = cell.add_paragraph(style="Caption")
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = cap.add_run(f"Figure {self.figure}  ")
                run.bold, run.font.color.rgb = True, RGBColor.from_string(self.accent)
                cap.add_run(caption)
            else:
                self.missing.add(name)
        self._spacer(4)

    def _callouts(self, callouts):
        """The legend for the numbered gold badges on a screenshot."""
        table = self.doc.add_table(rows=0, cols=2)
        self._no_borders(table)
        self._table_width(table, CONTENT_WIDTH_CM)
        for number, text in callouts:
            a, b = table.add_row().cells
            a.width, b.width = Cm(0.9), Cm(CONTENT_WIDTH_CM - 0.9)
            self._cell_margins(a, top=0.05, bottom=0.05, left=0, right=0.05)
            self._cell_margins(b, top=0.05, bottom=0.05, left=0.12, right=0.05)
            p = a.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(f" {number} ")
            run.font.bold, run.font.size, run.font.color.rgb = True, Pt(9.5), RGBColor(0x1C, 0x13, 0x00)
            self._run_shade(run, GOLD)
            q = b.paragraphs[0]
            q.paragraph_format.space_after = Pt(0)
            self._rich(q, text, size=9.5)
        self._spacer(8)

    # ================================================================== help
    def _b_help(self):
        self._b_part("Getting help", "NBTE Quality Assurance is here to help you through every step.")
        rows = [["How-to page", "The portal's **How it works** page explains every process with diagrams."]]
        if self.email:
            rows.append(["E-mail", f"**{self.email}**. Include your institution's name and the reference you are "
                                   "asking about (for example a request, bill or query number)."])
        if self.phone:
            rows.append(["Phone", f"**{self.phone}**, Monday to Friday during office hours."])
        rows.append(["On the portal", "Answer queries from NBTE on the **Queries** page so the reply stays on your "
                                      "record. The **?** button at the top of every page e-mails NBTE."])
        self._b_table(["Where", "How"], rows, widths=[3.6, CONTENT_WIDTH_CM - 3.6])
        self._box("note", "About the screens in this guide",
                  "The screenshots use demonstration data (fictional institutions and people). Screens may change "
                  "slightly as the portal improves; the steps stay the same.")

    # ================================================================== helpers
    def _page_break(self):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.add_run().add_break(WD_BREAK.PAGE)

    def _spacer(self, points):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.line_spacing = Pt(max(points, 1))
        return p

    def _accent_rule(self):
        p = self.doc.add_paragraph()
        p.paragraph_format.space_after = Pt(10)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.line_spacing = Pt(2)
        p.paragraph_format.keep_with_next = True
        run = p.add_run(" " * 18)
        run.font.size = Pt(2)
        self._run_shade(run, self.accent)

    @staticmethod
    def _spacing(run, value):
        rpr = run._r.get_or_add_rPr()
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), str(value))
        rpr.append(sp)

    @staticmethod
    def _run_shade(run, fill):
        rpr = run._r.get_or_add_rPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        rpr.append(shd)

    @staticmethod
    def _bottom_border(paragraph, colour, size):
        p_pr = paragraph._p.get_or_add_pPr()
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for key, value in (("w:val", "single"), ("w:sz", str(size)), ("w:space", "4"), ("w:color", str(colour))):
            bottom.set(qn(key), value)
        borders.append(bottom)
        p_pr.append(borders)

    @staticmethod
    def _shade(cell, hex_fill):
        tc_pr = cell._tc.get_or_add_tcPr()
        shading = OxmlElement("w:shd")
        shading.set(qn("w:val"), "clear")
        shading.set(qn("w:color"), "auto")
        shading.set(qn("w:fill"), hex_fill)
        tc_pr.append(shading)

    @staticmethod
    def _cell_margins(cell, *, top=0.1, bottom=0.1, left=0.15, right=0.15):
        tc_pr = cell._tc.get_or_add_tcPr()
        mar = OxmlElement("w:tcMar")
        for side, value in (("top", top), ("bottom", bottom), ("start", left), ("left", left), ("end", right),
                            ("right", right)):
            el = OxmlElement(f"w:{side}")
            el.set(qn("w:w"), str(int(value / 2.54 * 1440)))
            el.set(qn("w:type"), "dxa")
            mar.append(el)
        tc_pr.append(mar)

    @staticmethod
    def _cell_bottom_border(cell, colour):
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:color", colour), ("w:space", "0")):
            bottom.set(qn(k), v)
        borders.append(bottom)
        tc_pr.append(borders)

    @staticmethod
    def _cant_split(row):
        tr_pr = row._tr.get_or_add_trPr()
        el = OxmlElement("w:cantSplit")
        el.set(qn("w:val"), "true")
        tr_pr.append(el)

    @staticmethod
    def _repeat_header(row):
        tr_pr = row._tr.get_or_add_trPr()
        el = OxmlElement("w:tblHeader")
        el.set(qn("w:val"), "true")
        tr_pr.append(el)

    @staticmethod
    def _no_borders(table):
        tbl_pr = table._tbl.tblPr
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "nil")
            borders.append(el)
        tbl_pr.append(borders)

    @staticmethod
    def _table_width(table, width_cm):
        """A fixed layout at the given width, so Word keeps our column widths."""
        table.autofit = False
        tbl_pr = table._tbl.tblPr
        for tag in ("w:tblW", "w:tblLayout", "w:tblInd"):
            for el in tbl_pr.findall(qn(tag)):
                tbl_pr.remove(el)
        width = OxmlElement("w:tblW")
        width.set(qn("w:w"), str(int(width_cm / 2.54 * 1440)))
        width.set(qn("w:type"), "dxa")
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tbl_pr.append(width)
        tbl_pr.append(layout)
