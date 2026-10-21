"""Nominal-roll XLSX exporter and blank template (openpyxl).

    export_nominal_roll(queryset, per_department=False) -> bytes
    build_template() -> bytes

The column order matches the official HR file, so an export can be edited
and imported again through staff.nominal_roll.
"""
from __future__ import annotations

import io
import re

from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from .account_provisioning import is_placeholder_email
from .models import Department, Designation, StaffState, StateOfOrigin
from .nominal_roll import EXTRA_HEADERS, STANDARD_HEADERS as OFFICIAL_HEADERS

# The 17 official columns, then the optional contact columns.
STANDARD_HEADERS = OFFICIAL_HEADERS + EXTRA_HEADERS

DATE_FORMAT = "d-mmm-yy"          # 7-Mar-58, as on the source file
TITLE = "NATIONAL BOARD FOR TECHNICAL EDUCATION, KADUNA"

# Brand colours as plain hex for the workbook (no CSS here).
HEADER_FILL = "1F3B5C"
HEADER_FONT = "FFFFFF"
EXAMPLE_FILL = "EEF1F5"
EXAMPLE_FONT = "6B7280"

COLUMN_WIDTHS = [6, 18, 26, 5, 9, 14, 16, 30, 12, 18, 11, 11, 11, 11, 11, 14, 60, 30, 16, 20]

COLUMN_HELP = {
    "S/NO": ("Serial number. Any number; it is not imported.", "1"),
    "SURNAME": ("Family name, as on the staff file.", "ABDULKADIR"),
    "OTHER NAME": ("First and middle names (and title, if HR keeps it here).", "AMINA BELLO"),
    "SEX": ("M or F.", "F"),
    "FILE NO": ("The staff file number. Required — it is how the import recognises a person. "
                "Must be unique.", "1520"),
    "ST_ORIGIN": ("State of origin — pick from the list (REFERENCE sheet). FCT for Abuja.", "KADUNA"),
    "LGA": ("Local government area, free text.", "ZARIA"),
    "DESIGNATION": ("Post title. Pick from the list; a new title is added to the list on import.",
                    "PROG. OFF. II"),
    "CONTEDISS/CONHESS": ("Salary level as a number (e.g. 7). 'CONHESS 06' is read as 6.", "7"),
    "DEPARTMT": ("Department short name — pick from the list.", "ICT"),
    "D_BIRTH": ("Date of birth. Type a real date (e.g. 7-Mar-1990) or dd-mm-yy (07-03-90).",
                "7-Mar-90"),
    "D_FAPPMT": ("Date of first appointment (into the public service).", "2-Jan-26"),
    "D_JBOARD": ("Date of joining the Board (NBTE).", "2-Jan-26"),
    "D_PRAPPMT": ("Date of present appointment (last promotion / current post).", "2-Jan-26"),
    "D_COF": ("Date of confirmation. Leave blank while on probation.", ""),
    "REMARKS": ("Staff state: CONFIRMED or PROBATION (or another state on the list).", "PROBATION"),
    "EMAIL": ("Recommended. Work email — it becomes the person's sign-in name, receives the welcome "
              "email and lets them reset a forgotten password.", "a.bello@nbte.gov.ng"),
    "PHONE": ("Optional. Phone number.", "08030000000"),
    "OFFICE": ("Optional. Office / unit within the department (added to the list if new).", ""),
    "QUALIFICATION (S)": ("Qualifications with years, separated by commas or semicolons.",
                          "FSLC 2004; SSCE 2010; B.Sc Computer Science 2016"),
}


def _openpyxl():
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    return openpyxl, Alignment, Border, Font, PatternFill, Side


def ordered(queryset):
    """Department, then seniority (rank), then surname — the HR file's order."""
    return queryset.select_related(
        "department", "designation", "state_of_origin", "state", "office",
    ).order_by(
        "department__sort_order", "department__name",
        Case(When(designation__isnull=True, then=Value(0)),
             default="designation__rank", output_field=IntegerField()).desc(),
        "last_name", "first_name",
    )


def _number_or_text(value):
    text = (value or "").strip()
    return int(text) if re.fullmatch(r"\d{1,9}", text) else text


