"""Migrate from the previous system (Phase 8) — bring existing institutions,
their programmes and their accreditation history over without restarting
them.

One workbook, three data sheets (the template also has an Instructions
sheet, an Examples sheet and a Reference sheet of valid values):

* **Institutions** — created *approved* (or the status on the sheet) directly
  in their handling department: no SOP-01 application, no fees. The portal
  administrator's account is created **inactive**; a one-time set-password
  link is e-mailed on request (a checkbox when the second officer confirms,
  or later with Send Login Credentials, one by one or in bulk from the batch).
* **Programmes** — created with their status, approval type, streams and
  dates. A blank expiry is derived from the commencement date and the
  approval type's duration. A professional body named on the row is linked
  through the NBTE-wide programme → body mapping.
* **Accreditation history** (optional) — past decisions, recorded on the
  timeline at their own dates.

Validation works like the bulk import: every row is checked first, one bad
row rejects the batch, the preview shows what will be created / updated /
left unchanged, and nothing happens until a second officer confirms
(ActionPolicy ``institutions.bulk_import``). Re-running the same workbook
changes nothing: institutions match by code, programmes by institution +
name + level, history rows by their content.

``accreditation.ApprovalType`` / ``Programme.approval_type`` may or may not
exist yet (another Phase 8 slice adds them). The code resolves the model at
run time: when present, the programme's ``approval_type`` is set; otherwise
the approval type's name is kept on the timeline.
"""
# scoped-by-caller
from __future__ import annotations

import calendar
import datetime
import io
import re

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from qa_core import timeline

from . import services
from .importexport import _cell, _date, _lookup, _norm, _state
from .models import (
    Institution,
    InstitutionAddress,
    InstitutionCategory,
    InstitutionContact,
    InstitutionOwnership,
    InstitutionType,
    InstitutionUser,
    ProfessionalBody,
    Programme,
    ProgrammeBodyMapping,
    ProgrammeCategory,
    ProgrammeLevel,
    programme_key,
)

KIND = "legacy"
SHEET_INSTITUTIONS, SHEET_PROGRAMMES, SHEET_HISTORY = "Institutions", "Programmes", "Accreditation history"

INSTITUTION_COLUMNS = [
    ("Code", "code", True, "The institution's code in the previous system (3–6 letters/digits). Never changes."),
    ("Name", "name", True, "Full name."),
    ("Ownership", "ownership", True, "Federal, State or Private."),
    ("Type", "type", True, "e.g. Polytechnic."),
    ("Category", "category", True, "Decides the handling department."),
    ("State", "state", True, ""),
    ("LGA", "lga", True, ""),
    ("Address", "address", False, "Permanent-site address."),
    ("Town", "town", False, ""),
    ("Official e-mail", "official_email", False, ""),
    ("Phone", "phone", False, ""),
    ("Head of institution", "head", False, "Name of the Rector / Provost / Principal."),
    ("Year established", "year_established", False, "e.g. 1985."),
    ("Website", "website", False, "https://…"),
    ("CAC RC number", "cac_rc_number", False, "Private institutions."),
    ("Portal admin name", "admin_name", False, "The person who will manage the portal account."),
    ("Portal admin e-mail", "admin_email", False, "Their account is created inactive; they get a set-password link."),
    ("Status", "status", False, "approved (default), suspended or proposed."),
    ("Date approved", "approved_on", False, "YYYY-MM-DD."),
]
PROGRAMME_COLUMNS = [
    ("Institution code", "institution_code", True, "A code on the Institutions sheet or already in the system."),
    ("Programme name", "name", True, ""),
    ("Level", "level", True, "ND, HND, NID, NVC …"),
    ("Category", "category", False, "Programme category (see Reference)."),
    ("Status", "status", True, "accredited, interim, approval to commence, expired, denied …"),
    ("Approval type", "approval_type", False, "Full, Interim or Approval to Commence."),
    ("Approved streams", "approved_streams", False, "Whole number."),
    ("Commencement date", "commencement_date", False, "YYYY-MM-DD."),
    ("Expiry date", "expiry_date", False, "YYYY-MM-DD. Blank = derived from the approval type."),
    ("Professional body", "professional_body", False, "Name or acronym of a body on the professional bodies list."),
]
HISTORY_COLUMNS = [
    ("Institution code", "institution_code", True, ""),
    ("Programme name", "name", True, ""),
    ("Level", "level", True, ""),
    ("Date", "date", True, "YYYY-MM-DD — when the decision was made."),
    ("Status", "status", True, "The outcome then: accredited, interim, denied …"),
    ("Approval type", "approval_type", False, ""),
    ("Note", "note", False, "e.g. the letter reference."),
]
SHEETS = [(SHEET_INSTITUTIONS, INSTITUTION_COLUMNS, True), (SHEET_PROGRAMMES, PROGRAMME_COLUMNS, False),
          (SHEET_HISTORY, HISTORY_COLUMNS, False)]

INSTITUTION_STATUSES = (Institution.APPROVED, Institution.SUSPENDED, Institution.PROPOSED)
PROGRAMME_STATUSES = dict(Programme.STATUS_CHOICES)
# Built-in durations, used only while accreditation.ApprovalType does not exist.
DEFAULT_APPROVAL_TYPES = [("full", "Full", 60), ("interim", "Interim", 12),
                          ("approval-to-commence", "Approval to Commence", 24)]
ALIASES = {"atc": "approval-to-commence", "approvaltocommence": "approval-to-commence",
           "fullaccreditation": "full", "interimaccreditation": "interim"}

