"""Smart Excel import / export of the ND/HND facilities and equipment register.

One workbook layout is used everywhere (the file built from the curricula Word
files, the export, and the import):

* sheet **Facilities & Equipment** – one row per item, columns in ``ITEM_COLUMNS``;
* sheet **Programmes** – one row per programme (levels, source curricula, notes;
  programmes with no list in their curricula appear only here);
* sheet **Instructions**.

The import is a two-step affair: ``read_workbook`` + ``build_preview`` say, per
programme, what will be created / updated / left alone and whether the
programme exists on the national programme master list (exact, alias or fuzzy
match); ``apply`` then saves it, following the officer's decision for each
unmatched programme (create it on the master list, map it to an existing one,
import it unlinked, or skip it). Re-importing the same file changes nothing.
"""
from __future__ import annotations

import datetime
import difflib
import io
import re
from collections import OrderedDict
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import FacilityItem, FacilityProgramme, FacilitySection

ITEM_SHEET = "Facilities & Equipment"
PROGRAMME_SHEET = "Programmes"
INSTRUCTIONS_SHEET = "Instructions"
MAX_ROWS = 60000

# (field, header, width, help)
ITEM_COLUMNS = [
    ("programme", "Programme", 34, "Programme name exactly as on the curriculum, e.g. Statistics. Required."),
    ("levels", "Levels (ND/HND)", 12, "ND, HND or ND, HND."),
    ("facility_code", "Facility code", 9, "A, B, C … as in the curriculum (orders the facilities)."),
    ("facility", "Facility (lab / workshop / studio)", 38, "Laboratory, workshop, studio or room. Required."),
    ("group", "Group / sub-heading", 26, "Optional heading inside the facility (e.g. Consumables)."),
    ("sn", "S/N", 6, "Serial number in the curriculum (for reference only)."),
    ("name", "Equipment / item", 52, "The equipment or item. Required."),
    ("nd_qty", "ND qty", 10, "Number, or text such as '1 set'. Blank or '-' = not required."),
    ("hnd_qty", "HND qty", 10, "As ND qty, for HND."),
    ("remarks", "Remarks", 30, "Optional."),
    ("is_critical", "Critical (Y/N)", 10, "Y = counts double in the SSQ shortfall score."),
    ("is_active", "Active (Y/N)", 9, "N = keep on record but no longer required. Blank = Y."),
    ("sort_order", "Sort order", 9, "Order of the item inside its facility (facility × 1000 + item)."),
]
PROGRAMME_COLUMNS = [
    ("programme", "Programme", 40),
    ("levels", "Levels (ND/HND)", 12),
    ("facilities", "Facilities", 10),
    ("items", "Items", 8),
    ("matched", "Matched existing programme? (Y/N)", 16),
    ("match_name", "Programme master match", 40),
    ("match_kind", "Match type", 14),
    ("source", "Source curricula", 50),
    ("notes", "Notes", 60),
    ("file", "Source file", 40),
]

