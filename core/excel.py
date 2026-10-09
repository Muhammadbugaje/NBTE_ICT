"""Excel reader/writer for the shared import/export infrastructure.

Template generation includes:
- A "Reference" sheet listing every valid value for lookup columns
  (pulled live from the database at template-download time)
- Data validation dropdowns on lookup columns
- Example values that are real DB values or blank — never invented

Writers return bytes; callers save them through the shared storage utility.
"""
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


_HEADER_FILL = PatternFill("solid", fgColor="1F6B3E")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_SECTION_FONT = Font(bold=True, color="134326")
_ERROR_FILL = PatternFill("solid", fgColor="FFE5E5")
_REF_HEAD_FILL = PatternFill("solid", fgColor="D9EBDE")


def read_rows(file_like, sheet_index=0, header_row=1):
    """Yield dicts of {header: value} from the first sheet of a workbook."""
    wb = load_workbook(file_like, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[sheet_index]
    except IndexError:
        return
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [_clean(h) for h in next(rows_iter)]
    except StopIteration:
        return
    for row in rows_iter:
        if all(_clean(c) in (None, "") for c in row):
            continue
        yield {
            header[i]: row[i]
            for i in range(min(len(header), len(row)))
            if header[i]
        }


def write_template(columns, sheet_title="Template"):
    """Build the template workbook.

    Sheet 1 ("Data") — header + one example row + dropdowns for lookup columns.
    Sheet 2 ("Reference") — every valid value per lookup column.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"

    # Fetch lookup values ONCE so we don't hammer the DB per cell.
    lookup_values = {}
    for col in columns:
        if getattr(col, "lookup", None):
            try:
                values = [str(v) for v in col.lookup()]
            except Exception:
                values = []
            lookup_values[col.field] = values

    # ---- Header row ----------------------------------------------------
    for col_index, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_index, value=col.name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.column_dimensions[get_column_letter(col_index)].width = max(len(col.name) + 4, 20)

        help_text = col.help or ""
        if getattr(col, "lookup", None):
            available = lookup_values.get(col.field) or []
            if available:
                help_text = (help_text + " " if help_text else "") + \
                    f"Valid values: {', '.join(available[:20])}" + \
                    (" …" if len(available) > 20 else "")
            else:
                help_text = (help_text + " " if help_text else "") + \
                    "No values exist in the system yet — leave blank or contact ICT."
        if col.required:
            help_text = ("REQUIRED. " + help_text).strip()
        if help_text:
            cell.comment = Comment(help_text, "NBTE ICT")

    # ---- Example row ---------------------------------------------------
    for col_index, col in enumerate(columns, start=1):
        example = ""
        if getattr(col, "lookup", None):
            available = lookup_values.get(col.field) or []
            if available:
                example = available[0]
        elif col.example:
            example = col.example
        ws.cell(row=2, column=col_index, value=example)

    # ---- Dropdowns for lookup columns ----------------------------------
    for col_index, col in enumerate(columns, start=1):
        if not getattr(col, "lookup", None):
            continue
        available = lookup_values.get(col.field) or []
        if not available:
            continue
        inline = ",".join(available)
        letter = get_column_letter(col_index)
        rng = f"{letter}2:{letter}10000"
        if len(inline) <= 255:
            dv = DataValidation(type="list", formula1=f'"{inline}"',
                                allow_blank=True, showDropDown=False)
        else:
            ref_letter = get_column_letter(_lookup_column_index(columns, col.field))
            dv = DataValidation(
                type="list",
                formula1=f"=Reference!${ref_letter}$2:${ref_letter}${len(available) + 1}",
                allow_blank=True, showDropDown=False,
            )
        dv.error = "Please choose a value from the dropdown (or leave blank)."
        dv.errorTitle = "Invalid value"
        dv.prompt = "Choose from the list"
        dv.promptTitle = col.name
        ws.add_data_validation(dv)
        dv.add(rng)

    # ---- Reference sheet -----------------------------------------------
    lookup_columns = [c for c in columns if getattr(c, "lookup", None)]
    if lookup_columns:
        ref = wb.create_sheet("Reference")
        max_rows = 0
        for idx, col in enumerate(lookup_columns, start=1):
            letter = get_column_letter(idx)
            cell = ref.cell(row=1, column=idx, value=col.name)
            cell.font = _SECTION_FONT
            cell.fill = _REF_HEAD_FILL
            ref.column_dimensions[letter].width = max(len(col.name) + 6, 22)
            values = lookup_values.get(col.field) or []
            if not values:
                ref.cell(row=2, column=idx, value="(none yet)")
                max_rows = max(max_rows, 2)
            else:
                for row_idx, value in enumerate(values, start=2):
                    ref.cell(row=row_idx, column=idx, value=value)
                max_rows = max(max_rows, len(values) + 1)
        note_row = max_rows + 2
        ref.cell(row=note_row, column=1, value=(
            "Reference sheet — lists the values valid for the lookup columns "
            "on the Data sheet. Pulled live from the system; do not edit."
        )).font = Font(italic=True, color="5A616B")

    return _to_bytes(wb)


def write_export(header, rows, sheet_title="Export"):
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_title or "Export")[:31]
    for col_index, name in enumerate(header, start=1):
        cell = ws.cell(row=1, column=col_index, value=name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        ws.column_dimensions[get_column_letter(col_index)].width = max(len(str(name)) + 4, 18)
    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=value)
    return _to_bytes(wb)


def write_rejected(header, rows, sheet_title="Rejected rows"):
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_title or "Rejected rows")[:31]
    for col_index, name in enumerate(header, start=1):
        cell = ws.cell(row=1, column=col_index, value=name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        ws.column_dimensions[get_column_letter(col_index)].width = max(len(str(name)) + 4, 18)
    error_col = len(header)
    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            if c == error_col:
                cell.fill = _ERROR_FILL
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _clean(value):
    if isinstance(value, str):
        return value.strip()
    return value


def _to_bytes(wb):
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _lookup_column_index(columns, field):
    """1-based index on the Reference sheet for a lookup field."""
    idx = 1
    for c in columns:
        if getattr(c, "lookup", None):
            if c.field == field:
                return idx
            idx += 1
    return 1