EXAMPLE_INSTITUTIONS = [
    ["KDP", "Kaduna Polytechnic", "Federal", "Polytechnic", "Polytechnic", "KADUNA", "Kaduna North",
     "Tudun Wada", "Kaduna", "registrar@kadunapolytechnic.edu.ng", "08030000000", "Dr. A. Bello", 1956, "", "",
     "Musa Danjuma", "ict@kadunapolytechnic.edu.ng", "approved", "1968-10-01"],
]
EXAMPLE_PROGRAMMES = [
    ["KDP", "Computer Science", "ND", "Computer Science", "accredited", "Full", 2, "2021-10-01", "", ""],
    ["KDP", "Pharmaceutical Technology", "ND", "", "interim", "Interim", 1, "2024-01-15", "2025-01-15",
     "PCN"],
]
EXAMPLE_HISTORY = [
    ["KDP", "Computer Science", "ND", "2016-09-30", "accredited", "Full", "Letter NBTE/QA/2016/114"],
]


# ===========================================================================
# Approval types (works before and after accreditation.ApprovalType exists)
# ===========================================================================
def approval_type_model():
    try:
        return apps.get_model("accreditation", "ApprovalType")
    except LookupError:
        return None


def programme_has_approval_type():
    return any(f.name == "approval_type" for f in Programme._meta.get_fields())


def approval_types():
    """[(key, name, duration_months, obj or None)] — the model's rows when it
    exists, otherwise the built-in three."""
    model = approval_type_model()
    if model is not None:
        out = []
        qs = model.objects.all()
        if hasattr(model, "is_active"):
            qs = qs.filter(is_active=True)
        for obj in qs:
            key = getattr(obj, "code", "") or getattr(obj, "name", "")
            out.append((str(key), str(getattr(obj, "name", key)), getattr(obj, "duration_months", None), obj))
        if out:
            return out
    return [(k, n, d, None) for k, n, d in DEFAULT_APPROVAL_TYPES]


def resolve_approval_type(value):
    key = _norm(value)
    if not key:
        return None
    key = _norm(ALIASES.get(key, key))
    for item in approval_types():
        if key in (_norm(item[0]), _norm(item[1])):
            return item
    return None


def add_months(day, months):
    month = day.month - 1 + int(months)
    year = day.year + month // 12
    month = month % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _programme_status(value):
    key = _norm(value)
    for code, label in Programme.STATUS_CHOICES:
        if key in (_norm(code), _norm(label)):
            return code
    if key == "atc":
        return "approval_to_commence"
    return None


def _match_lga(state, lga):
    """The LGA spelled as the state's list spells it (case and spacing ignored)."""
    options = services.lgas_for(state)
    if not options:
        return lga
    for option in options:
        if _norm(option) == _norm(lga):
            return option
    return None


def _body(value):
    key = _norm(value)
    if not key:
        return None
    for body in ProfessionalBody.objects.filter(is_active=True):
        if key in (_norm(body.name), _norm(body.acronym)):
            return body
    return None


