"""NBTE nominal-roll importer.

Reads the HR nominal-roll workbook (one or more sheets of S/NO | SURNAME |
OTHER NAME | ... | QUALIFICATION (S)) and upserts Staff records.

Pipeline
    1. scan    — classify every row of every sheet (title / section header /
                 column header / data / blank) and map columns by header text.
    2. parse   — clean each data row into a record: dates through
                 parse_nbte_date, names of lookups normalised, warnings kept
                 with their sheet and row.
    3. merge   — sheets are applied oldest -> newest (by the date in their
                 title), so a later sheet overrides an earlier one; rows for
                 the same FILE NO are merged in memory, so each person is
                 written once. This is what makes a second run a no-op.
    4. apply   — inside ONE transaction: find-or-create lookups, match each
                 person (FILE NO, then name + date of birth, then a unique
                 name), and write only the fields whose incoming value is
                 non-empty and different. Dry runs roll the transaction back.

Used by `manage.py import_nominal_roll` and the Nominal roll web page.
"""
from __future__ import annotations

import io
import re
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from accounts.audience import nbte_staff_records_q
from core import audit

from . import nbte_reference as ref
from .models import (
    Department, Designation, ImportSession, Staff, StaffState, StateOfOrigin,
)

# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------
# Header text (letters, digits, "/" and "_" only, upper-case) -> field.
HEADER_MAP = {
    "S/NO": "serial", "SNO": "serial", "SN": "serial",
    "SURNAME": "last_name", "LASTNAME": "last_name",
    "OTHERNAME": "first_name", "OTHERNAMES": "first_name", "FIRSTNAME": "first_name",
    "SEX": "gender", "GENDER": "gender",
    "FILENO": "employee_no", "FILENUMBER": "employee_no",
    "ST_ORIGIN": "state_of_origin", "STORIGIN": "state_of_origin",
    "STATEOFORIGIN": "state_of_origin", "STATE": "state_of_origin",
    "LGA": "lga",
    "DESIGNATION": "designation", "RANK": "designation",
    "CONTEDISS/CONHESS": "grade_level", "CONTEDISS": "grade_level", "CONHESS": "grade_level",
    "GRADELEVEL": "grade_level", "GL": "grade_level",
    "DEPARTMT": "department", "DEPARTMENT": "department", "DEPT": "department",
    "D_BIRTH": "date_of_birth", "DOB": "date_of_birth", "DATEOFBIRTH": "date_of_birth",
    "D_FAPPMT": "date_first_appointment",
    "D_JBOARD": "date_joined_board",
    "D_PRAPPMT": "date_present_appointment",
    "D_COF": "date_confirmation",
    "REMARKS": "remarks", "REMARK": "remarks",
    "QUALIFICATIONS": "qualifications", "QUALIFICATION": "qualifications",
    # Optional extra columns (the old "Staff directory" import's contact fields)
    "EMAIL": "email", "EMAILADDRESS": "email", "EMAIL_ADDRESS": "email",
    "PHONE": "phone", "PHONENO": "phone", "PHONENUMBER": "phone", "GSM": "phone", "GSMNO": "phone",
    "OFFICE": "office",
}
# Optional columns written after the 17 official ones by the exporter/template.
EXTRA_HEADERS = ["EMAIL", "PHONE", "OFFICE"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# The official column order, used for sheets that have no header row
# (NEW STAFF 2023) and by the exporter / template.
STANDARD_HEADERS = [
    "S/NO", "SURNAME", "OTHER NAME", "SEX", "FILE NO", "ST_ORIGIN", "LGA",
    "DESIGNATION", "CONTEDISS/CONHESS", "DEPARTMT", "D_BIRTH", "D_FAPPMT",
    "D_JBOARD", "D_PRAPPMT", "D_COF", "REMARKS", "QUALIFICATION (S)",
]
STANDARD_FIELDS = [
    "serial", "last_name", "first_name", "gender", "employee_no", "state_of_origin", "lga",
    "designation", "grade_level", "department", "date_of_birth", "date_first_appointment",
    "date_joined_board", "date_present_appointment", "date_confirmation", "remarks",
    "qualifications",
]
DATE_FIELDS = [
    "date_of_birth", "date_first_appointment", "date_joined_board",
    "date_present_appointment", "date_confirmation",
]
DATE_LABELS = {
    "date_of_birth": "D_BIRTH", "date_first_appointment": "D_FAPPMT",
    "date_joined_board": "D_JBOARD", "date_present_appointment": "D_PRAPPMT",
    "date_confirmation": "D_COF",
}
# Plain text fields written straight onto Staff.
TEXT_FIELDS = ["last_name", "first_name", "gender", "lga", "grade_level", "qualifications",
               "email", "phone"]
PLACEHOLDER_NO = re.compile(r"^USR-\d+$", re.I)   # records auto-made for a user account

MODES = ("update", "skip", "fail")
EMPTY_SHEET_NAME = re.compile(r"^sheet\d+$", re.I)


def header_key(value) -> str:
    return re.sub(r"[^A-Z0-9/_]", "", ref.clean_text(value).upper())


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class DateParse:
    value: date | None = None
    level: str = ""        # "" | "info" | "warning"
    message: str = ""


def _month_from_word(word: str) -> int | None:
    word = word.upper()
    if word[:3] in MONTHS:
        return MONTHS.index(word[:3]) + 1
    if len(word) >= 2:  # 'JA' -> January; only when the prefix is unique
        hits = [i for i, m in enumerate(MONTHS) if m.startswith(word[:3]) or m.startswith(word)]
        if len(hits) == 1:
            return hits[0] + 1
    return None


def _year(token: str) -> tuple[int, str]:
    """Return (year, note). 2-digit: 00-40 -> 20xx, 41-99 -> 19xx."""
    if len(token) == 4:
        return int(token), ""
    if len(token) == 2 or len(token) == 1:
        n = int(token)
        return (2000 + n if n <= 40 else 1900 + n), ""
    if len(token) == 3:  # '234' -> 2024: a stray digit typed inside the year
        n = int(token[0] + token[2])
        return 2000 + n, f"3-digit year '{token}' read as {2000 + n}"
    raise ValueError(token)


def parse_nbte_date(value) -> DateParse:
    """Parse any date spelling found on the NBTE nominal roll.

    Accepts datetime/date objects, Excel serial numbers and strings such as
    '7th Mar. 58', '1st - MAY- 88', '29-3-23', '30-3-234', '2-0CT-26',
    '16th Mar., 21', '5/13/96'. Day-first is assumed when both parts could be
    a month (logged at info level); month-first is used only when the second
    part cannot be a month.
    """
    if value is None:
        return DateParse()
    if isinstance(value, datetime):
        return DateParse(value.date())
    if isinstance(value, date):
        return DateParse(value)
    if isinstance(value, (int, float)):
        if 1000 < value < 80000:
            return DateParse(EXCEL_EPOCH + timedelta(days=int(value)))
        return DateParse(None, "warning", f"number {value!r} is not a date")
    text = ref.clean_text(value).upper()
    if text in ("", "-", "--", "NIL", "N/A", "NA", "NAN", "#REF!", "NONE"):
        return DateParse()
    raw = text
    # ISO date or timestamp
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$", text)
    if m:
        try:
            return DateParse(date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            return DateParse(None, "warning", f"'{raw}' is not a valid date")
    text = re.sub(r"(?<=[A-Z])0|0(?=[A-Z])", "O", text)        # 0CT -> OCT, N0V -> NOV
    text = re.sub(r"(\d+)\s*(ST|ND|RD|TH)\b", r"\1", text)      # 1st -> 1
    tokens = re.findall(r"[A-Z]+|\d+", text)
    words = [t for t in tokens if t.isalpha()]
    nums = [t for t in tokens if t.isdigit()]
    notes = []
    try:
        if words:
            if len(words) != 1 or len(nums) != 2:
                return DateParse(None, "warning", f"'{raw}' is not a date")
            month = _month_from_word(words[0])
            if month is None:
                return DateParse(None, "warning", f"'{raw}' is not a date")
            day_tok, year_tok = nums
            day = int(day_tok)
            year, note = _year(year_tok)
        else:
            if len(nums) != 3:
                return DateParse(None, "warning", f"'{raw}' is not a date")
            a, b, y = nums
            year, note = _year(y)
            day, month = int(a), int(b)
            if month > 12 and day <= 12:
                day, month = month, day
                notes.append(f"'{raw}' read as month/day ({year}-{month:02d}-{day:02d})")
            elif day <= 12 and month <= 12 and day != month:
                notes.append(f"'{raw}' is ambiguous; read as day-month ({day} {MONTHS[month - 1].title()})")
        if note:
            notes.append(note)
        result = date(year, month, day)
    except (ValueError, IndexError):
        return DateParse(None, "warning", f"'{raw}' is not a valid date")
    if any("3-digit" in n or "month/day" in n for n in notes):
        return DateParse(result, "warning", "; ".join(notes))
    if notes:
        return DateParse(result, "info", "; ".join(notes))
    return DateParse(result)


def format_nbte_date(value) -> str:
    """date(1958, 3, 7) -> '7-Mar-58' (the file's own style)."""
    if not value:
        return ""
    return f"{value.day}-{MONTHS[value.month - 1].title()}-{value:%y}"


# ---------------------------------------------------------------------------
# Value cleaning
# ---------------------------------------------------------------------------
def clean_file_no(value) -> str:
    text = ref.clean_text(value)
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def clean_grade(value) -> tuple[str, bool]:
    """-> (grade, valid). '9', 9.0, 'CONHES 06' -> '9' / '6'."""
    text = ref.clean_text(value)
    if not text:
        return "", True
    m = re.fullmatch(r"(?:CON[A-Z]*\.?\s*)?0*(\d{1,2})", text.upper())
    if m:
        return m[1], True
    return text, False


def clean_gender(value) -> tuple[str, bool]:
    text = ref.clean_text(value).upper()
    if not text:
        return "", True
    if text in ("M", "MALE"):
        return "M", True
    if text in ("F", "FEMALE"):
        return "F", True
    return text, False


def _norm_name(value) -> str:
    return " ".join(sorted(re.sub(r"[^A-Z ]", " ", ref.clean_text(value).upper()).split()))


def names_match(a_last, a_first, b_last, b_first) -> bool:
    """Loose comparison: any shared name token counts (the file reorders and
    re-spells names between sheets)."""
    a = set(_norm_name(f"{a_last} {a_first}").split())
    b = set(_norm_name(f"{b_last} {b_first}").split())
    return bool(a & b)


# ---------------------------------------------------------------------------
# Sheet scanning
# ---------------------------------------------------------------------------
@dataclass
class Warning_:
    sheet: str
    row: int
    message: str
    kind: str = "general"     # general | date | duplicate | info
    level: str = "warning"    # warning | info | error

    @property
    def sheet_label(self):
        return self.sheet.strip() or "untitled sheet"

    def as_dict(self):
        return {"sheet": self.sheet, "row": self.row, "message": self.message,
                "kind": self.kind, "level": self.level}


@dataclass
class Record:
    sheet: str
    row: int
    section: str
    data: dict


@dataclass
class SheetScan:
    name: str
    title: str = ""
    order: tuple = (0, 0)
    has_header: bool = False
    rows: list = field(default_factory=list)          # [Record]
    blank_rows: int = 0
    is_empty: bool = True

    @property
    def label(self):
        return self.name if self.name.strip() else f"(untitled sheet: {self.title or 'no title'})"


def _sheet_order(name: str, titles: list[str]) -> tuple:
    """(year, month) from 'NOMINAL ROLL AS AT 1st SEPTEMBER, 2022' etc."""
    for text in [*titles, name]:
        up = text.upper()
        year = re.search(r"(19|20)\d{2}", up)
        if not year:
            continue
        month = 0
        for i, m in enumerate(MONTHS):
            if re.search(rf"\b{m}[A-Z]*\b", up):
                month = i + 1
                break
        return (int(year[0]), month)
    return (0, 0)


def _is_title(text: str) -> bool:
    up = text.upper()
    return any(k in up for k in ("NATIONAL BOARD", "NOMINAL ROLL", "LIST OF"))


def scan_workbook(wb, only_sheets=None) -> list[SheetScan]:
    scans = []
    for ws in wb.worksheets:
        if only_sheets is not None and ws.title not in only_sheets:
            continue
        if EMPTY_SHEET_NAME.match(ws.title.strip()):
            scan = SheetScan(name=ws.title)
            scans.append(scan)
            continue
        scans.append(_scan_sheet(ws))
    return scans


def _header_mapping(keys, previous=None):
    """Column index -> field for one header row.

    Headers are matched by text. A cell that is not a known header (the file
    has repeated header rows with a stray number in SURNAME, a blank where
    D_FAPPMT should be, or a date typed over D_BIRTH) keeps the field the
    previous header had in that column, or else the standard field for that
    position — so one damaged header never drops a column."""
    mapping = {}
    for i, k in enumerate(keys):
        f = HEADER_MAP.get(re.sub(r"[()]", "", k))
        if f and f not in mapping.values():
            mapping[i] = f
    known = set(mapping.values())
    width = max(len(keys), len(STANDARD_FIELDS))
    for i in range(width):
        if i in mapping:
            continue
        fallback = (previous or {}).get(i) or (STANDARD_FIELDS[i] if i < len(STANDARD_FIELDS) else None)
        if fallback and fallback not in known and (i >= len(keys) or keys[i] == "" or
                                                   not HEADER_MAP.get(re.sub(r"[()]", "", keys[i]))):
            mapping[i] = fallback
            known.add(fallback)
    return mapping


def _scan_sheet(ws) -> SheetScan:
    scan = SheetScan(name=ws.title)
    mapping = None
    titles = []
    section = ""
    for rownum, values in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = list(values)
        filled = [(i, c) for i, c in enumerate(cells) if ref.clean_text(c) != ""]
        if not filled:
            scan.blank_rows += 1
            continue
        keys = [header_key(c) for c in cells]
        # Column header (also catches repeated headers mid-sheet)
        if "SURNAME" in keys or (keys and keys[0] in ("S/NO", "SNO")):
            mapping = _header_mapping(keys, mapping)
            scan.has_header = True
            continue
        texts = [ref.clean_text(c) for _, c in filled]
        if len(filled) == 1 and isinstance(filled[0][1], str):
            if _is_title(texts[0]):
                titles.append(texts[0])
                scan.title = scan.title or texts[0]
                if "NATIONAL BOARD" not in texts[0].upper():
                    scan.title = texts[0]
            else:
                section = texts[0]
            continue
        if all(isinstance(c, str) for _, c in filled) and any(_is_title(t) for t in texts):
            titles.extend(texts)
            continue
        if mapping is None:
            # No header row (NEW STAFF 2023): numbered rows in the standard order.
            first = cells[0] if cells else None
            if isinstance(first, (int, float)) and len(cells) >= 10:
                mapping = {i: f for i, f in enumerate(STANDARD_FIELDS)}
            else:
                continue
        data = {}
        serial = ""
        for i, f in mapping.items():
            if i < len(cells):
                if f == "serial":
                    serial = ref.clean_text(cells[i]).upper()
                else:
                    data[f] = cells[i]
        if serial.startswith(("E.G", "EG.", "EXAMPLE")):   # the template's sample row
            continue
        if not any(ref.clean_text(v) for k, v in data.items()):
            scan.blank_rows += 1
            continue
        scan.rows.append(Record(sheet=ws.title, row=rownum, section=section, data=data))
    scan.order = _sheet_order(ws.title, titles)
    scan.is_empty = not scan.rows
    if not scan.has_header and scan.rows:
        scan.title = scan.title or ws.title
    return scan


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass
class SheetStats:
    name: str
    label: str
    title: str = ""
    total_rows: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_existing: int = 0
    superseded: int = 0
    skipped_blank: int = 0
    skipped_invalid: int = 0
    date_warnings: int = 0
    duplicates: int = 0
    lookups: dict = field(default_factory=lambda: {
        "Departments": [], "Designations": [], "States": [], "Staff states": [], "Offices": []})

    @property
    def lookups_total(self):
        return sum(len(v) for v in self.lookups.values())

    @property
    def skipped(self):
        return self.skipped_blank + self.skipped_invalid + self.skipped_existing


@dataclass
class ImportResult:
    filename: str
    mode: str
    dry_run: bool
    sheets: list = field(default_factory=list)       # [SheetStats] in processing order
    ignored_sheets: list = field(default_factory=list)
    warnings: list = field(default_factory=list)     # [Warning_]
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    aborted: str = ""
    staff_before: int = 0
    staff_after: int = 0
    missing_from_latest: list = field(default_factory=list)
    session: ImportSession | None = None
    linked_accounts: list = field(default_factory=list)   # [(placeholder no, file no, name)]
    accounts_planned: int = 0
    accounts_with_email: int = 0
    accounts_created: int = 0
    accounts_emailed: int = 0
    credentials: list = field(default_factory=list)

    @property
    def status(self):
        if self.aborted:
            return "failed"
        return "partial" if self.errors else "ok"

    @property
    def date_warnings(self):
        return [w for w in self.warnings if w.kind == "date" and w.level == "warning"]

    @property
    def duplicate_warnings(self):
        return [w for w in self.warnings if w.kind == "duplicate"]

    @property
    def other_warnings(self):
        return [w for w in self.warnings
                if w.kind not in ("date", "duplicate") and w.level != "info"]

    @property
    def info_notes(self):
        return [w for w in self.warnings if w.level == "info"]

    def report_text(self) -> str:
        return render_report(self)


class ImportAborted(Exception):
    pass


# ---------------------------------------------------------------------------
# Lookup resolution (shared with seed_nbte)
# ---------------------------------------------------------------------------
class LookupResolver:
    """Find-or-create Department / Designation / StateOfOrigin / StaffState.

    Existing records are never modified. Created ones are recorded in
    `created[kind]` so the report can list them."""

    def __init__(self):
        self.created = defaultdict(list)
        self._dept_alias = ref.department_alias_index()
        self._dept_cache = {}
        self._desig_cache = None
        self._state_cache = {}
        self._staffstate_cache = {}
        self.designation_spellings = defaultdict(Counter)   # key -> Counter(raw spellings)
        self.origin = {}                                     # (kind, name) -> sheet

    # Departments --------------------------------------------------------
    def department_name(self, column_value, section) -> str:
        """Canonical department name from the DEPARTMT cell and the section
        header above the row — whichever is more specific."""
        col = ref.clean_text(column_value)
        sec = ref.clean_text(section)
        col_key = ref.compact_key(col)
        if col and not col_key.isdigit():
            canon = self._dept_alias.get(col_key, col.upper() if col.isupper() else col)
            if ref.compact_key(canon) == "ZONALOFFICE":
                zonal = ref.zonal_office_from(sec)
                if zonal:
                    return zonal
            return canon
        if sec:
            zonal = ref.zonal_office_from(sec)
            if zonal:
                return zonal
            return self._dept_alias.get(ref.compact_key(sec), sec)
        return ""

    def department(self, name: str):
        if not name:
            return None
        key = ref.compact_key(name)
        if key in self._dept_cache:
            return self._dept_cache[key]
        obj = Department.objects.filter(name__iexact=name).first()
        if obj is None:
            code = self._department_code(name)
            obj = Department.objects.filter(code=code).first() if code else None
        if obj is None:
            for _n, _c, title, aliases in ref.DEPARTMENTS:
                if _n == name:
                    obj = Department.objects.filter(name__iexact=title).first() or next(
                        (Department.objects.filter(name__iexact=a).first() for a in aliases
                         if Department.objects.filter(name__iexact=a).exists()), None)
                    break
        if obj is None:
            # sort_order 500: after the seeded NBTE departments in lists and exports
            obj = Department(name=name, code=self._unique_dept_code(name), sort_order=500)
            obj.save()
            self.created["Departments"].append(name)
        self._dept_cache[key] = obj
        return obj

    @staticmethod
    def _department_code(name):
        for n, code, _t, _a in ref.DEPARTMENTS:
            if n == name:
                return code
        return ""

    @staticmethod
    def _unique_dept_code(name):
        from django.utils.text import slugify
        base = LookupResolver._department_code(name) or slugify(name.replace("&", " and "))[:44] or "dept"
        code, n = base, 2
        while Department.objects.filter(code=code).exists():
            code, n = f"{base}-{n}", n + 1
        return code

    # Designations -------------------------------------------------------
    def _load_designations(self):
        self._desig_cache = {}
        for d in Designation.objects.all():
            for spelling in [d.name, *d.alias_list]:
                k = ref.designation_key(spelling)
                if k and k not in self._desig_cache:
                    self._desig_cache[k] = d

    def note_designation(self, raw):
        k = ref.designation_key(raw)
        if k:
            self.designation_spellings[k][ref.clean_text(raw)] += 1

    def designation(self, raw):
        if self._desig_cache is None:
            self._load_designations()
        key = ref.designation_key(raw)
        if not key:
            return None
        obj = self._desig_cache.get(key)
        if obj is not None:
            return obj
        spellings = self.designation_spellings.get(key) or Counter({ref.clean_text(raw): 1})
        ordered = [s for s, _ in sorted(spellings.items(), key=lambda kv: (-kv[1], kv[0]))]
        name = ordered[0]
        if Designation.objects.filter(name__iexact=name).exists():  # differently-keyed twin
            obj = Designation.objects.filter(name__iexact=name).first()
        else:
            obj = Designation(name=name, rank=0, aliases="\n".join(ordered[1:]))
            obj.save()
            self.created["Designations"].append(name)
        self._desig_cache[key] = obj
        return obj

    # States of origin ---------------------------------------------------
    def state_of_origin(self, raw):
        text = ref.clean_text(raw)
        if not text:
            return None
        canon = ref.canonical_state(text) or text
        key = ref.compact_key(canon)
        if key in self._state_cache:
            return self._state_cache[key]
        obj = StateOfOrigin.objects.filter(name__iexact=canon).first()
        if obj is None:
            zone = next((z for n, _c, z in ref.STATES if n == canon), "")
            code = next((c for n, c, _z in ref.STATES if n == canon), None)
            if code and StateOfOrigin.objects.filter(code=code).exists():
                code = None
            obj = StateOfOrigin(name=canon, code=code, zone=zone)
            obj.save()
            self.created["States"].append(canon)
        self._state_cache[key] = obj
        return obj

    # Offices -------------------------------------------------------------
    def office(self, raw):
        """v2.2: an office column names a unit in the Department list."""
        from .models import Department, Office
        from .office_merge import _kind_and_parent_code
        name = ref.clean_text(raw)
        if not name:
            return None
        merged = Office.objects.filter(name__iexact=name, merged_into__isnull=False).select_related("merged_into").first()
        obj = merged.merged_into if merged else Department.objects.filter(name__iexact=name).first()
        if obj is None:
            kind, parent_code = _kind_and_parent_code(name)
            obj = Department.objects.create(name=name, kind=kind, sort_order=500,
                                            parent=Department.objects.filter(code=parent_code).first()
                                            if parent_code else None)
            self.created["Offices"].append(name)
        return obj

    # Staff states (REMARKS) ----------------------------------------------
    def staff_state(self, name):
        if not name:
            return None
        key = name.lower()
        if key in self._staffstate_cache:
            return self._staffstate_cache[key]
        obj = StaffState.objects.filter(name__iexact=name).first()
        if obj is None:
            obj = StaffState(name=name)
            obj.save()
            self.created["Staff states"].append(name)
        self._staffstate_cache[key] = obj
        return obj


# ---------------------------------------------------------------------------
# The importer
# ---------------------------------------------------------------------------
class NominalRollImporter:
    """One import run. Usage:

        imp = NominalRollImporter(path_or_fileobj, filename="roll.xlsx",
                                  sheets=None, mode="update", actor=user)
        result = imp.run(dry_run=True)
    """

    def __init__(self, source, *, filename="", sheets=None, mode="update", actor=None, accounts=None):
        """accounts: None, or {"scheme": "surname_fileno"|"shared"|"random",
        "password": "<for shared>"} to give every imported person without a
        login an account (staff.account_provisioning)."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if accounts:
            from .account_provisioning import SCHEMES
            if accounts.get("scheme") not in SCHEMES:
                raise ValueError("Unknown password scheme.")
            if accounts["scheme"] == "shared" and len(accounts.get("password") or "") < 8:
                raise ValueError("The starter password must be at least 8 characters.")
        self.accounts = accounts or None
        self.source = source
        self.filename = filename or getattr(source, "name", "") or str(source)
        self.only_sheets = set(sheets) if sheets else None
        self.mode = mode
        self.actor = actor if getattr(actor, "pk", None) else None
        self._scans = None

    # -- reading -----------------------------------------------------------
    def _load(self):
        import openpyxl
        src = self.source
        if hasattr(src, "read"):
            if hasattr(src, "seek"):
                src.seek(0)
            src = io.BytesIO(src.read())
        return openpyxl.load_workbook(src, read_only=True, data_only=True)

    def scan(self) -> list[SheetScan]:
        if self._scans is None:
            wb = self._load()
            try:
                self._scans = scan_workbook(wb)
            finally:
                wb.close()
        return self._scans

    def sheet_choices(self):
        """[(name, label, data_rows)] for the sheets that hold data, in file order."""
        return [(s.name, s.label, len(s.rows)) for s in self.scan() if not s.is_empty]

    # -- running -------------------------------------------------------------
    def run(self, dry_run=True) -> ImportResult:
        result = ImportResult(filename=self.filename, mode=self.mode, dry_run=dry_run)
        scans = self.scan()
        chosen = [s for s in scans if not s.is_empty and
                  (self.only_sheets is None or s.name in self.only_sheets)]
        result.ignored_sheets = [s.label for s in scans if s not in chosen]
        chosen.sort(key=lambda s: s.order)        # oldest first; later sheets win
        result.staff_before = Staff.objects.count()
        previous_actor = audit.get_current_actor()
        audit.set_current_actor(self.actor)
        try:
            with transaction.atomic():
                self._apply(chosen, result)
                self._accounts(result, dry_run)
                result.staff_after = Staff.objects.count()
                if dry_run:
                    transaction.set_rollback(True)
        except ImportAborted as exc:
            result.aborted = str(exc)
            result.errors += 1
            result.staff_after = result.staff_before
        except Exception as exc:  # never leave a partial import
            where = getattr(exc, "_nr_where", "")
            result.aborted = f"{where}{type(exc).__name__}: {exc}"
            result.errors += 1
            result.staff_after = result.staff_before
        finally:
            audit.set_current_actor(previous_actor)
        if not dry_run:
            result.session = self._record_session(result, chosen)
        return result

    # -- the work ------------------------------------------------------------
    def _apply(self, chosen, result):
        resolver = LookupResolver()
        stats = OrderedDict((s.name, SheetStats(name=s.name, label=s.label, title=s.title))
                            for s in chosen)
        result.sheets = list(stats.values())
        warn = result.warnings

        # 1. parse every row ------------------------------------------------
        people = OrderedDict()   # merge key -> {"rec": merged dict, "src": (sheet,row), "rows": [...]}
        name_index = defaultdict(set)   # normalised full name -> {merge keys}
        for scan in chosen:
            st = stats[scan.name]
            seen_in_sheet = {}
            for rec in scan.rows:
                st.total_rows += 1
                parsed = self._parse_record(rec, resolver, warn, st)
                if parsed is None:
                    st.skipped_blank += 1
                    continue
                emp = parsed.get("employee_no", "")
                who = f"{parsed['last_name']} {parsed['first_name']}".strip()
                name_key = _norm_name(who)
                if not re.search(r"\d", emp):
                    # No usable FILE NO: merge with the same name seen earlier in the
                    # file, or (at write time) with a unique existing record.
                    shown = f"'{emp}'" if emp else "blank"
                    twins = name_index.get(name_key, set())
                    if len(twins) == 1:
                        key = next(iter(twins))
                        warn.append(Warning_(
                            rec.sheet, rec.row,
                            f"{who}: FILE NO is {shown} — matched by name to FILE NO "
                            f"{people[key]['rec']['employee_no']}. Fill in the FILE NO in the file."))
                    else:
                        key = "NAME:" + name_key
                        warn.append(Warning_(
                            rec.sheet, rec.row,
                            f"{who}: FILE NO is {shown} — the row can only update an existing record "
                            "with the same name. Fill in the FILE NO in the file."))
                    parsed["employee_no"] = "" if key.startswith("NAME:") else people[key]["rec"]["employee_no"]
                else:
                    key = emp.upper()
                if key in seen_in_sheet:
                    prev = seen_in_sheet[key]
                    if not names_match(prev["last_name"], prev["first_name"],
                                       parsed["last_name"], parsed["first_name"]):
                        st.duplicates += 1
                        warn.append(Warning_(
                            rec.sheet, rec.row,
                            f"Duplicate FILE NO {emp}: {parsed['last_name']} {parsed['first_name']} "
                            f"(row {rec.row}) has the same FILE NO as {prev['last_name']} "
                            f"{prev['first_name']} (row {prev['_row']}). This row was skipped — "
                            "correct the FILE NO and import again.", kind="duplicate"))
                        st.skipped_invalid += 1
                        continue
                parsed["_row"] = rec.row
                seen_in_sheet[key] = parsed
                if not key.startswith("NAME:"):
                    name_index[name_key].add(key)
                entry = people.get(key)
                if entry is None:
                    people[key] = {"rec": dict(parsed), "src": (rec.sheet, rec.row),
                                   "sheets": [rec.sheet]}
                    continue
                old = entry["rec"]
                if entry["src"][0] != rec.sheet and not names_match(
                        old["last_name"], old["first_name"], parsed["last_name"], parsed["first_name"]):
                    warn.append(Warning_(
                        rec.sheet, rec.row,
                        f"FILE NO {emp} is {old['last_name']} {old['first_name']} on "
                        f"'{entry['src'][0].strip() or 'untitled sheet'}' but {parsed['last_name']} "
                        f"{parsed['first_name']} here — the newer sheet is used.", kind="duplicate"))
                    st.duplicates += 1
                if entry["src"][0] != rec.sheet:
                    stats[entry["src"][0]].superseded += 1
                for k, v in parsed.items():
                    if v not in (None, ""):
                        old[k] = v
                entry["src"] = (rec.sheet, rec.row)
                entry["sheets"].append(rec.sheet)

        # 2. people absent from the latest full roll ------------------------
        full = [s for s in chosen if re.search(r"NOMINAL ROLL AS AT", (s.title or "").upper())]
        if full:
            latest = full[-1]
            on_latest = {clean_file_no(r.data.get("employee_no")).upper() for r in latest.rows}
            latest_names = {_norm_name(f"{ref.clean_text(r.data.get('last_name'))} "
                                       f"{ref.clean_text(r.data.get('first_name'))}") for r in latest.rows}
            for key, entry in people.items():
                full_name = f"{entry['rec']['last_name']} {entry['rec']['first_name']}"
                if key not in on_latest and _norm_name(full_name) not in latest_names:
                    result.missing_from_latest.append(
                        (entry["rec"]["employee_no"],
                         f"{entry['rec']['last_name']} {entry['rec']['first_name']}".strip(),
                         entry["src"][0].strip() or "untitled sheet"))

        # 3. resolve lookups + write ------------------------------------------
        fail_hits = []
        self._created_pks = set()
        self._touched = set()
        self._claimed = set()
        self._linked = result.linked_accounts
        self._incoming_nos = {e["rec"]["employee_no"] for e in people.values() if e["rec"]["employee_no"]}
        for key, entry in people.items():
            data = entry["rec"]
            sheet, row = entry["src"]
            st = stats[sheet]
            try:
                outcome = self._upsert(data, sheet, row, resolver, warn, st, fail_hits)
            except ImportAborted:
                raise
            except Exception as exc:
                exc._nr_where = f"Sheet '{sheet.strip() or 'untitled'}', row {row} (FILE NO {data.get('employee_no')}): "
                raise
            if outcome == "new":
                st.new += 1
            elif outcome == "updated":
                st.updated += 1
            elif outcome == "unchanged":
                st.unchanged += 1
            elif outcome == "skipped":
                st.skipped_existing += 1
        if fail_hits:
            sample = ", ".join(fail_hits[:10])
            more = f" and {len(fail_hits) - 10} more" if len(fail_hits) > 10 else ""
            raise ImportAborted(
                f"'Fail on any duplicate' is selected and {len(fail_hits)} row(s) match staff "
                f"already in the system (FILE NO {sample}{more}). Nothing was imported.")

        # attribute created lookups to the sheet that introduced them
        for kind, names in resolver.created.items():
            for name in names:
                target = stats.get(resolver.origin.get((kind, name))) or (
                    result.sheets[0] if result.sheets else None)
                if target is not None:
                    target.lookups[kind].append(name)

        result.new = sum(s.new for s in result.sheets)
        result.updated = sum(s.updated for s in result.sheets)
        result.unchanged = sum(s.unchanged for s in result.sheets)
        result.skipped = sum(s.skipped for s in result.sheets)

    # -- one row -------------------------------------------------------------
    def _parse_record(self, rec: Record, resolver, warn, st):
        d = rec.data
        out = {
            "employee_no": clean_file_no(d.get("employee_no")),
            "last_name": ref.clean_text(d.get("last_name")),
            "first_name": ref.clean_text(d.get("first_name")),
            "lga": ref.clean_text(d.get("lga")),
            "qualifications": ref.clean_text(d.get("qualifications")),
            "phone": ref.clean_text(d.get("phone")),
            "office": ref.clean_text(d.get("office")),
        }
        if not (out["last_name"] or out["first_name"]):
            return None
        email = ref.clean_text(d.get("email")).lower()
        if email and not EMAIL_RE.match(email):
            warn.append(Warning_(rec.sheet, rec.row,
                                 f"{out['last_name']} {out['first_name']}: EMAIL '{email}' is not an email "
                                 "address — ignored"))
            email = ""
        out["email"] = email
        who = f"{out['last_name']} {out['first_name']}".strip()

        gender, ok = clean_gender(d.get("gender"))
        if not ok:
            warn.append(Warning_(rec.sheet, rec.row, f"{who}: SEX '{gender}' is not M or F — ignored"))
            gender = ""
        out["gender"] = gender

        grade, grade_ok = clean_grade(d.get("grade_level"))
        designation_raw = ref.clean_text(d.get("designation"))
        dept_raw = d.get("department")
        if not grade_ok:
            # A row shifted one cell right: DESIGNATION | <new designation> | <grade>.
            shifted_grade, sg_ok = clean_grade(dept_raw)
            if ref.designation_key(grade) and sg_ok and shifted_grade:
                warn.append(Warning_(
                    rec.sheet, rec.row,
                    f"{who}: CONTEDISS/CONHESS holds '{grade}' and DEPARTMT holds '{shifted_grade}' — "
                    f"read as designation '{grade}', level {shifted_grade}, department from the "
                    "section heading."))
                designation_raw, grade, dept_raw = grade, shifted_grade, None
            else:
                warn.append(Warning_(rec.sheet, rec.row,
                                     f"{who}: CONTEDISS/CONHESS '{grade}' is not a level — ignored"))
                grade = ""
        out["grade_level"] = grade

        if designation_raw and not ref.designation_key(designation_raw):
            warn.append(Warning_(rec.sheet, rec.row,
                                 f"{who}: DESIGNATION '{designation_raw}' is not a post title — ignored"))
            designation_raw = ""
        out["designation"] = designation_raw
        if designation_raw:
            resolver.note_designation(designation_raw)

        out["department"] = resolver.department_name(dept_raw, rec.section)
        state_raw = ref.clean_text(d.get("state_of_origin"))
        if state_raw and not ref.canonical_state(state_raw):
            warn.append(Warning_(rec.sheet, rec.row,
                                 f"{who}: ST_ORIGIN '{state_raw}' is not a Nigerian state — kept as written",
                                 level="info"))
        out["state_of_origin"] = state_raw
        out["remarks"] = ref.canonical_remark(d.get("remarks"))

        today = timezone.localdate()
        for f in DATE_FIELDS:
            res = parse_nbte_date(d.get(f))
            value = res.value
            if res.level:
                kind = "date"
                warn.append(Warning_(rec.sheet, rec.row, f"{who}: {DATE_LABELS[f]} {res.message}",
                                     kind=kind, level=res.level))
                if res.level == "warning":
                    st.date_warnings += 1
            if value is not None:
                if f == "date_of_birth" and not (1930 <= value.year <= today.year - 15):
                    warn.append(Warning_(rec.sheet, rec.row,
                                         f"{who}: D_BIRTH {value:%Y-%m-%d} is not a plausible birth date — "
                                         "not stored", kind="date"))
                    st.date_warnings += 1
                    value = None
                elif f != "date_of_birth" and value > today + timedelta(days=366 * 3):
                    warn.append(Warning_(rec.sheet, rec.row,
                                         f"{who}: {DATE_LABELS[f]} {value:%Y-%m-%d} is far in the future — "
                                         "not stored", kind="date"))
                    st.date_warnings += 1
                    value = None
            out[f] = value
        return out

    def _match(self, data):
        """FILE NO first; then surname + other name + date of birth; then a
        unique surname + other name. The name fallbacks only consider records
        that existed before this run and whose FILE NO is not in the file —
        two people who share a name never collapse into one record."""
        emp = data["employee_no"]
        if emp:
            obj = Staff.all_objects.filter(employee_no__iexact=emp).first()
            if obj is not None:
                return obj
        last, first, dob = data["last_name"], data["first_name"], data.get("date_of_birth")
        qs = (Staff.objects.filter(last_name__iexact=last, first_name__iexact=first)
              .filter(nbte_staff_records_q())       # never adopt a portal account's record
              .exclude(pk__in=self._created_pks)
              .exclude(employee_no__in=self._incoming_nos))
        if dob:
            hit = list(qs.filter(date_of_birth=dob)[:2])
            if len(hit) == 1:
                return hit[0]
        hit = list(qs[:2])
        if len(hit) == 1:
            return hit[0]
        if data.get("email"):
            hit = list(Staff.objects.filter(email__iexact=data["email"]).filter(nbte_staff_records_q())
                       .exclude(pk__in=self._claimed)[:2])
            if len(hit) == 1:
                return hit[0]
        return self._placeholder_match(data)

    def _placeholder_match(self, data):
        """A directory record made automatically for a user account (USR-…)
        with the same surname and a shared other name — the person's own
        account, made before the roll was imported. Adopted only when exactly
        one such record fits."""
        last = data["last_name"]
        if not last:
            return None
        wanted = set(_norm_name(data["first_name"]).split())
        cands = [s for s in Staff.objects.filter(last_name__iexact=last, user__isnull=False,
                                                 user__user_type="staff")
                 .exclude(pk__in=self._claimed)
                 if PLACEHOLDER_NO.match(s.employee_no or "")
                 and wanted & set(_norm_name(s.first_name).split())]
        if len(cands) == 1:
            cands[0]._adopted_from = cands[0].employee_no
            return cands[0]
        return None

    def _upsert(self, data, sheet, row, resolver, warn, st, fail_hits):
        obj = self._match(data)
        who = f"{data['last_name']} {data['first_name']}".strip()
        if obj is not None and obj.is_deleted:
            warn.append(Warning_(sheet, row,
                                 f"{who}: FILE NO {data['employee_no']} belongs to a deleted record — "
                                 "restore it in the admin first; row skipped"))
            return "skipped"
        if obj is not None and self.mode == "skip":
            return "skipped"
        if obj is not None and self.mode == "fail":
            fail_hits.append(data["employee_no"])
            return "skipped"

        if obj is not None:
            self._claimed.add(obj.pk)
        values = {f: data.get(f, "") for f in TEXT_FIELDS}
        if values.get("email") and obj is not None and obj.user_id and obj.email:
            values["email"] = ""          # an account's email is changed on the account, not here
        for f in DATE_FIELDS:
            values[f] = data.get(f)
        before = {k: list(v) for k, v in resolver.created.items()}

        values["department"] = resolver.department(data.get("department"))
        values["designation"] = resolver.designation(data.get("designation"))
        values["state_of_origin"] = resolver.state_of_origin(data.get("state_of_origin"))
        # v2.2: an office/unit column posts the person to that unit (one list).
        unit = resolver.office(data.get("office"))
        if unit is not None:
            values["department"] = unit
        remark_state = resolver.staff_state(data.get("remarks"))
        for kind, names in resolver.created.items():
            for name in names[len(before.get(kind, [])):]:
                resolver.origin[(kind, name)] = sheet

        creating = obj is None
        if creating and not data["employee_no"]:
            warn.append(Warning_(sheet, row, f"{who}: no FILE NO and no existing record with this "
                                             "name — row skipped"))
            return "skipped"
        if creating:
            obj = Staff(employee_no=data["employee_no"])
            values["state"] = remark_state or resolver.staff_state("Confirmed")
        else:
            values["state"] = remark_state

        changed = []
        for f, v in values.items():
            if v in (None, ""):
                continue            # empty incoming never clobbers
            current = getattr(obj, f)
            if f in ("department", "designation", "state_of_origin", "state", "office"):
                if getattr(obj, f"{f}_id") != v.pk:
                    setattr(obj, f, v)
                    changed.append(f)
            elif current != v:
                setattr(obj, f, v)
                changed.append(f)
        dob = values.get("date_of_birth")
        if dob and not obj.birth_month and not obj.birth_day:   # keep the legacy pair in step
            obj.birth_month, obj.birth_day = dob.month, dob.day
            changed.append("birth_month")
        adopted = getattr(obj, "_adopted_from", "")
        if adopted and data["employee_no"]:
            obj.employee_no = data["employee_no"]
            changed.append("employee_no")
            self._linked.append((adopted, data["employee_no"], who))
            warn.append(Warning_(sheet, row, f"{who}: merged with the account record {adopted} "
                                             f"({obj.first_name} {obj.last_name}) — it now has FILE NO "
                                             f"{data['employee_no']}. Check it is the same person.",
                                 kind="general", level="info"))
        if obj.pk:
            self._touched.add(obj.pk)
        if not creating and not changed:
            return "unchanged"
        obj._transfer_source = "import"
        obj._state_changed_by = self.actor
        obj._state_change_reason = f"Nominal roll import ({sheet.strip() or 'untitled sheet'}, row {row})"
        obj.save()
        if creating:
            self._created_pks.add(obj.pk)
        self._touched.add(obj.pk)
        audit.log("import_nominal_roll", obj, actor=self.actor,
                  changes={"source": sheet, "row": row, "created": creating,
                           "fields": sorted(set(changed))},
                  note=self.filename[:200])
        return "new" if creating else "updated"

    def _accounts(self, result, dry_run):
        if not self.accounts:
            return
        qs = Staff.objects.filter(pk__in=self._touched, user__isnull=True).exclude(employee_no="")
        result.accounts_planned = qs.count()
        result.accounts_with_email = qs.exclude(email="").count()
        if dry_run or not result.accounts_planned:
            return
        from .account_provisioning import create_accounts
        created, creds, warns, emailed = create_accounts(
            qs, scheme=self.accounts["scheme"], shared_password=self.accounts.get("password", ""),
            actor=self.actor, send_email=bool(self.accounts.get("email")),
            login_url=self.accounts.get("login_url", ""))
        result.accounts_created = created
        result.accounts_emailed = emailed
        result.credentials = creds
        for w in warns:
            result.warnings.append(Warning_("Accounts", 0, w))

    # -- bookkeeping -------------------------------------------------------
    def _record_session(self, result, chosen):
        session = ImportSession(
            source_filename=self.filename.rsplit("/", 1)[-1][:255],
            uploaded_by=self.actor,
            sheets_processed=[s.label for s in result.sheets] or [s.label for s in chosen],
            new_count=result.new if not result.aborted else 0,
            updated_count=result.updated if not result.aborted else 0,
            skipped_count=result.skipped,
            error_count=result.errors,
            warnings=[w.as_dict() for w in result.warnings if w.level != "info"][:2000],
            status=result.status,
            mode=self.mode,
            accounts_created=result.accounts_created,
        )
        with audit_actor(self.actor):
            session.save()
            session.report_file.save("import-report.txt",
                                     ContentFile(result.report_text().encode("utf-8")), save=True)
            if result.credentials:
                from .account_provisioning import credentials_xlsx
                session.credentials_file.save("sign-in-details.xlsx",
                                              ContentFile(credentials_xlsx(result.credentials)), save=True)
        return session


class audit_actor:
    """Context manager: attribute auto-audit entries to `user`."""

    def __init__(self, user):
        self.user = user

    def __enter__(self):
        self.prev = audit.get_current_actor()
        audit.set_current_actor(self.user)

    def __exit__(self, *exc):
        audit.set_current_actor(self.prev)
        return False


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _short(names, limit=8):
    if not names:
        return ""
    head = ", ".join(names[:limit])
    return f" ({head}{', …' if len(names) > limit else ''})"


def render_report(result: ImportResult) -> str:
    out = []
    w = out.append
    w("NBTE nominal roll import — " + ("DRY RUN (nothing saved)" if result.dry_run else "IMPORT"))
    w(f"File: {result.filename}")
    w(f"Mode: {dict(update='Update existing', skip='Skip existing', fail='Fail on any duplicate')[result.mode]}")
    w(f"Run at: {timezone.localtime():%Y-%m-%d %H:%M}")
    w("")
    if result.aborted:
        w(f"IMPORT STOPPED — nothing was saved.\n  {result.aborted}")
        w("")
    w("Sheets are applied oldest first; a later sheet overrides an earlier one for the same FILE NO.")
    w("")
    for s in result.sheets:
        w(f"Sheet: {s.label}")
        if s.title:
            w(f"  Title: {s.title}")
        w(f"  Total rows: {s.total_rows}")
        w(f"  New staff: {s.new}")
        w(f"  Updated: {s.updated}")
        w(f"  Unchanged: {s.unchanged}")
        w(f"  Superseded by a newer sheet: {s.superseded}")
        if s.skipped_existing:
            w(f"  Skipped (already in the system): {s.skipped_existing}")
        w(f"  Skipped (blank): {s.skipped_blank}")
        if s.skipped_invalid:
            w(f"  Skipped (invalid / duplicate): {s.skipped_invalid}")
        w("  Lookups created:")
        for kind in ("Departments", "Designations", "States", "Staff states", "Offices"):
            names = s.lookups.get(kind, [])
            w(f"    {kind}: {len(names)}{_short(names)}")
        w(f"  Date warnings: {s.date_warnings} (see warnings section)")
        w(f"  Duplicate FILE NOs within the file: {s.duplicates} (see warnings section)")
        w("")
    if result.ignored_sheets:
        w("Sheets not imported (empty or not selected): " + ", ".join(
            repr(n) for n in result.ignored_sheets))
        w("")
    w("Totals")
    w(f"  Staff before: {result.staff_before}   after: {result.staff_after}")
    w(f"  New: {result.new}   Updated: {result.updated}   Unchanged: {result.unchanged}   "
      f"Skipped: {result.skipped}   Errors: {result.errors}")
    if result.accounts_planned or result.accounts_created:
        if result.dry_run:
            w(f"  Sign-in accounts that would be created: {result.accounts_planned} "
              f"({result.accounts_with_email} with an email address)")
        else:
            w(f"  Sign-in accounts created: {result.accounts_created}; welcome emails queued: "
              f"{result.accounts_emailed}. Staff sign in with their email (or FILE NO if none yet) "
              "and must change the password at first sign-in.")
    w("")
    if result.linked_accounts:
        w(f"Merged with existing account records ({len(result.linked_accounts)})")
        for old, new, name in result.linked_accounts:
            w(f"  {old} -> FILE NO {new}  {name}")
        w("")

    def section(title, items):
        w(f"{title} ({len(items)})")
        for it in items:
            w(f"  [{it.sheet.strip() or 'untitled sheet'} row {it.row}] {it.message}")
        if not items:
            w("  none")
        w("")

    section("Date warnings", result.date_warnings)
    section("Duplicate FILE NOs", result.duplicate_warnings)
    section("Other warnings", result.other_warnings)
    if result.missing_from_latest:
        w(f"Not on the latest full nominal roll ({len(result.missing_from_latest)}) — imported from an "
          "older sheet; they may have left the Board. Review and set their state if so.")
        for emp, name, sheet in result.missing_from_latest:
            w(f"  {emp}  {name}  (last seen on '{sheet}')")
        w("")
    section("Notes (ambiguous dates read day-first, etc.)", result.info_notes)
    return "\n".join(out)