def row_values(staff, serial):
    """The cells for one staff member, in STANDARD_HEADERS order."""
    return [
        serial,
        staff.last_name,
        staff.first_name,
        staff.gender or "",
        _number_or_text(staff.employee_no),
        staff.state_of_origin.name if staff.state_of_origin_id else "",
        staff.lga,
        staff.designation.name if staff.designation_id else "",
        _number_or_text(staff.grade_level),
        staff.department.name if staff.department_id else "",
        staff.date_of_birth,
        staff.date_first_appointment,
        staff.date_joined_board,
        staff.date_present_appointment,
        staff.date_confirmation,
        staff.state.name.upper() if staff.state_id else "",
        staff.qualifications,
        "" if is_placeholder_email(staff.email) else staff.email,
        staff.phone,
        staff.office.name if staff.office_id else "",
    ]


def _write_sheet(ws, staff_iter, subtitle):
    openpyxl, Alignment, Border, Font, PatternFill, Side = _openpyxl()
    ws["C1"] = TITLE
    ws["C1"].font = Font(bold=True, size=12)
    ws["C3"] = subtitle
    ws["C3"].font = Font(bold=True)
    header_row = 5
    for col, text in enumerate(STANDARD_HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.font = Font(bold=True, color=HEADER_FONT)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for col, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width
    row = header_row
    for serial, staff in enumerate(staff_iter, start=1):
        row += 1
        for col, value in enumerate(row_values(staff, serial), start=1):
            cell = ws.cell(row=row, column=col, value=value)
            if 11 <= col <= 15 and value:
                cell.number_format = DATE_FORMAT
            if col == 17:
                cell.alignment = Alignment(wrap_text=False)
    ws.freeze_panes = ws.cell(row=header_row + 1, column=3)
    last = openpyxl.utils.get_column_letter(len(STANDARD_HEADERS))
    ws.auto_filter.ref = f"A{header_row}:{last}{max(row, header_row + 1)}"
    return row - header_row


FULL_MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
               "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


def _as_at():
    today = timezone.localdate()
    return f"NOMINAL ROLL AS AT {FULL_MONTHS[today.month - 1]}, {today.year}"


def _sheet_title(name, used):
    clean = re.sub(r"[\[\]:*?/\\]", "-", name or "No department").strip() or "No department"
    clean = clean[:31]
    title, n = clean, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title, n = clean[:31 - len(suffix)] + suffix, n + 1
    used.add(title.lower())
    return title


def export_nominal_roll(queryset, per_department=False) -> tuple[bytes, int]:
    """-> (xlsx bytes, number of staff rows)."""
    openpyxl = _openpyxl()[0]
    wb = openpyxl.Workbook()
    rows = list(ordered(queryset))
    if not per_department:
        ws = wb.active
        ws.title = "NOMINAL ROLL"
        count = _write_sheet(ws, rows, _as_at())
    else:
        wb.remove(wb.active)
        groups = {}
        for s in rows:
            groups.setdefault(s.department.name if s.department_id else "", []).append(s)
        used, count = set(), 0
        for dept, members in groups.items():
            ws = wb.create_sheet(_sheet_title(dept, used))
            count += _write_sheet(ws, members, f"{_as_at()} — {dept or 'NO DEPARTMENT'}")
        if not groups:
            _write_sheet(wb.create_sheet("NOMINAL ROLL"), [], _as_at())
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), count