# ===========================================================================
# Template
# ===========================================================================
def template_xlsx():
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    intro = wb.active
    intro.title = "Instructions"
    head_fill = PatternFill("solid", fgColor="1F6B3E")
    lines = [
        ("Migrate from the previous system", True),
        ("Use this workbook to bring institutions that already exist in the previous NBTE system into the new "
         "QA system, with their programmes and accreditation history, so they continue where they left off.", False),
        ("", False),
        ("1. Institutions sheet — one row per institution. Code, name, ownership, type, category, state and LGA "
         "are required. Status is normally 'approved'. The portal administrator's account is created inactive.",
         False),
        ("2. Programmes sheet — one row per programme (institution code + programme name + level). Status is "
         "required. If the expiry date is blank, it is worked out from the commencement date and the approval "
         "type (Full 60 months, Interim 12, Approval to Commence 24 unless Settings say otherwise).", False),
        ("3. Accreditation history sheet (optional) — past decisions, one per row, with the date they were made.",
         False),
        ("", False),
        ("Rules", True),
        ("• Leave the header row as it is. Columns marked * are required. Use the values on the Reference sheet.",
         False),
        ("• The whole workbook is checked first. One bad row stops everything; nothing is changed until it is "
         "fixed and uploaded again.", False),
        ("• A valid workbook waits for a second officer. When confirming, they choose whether to e-mail the "
         "portal administrators a one-time set-password link now.", False),
        ("• Running the same workbook again changes nothing: institutions are matched by code, programmes by "
         "institution, name and level.", False),
        ("• An institution already in the new system keeps its status; change status with the actions.", False),
        ("• The Examples sheet shows filled-in rows. Do not type on it — it is not imported.", False),
    ]
    intro.column_dimensions["A"].width = 120
    for r, (text, bold) in enumerate(lines, start=1):
        cell = intro.cell(row=r, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = Font(bold=True, size=13 if r == 1 else 11, color="1F6B3E")

    lists = _reference_lists()
    ref_letters = {}
    sheets = {}
    for title, columns, _required in SHEETS:
        ws = wb.create_sheet(title)
        sheets[title] = ws
        for i, (header, _key, required, help_text) in enumerate(columns, start=1):
            cell = ws.cell(row=1, column=i, value=header + (" *" if required else ""))
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = head_fill
            ws.column_dimensions[get_column_letter(i)].width = max(14, min(36, len(header) + 10))
            if help_text:
                cell.comment = Comment(help_text, "NBTE QA")
        ws.freeze_panes = "A2"
    ex = wb.create_sheet("Examples")
    row = 1
    for title, columns, _r in SHEETS:
        examples = {SHEET_INSTITUTIONS: EXAMPLE_INSTITUTIONS, SHEET_PROGRAMMES: EXAMPLE_PROGRAMMES,
                    SHEET_HISTORY: EXAMPLE_HISTORY}[title]
        ex.cell(row=row, column=1, value=f"{title} (example — not imported)").font = Font(bold=True,
                                                                                         color="1F6B3E")
        row += 1
        for i, (header, *_rest) in enumerate(columns, start=1):
            ex.cell(row=row, column=i, value=header).font = Font(bold=True)
            ex.column_dimensions[get_column_letter(i)].width = 22
        row += 1
        for values in examples:
            for i, value in enumerate(values, start=1):
                ex.cell(row=row, column=i, value=value).font = Font(italic=True, color="5B6577")
            row += 1
        row += 1
    ref = wb.create_sheet("Reference")
    for c, (title, values) in enumerate(lists.items(), start=1):
        ref.cell(row=1, column=c, value=title).font = Font(bold=True)
        ref.column_dimensions[get_column_letter(c)].width = 30
        for r, value in enumerate(values, start=2):
            ref.cell(row=r, column=c, value=value)
        ref_letters[title] = (get_column_letter(c), len(values))
    dropdowns = {SHEET_INSTITUTIONS: {"Ownership": "Ownership", "Type": "Type", "Category": "Institution category",
                                      "State": "State", "Status": "Institution status"},
                 SHEET_PROGRAMMES: {"Level": "Level", "Category": "Programme category",
                                    "Status": "Programme status", "Approval type": "Approval type",
                                    "Professional body": "Professional body"},
                 SHEET_HISTORY: {"Level": "Level", "Status": "Programme status", "Approval type": "Approval type"}}
    for title, columns, _r in SHEETS:
        ws = sheets[title]
        for i, (header, *_rest) in enumerate(columns, start=1):
            source = dropdowns[title].get(header)
            if not source or source not in ref_letters or not ref_letters[source][1]:
                continue
            letter, n = ref_letters[source]
            dv = DataValidation(type="list", formula1=f"=Reference!${letter}$2:${letter}${n + 1}", allow_blank=True,
                                showErrorMessage=False)
            ws.add_data_validation(dv)
            col = get_column_letter(i)
            dv.add(f"{col}2:{col}3000")
    wb.active = 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _reference_lists():
    from staff.models import StateOfOrigin

    return {
        "Ownership": [o.name for o in InstitutionOwnership.objects.filter(is_active=True)],
        "Type": [t.name for t in InstitutionType.objects.filter(is_active=True)],
        "Institution category": [c.name for c in InstitutionCategory.objects.filter(is_active=True)],
        "State": [s.name for s in StateOfOrigin.objects.order_by("name")],
        "Institution status": list(INSTITUTION_STATUSES),
        "Level": [lv.code.upper() for lv in ProgrammeLevel.objects.filter(is_active=True)],
        "Programme category": [c.name for c in ProgrammeCategory.objects.filter(is_active=True)],
        "Programme status": [code for code, _l in Programme.STATUS_CHOICES if code != "dropped"],
        "Approval type": [name for _k, name, _d, _o in approval_types()],
        "Professional body": [b.acronym or b.name for b in ProfessionalBody.objects.filter(is_active=True)],
    }


# ===========================================================================
# Read
# ===========================================================================
def read_workbook(upload):
    """{sheet title: [raw rows]} (raw = {field: text, "_row": n})."""
    from openpyxl import load_workbook

    try:
        upload.seek(0)
        wb = load_workbook(upload, read_only=True, data_only=True)
    except Exception:
        raise ValidationError("The file could not be read as an Excel workbook (.xlsx).") from None
    names = {_norm(n): n for n in wb.sheetnames}
    out, total = {}, 0
    for title, columns, required in SHEETS:
        name = names.get(_norm(title))
        if name is None:
            if required:
                raise ValidationError(f"The workbook has no '{title}' sheet. Download the migration template.")
            out[title] = []
            continue
        ws = wb[name]
        header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None) or ()
        positions = {}
        for idx, value in enumerate(header):
            key = _norm(str(value or "").replace("*", ""))
            for h, field, _req, _help in columns:
                if key == _norm(h):
                    positions[field] = idx
        missing = [h for h, f, req, _x in columns if req and f not in positions]
        rows = []
        for number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            data = {field: _cell(values[pos]) if pos < len(values) else "" for field, pos in positions.items()}
            if not any(data.values()):
                continue
            data["_row"] = number
            rows.append(data)
        if rows and missing:
            raise ValidationError(f"The '{title}' sheet is missing column(s): {', '.join(missing)}. "
                                  "Keep the template's headers.")
        out[title] = rows
        total += len(rows)
    wb.close()
    if not out.get(SHEET_INSTITUTIONS) and not out.get(SHEET_PROGRAMMES) and not out.get(SHEET_HISTORY):
        raise ValidationError("The workbook has no data rows.")
    if total > 5000:
        raise ValidationError("Migrate at most 5,000 rows at a time (split the workbook).")
    return out


