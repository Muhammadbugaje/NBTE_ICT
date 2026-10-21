"""Excel import / export of professional bodies and programme mappings.

One workbook, two sheets:

* **Professional bodies** — Name, Acronym, Address, Contact person, E-mail,
  Phone, Active.
* **Programme mappings** — Professional body (name or acronym), Programme,
  Level (ND, HND … or blank for every level), Note, Active.
* **Reference** (template only) — the valid levels and the bodies on file.

Import rules (the same as the institution importer):

* the whole workbook is validated first; one bad row rejects the file and the
  officer gets every error with its sheet and row number;
* rows match existing records (bodies by name, then acronym; mappings by body
  + programme + level), so re-importing an unchanged export changes nothing;
* a valid file is applied in one transaction. ``dry_run`` checks without
  saving.
"""
# scoped-by-caller (NBTE-wide reference data, no institution rows)
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .models import ProfessionalBody, ProgrammeBodyMapping, ProgrammeLevel, programme_key

BODY_SHEET, MAP_SHEET, REF_SHEET = "Professional bodies", "Programme mappings", "Reference"
BODY_COLUMNS = [
    ("Name", "name", True, "Full name, e.g. Pharmacy Council of Nigeria."),
    ("Acronym", "acronym", False, "e.g. PCN."),
    ("Address", "address", False, ""),
    ("Contact person", "contact_person", False, "Usually the Registrar."),
    ("E-mail", "email", False, "Joint QA notices are sent here."),
    ("Phone", "phone", False, ""),
    ("Active", "is_active", False, "Yes or No. Blank = Yes."),
]
MAP_COLUMNS = [
    ("Professional body", "body", True, "Name or acronym of a body on the first sheet or already on file."),
    ("Programme", "programme_name", True, "Curriculum programme name, e.g. Pharmacy Technician."),
    ("Level", "level", False, "ND, HND, NID, NVC — blank = every level."),
    ("Note", "note", False, ""),
    ("Active", "is_active", False, "Yes or No. Blank = Yes."),
]
MAX_ROWS = 5000

_HEAD_FILL = PatternFill("solid", fgColor="1F6B3E")
_HEAD_FONT = Font(bold=True, color="FFFFFF")


@dataclass
class ImportResult:
    errors: list = field(default_factory=list)  # [{"sheet", "row", "message"}]
    bodies_created: int = 0
    bodies_updated: int = 0
    bodies_unchanged: int = 0
    mappings_created: int = 0
    mappings_updated: int = 0
    mappings_unchanged: int = 0
    dry_run: bool = False

    @property
    def ok(self):
        return not self.errors

    @property
    def changed(self):
        return self.bodies_created + self.bodies_updated + self.mappings_created + self.mappings_updated


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


def _bool(value, default=True):
    text = _cell(value).lower()
    if text == "":
        return default
    if text in ("yes", "y", "true", "1", "active"):
        return True
    if text in ("no", "n", "false", "0", "inactive"):
        return False
    raise ValueError("Use Yes or No.")


def _level(value):
    text = _norm(value)
    if not text:
        return None
    for level in ProgrammeLevel.objects.all():
        if text in (_norm(level.code), _norm(level.name)):
            return level
    raise ValueError(f"Unknown level “{_cell(value)}”. Use ND, HND, NID or NVC, or leave it blank.")


# ---------------------------------------------------------------------------
# Template and export
# ---------------------------------------------------------------------------
def _sheet(wb, title, columns, rows, *, first=False):
    ws = wb.active if first else wb.create_sheet(title)
    ws.title = title
    for i, (header, _key, required, help_text) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i, value=header + (" *" if required else ""))
        cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = max(len(header) + 6, 26 if i <= 2 else 18)
        if help_text:
            from openpyxl.comments import Comment

            cell.comment = Comment(help_text, "NBTE")
    for r, values in enumerate(rows, start=2):
        for c, value in enumerate(values, start=1):
            ws.cell(row=r, column=c, value=value)
    ws.freeze_panes = "A2"
    return ws