def build_template() -> bytes:
    """Blank NBTE_Nominal_Roll_Template.xlsx: NOMINAL ROLL / REFERENCE / README."""
    openpyxl, Alignment, Border, Font, PatternFill, Side = _openpyxl()
    from openpyxl.comments import Comment
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NOMINAL ROLL"
    for col, text in enumerate(STANDARD_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=text)
        cell.font = Font(bold=True, color=HEADER_FONT)
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        help_text, _example = COLUMN_HELP[text]
        comment = Comment(help_text, "NBTE ICT")
        comment.width, comment.height = 260, 90
        cell.comment = comment
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = COLUMN_WIDTHS[col - 1]
    # Greyed example row. 'e.g.' in S/NO tells the importer to skip it.
    for col, text in enumerate(STANDARD_HEADERS, start=1):
        example = COLUMN_HELP[text][1] if col > 1 else "e.g."
        cell = ws.cell(row=2, column=col, value=example)
        cell.font = Font(italic=True, color=EXAMPLE_FONT)
        cell.fill = PatternFill("solid", fgColor=EXAMPLE_FILL)
    ws.freeze_panes = "C3"

    # REFERENCE lists
    ref = wb.create_sheet("REFERENCE")
    lists = [
        ("DEPARTMT", list(Department.objects.filter(is_active=True)
                          .order_by("sort_order", "name").values_list("name", flat=True))),
        ("DESIGNATION", list(Designation.objects.filter(is_active=True)
                             .order_by("-rank", "name").values_list("name", flat=True))),
        ("ST_ORIGIN", list(StateOfOrigin.objects.filter(is_active=True)
                           .order_by("name").values_list("name", flat=True))),
        ("REMARKS", [n.upper() for n in StaffState.objects.filter(is_active=True)
                     .order_by("sort_order", "name").values_list("name", flat=True)]),
        ("SEX", ["M", "F"]),
    ]
    target_col = {h: i + 1 for i, h in enumerate(STANDARD_HEADERS)}
    for i, (header, values) in enumerate(lists, start=1):
        letter = openpyxl.utils.get_column_letter(i)
        head = ref.cell(row=1, column=i, value=header)
        head.font = Font(bold=True, color=HEADER_FONT)
        head.fill = PatternFill("solid", fgColor=HEADER_FILL)
        ref.column_dimensions[letter].width = 34 if header == "DESIGNATION" else 22
        for r, v in enumerate(values, start=2):
            ref.cell(row=r, column=i, value=v)
        last = max(len(values) + 1, 2)
        tcol = openpyxl.utils.get_column_letter(target_col[header])
        dv = DataValidation(type="list", formula1=f"=REFERENCE!${letter}$2:${letter}${last}",
                            allow_blank=True, showDropDown=False)
        # A warning (not a stop) so HR can still type a new designation.
        dv.errorStyle = "warning"
        dv.errorTitle = "Not on the list"
        dv.error = f"This {header} is not on the REFERENCE list. Keep it anyway?"
        dv.promptTitle = header
        dv.prompt = COLUMN_HELP[STANDARD_HEADERS[target_col[header] - 1]][0][:250]
        dv.showInputMessage = True
        dv.showErrorMessage = True
        ws.add_data_validation(dv)
        dv.add(f"{tcol}3:{tcol}2000")
    ref.freeze_panes = "A2"

    # README
    readme = wb.create_sheet("README")
    readme.column_dimensions["A"].width = 22
    readme.column_dimensions["B"].width = 90
    readme["A1"] = "NBTE nominal roll template"
    readme["A1"].font = Font(bold=True, size=13)
    readme["A2"] = ("Fill in one row per staff member on the NOMINAL ROLL sheet, starting on row 3. "
                    "Row 2 is an example and is skipped on import. Upload the file on People → "
                    "Nominal roll → Import; you see a preview before anything is saved.")
    readme["A2"].alignment = Alignment(wrap_text=True)
    readme.merge_cells("A2:B2")
    readme.row_dimensions[2].height = 48
    readme["A4"], readme["B4"] = "Column", "What to enter"
    for c in ("A4", "B4"):
        readme[c].font = Font(bold=True)
    for r, header in enumerate(STANDARD_HEADERS, start=5):
        help_text, example = COLUMN_HELP[header]
        readme.cell(row=r, column=1, value=header).font = Font(bold=True)
        cell = readme.cell(row=r, column=2,
                           value=help_text + (f"  Example: {example}" if example else ""))
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    tail = 5 + len(STANDARD_HEADERS) + 1
    readme.cell(row=tail, column=1, value="Dates").font = Font(bold=True)
    readme.cell(row=tail, column=2, value=(
        "Best: a real Excel date (format it as d-mmm-yy). Also accepted: 7-Mar-58, 07-03-58, "
        "7th Mar. 58, 1st - MAY- 88, 2024-01-08. Two-digit years 00-40 are 2000-2040 and 41-99 are "
        "1941-1999. When day and month could be swapped (07-03-58) the day comes first.")
    ).alignment = Alignment(wrap_text=True, vertical="top")
    readme.cell(row=tail + 1, column=1, value="Updating staff").font = Font(bold=True)
    readme.cell(row=tail + 1, column=2, value=(
        "A row whose FILE NO is already in the system updates that person. Empty cells never "
        "erase what is already saved.")).alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