# ===========================================================================
# Validate
# ===========================================================================
def validate(sheets):
    """Return (clean_rows, errors). Every clean row keeps its raw cells in
    ``_raw`` so the batch can be validated again at confirmation."""
    clean, errors = [], []
    inst_rows, inst_errors, codes = _validate_institutions(sheets.get(SHEET_INSTITUTIONS) or [])
    clean += inst_rows
    errors += inst_errors
    prog_rows, prog_errors = _validate_programmes(sheets.get(SHEET_PROGRAMMES) or [], codes)
    clean += prog_rows
    errors += prog_errors
    hist_rows, hist_errors = _validate_history(sheets.get(SHEET_HISTORY) or [], codes,
                                               {(r["institution_code"], _norm(r["name"]), r["level_id"])
                                                for r in prog_rows})
    clean += hist_rows
    errors += hist_errors
    return clean, errors


def _err(errors, sheet, n, label, problems):
    errors.append({"row": f"{sheet} {n}", "label": label or "(blank)", "errors": problems})


def _validate_institutions(rows):
    from accounts.models import User

    clean, errors, codes, seen = [], [], set(), {}
    for raw in rows:
        n, problems = raw["_row"], []
        code = raw.get("code", "").strip().upper()
        name = " ".join(raw.get("name", "").split())
        if not re.fullmatch(r"[A-Z0-9]{3,6}", code or ""):
            problems.append(f"Code '{code}' must be 3–6 letters or digits (it is required when migrating).")
        if len(name) < 4:
            problems.append("Name is required.")
        own = _lookup(InstitutionOwnership, raw.get("ownership"))
        itype = _lookup(InstitutionType, raw.get("type"))
        cat = _lookup(InstitutionCategory, raw.get("category"))
        state = _state(raw.get("state"))
        for label, value, obj in (("Ownership", raw.get("ownership"), own), ("Type", raw.get("type"), itype),
                                  ("Category", raw.get("category"), cat), ("State", raw.get("state"), state)):
            if not value:
                problems.append(f"{label} is required.")
            elif obj is None:
                problems.append(f"{label} '{value}' is not recognised (see the Reference sheet).")
        if cat and itype and cat.institution_type_id and cat.institution_type_id != itype.pk:
            problems.append(f"Category '{cat.name}' does not belong to type '{itype.name}'.")
        lga = raw.get("lga", "").strip()
        if not lga:
            problems.append("LGA is required.")
        elif state:
            matched = _match_lga(state, lga)
            if matched is None:
                problems.append(f"'{lga}' is not an LGA of {state.name}.")
            else:
                lga = matched
        status = (raw.get("status") or Institution.APPROVED).strip().lower()
        if status not in INSTITUTION_STATUSES:
            problems.append(f"Status '{status}' must be one of: {', '.join(INSTITUTION_STATUSES)}.")
        approved_on = None
        try:
            approved_on = _date(raw.get("approved_on"))
        except ValueError as exc:
            problems.append(str(exc))
        if approved_on and approved_on > timezone.localdate():
            problems.append("Date approved is in the future.")
        if status != Institution.PROPOSED and cat is not None and not (
                cat.routing_department_id or (itype and itype.default_department_id)):
            problems.append(f"Category '{cat.name}' is not mapped to a handling department "
                            "(Settings → Lookups).")
        for key, label in (("official_email", "Official e-mail"), ("admin_email", "Portal admin e-mail")):
            value = raw.get(key, "").strip()
            if value:
                try:
                    validate_email(value)
                except ValidationError:
                    problems.append(f"{label} '{value}' is not a valid e-mail address.")
        year = raw.get("year_established", "").strip()
        if year and (not year.isdigit() or not 1900 <= int(year) <= timezone.localdate().year):
            problems.append(f"Year established '{year}' is not a year.")
        website = raw.get("website", "").strip()
        if website and not website.lower().startswith(("http://", "https://")):
            website = "https://" + website
        existing = Institution.unscoped.filter(code=code).first() if code else None
        by_name = Institution.unscoped.filter(name__iexact=name).exclude(code=code).first() if name else None
        if by_name is not None:
            problems.append(f"Another institution is already called '{name}' ({by_name.code}).")
        if existing is not None and existing.status != status:
            problems.append(f"{code} is already {existing.status_label.lower()} in the new system; change its "
                            "status with the actions, not by migration.")
        admin_email = raw.get("admin_email", "").strip().lower()
        if admin_email:
            holder = User.objects.filter(email__iexact=admin_email).first()
            if holder is not None and (existing is None or holder.institution_id != existing.pk):
                problems.append(f"{admin_email} already has an account elsewhere.")
        if code in seen:
            problems.append(f"Same code as row {seen[code]}.")
        seen[code] = n
        if problems:
            _err(errors, SHEET_INSTITUTIONS, n, f"{code} {name}".strip(), problems)
            continue
        codes.add(code)
        row = {"_sheet": "institution", "_row": n, "_raw": {k: v for k, v in raw.items() if k != "_row"},
               "code": code, "name": name, "ownership_id": own.pk, "type_id": itype.pk, "category_id": cat.pk,
               "state_id": state.pk, "lga": lga, "address": raw.get("address", "").strip(),
               "town": raw.get("town", "").strip(), "official_email": raw.get("official_email", "").strip().lower(),
               "phone": raw.get("phone", "").strip(), "head": raw.get("head", "").strip(),
               "year_established": int(year) if year else None, "website": website,
               "cac_rc_number": raw.get("cac_rc_number", "").strip(), "admin_name": raw.get("admin_name", "").strip(),
               "admin_email": admin_email, "status": status,
               "approved_on": approved_on.isoformat() if approved_on else "",
               "_label": f"Institution · {code} {name}"}
        if existing is None:
            dept = _department_for(cat, itype, status)
            row["_action"] = "create"
            row["_after"] = (f"new · {status} · {getattr(dept, 'name', '—')}"
                             + (f" · portal admin {admin_email} (inactive)" if admin_email else ""))
        else:
            row["_id"] = existing.pk
            changes = _institution_changes(existing, row)
            row["_action"] = "update" if changes else "unchanged"
            row["_after"] = "; ".join(changes)[:250] or "no change"
        clean.append(row)
    return clean, errors, codes