_ALIASES = {
    "programme": ("programme", "program", "programmename", "programname"),
    "levels": ("levels", "level", "levelsndhnd", "levelndhnd"),
    "facility_code": ("facilitycode", "code", "facilityno", "sectioncode"),
    "facility": ("facility", "facilitylabworkshopstudio", "facilitylabworkshop", "facilityname", "section",
                 "laboratoryworkshop", "labworkshop"),
    "group": ("group", "groupsubheading", "subgroup", "subheading", "sub group"),
    "sn": ("sn", "serialno", "serialnumber", "no"),
    "name": ("equipmentitem", "equipment", "item", "items", "itemname", "description", "descriptionofitems"),
    "nd_qty": ("ndqty", "ndquantity", "nd"),
    "hnd_qty": ("hndqty", "hndquantity", "hnd"),
    "remarks": ("remarks", "remark", "comments"),
    "is_critical": ("criticalyn", "critical"),
    "is_active": ("activeyn", "active"),
    "sort_order": ("sortorder", "order", "sort"),
}
_PROG_ALIASES = {
    "programme": _ALIASES["programme"],
    "levels": _ALIASES["levels"],
    "source": ("sourcecurricula", "source"),
    "notes": ("notes", "note"),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return " ".join(str(value).split())


def _hkey(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _yes(value, default):
    t = _text(value).lower()
    if not t:
        return default
    if t in ("y", "yes", "true", "1", "x", "active"):
        return True
    if t in ("n", "no", "false", "0", "inactive"):
        return False
    return None


def _levels(value):
    text = _text(value).upper().replace("/", ",").replace("&", ",").replace(" AND ", ",")
    parts = {p.strip() for p in text.split(",")}
    return [lv for lv in ("ND", "HND") if lv in parts]


def key(text):
    """Case/space-insensitive identity used for programmes, facilities and items."""
    return " ".join(str(text or "").split()).casefold()


_SYN = {"tech": "technology", "technol": "technology", "mgt": "management", "mgmt": "management",
        "engr": "engineering", "eng": "engineering", "engg": "engineering", "sci": "science",
        "admin": "administration", "lab": "laboratory", "programme": "", "program": "", "&": "and"}
_STOP = {"and", "of", "the", "in", "for", "a", "an", "nd", "hnd", "national", "diploma", "higher"}


def norm_name(text):
    """'Electrical/Electronic Engg. Tech' → 'electrical electronic engineering technology'."""
    t = str(text or "").lower().replace("&", " and ")
    t = re.sub(r"\([^)]*\)", " ", t)
    words = [_SYN.get(w, w) for w in re.findall(r"[a-z0-9]+", t)]
    return " ".join(w for w in words if w)


def _tokens(text):
    return {w for w in norm_name(text).split() if w not in _STOP}


_GENERIC = {"technology", "engineering", "management", "studies", "laboratory", "and"}


def _close(word, words):
    return any(w == word or (len(word) > 3 and difflib.SequenceMatcher(None, word, w).ratio() >= 0.85)
               for w in words)


def similarity(a, b):
    """0..1 likeness of two programme names. Shared generic words
    ('Engineering Technology') count for little; distinctive words may differ
    by a typo ('Glasss' ~ 'Glass')."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = _tokens(a) - _GENERIC, _tokens(b) - _GENERIC
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    if not ta or not tb:
        return round(seq * 0.6, 3)
    hits = sum(1 for w in ta if _close(w, tb))
    overlap = hits / max(len(ta), len(tb))
    return round(0.6 * overlap + 0.4 * seq, 3)


# ---------------------------------------------------------------------------
# The data a workbook holds
# ---------------------------------------------------------------------------
@dataclass
class ItemRow:
    group: str
    name: str
    nd_qty: str = ""
    hnd_qty: str = ""
    remarks: str = ""
    is_critical: bool = False
    is_active: bool = True
    sort_order: int = 0
    sn: str = ""


@dataclass
class FacilityRow:
    name: str
    code: str = ""
    order: int = 0
    note: str = ""
    items: "OrderedDict[tuple, ItemRow]" = field(default_factory=OrderedDict)


@dataclass
class ProgrammeRow:
    name: str
    levels: list = field(default_factory=list)
    source: str = ""
    notes: str = ""
    file: str = ""
    facilities: "OrderedDict[str, FacilityRow]" = field(default_factory=OrderedDict)

    @property
    def item_count(self):
        return sum(len(f.items) for f in self.facilities.values())


@dataclass
class WorkbookData:
    programmes: "OrderedDict[str, ProgrammeRow]" = field(default_factory=OrderedDict)
    rows: int = 0
    skipped: list = field(default_factory=list)     # (row, reason)
    merged: list = field(default_factory=list)      # duplicates folded into the first row
    warnings: list = field(default_factory=list)

    def programme(self, name, levels=None):
        k = key(name)
        prog = self.programmes.get(k)
        if prog is None:
            prog = self.programmes[k] = ProgrammeRow(name=" ".join(name.split())[:200], levels=list(levels or []))
        elif levels and not prog.levels:
            prog.levels = list(levels)
        return prog

    @property
    def item_count(self):
        return sum(p.item_count for p in self.programmes.values())

    @property
    def facility_count(self):
        return sum(len(p.facilities) for p in self.programmes.values())


def _facility(prog, name, code=""):
    k = key(name)
    fac = prog.facilities.get(k)
    if fac is None:
        fac = prog.facilities[k] = FacilityRow(name=" ".join(name.split())[:255], code=(code or "")[:4],
                                               order=len(prog.facilities) + 1)
    elif code and not fac.code:
        fac.code = code[:4]
    return fac


def _add_item(data, prog, fac, item, where, keep_variants=False):
    k = (key(item.group), key(item.name))
    if k in fac.items:
        first = fac.items[k]
        same = (first.nd_qty, first.hnd_qty, first.remarks) == (item.nd_qty, item.hnd_qty, item.remarks)
        if not same and keep_variants:
            # The curriculum lists the item twice with different quantities: keep both.
            item.name = f"{item.name} (listed again{', S/N ' + item.sn.rstrip('.') if item.sn else ''})"[:500]
            data.merged.append(f"{where}: '{first.name}' is listed twice in {prog.name} › {fac.name} with "
                               f"different quantities; the second is kept as '{item.name}'")
            return _add_item(data, prog, fac, item, where, keep_variants)
        data.merged.append(f"{where}: '{item.name}' appears twice in {prog.name} › {fac.name}"
                           + ("" if same else f" (kept ND {first.nd_qty or '-'} / HND {first.hnd_qty or '-'}; "
                                              f"ignored ND {item.nd_qty or '-'} / HND {item.hnd_qty or '-'})"))
        return
    fac.items[k] = item


def _finalise(data):
    """Facilities ordered by their code (A, B, … AA) when every one has one;
    items without a sort order numbered facility × 1000 + position."""
    for prog in data.programmes.values():
        facs = list(prog.facilities.values())
        if facs and all(f.code for f in facs):
            for n, f in enumerate(sorted(facs, key=lambda f: (len(f.code), f.code, f.order)), 1):
                f.order = n
        for f in facs:
            for n, it in enumerate(f.items.values(), 1):
                if not it.sort_order:
                    it.sort_order = f.order * 1000 + n
    return data


# ---------------------------------------------------------------------------
# Sources → WorkbookData
# ---------------------------------------------------------------------------
def from_parsed(parsed_list):
    """docx_parser.parse() results → WorkbookData (duplicates folded, noted)."""
    data = WorkbookData()
    for p in sorted(parsed_list, key=lambda p: key(p["programme"])):
        if not p.get("programme"):
            continue
        prog = data.programme(p["programme"], p.get("levels"))
        prog.source = prog.source or (p.get("source") or "")[:500]
        prog.notes = prog.notes or "\n".join(p.get("notes") or [])
        prog.file = prog.file or (p.get("file") or "").rsplit("/", 1)[-1]
        for s in p["sections"]:
            fac = _facility(prog, s["name"], s.get("code") or "")
            if s.get("note") and not s["items"]:
                fac.note = s["note"][:500]
                line = f"{fac.code + '. ' if fac.code else ''}{fac.name}: {s['note']}"
                prog.notes = (prog.notes + "\n" if prog.notes else "") + line
            for it in s["items"]:
                data.rows += 1
                _add_item(data, prog, fac, ItemRow(group=(it.get("group") or "")[:255], name=it["name"][:500],
                                                   nd_qty=(it.get("nd") or "")[:60], hnd_qty=(it.get("hnd") or "")[:60],
                                                   remarks=(it.get("remarks") or "")[:500], sn=it.get("sn") or ""),
                          prog.file or prog.name, keep_variants=True)
    return _finalise(data)


def from_db(programmes=None):
    """The register (or some programmes of it) → WorkbookData, for export."""
    data = WorkbookData()
    qs = FacilityProgramme.objects.all().order_by("name")
    if programmes is not None:
        qs = qs.filter(pk__in=[p.pk for p in programmes])
    for fp in qs:
        prog = data.programme(fp.name, _levels(fp.levels))
        prog.source, prog.notes = fp.source, fp.notes
        sections = list(fp.sections.order_by("sort_order", "code", "name"))
        for s in sections:
            _facility(prog, s.name, s.code)
        items = FacilityItem.objects.filter(programme=fp).select_related("section").order_by(
            "section__sort_order", "sort_order", "pk")
        for it in items:
            fac = _facility(prog, it.section.name, it.section.code)
            fac.items[(key(it.group), key(it.name))] = ItemRow(
                group=it.group, name=it.name, nd_qty=it.nd_qty, hnd_qty=it.hnd_qty, remarks=it.remarks,
                is_critical=it.is_critical, is_active=it.is_active, sort_order=it.sort_order)
            data.rows += 1
    return data


def _find_header(ws, aliases, needed):
    """(row number, {field: column index}) of the header row, or (None, {})."""
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=8, values_only=True), 1):
        cols = {}
        for c_idx, value in enumerate(row):
            h = _hkey(value)
            for f, names in aliases.items():
                if f not in cols and h in {_hkey(n) for n in names}:
                    cols[f] = c_idx
                    break
        if all(f in cols for f in needed):
            return r_idx, cols
    return None, {}


def read_workbook(fileobj):
    """An uploaded .xlsx → WorkbookData. Raises ValidationError if the file is
    not a workbook or has no sheet with Programme / Facility / Equipment columns."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception as exc:  # zip/xml errors of a damaged or wrong file
        raise ValidationError(f"That file is not an Excel workbook (.xlsx) ({type(exc).__name__}).")
    data = WorkbookData()
    try:
        item_ws = header_row = cols = None
        ordered = sorted(wb.worksheets, key=lambda ws: ws.title != ITEM_SHEET)
        for ws in ordered:
            if ws.title in (INSTRUCTIONS_SHEET, PROGRAMME_SHEET):
                continue
            header_row, cols = _find_header(ws, _ALIASES, ("programme", "facility", "name"))
            if header_row:
                item_ws = ws
                break
        if item_ws is None:
            raise ValidationError("No sheet with Programme, Facility and Equipment columns was found. "
                                  "Download the template from this page and keep its header row.")
        # Programme sheet first: levels, source, notes, and programmes with no list.
        if PROGRAMME_SHEET in wb.sheetnames:
            pws = wb[PROGRAMME_SHEET]
            p_head, p_cols = _find_header(pws, _PROG_ALIASES, ("programme",))
            if p_head:
                for row in pws.iter_rows(min_row=p_head + 1, values_only=True):
                    name = _text(row[p_cols["programme"]] if p_cols["programme"] < len(row) else "")
                    if not name or name.casefold() in ("total", "totals", "grand total"):
                        continue
                    get = lambda f: _text(row[p_cols[f]]) if f in p_cols and p_cols[f] < len(row) else ""  # noqa: E731
                    prog = data.programme(name, _levels(get("levels")))
                    prog.source = get("source")[:500]
                    raw_notes = row[p_cols["notes"]] if "notes" in p_cols and p_cols["notes"] < len(row) else ""
                    prog.notes = str(raw_notes or "").strip()[:5000]
        for r_idx, row in enumerate(item_ws.iter_rows(min_row=header_row + 1, values_only=True), header_row + 1):
            def get(f, _row=row):
                i = cols.get(f)
                return _text(_row[i]) if i is not None and i < len(_row) else ""

            if not any(_text(v) for v in row):
                continue
            data.rows += 1
            if data.rows > MAX_ROWS:
                raise ValidationError(f"The sheet has more than {MAX_ROWS:,} rows; split it into smaller files.")
            programme, facility, name = get("programme"), get("facility"), get("name")
            if programme and facility and not name:      # a facility with no equipment listed
                prog = data.programme(programme, _levels(get("levels")))
                fac = _facility(prog, facility[:255], get("facility_code").upper())
                fac.note = fac.note or get("remarks")[:500]
                continue
            missing = [label for label, v in (("Programme", programme), ("Facility", facility),
                                              ("Equipment", name)) if not v]
            if missing:
                data.skipped.append((r_idx, f"no {', '.join(missing)}"))
                continue
            critical = _yes(get("is_critical"), False)
            active = _yes(get("is_active"), True)
            if critical is None or active is None:
                data.skipped.append((r_idx, "Critical / Active must be Y or N"))
                continue
            sort_txt = get("sort_order")
            try:
                sort = int(float(sort_txt)) if sort_txt else 0
            except ValueError:
                data.skipped.append((r_idx, f"sort order '{sort_txt}' is not a number"))
                continue
            for label, value, limit in (("Programme", programme, 200), ("Facility", facility, 255),
                                        ("Equipment", name, 500), ("Group", get("group"), 255)):
                if len(value) > limit:
                    data.warnings.append(f"Row {r_idx}: {label} cut to {limit} characters.")
            prog = data.programme(programme, _levels(get("levels")))
            fac = _facility(prog, facility[:255], get("facility_code").upper())
            _add_item(data, prog, fac, ItemRow(
                group=get("group")[:255], name=name[:500], nd_qty=_qty(get("nd_qty")), hnd_qty=_qty(get("hnd_qty")),
                remarks=get("remarks")[:500], is_critical=critical, is_active=active, sort_order=max(sort, 0),
                sn=get("sn")), f"Row {r_idx}")
    finally:
        wb.close()
    if not data.programmes:
        raise ValidationError("The workbook holds no programmes.")
    return _finalise(data)


def _qty(text):
    return "" if text in ("—", "–") else text[:60]


# ---------------------------------------------------------------------------
# WorkbookData → .xlsx
# ---------------------------------------------------------------------------
def _letter(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out or "A"


def build_workbook(data, *, matches=None, title="NBTE ND/HND minimum facilities and equipment"):
    """WorkbookData → .xlsx bytes (Instructions, Facilities & Equipment, Programmes)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    matches = matches if matches is not None else match_programmes(data)
    green, pale = "0B6E4F", "E8F3EE"
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=green)
    band = PatternFill("solid", fgColor="F6F8F7")
    thin = Border(bottom=Side(style="thin", color="D5DDD9"))
    wrap = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")

    wb = Workbook()
    # --- Instructions -------------------------------------------------------
    ins = wb.active
    ins.title = INSTRUCTIONS_SHEET
    ins.sheet_view.showGridLines = False
    ins.column_dimensions["A"].width = 30
    ins.column_dimensions["B"].width = 100
    ins["A1"] = title
    ins["A1"].font = Font(bold=True, size=16, color=green)
    ins["A2"] = (f"National Board for Technical Education · generated {timezone.localtime():%d %b %Y %H:%M} · "
                 f"{len(data.programmes)} programmes, {data.facility_count:,} facilities, {data.item_count:,} items")
    ins["A2"].font = Font(italic=True, color="555555")
    steps = [
        ("How to use", ""),
        ("1", f"Edit the '{ITEM_SHEET}' sheet: one row per item of equipment. Keep the header row as it is."),
        ("2", "Add a row to add an item; change ND / HND qty, Remarks, Critical or Active to update one. "
              "Rows are matched by Programme + Facility + Group + Equipment (capitals and spaces are ignored), "
              "so renaming an item creates a new one: set Active = N on the old row instead of deleting it."),
        ("3", f"The '{PROGRAMME_SHEET}' sheet lists every programme with its levels, source curricula and notes. "
              "Programmes with no list in their curricula appear only there; they are imported with no facilities."),
        ("4", "In the portal open QA › Facilities & equipment › Smart Excel import, upload this file and check the "
              "preview: per programme it shows how many facilities and items will be created, updated or left "
              "unchanged, and asks what to do with programmes that are not yet on the national programme list "
              "(create them, map them to an existing programme, import them unlinked, or skip them)."),
        ("5", "Nothing is saved until you confirm. Importing the same file again changes nothing."),
        ("6", "A row with a Programme and Facility but no Equipment records a facility the curriculum requires "
              "without listing equipment (its Remarks say why, e.g. 'See Science Laboratory Technology curriculum')."),
        ("", ""),
        ("Columns", ""),
    ] + [(header, help_) for _f, header, _w, help_ in ITEM_COLUMNS]
    r = 4
    for a, b in steps:
        ins.cell(row=r, column=1, value=a)
        ins.cell(row=r, column=2, value=b).alignment = Alignment(wrap_text=True, vertical="top")
        if a in ("How to use", "Columns"):
            ins.cell(row=r, column=1).font = Font(bold=True, size=12, color=green)
        else:
            ins.cell(row=r, column=1).font = Font(bold=True)
            ins.cell(row=r, column=1).alignment = top
        r += 1
    if data.merged:
        r += 1
        ins.cell(row=r, column=1, value="Duplicates folded").font = Font(bold=True, size=12, color=green)
        ins.cell(row=r, column=2, value="Items listed twice in the same facility were kept once:")
        for line in data.merged[:300]:
            r += 1
            ins.cell(row=r, column=2, value=line).alignment = Alignment(wrap_text=True, vertical="top")

    # --- Items ----------------------------------------------------------------
    ws = wb.create_sheet(ITEM_SHEET)
    ws.append([h for _f, h, _w, _h in ITEM_COLUMNS])
    for c, (_f, _h, width, _help) in enumerate(ITEM_COLUMNS, 1):
        cell = ws.cell(row=1, column=c)
        cell.font, cell.fill = head_font, head_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.row_dimensions[1].height = 32
    fields = [f for f, *_ in ITEM_COLUMNS]
    name_col = fields.index("name") + 1
    shade = False
    rn = 1                       # current row (ws.max_row is O(rows) in openpyxl)
    for prog in data.programmes.values():
        shade = not shade
        levels = ", ".join(prog.levels)
        for fac in sorted(prog.facilities.values(), key=lambda f: f.order):
            code = fac.code or _letter(fac.order)
            if not fac.items:
                values = {f: "" for f in fields}
                values.update(programme=prog.name, levels=levels, facility_code=code, facility=fac.name,
                              remarks=fac.note or "No items listed")
                ws.append([values[f] for f in fields])
                rn += 1
                for c in range(1, len(fields) + 1):
                    ws.cell(row=rn, column=c).font = Font(italic=True, color="7A5B00")
                continue
            for it in sorted(fac.items.values(), key=lambda i: i.sort_order):
                values = {"programme": prog.name, "levels": levels, "facility_code": code, "facility": fac.name,
                          "group": it.group, "sn": it.sn, "name": it.name, "nd_qty": it.nd_qty,
                          "hnd_qty": it.hnd_qty, "remarks": it.remarks,
                          "is_critical": "Y" if it.is_critical else "N", "is_active": "Y" if it.is_active else "N",
                          "sort_order": it.sort_order}
                ws.append([values[f] for f in fields])
                rn += 1
                if shade:
                    for c in range(1, len(fields) + 1):
                        ws.cell(row=rn, column=c).fill = band
                ws.cell(row=rn, column=name_col).alignment = wrap
    last = rn
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(fields))}{max(last, 2)}"
    yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(yn)
    for f in ("is_critical", "is_active"):
        col = get_column_letter(fields.index(f) + 1)
        yn.add(f"{col}2:{col}{max(last, 2) + 500}")
    # Quantities are text ("1 set"): keep Excel from turning "1-2" into a date.
    for f in ("nd_qty", "hnd_qty", "sn"):
        col = fields.index(f) + 1
        for row in ws.iter_rows(min_row=2, max_row=last, min_col=col, max_col=col):
            for cell in row:
                cell.number_format = "@"

    # --- Programmes -----------------------------------------------------------
    ps = wb.create_sheet(PROGRAMME_SHEET)
    ps.append([h for _f, h, _w in PROGRAMME_COLUMNS])
    for c, (_f, _h, width) in enumerate(PROGRAMME_COLUMNS, 1):
        cell = ps.cell(row=1, column=c)
        cell.font, cell.fill = head_font, head_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ps.column_dimensions[get_column_letter(c)].width = width
    ps.row_dimensions[1].height = 32
    pr = 1
    for k, prog in data.programmes.items():
        m = matches.get(k) or {}
        row = {"programme": prog.name, "levels": ", ".join(prog.levels), "facilities": len(prog.facilities),
               "items": prog.item_count, "matched": "Y" if m.get("status") in ("linked", "exact") else "N",
               "match_name": (m.get("master_label") or (f"Closest: {m['candidate_label']} ({m['score']:.0%})"
                                                         if m.get("candidate_label") else "")),
               "match_kind": {"linked": "Linked", "exact": "Exact name", "candidate": "Close match only",
                              "none": "Not found"}.get(m.get("status"), ""),
               "source": prog.source, "notes": prog.notes, "file": prog.file}
        ps.append([row[f] for f, *_ in PROGRAMME_COLUMNS])
        pr += 1
        for c in range(1, len(PROGRAMME_COLUMNS) + 1):
            ps.cell(row=pr, column=c).alignment = wrap
        if not prog.item_count:
            for c in range(1, len(PROGRAMME_COLUMNS) + 1):
                ps.cell(row=pr, column=c).fill = PatternFill("solid", fgColor="FFF4E5")
        elif row["matched"] == "Y":
            ps.cell(row=pr, column=5).fill = PatternFill("solid", fgColor=pale)
    ps.freeze_panes = "B2"
    ps.auto_filter.ref = f"A1:{get_column_letter(len(PROGRAMME_COLUMNS))}{max(pr, 2)}"
    last_p = pr
    total = last_p + 2
    ps.cell(row=total, column=1, value="Total").font = Font(bold=True)
    for col in ("C", "D"):
        ps[f"{col}{total}"] = f"=SUM({col}2:{col}{last_p})"
        ps[f"{col}{total}"].font = Font(bold=True)

    wb.active = 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Matching programmes to the national programme master list
# ---------------------------------------------------------------------------
def _master_index():
    from institutions.models import ProgrammeMaster

    masters = list(ProgrammeMaster.objects.select_related("level").order_by("name", "level__sort_order"))
    by_name = OrderedDict()   # norm name (and aliases) → [masters]
    names = OrderedDict()     # display name → [masters] (one entry per distinct programme name)
    for m in masters:
        names.setdefault(m.name, []).append(m)
        for alias in [m.name] + [a for a in (m.aliases or "").splitlines() if a.strip()]:
            by_name.setdefault(norm_name(alias), []).append(m)
    return masters, by_name, names


def _pick_level(masters, levels):
    for lv in [l.lower() for l in levels] + ["nd", "hnd"]:
        for m in masters:
            if m.level_id and m.level.code == lv:
                return m
    return masters[0] if masters else None


def _label(m):
    if m is None:
        return ""
    return f"{m.name} ({m.level.code.upper()})" if m.level_id else m.name


def match_programmes(data, index=None):
    """{programme key: {status, master_id, master_label, candidate_id, candidate_label, score, candidates}}.

    status: 'linked' (register programme already linked), 'exact' (same name or
    alias on the master list, ignoring case/punctuation/abbreviations),
    'candidate' (only a close name, score ≥ 0.6), 'none'."""
    try:
        masters, by_name, names = index or _master_index()
    except Exception:  # institutions app unavailable
        return {}
    existing = {key(fp.name): fp for fp in FacilityProgramme.objects.select_related(
        "programme_master__level").filter(name__in=[p.name for p in data.programmes.values()])}
    # name__in is case-sensitive on some backends; a second, case-insensitive pass.
    if len(existing) < len(data.programmes):
        wanted = set(data.programmes)
        for fp in FacilityProgramme.objects.select_related("programme_master__level"):
            if key(fp.name) in wanted:
                existing.setdefault(key(fp.name), fp)
    out = {}
    for k, prog in data.programmes.items():
        fp = existing.get(k)
        info = {"status": "none", "master_id": None, "master_label": "", "candidate_id": None,
                "candidate_label": "", "score": 0.0, "candidates": [], "register_exists": fp is not None}
        if fp is not None and fp.programme_master_id:
            m = fp.programme_master
            info.update(status="linked", master_id=m.pk, master_label=_label(m))
            out[k] = info
            continue
        exact = by_name.get(norm_name(prog.name))
        if exact:
            m = _pick_level(exact, prog.levels)
            info.update(status="exact", master_id=m.pk, master_label=_label(m))
            out[k] = info
            continue
        scored = sorted(((similarity(prog.name, n), n) for n in names), reverse=True)[:5]
        cands = [(s, n) for s, n in scored if s >= 0.45]
        info["candidates"] = [{"id": _pick_level(names[n], prog.levels).pk,
                               "label": _label(_pick_level(names[n], prog.levels)), "score": s} for s, n in cands]
        if cands and cands[0][0] >= 0.6:
            best = info["candidates"][0]
            info.update(status="candidate", candidate_id=best["id"], candidate_label=best["label"],
                        score=best["score"])
        out[k] = info
    return out


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
def _existing_items(fp):
    """{(facility key, group key, name key): item} of one register programme."""
    out = {}
    for it in FacilityItem.objects.filter(programme=fp).select_related("section"):
        out.setdefault((key(it.section.name), key(it.group), key(it.name)), it)
    return out


def _item_changes(item, row, fac_order):
    changes = []
    for f in ("nd_qty", "hnd_qty", "remarks", "is_critical", "is_active", "sort_order"):
        new = getattr(row, f)
        if f == "sort_order" and not new:
            continue
        if getattr(item, f) != new:
            changes.append(f)
    if item.group != row.group:
        changes.append("group")   # same key, different capitals
    if item.name != row.name:
        changes.append("name")
    return changes


def build_preview(data):
    """Per-programme counts of what ``apply`` would do, plus the master-list match."""
    matches = match_programmes(data)
    existing = {key(fp.name): fp for fp in FacilityProgramme.objects.all()}
    rows, totals = [], {"programmes_new": 0, "programmes_existing": 0, "facilities_new": 0,
                        "facilities_existing": 0, "items_new": 0, "items_updated": 0, "items_unchanged": 0,
                        "items_not_in_file": 0, "need_decision": 0}
    for k, prog in data.programmes.items():
        fp = existing.get(k)
        r = {"key": k, "name": prog.name, "levels": ", ".join(prog.levels) or "—", "register_exists": fp is not None,
             "facilities": len(prog.facilities), "items": prog.item_count, "facilities_new": 0,
             "facilities_existing": 0, "items_new": 0, "items_updated": 0, "items_unchanged": 0,
             "items_not_in_file": 0, "slug": fp.slug if fp else "", "match": matches.get(k, {"status": "none"}),
             "examples": []}
        if fp is None:
            r["facilities_new"] = len(prog.facilities)
            r["items_new"] = prog.item_count
        else:
            secs = {key(s.name) for s in fp.sections.all()}
            current = _existing_items(fp)
            seen = set()
            for fk, fac in prog.facilities.items():
                r["facilities_existing" if fk in secs else "facilities_new"] += 1
                for (gk, nk), row in fac.items.items():
                    item = current.get((fk, gk, nk))
                    if item is None:
                        r["items_new"] += 1
                        continue
                    seen.add(item.pk)
                    changes = _item_changes(item, row, fac.order)
                    if changes:
                        r["items_updated"] += 1
                        if len(r["examples"]) < 4:
                            r["examples"].append(f"{item.name}: " + ", ".join(
                                f"{c.replace('_', ' ')} {getattr(item, c)!s} → {getattr(row, c)!s}"
                                for c in changes[:3]))
                    else:
                        r["items_unchanged"] += 1
            r["items_not_in_file"] = sum(1 for it in current.values() if it.pk not in seen and it.is_active)
        r["needs_decision"] = r["match"]["status"] in ("candidate", "none")
        r["action"] = "new" if fp is None else ("change" if r["items_new"] or r["items_updated"]
                                                or r["facilities_new"] else "same")
        totals["programmes_new" if fp is None else "programmes_existing"] += 1
        for f in ("facilities_new", "facilities_existing", "items_new", "items_updated", "items_unchanged",
                  "items_not_in_file"):
            totals[f] += r[f]
        totals["need_decision"] += r["needs_decision"]
        rows.append(r)
    return {"programmes": rows, "totals": totals, "rows": data.rows, "skipped": data.skipped,
            "merged": data.merged, "warnings": data.warnings, "item_count": data.item_count,
            "facility_count": data.facility_count}


def master_choices():
    """All master-list rows for the 'map to existing' dropdown."""
    try:
        from institutions.models import ProgrammeMaster
    except Exception:
        return []
    return [(m.pk, _label(m)) for m in ProgrammeMaster.objects.select_related("level").order_by("name",
                                                                                                  "level__sort_order")]


def default_decision(match):
    """'link' for linked/exact; 'map:<id>' for a very close candidate; else 'create'."""
    status = match.get("status")
    if status in ("linked", "exact"):
        return "link"
    if status == "candidate" and match.get("score", 0) >= 0.9:
        return f"map:{match['candidate_id']}"
    return "create"


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def _create_masters(prog, actor):
    """One master row per level (ND first), reusing rows that already exist."""
    from institutions.models import ProgrammeLevel, ProgrammeMaster

    codes = [lv.lower() for lv in (prog.levels or ["ND"])]
    levels = {lv.code: lv for lv in ProgrammeLevel.objects.filter(code__in=codes)}
    if not levels:
        raise ValidationError(f"The programme levels {', '.join(prog.levels) or 'ND'} are not set up "
                              "(Lookups › Programme levels).")
    first, created = None, []
    who = getattr(actor, "get_full_name", lambda: "")() or getattr(actor, "email", "") or "the system"
    for code in ("nd", "hnd"):
        level = levels.get(code)
        if level is None:
            continue
        master = ProgrammeMaster.objects.filter(name__iexact=prog.name, level=level).first()
        if master is None:
            master = ProgrammeMaster(
                name=prog.name, level=level, curriculum_ref="NBTE curriculum register",
                notes=f"Added from the facilities and equipment Excel import on "
                      f"{timezone.localdate():%d %b %Y} by {who}.")
            master.save()
            created.append(master)
        first = first or master
    return first, created


@transaction.atomic
def apply(data, decisions=None, *, actor=None, deactivate_missing=False):
    """Save the workbook. ``decisions`` maps programme key → 'link' | 'create' |
    'map:<master id>' | 'none' | 'skip' (missing keys use ``default_decision``).
    Returns counts plus the ids of everything created (``created_ids``)."""
    from institutions.models import ProgrammeMaster

    from . import services

    decisions = decisions or {}
    matches = match_programmes(data)
    out = {"programmes_created": 0, "programmes_updated": 0, "programmes_skipped": 0, "masters_created": 0,
           "masters_linked": 0, "facilities_created": 0, "facilities_updated": 0, "items_created": 0,
           "items_updated": 0, "items_unchanged": 0, "items_deactivated": 0, "programmes": [],
           "created_ids": {"programmes": [], "masters": []}}
    now = timezone.now()
    actor_obj = actor if getattr(actor, "pk", None) else None
    services._BATCH["depth"] += 1
    try:
        for k, prog in data.programmes.items():
            match = matches.get(k, {"status": "none"})
            decision = decisions.get(k) or default_decision(match)
            if decision == "skip":
                out["programmes_skipped"] += 1
                continue
            fp = FacilityProgramme.objects.filter(name__iexact=prog.name).first()
            if fp is None:
                fp = FacilityProgramme.objects.create(name=prog.name, levels=", ".join(prog.levels),
                                                      source=prog.source[:500], notes=prog.notes)
                out["programmes_created"] += 1
                out["created_ids"]["programmes"].append(fp.pk)
                fp_changed = True
            else:
                fp_changed = False
                upd = []
                if prog.levels and fp.levels != ", ".join(prog.levels):
                    fp.levels = ", ".join(prog.levels)
                    upd.append("levels")
                if prog.source and fp.source != prog.source[:500]:
                    fp.source = prog.source[:500]
                    upd.append("source")
                if prog.notes and fp.notes != prog.notes:
                    fp.notes = prog.notes
                    upd.append("notes")
                if upd:
                    fp.save(update_fields=upd + ["updated_at"])
                    fp_changed = True
            # --- the master list
            master = None
            if decision == "link" and match.get("master_id"):
                master = ProgrammeMaster.objects.filter(pk=match["master_id"]).first()
            elif decision.startswith("map:"):
                try:
                    master = ProgrammeMaster.objects.filter(pk=int(decision[4:])).first()
                except ValueError:
                    master = None
                if master is None:
                    raise ValidationError(f"Choose an existing programme for {prog.name}.")
            elif decision == "create":
                master, created = _create_masters(prog, actor)
                out["masters_created"] += len(created)
                out["created_ids"]["masters"] += [m.pk for m in created]
            if master is not None and fp.programme_master_id != master.pk and (
                    decision != "link" or fp.programme_master_id is None):
                fp.programme_master = master
                fp.save(update_fields=["programme_master", "updated_at"])
                out["masters_linked"] += 1
            # --- facilities
            sections = {key(s.name): s for s in fp.sections.all()}
            for fk, fac in prog.facilities.items():
                code = (fac.code or _letter(fac.order))[:4]
                sec = sections.get(fk)
                if sec is None:
                    sec = sections[fk] = FacilitySection.objects.create(programme=fp, name=fac.name, code=code,
                                                                        sort_order=fac.order)
                    out["facilities_created"] += 1
                elif (sec.code, sec.sort_order, sec.name) != (code, fac.order, fac.name):
                    sec.code, sec.sort_order, sec.name = code, fac.order, fac.name
                    sec.save(update_fields=["code", "sort_order", "name"])
                    out["facilities_updated"] += 1
            # --- items
            current = _existing_items(fp)
            seen, new, changed = set(), [], []
            from .docx_parser import qty_number

            for fk, fac in prog.facilities.items():
                sec = sections[fk]
                for (gk, nk), row in fac.items.items():
                    item = current.get((fk, gk, nk))
                    if item is None:
                        new.append(FacilityItem(
                            programme=fp, section=sec, group=row.group, name=row.name, nd_qty=row.nd_qty,
                            hnd_qty=row.hnd_qty, nd_qty_num=qty_number(row.nd_qty),
                            hnd_qty_num=qty_number(row.hnd_qty), remarks=row.remarks, is_critical=row.is_critical,
                            is_active=row.is_active, sort_order=row.sort_order, updated_by=actor_obj))
                        continue
                    seen.add(item.pk)
                    changes = _item_changes(item, row, fac.order)
                    if not changes:
                        out["items_unchanged"] += 1
                        continue
                    for f in changes:
                        setattr(item, f, getattr(row, f))
                    item.nd_qty_num, item.hnd_qty_num = qty_number(item.nd_qty), qty_number(item.hnd_qty)
                    item.updated_by, item.updated_at = actor_obj, now
                    changed.append(item)
            if deactivate_missing:
                for it in current.values():
                    if it.pk not in seen and it.is_active:
                        it.is_active, it.updated_by, it.updated_at = False, actor_obj, now
                        changed.append(it)
                        out["items_deactivated"] += 1
            # Bulk writes: ~15,000 rows in seconds, one audit entry per programme below.
            FacilityItem.objects.bulk_create(new, batch_size=500)
            if changed:
                FacilityItem.objects.bulk_update(
                    changed, ["group", "name", "nd_qty", "hnd_qty", "nd_qty_num", "hnd_qty_num", "remarks",
                              "is_critical", "is_active", "sort_order", "updated_by", "updated_at"], batch_size=500)
            out["items_created"] += len(new)
            out["items_updated"] += len(changed)
            if not fp_changed and (new or changed):
                out["programmes_updated"] += 1
            out["programmes"].append({"name": fp.name, "slug": fp.slug, "new": len(new), "updated": len(changed),
                                      "master": _label(fp.programme_master) if fp.programme_master_id else ""})
            if new or changed:
                try:
                    from core import audit

                    audit.log("other", fp, actor=actor_obj,
                              note=f"Facilities Excel import: {len(new)} items added, {len(changed)} updated")
                except Exception:
                    pass
        out["items_updated"] -= out["items_deactivated"]
    finally:
        services._BATCH["depth"] -= 1
        services.bump_stats()
    return out


def export_bytes(programmes=None):
    data = from_db(programmes)
    return build_workbook(data)


def export_filename(prog=None):
    return f"NBTE_Facilities_and_Equipment_{prog.slug + '_' if prog else ''}{datetime.date.today():%Y%m%d}.xlsx"
