"""Excel employee-ID filter for the promotions list (D6).

read_ids(fileobj) reads an .xlsx and returns the IDs in the first column whose
header looks like "Employee ID", "Staff No", "File No" (any sheet, first
header row found in the top 10 rows); with no such header, column A.

MERGE NOTE (X45): when core/importdiff.py lands this stays a read-only filter
(nothing is written), so it does not need the diff engine.
"""
from __future__ import annotations

import re

HEADER_RE = re.compile(r"^(employee|staff|file|personnel|ippis)\s*(id|no|number|num|#)?\.?$", re.I)
MAX_IDS = 5000


class IdFilterError(Exception):
    pass


def normalise(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", "", str(value)).upper()


def read_ids(fileobj) -> list[str]:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(fileobj, read_only=True, data_only=True)
    except Exception as exc:  # zip errors, not a workbook
        raise IdFilterError("That file is not an Excel workbook (.xlsx).") from exc
    try:
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    col, start = 0, 0
    for i, row in enumerate(rows[:10]):
        for j, cell in enumerate(row or ()):
            if isinstance(cell, str) and HEADER_RE.match(cell.strip().replace("_", " ")):
                col, start = j, i + 1
                break
        else:
            continue
        break
    ids, seen = [], set()
    for row in rows[start:]:
        if not row or col >= len(row):
            continue
        v = normalise(row[col])
        if v and v not in seen:
            seen.add(v)
            ids.append(v)
        if len(ids) >= MAX_IDS:
            break
    if not ids:
        raise IdFilterError("No employee IDs found. Put them in a column headed “Employee ID” or “File No”.")
    return ids


def match(qs, ids):
    """(matched queryset, unmatched ids) — case-insensitive on employee_no."""
    wanted = {normalise(i) for i in ids}
    found = {}
    for pk, emp in qs.values_list("pk", "employee_no"):
        key = normalise(emp)
        if key in wanted:
            found[key] = pk
    unmatched = [i for i in ids if normalise(i) not in found]
    return qs.filter(pk__in=found.values()), unmatched