def _department_for(category, itype, status):
    probe = Institution(category=category, institution_type=itype, status=status)
    return probe.derive_handling_department(status=status)


def _institution_changes(inst, row):
    out = []

    def cmp(label, before, after):
        if (before or "") != (after or ""):
            out.append(f"{label}: {before or '—'} → {after or '—'}")

    cmp("name", inst.name, row["name"])
    cmp("ownership", getattr(inst.ownership, "name", ""), InstitutionOwnership.objects.get(pk=row["ownership_id"]).name)
    cmp("type", getattr(inst.institution_type, "name", ""), InstitutionType.objects.get(pk=row["type_id"]).name)
    cmp("category", getattr(inst.category, "name", ""), InstitutionCategory.objects.get(pk=row["category_id"]).name)
    from staff.models import StateOfOrigin

    cmp("state", inst.state_name, StateOfOrigin.objects.get(pk=row["state_id"]).name)
    cmp("LGA", inst.lga, row["lga"])
    if row.get("year_established"):
        cmp("year", str(inst.year_established or ""), str(row["year_established"]))
    if row.get("website"):
        cmp("website", inst.website, row["website"])
    if row.get("cac_rc_number"):
        cmp("CAC", inst.cac_rc_number, row["cac_rc_number"])
    for kind, key, attr in (("official_email", "official_email", "value"), ("phone", "phone", "value"),
                            ("head_of_institution", "head", "name")):
        if row.get(key):
            current = inst.contact(kind)
            cmp(kind.replace("_", " "), getattr(current, attr, "") if current else "", row[key])
    if row.get("address"):
        addr = inst.address("permanent_site")
        cmp("address", addr.line1 if addr else "", row["address"])
    if row.get("admin_email") and not InstitutionUser.unscoped.filter(institution=inst,
                                                                      user__email__iexact=row["admin_email"]).exists():
        out.append(f"portal admin: add {row['admin_email']} (inactive)")
    return out


def _programme_key(raw, codes, problems):
    code = raw.get("institution_code", "").strip().upper()
    if not code:
        problems.append("Institution code is required.")
    elif code not in codes and not Institution.unscoped.filter(code=code).exists():
        problems.append(f"No institution has the code '{code}' (on the Institutions sheet or in the system).")
    name = " ".join(raw.get("name", "").split())
    if len(name) < 2:
        problems.append("Programme name is required.")
    level = _lookup(ProgrammeLevel, raw.get("level"))
    if level is None:
        problems.append(f"Level '{raw.get('level', '')}' is not recognised.")
    return code, name, level


def _validate_programmes(rows, codes):
    clean, errors, seen = [], [], {}
    for raw in rows:
        n, problems = raw["_row"], []
        code, name, level = _programme_key(raw, codes, problems)
        category = None
        if raw.get("category"):
            category = ProgrammeCategory.objects.filter(name__iexact=raw["category"].strip()).first()
            if category is None:
                problems.append(f"Category '{raw['category']}' is not recognised.")
        status = _programme_status(raw.get("status"))
        if status is None or status == "dropped":
            problems.append(f"Status '{raw.get('status', '')}' is not valid "
                            f"({', '.join(c for c in PROGRAMME_STATUSES if c != 'dropped')}).")
        atype = None
        if raw.get("approval_type"):
            atype = resolve_approval_type(raw["approval_type"])
            if atype is None:
                problems.append(f"Approval type '{raw['approval_type']}' is not recognised "
                                f"({', '.join(n for _k, n, _d, _o in approval_types())}).")
        streams = raw.get("approved_streams", "").strip() or "0"
        if not streams.isdigit():
            problems.append(f"Approved streams '{streams}' must be a whole number.")
        dates = {}
        for key in ("commencement_date", "expiry_date"):
            try:
                dates[key] = _date(raw.get(key))
            except ValueError as exc:
                problems.append(str(exc))
        start, expiry, derived = dates.get("commencement_date"), dates.get("expiry_date"), False
        if expiry is None and start is not None and atype is not None and atype[2]:
            expiry, derived = add_months(start, atype[2]), True
        if start and expiry and expiry <= start:
            problems.append("Expiry date must be after the commencement date.")
        body = None
        if raw.get("professional_body"):
            body = _body(raw["professional_body"])
            if body is None:
                problems.append(f"Professional body '{raw['professional_body']}' is not on the professional "
                                "bodies list (add it there first).")
        key = (code, _norm(name), getattr(level, "pk", None))
        if key in seen:
            problems.append(f"Same programme as row {seen[key]}.")
        seen[key] = n
        if problems:
            _err(errors, SHEET_PROGRAMMES, n, f"{code} {name}".strip(), problems)
            continue
        inst = Institution.unscoped.filter(code=code).first()
        existing = (Programme.unscoped.filter(institution=inst, name__iexact=name, level=level).first()
                    if inst is not None else None)
        row = {"_sheet": "programme", "_row": n, "_raw": {k: v for k, v in raw.items() if k != "_row"},
               "institution_code": code, "name": name, "level_id": level.pk,
               "category_id": category.pk if category else None, "status": status,
               "approval_type": atype[1] if atype else "", "approved_streams": int(streams),
               "commencement_date": start.isoformat() if start else "",
               "expiry_date": expiry.isoformat() if expiry else "", "expiry_derived": derived,
               "body_id": body.pk if body else None,
               "_label": f"Programme · {code} · {level.code.upper()} {name}"}
        after = [PROGRAMME_STATUSES.get(status, status)]
        if atype:
            after.append(atype[1])
        if expiry:
            after.append(f"expires {expiry:%d %b %Y}{' (derived)' if derived else ''}")
        if body:
            after.append(body.acronym or body.name)
        if existing is None:
            row["_action"], row["_after"] = "create", "new · " + " · ".join(after)
        else:
            row["_id"] = existing.pk
            changes = _programme_changes(existing, row)
            row["_action"] = "update" if changes else "unchanged"
            row["_after"] = ", ".join(changes) or "no change"
        clean.append(row)
    return clean, errors