def _workbook(body_rows, map_rows, *, with_reference):
    wb = Workbook()
    _sheet(wb, BODY_SHEET, BODY_COLUMNS, body_rows, first=True)
    ws_map = _sheet(wb, MAP_SHEET, MAP_COLUMNS, map_rows)
    yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    wb[BODY_SHEET].add_data_validation(yes_no)
    yes_no.add(f"G2:G{MAX_ROWS}")
    yes_no_map = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    ws_map.add_data_validation(yes_no_map)
    yes_no_map.add(f"E2:E{MAX_ROWS}")
    levels = [lv.code.upper() for lv in ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin")]
    if with_reference:
        ref = wb.create_sheet(REF_SHEET)
        ref["A1"], ref["B1"] = "Levels", "Professional bodies on file"
        for cell in (ref["A1"], ref["B1"]):
            cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
        for i, code in enumerate(levels, start=2):
            ref.cell(row=i, column=1, value=code)
        for i, body in enumerate(ProfessionalBody.objects.order_by("name"), start=2):
            ref.cell(row=i, column=2, value=body.name)
        ref.column_dimensions["A"].width, ref.column_dimensions["B"].width = 12, 48
    if levels:
        level_dv = DataValidation(type="list", formula1='"' + ",".join(levels) + '"', allow_blank=True)
        ws_map.add_data_validation(level_dv)
        level_dv.add(f"C2:C{MAX_ROWS}")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def template_xlsx():
    example_bodies = [["Pharmacy Council of Nigeria", "PCN", "Plot 7/9 Industrial Layout, Idu, Abuja",
                       "The Registrar", "registrar@example.org", "", "Yes"]]
    example_maps = [["PCN", "Pharmacy Technician", "ND", "Joint accreditation visit", "Yes"]]
    return _workbook(example_bodies, example_maps, with_reference=True)


def export_xlsx():
    bodies = [[b.name, b.acronym, b.address, b.contact_person, b.email, b.phone, "Yes" if b.is_active else "No"]
              for b in ProfessionalBody.objects.order_by("name")]
    maps = [[m.body.acronym or m.body.name, m.programme_name, m.level.code.upper() if m.level_id else "", m.note,
             "Yes" if m.is_active else "No"]
            for m in ProgrammeBodyMapping.objects.select_related("body", "level")
            .order_by("body__name", "programme_name", "level__sort_order")]
    return _workbook(bodies, maps, with_reference=False)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def _read(ws, columns):
    rows = ws.iter_rows(values_only=True)
    try:
        header = [_norm(str(h or "").replace("*", "")) for h in next(rows)]
    except StopIteration:
        return [], ["The sheet is empty."]
    index = {}
    problems = []
    for label, key, required, _help in columns:
        pos = header.index(_norm(label)) if _norm(label) in header else None
        if pos is None and required:
            problems.append(f"Column “{label}” is missing.")
        index[key] = pos
    out = []
    for number, row in enumerate(rows, start=2):
        if all(_cell(c) == "" for c in row):
            continue
        out.append((number, {key: (row[pos] if pos is not None and pos < len(row) else None)
                             for key, pos in index.items()}))
        if len(out) > MAX_ROWS:
            problems.append(f"At most {MAX_ROWS} rows per sheet.")
            break
    return out, problems


def import_xlsx(file_obj, *, user=None, dry_run=False):
    result = ImportResult(dry_run=dry_run)

    def err(sheet, row, message):
        result.errors.append({"sheet": sheet, "row": row, "message": message})

    try:
        wb = load_workbook(file_obj, read_only=True, data_only=True)
    except Exception:
        raise ValidationError("That file is not an Excel workbook (.xlsx). Download the template and use it.")
    if BODY_SHEET not in wb.sheetnames and MAP_SHEET not in wb.sheetnames:
        raise ValidationError(f"The workbook needs a “{BODY_SHEET}” sheet, a “{MAP_SHEET}” sheet, or both. "
                              "Download the template and use it.")

    # ---- Validate the bodies sheet -----------------------------------------
    body_specs = []  # (row, data)
    names_in_file = {}
    if BODY_SHEET in wb.sheetnames:
        rows, problems = _read(wb[BODY_SHEET], BODY_COLUMNS)
        for p in problems:
            err(BODY_SHEET, 1, p)
        for number, raw in rows:
            data = {k: _cell(raw.get(k)) for k in ("name", "acronym", "address", "contact_person", "email", "phone")}
            if not data["name"]:
                err(BODY_SHEET, number, "Name is required.")
                continue
            if len(data["name"]) > 200:
                err(BODY_SHEET, number, "Name is longer than 200 characters.")
            if len(data["acronym"]) > 20:
                err(BODY_SHEET, number, "Acronym is longer than 20 characters.")
            if data["email"]:
                try:
                    validate_email(data["email"])
                except ValidationError:
                    err(BODY_SHEET, number, f"“{data['email']}” is not a valid e-mail address.")
            try:
                data["is_active"] = _bool(raw.get("is_active"))
            except ValueError as exc:
                err(BODY_SHEET, number, f"Active: {exc}")
            key = _norm(data["name"])
            if key in names_in_file:
                err(BODY_SHEET, number, f"Duplicate of row {names_in_file[key]}.")
                continue
            names_in_file[key] = number
            body_specs.append((number, data))

    # Bodies the mapping sheet may refer to: those on file plus those in the file.
    known = {}
    for body in ProfessionalBody.objects.all():
        known[_norm(body.name)] = body.name
        if body.acronym:
            known.setdefault(_norm(body.acronym), body.name)
    for _n, data in body_specs:
        known[_norm(data["name"])] = data["name"]
        if data["acronym"]:
            known[_norm(data["acronym"])] = data["name"]

    # ---- Validate the mappings sheet ---------------------------------------
    map_specs = []
    seen = {}
    if MAP_SHEET in wb.sheetnames:
        rows, problems = _read(wb[MAP_SHEET], MAP_COLUMNS)
        for p in problems:
            err(MAP_SHEET, 1, p)
        for number, raw in rows:
            body_ref = _cell(raw.get("body"))
            programme = _cell(raw.get("programme_name"))
            if not body_ref:
                err(MAP_SHEET, number, "Professional body is required.")
            elif _norm(body_ref) not in known:
                err(MAP_SHEET, number, f"No professional body called “{body_ref}”. Add it to the first sheet.")
            if not programme:
                err(MAP_SHEET, number, "Programme is required.")
            elif len(programme) > 200:
                err(MAP_SHEET, number, "Programme is longer than 200 characters.")
            try:
                level = _level(raw.get("level"))
            except ValueError as exc:
                err(MAP_SHEET, number, str(exc))
                level = None
            try:
                active = _bool(raw.get("is_active"))
            except ValueError as exc:
                err(MAP_SHEET, number, f"Active: {exc}")
                active = True
            if not body_ref or not programme or _norm(body_ref) not in known:
                continue
            body_name = known[_norm(body_ref)]
            dedupe = (_norm(body_name), programme_key(programme), level.pk if level else None)
            if dedupe in seen:
                err(MAP_SHEET, number, f"Duplicate of row {seen[dedupe]}.")
                continue
            seen[dedupe] = number
            map_specs.append((number, {"body_name": body_name, "programme_name": programme, "level": level,
                                       "note": _cell(raw.get("note"))[:255], "is_active": active}))

    if result.errors:
        return result

    # ---- Apply ----------------------------------------------------------------
    with transaction.atomic():
        by_key = {}
        for body in ProfessionalBody.objects.select_for_update():
            by_key[_norm(body.name)] = body
        for _n, data in body_specs:
            body = by_key.get(_norm(data["name"]))
            if body is None and data["acronym"]:
                body = next((b for b in by_key.values() if _norm(b.acronym) == _norm(data["acronym"])), None)
            if body is None:
                body = ProfessionalBody.objects.create(created_by=user if getattr(user, "pk", None) else None,
                                                       **data)
                result.bodies_created += 1
            else:
                changes = [k for k, v in data.items() if getattr(body, k) != v]
                if changes:
                    for k in changes:
                        setattr(body, k, data[k])
                    body.save()
                    result.bodies_updated += 1
                else:
                    result.bodies_unchanged += 1
            by_key[_norm(body.name)] = body
            if body.acronym:
                by_key.setdefault(_norm(body.acronym), body)
        for _n, data in map_specs:
            body = by_key.get(_norm(data["body_name"]))
            mapping = ProgrammeBodyMapping.objects.filter(body=body, programme_key=programme_key(data["programme_name"]),
                                                          level=data["level"]).first()
            if mapping is None:
                ProgrammeBodyMapping.objects.create(body=body, programme_name=data["programme_name"],
                                                    level=data["level"], note=data["note"],
                                                    is_active=data["is_active"],
                                                    created_by=user if getattr(user, "pk", None) else None)
                result.mappings_created += 1
            else:
                fields = {"programme_name": data["programme_name"], "note": data["note"],
                          "is_active": data["is_active"]}
                changes = [k for k, v in fields.items() if getattr(mapping, k) != v]
                if changes:
                    for k in changes:
                        setattr(mapping, k, fields[k])
                    mapping.save()
                    result.mappings_updated += 1
                else:
                    result.mappings_unchanged += 1
        if dry_run:
            transaction.set_rollback(True)
    return result