def _approval_type_name(programme):
    if programme_has_approval_type():
        obj = getattr(programme, "approval_type", None)
        return str(getattr(obj, "name", "") or "") if obj is not None else ""
    return ""


def _programme_changes(existing, row):
    changes = []
    if existing.status != row["status"]:
        changes.append(f"status {existing.status} → {row['status']}")
    if row["category_id"] and (existing.category_id or None) != row["category_id"]:
        changes.append("category")
    if existing.approved_streams != row["approved_streams"]:
        changes.append(f"streams {existing.approved_streams} → {row['approved_streams']}")
    for key in ("commencement_date", "expiry_date"):
        before = getattr(existing, key)
        if row[key] and (before.isoformat() if before else "") != row[key]:
            changes.append(key.replace("_", " "))
    if row["approval_type"] and programme_has_approval_type() and \
            _norm(_approval_type_name(existing)) != _norm(row["approval_type"]):
        changes.append(f"approval type → {row['approval_type']}")
    if row.get("body_id"):
        from .professional_bodies import bodies_for_programme

        if row["body_id"] not in {b.pk for b in bodies_for_programme(existing)}:
            changes.append("professional body link")
    return changes


HISTORY_EVENT = "programme.history_imported"


def _history_text(row, programme_label):
    parts = [f"{row['date']}: {programme_label} — {PROGRAMME_STATUSES.get(row['status'], row['status'])}"]
    if row.get("approval_type"):
        parts.append(f"({row['approval_type']})")
    text = " ".join(parts)
    if row.get("note"):
        text += f". {row['note']}"
    return f"Accreditation history from the previous system: {text}"


def _validate_history(rows, codes, new_programmes):
    from qa_core.models import TimelineEvent

    clean, errors, seen = [], [], {}
    for raw in rows:
        n, problems = raw["_row"], []
        code, name, level = _programme_key(raw, codes, problems)
        try:
            day = _date(raw.get("date"))
        except ValueError as exc:
            day = None
            problems.append(str(exc))
        if day is None and not any("date" in p for p in problems):
            problems.append("Date is required.")
        elif day and day > timezone.localdate():
            problems.append("The date is in the future.")
        status = _programme_status(raw.get("status"))
        if status is None:
            problems.append(f"Status '{raw.get('status', '')}' is not valid.")
        atype = resolve_approval_type(raw.get("approval_type")) if raw.get("approval_type") else None
        if raw.get("approval_type") and atype is None:
            problems.append(f"Approval type '{raw['approval_type']}' is not recognised.")
        if level is not None and code:
            inst = Institution.unscoped.filter(code=code).first()
            exists = inst is not None and Programme.unscoped.filter(institution=inst, name__iexact=name,
                                                                    level=level).exists()
            if not exists and (code, _norm(name), level.pk) not in new_programmes:
                problems.append("The programme is not on the Programmes sheet or in the system.")
        key = (code, _norm(name), getattr(level, "pk", None), day, status)
        if key in seen:
            problems.append(f"Same as row {seen[key]}.")
        seen[key] = n
        if problems:
            _err(errors, SHEET_HISTORY, n, f"{code} {name}".strip(), problems)
            continue
        row = {"_sheet": "history", "_row": n, "_raw": {k: v for k, v in raw.items() if k != "_row"},
               "institution_code": code, "name": name, "level_id": level.pk, "date": day.isoformat(),
               "status": status, "approval_type": atype[1] if atype else "", "note": raw.get("note", "").strip()[:300],
               "_label": f"History · {code} · {level.code.upper()} {name} · {day:%d %b %Y}"}
        text = _history_text(row, f"{level.code.upper()} {name}")
        row["_text"] = text
        inst = Institution.unscoped.filter(code=code).first()
        done = inst is not None and TimelineEvent.unscoped.filter(institution=inst, event_code=HISTORY_EVENT,
                                                                  public_text=text).exists()
        row["_action"] = "unchanged" if done else "create"
        row["_after"] = "already recorded" if done else PROGRAMME_STATUSES.get(status, status)
        clean.append(row)
    return clean, errors


def revalidate(batch):
    sheets = {SHEET_INSTITUTIONS: [], SHEET_PROGRAMMES: [], SHEET_HISTORY: []}
    titles = {"institution": SHEET_INSTITUTIONS, "programme": SHEET_PROGRAMMES, "history": SHEET_HISTORY}
    for row in batch.rows or []:
        sheets[titles[row["_sheet"]]].append({**row.get("_raw", {}), "_row": row["_row"]})
    clean, errors = validate(sheets)
    if errors:
        lines = [f"{e['row']}: {'; '.join(e['errors'])}" for e in errors[:10]]
        raise ValidationError(["The data changed since validation — upload the workbook again."] + lines)
    return clean


# ===========================================================================
# Apply (inside the PendingAction's transaction)
# ===========================================================================
def apply(batch, *, actor, send_invites=False):
    rows = revalidate(batch)
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    touched, created_codes = [], []
    by_code = {}
    for row in rows:
        if row["_action"] == "unchanged":
            counts["unchanged"] += 1
            if row["_sheet"] == "institution":
                by_code[row["code"]] = Institution.unscoped.get(pk=row["_id"])
                touched.append(row["code"])
            continue
        if row["_sheet"] == "institution":
            inst = _apply_institution(row, actor, batch)
            by_code[inst.code] = inst
            touched.append(inst.code)
            if row["_action"] == "create":
                created_codes.append(inst.code)
        elif row["_sheet"] == "programme":
            _apply_programme(row, actor, batch)
        else:
            _apply_history(row, actor)
        counts["created" if row["_action"] == "create" else "updated"] += 1
    invites = {}
    if send_invites:
        invites = send_set_password_links([by_code.get(c) or Institution.unscoped.get(code=c) for c in touched],
                                          actor=actor)
    batch.status = "applied"
    batch.result = {**counts, "created_codes": created_codes, "institution_codes": touched,
                    "invites": invites, "applied_at": timezone.now().isoformat()}
    batch.save(update_fields=["status", "result", "updated_at"])
    return counts


def _apply_institution(row, actor, batch):
    from accounts.models import Role, User
    from staff.models import StateOfOrigin

    state = StateOfOrigin.objects.get(pk=row["state_id"])
    if row["_action"] == "create":
        approved_at = None
        if row.get("approved_on"):
            day = datetime.date.fromisoformat(row["approved_on"])
            approved_at = timezone.make_aware(datetime.datetime.combine(day, datetime.time(12)))
        elif row["status"] != Institution.PROPOSED:
            approved_at = timezone.now()
        inst = Institution(
            code=row["code"], name=row["name"], ownership_id=row["ownership_id"], institution_type_id=row["type_id"],
            category_id=row["category_id"], state=state, lga=row["lga"], year_established=row.get("year_established"),
            website=row.get("website", ""), cac_rc_number=row.get("cac_rc_number", ""), status=row["status"],
            origin="legacy", portal_enabled=True, approved_at=approved_at)
        inst.handling_department = inst.derive_handling_department(status=row["status"])
        if inst.handling_department is None:
            raise ValidationError(f"{row['code']}: no handling department for its category.")
        inst.recompute_zone()
        inst.full_clean()
        inst.save()
        services.lifecycle(inst)  # adopted at its status, never through SOP-01
        for kind, value, name in (("official_email", row.get("official_email"), ""), ("phone", row.get("phone"), ""),
                                  ("head_of_institution", "", row.get("head"))):
            if value or name:
                InstitutionContact.unscoped.create(institution=inst, kind=kind, value=value or "", name=name or "")
        if row.get("address"):
            InstitutionAddress.unscoped.create(institution=inst, kind="permanent_site", line1=row["address"],
                                               town=row.get("town", ""), lga=row["lga"], state=state)
        timeline.record(inst, "institution.migrated", actor=actor, visibility="public",
                        public_text=f"{inst.name} ({inst.code}) was imported from the previous system as "
                                    f"{inst.status_label.lower()}.",
                        internal_text=f"Imported from the previous system (migration batch #{batch.pk}); handled by "
                                      f"{inst.handling_department.name}; no onboarding application.")
    else:
        inst = Institution.unscoped.select_for_update().get(pk=row["_id"])
        changes = _institution_changes(inst, row)
        if inst.name != row["name"]:
            inst.former_names = list(inst.former_names or []) + [
                {"name": inst.name, "until": timezone.localdate().isoformat()}]
            inst.name = row["name"]
        inst.ownership_id, inst.institution_type_id, inst.category_id = (row["ownership_id"], row["type_id"],
                                                                         row["category_id"])
        inst.state, inst.lga = state, row["lga"]
        inst.recompute_zone()
        for field in ("year_established", "website", "cac_rc_number"):
            if row.get(field):
                setattr(inst, field, row[field])
        if inst.status in (Institution.APPROVED, Institution.SUSPENDED):
            inst.handling_department = inst.derive_handling_department() or inst.handling_department
        inst.save()
        for kind, key, attr in (("official_email", "official_email", "value"), ("phone", "phone", "value"),
                                ("head_of_institution", "head", "name")):
            if row.get(key):
                current = inst.contact(kind)
                if (getattr(current, attr, "") if current else "") != row[key]:
                    if attr == "name":
                        services.set_contact(inst, kind, current.value if current else "", row[key], actor=actor)
                    else:
                        services.set_contact(inst, kind, row[key], current.name if current else "", actor=actor)
        if row.get("address"):
            addr = inst.address("permanent_site")
            if not addr or addr.line1 != row["address"]:
                services.set_address(inst, "permanent_site", line1=row["address"], town=row.get("town", ""),
                                     lga=row["lga"], state=state)
        timeline.record(inst, "institution.migrated", actor=actor, visibility="internal",
                        internal_text=f"Updated from the previous system (migration batch #{batch.pk}): "
                                      f"{'; '.join(changes)}"[:2000])
    email = row.get("admin_email")
    if email and not InstitutionUser.unscoped.filter(institution=inst, user__email__iexact=email).exists():
        first, _sp, last = (row.get("admin_name") or "").partition(" ")
        role = Role.objects.filter(code="inst-admin").first()
        user = User(email=email, first_name=first[:150], last_name=last[:150], user_type=User.INSTITUTION,
                    institution=inst, role=role, is_active=False, must_change_password=False)
        user.set_unusable_password()
        user.save()
        primary = not InstitutionUser.unscoped.filter(institution=inst, is_primary_contact=True).exists()
        InstitutionUser.unscoped.create(institution=inst, user=user, institution_role=role,
                                        is_primary_contact=primary, invited_at=timezone.now(),
                                        invited_by=actor if getattr(actor, "pk", None) else None)
        timeline.record(inst, "institution.migrated_admin", actor=actor, visibility="internal",
                        internal_text=f"Portal administrator {email} created inactive (migration). Send the "
                                      "login credentials to let them set a password.")
    return inst


def set_approval_type(programme, name):
    """Set ``programme.approval_type`` when the field and model exist. Returns
    True when stored on the record."""
    if not name or not programme_has_approval_type():
        return False
    item = resolve_approval_type(name)
    if item is None or item[3] is None:
        return False
    programme.approval_type = item[3]
    return True


def _apply_programme(row, actor, batch):
    inst = Institution.unscoped.get(code=row["institution_code"])
    def day(value):
        return datetime.date.fromisoformat(value) if value else None

    data = {"status": row["status"], "approved_streams": row["approved_streams"],
            "commencement_date": day(row.get("commencement_date")),
            "expiry_date": day(row.get("expiry_date"))}
    if row.get("category_id"):
        data["category_id"] = row["category_id"]
    if row["_action"] == "create":
        programme = Programme(institution=inst, name=row["name"], level_id=row["level_id"], **data)
    else:
        programme = Programme.unscoped.select_for_update().get(pk=row["_id"])
        for key, value in data.items():
            if key in ("commencement_date", "expiry_date") and not value:
                continue
            setattr(programme, key, value)
    stored_type = set_approval_type(programme, row.get("approval_type"))
    programme.save()
    body_text = ""
    if row.get("body_id"):
        body = ProfessionalBody.objects.get(pk=row["body_id"])
        link_body(programme, body, actor=actor)
        body_text = f" Professional body: {body.name}."
    label = PROGRAMME_STATUSES.get(row["status"], row["status"])
    bits = [label]
    if row.get("approval_type"):
        bits.append(f"approval type {row['approval_type']}")
    if programme.expiry_date:
        bits.append(f"expires {programme.expiry_date:%d %b %Y}")
    verb = "imported from the previous system" if row["_action"] == "create" else "updated from the previous system"
    note = "" if stored_type or not row.get("approval_type") else f" Approval type (kept here): {row['approval_type']}."
    derived = " The expiry date was derived from the approval type." if row.get("expiry_derived") else ""
    timeline.record(inst, "programme.migrated", subject=programme, actor=actor, visibility="public",
                    public_text=f"{programme} was {verb}: {', '.join(bits)}.",
                    internal_text=f"Migration batch #{batch.pk}.{note}{derived}{body_text}".strip())
    return programme


def link_body(programme, body, *, actor=None):
    """Make sure ``body`` regulates ``programme`` through the NBTE-wide
    mapping (by programme name; this level). Returns the mapping."""
    key = programme_key(programme.name)
    mapping = (ProgrammeBodyMapping.objects.filter(body=body, programme_key=key)
               .filter(models_q_level(programme)).first())
    if mapping is None:
        mapping = ProgrammeBodyMapping(body=body, programme_name=programme.name, level_id=programme.level_id,
                                       note="Linked by migration from the previous system",
                                       created_by=actor if getattr(actor, "pk", None) else None)
        mapping.save()
    elif not mapping.is_active:
        mapping.is_active = True
        mapping.save(update_fields=["is_active", "updated_at"])
    return mapping


def models_q_level(programme):
    from django.db.models import Q

    return Q(level__isnull=True) | Q(level_id=programme.level_id)


def _apply_history(row, actor):
    inst = Institution.unscoped.get(code=row["institution_code"])
    programme = Programme.unscoped.filter(institution=inst, name__iexact=row["name"], level_id=row["level_id"]).first()
    day = datetime.date.fromisoformat(row["date"])
    at = timezone.make_aware(datetime.datetime.combine(day, datetime.time(12)))
    timeline.record(inst, HISTORY_EVENT, subject=programme, actor=actor, visibility="public",
                    public_text=row["_text"], internal_text="Imported from the previous system.", at=at)


# ===========================================================================
# Set-password links (on confirmation, or later in bulk)
# ===========================================================================
def pending_invites(institutions):
    """Institutions whose primary portal user is inactive and not yet sent a link."""
    out = []
    for inst in institutions:
        if inst is None:
            continue
        link = (InstitutionUser.unscoped.filter(institution=inst).select_related("user")
                .order_by("-is_primary_contact", "id").first())
        if link and not link.user.is_active and link.credentials_last_sent_at is None and inst.portal_enabled:
            out.append(inst)
    return out


def send_set_password_links(institutions, *, actor):
    """E-mail a one-time set-password link to each institution's primary
    portal user that has none yet. Never raises: returns {code: "sent" | reason}."""
    result = {}
    for inst in pending_invites(institutions):
        try:
            services.send_credentials(inst, actor=actor)
            result[inst.code] = "sent"
        except ValidationError as exc:
            result[inst.code] = "; ".join(exc.messages)
        except Exception as exc:  # pragma: no cover - mail backend failure
            result[inst.code] = f"not sent ({type(exc).__name__})"
    return result
