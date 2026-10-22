"""Bulk import / export of institutions and programmes (openpyxl).

* **Template** — a styled workbook with dropdowns (ownership, type, category,
  state, level) and a Reference sheet of valid values.
* **Import** — the whole sheet is validated first. One bad row rejects the
  batch: nothing is applied, and the officer gets every error with its row
  number. A valid batch waits for a second officer (ActionPolicy
  ``institutions.bulk_import``); at confirmation it is validated again and
  applied in one transaction.
* **Idempotent** — rows match existing records by institution code, then by
  name (institutions) or by institution + name + level (programmes); a match
  is updated, never duplicated. Re-importing an unchanged export changes
  nothing.
* New institutions are always created with status ``proposed``; an import can
  never change an institution's status (that is what the §6 actions are for).
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import io
import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.db import transaction
from django.utils import timezone

from qa_core import timeline

from . import services
from .models import (
    Institution,
    InstitutionCategory,
    InstitutionImportBatch,
    InstitutionOwnership,
    InstitutionType,
    Programme,
    ProgrammeCategory,
    ProgrammeLevel,
)

INSTITUTION_COLUMNS = [
    # header, key, required, help
    ("Code", "code", False, "3–6 letters/digits. Blank = allocate one (new institutions)."),
    ("Name", "name", True, "Full name of the institution."),
    ("Ownership", "ownership", True, "Federal, State or Private."),
    ("Type", "type", True, "e.g. Polytechnic."),
    ("Category", "category", True, "Decides the handling department once approved."),
    ("State", "state", True, "State of the institution."),
    ("LGA", "lga", True, "Local government area."),
    ("Address", "address", False, "Permanent-site address."),
    ("Town", "town", False, ""),
    ("Official e-mail", "official_email", False, ""),
    ("Phone", "phone", False, ""),
    ("Head of institution", "head", False, "Name of the head (Rector/Provost/Principal)."),
    ("Year established", "year_established", False, "e.g. 2015."),
    ("Website", "website", False, "https://…"),
    ("CAC RC number", "cac_rc_number", False, "Private institutions."),
    ("Status", "status", False, "Leave blank. New rows are created as proposed; status cannot be changed here."),
]
PROGRAMME_COLUMNS = [
    ("Institution code", "institution_code", True, "Code of an existing institution."),
    ("Programme name", "name", True, "e.g. Computer Science."),
    ("Level", "level", True, "ND, HND, NID, NVC or ADMIN."),
    ("Category", "category", False, "Programme category (see Reference)."),
    ("Status", "status", False, "proposed (default), accredited, interim, approval_to_commence, expired, denied, "
                                "resource_inspected, dropped."),
    ("Approved streams", "approved_streams", False, "Whole number."),
    ("Commencement date", "commencement_date", False, "YYYY-MM-DD"),
    ("Expiry date", "expiry_date", False, "YYYY-MM-DD"),
]
COLUMNS = {"institutions": INSTITUTION_COLUMNS, "programmes": PROGRAMME_COLUMNS}
PROGRAMME_STATUSES = [code for code, _l in Programme.STATUS_CHOICES]


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def _lookup(model, value, *, fields=("code", "name")):
    key = _norm(value)
    if not key:
        return None
    for obj in model.objects.filter(is_active=True):
        if any(_norm(getattr(obj, f, "")) == key for f in fields):
            return obj
    return None


def _state(value):
    from staff.models import StateOfOrigin

    key = _norm(str(value).replace(" State", "").replace(" state", ""))
    if not key:
        return None
    for s in StateOfOrigin.objects.all():
        if _norm(s.name.replace(" State", "")) == key or _norm(s.code or "") == key:
            return s
    return None


def _date(value):
    text = _cell(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{text}' is not a date (use YYYY-MM-DD).")


# ===========================================================================
# Template and export
# ===========================================================================
def _workbook(kind, rows=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    columns = COLUMNS[kind]
    wb = Workbook()
    ws = wb.active
    ws.title = "Institutions" if kind == "institutions" else "Programmes"
    head_fill = PatternFill("solid", fgColor="1F6B3E")
    for i, (header, _key, required, help_text) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=i, value=header + (" *" if required else ""))
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = max(14, min(42, len(header) + 10))
        if help_text:
            from openpyxl.comments import Comment

            cell.comment = Comment(help_text, "NBTE QA")
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22
    for r, row in enumerate(rows or [], start=2):
        for c, (_h, key, _req, _help) in enumerate(columns, start=1):
            ws.cell(row=r, column=c, value=row.get(key, ""))

    ref = wb.create_sheet("Reference")
    lists = _reference_lists(kind)
    for c, (title, values) in enumerate(lists.items(), start=1):
        ref.cell(row=1, column=c, value=title).font = Font(bold=True)
        ref.column_dimensions[get_column_letter(c)].width = 34
        for r, value in enumerate(values, start=2):
            ref.cell(row=r, column=c, value=value)
        header_index = {h: i for i, (h, _k, _r, _x) in enumerate(columns, start=1)}
        if title in header_index and values:
            col = get_column_letter(header_index[title])
            letter = get_column_letter(c)
            dv = DataValidation(type="list", formula1=f"=Reference!${letter}$2:${letter}${len(values) + 1}",
                                allow_blank=True, showErrorMessage=False)
            ws.add_data_validation(dv)
            dv.add(f"{col}2:{col}2000")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _reference_lists(kind):
    from staff.models import StateOfOrigin

    if kind == "institutions":
        return {
            "Ownership": [o.name for o in InstitutionOwnership.objects.filter(is_active=True)],
            "Type": [t.name for t in InstitutionType.objects.filter(is_active=True)],
            "Category": [c.name for c in InstitutionCategory.objects.filter(is_active=True)],
            "State": [s.name for s in StateOfOrigin.objects.order_by("name")],
        }
    return {
        "Level": [lv.code.upper() for lv in ProgrammeLevel.objects.filter(is_active=True)],
        "Category": [c.name for c in ProgrammeCategory.objects.filter(is_active=True)],
        "Status": PROGRAMME_STATUSES,
    }


def template_xlsx(kind):
    return _workbook(kind)


def export_institutions_xlsx(institutions):
    rows = []
    for inst in institutions:
        addr = inst.address("permanent_site") or inst.address()
        head = inst.contact("head_of_institution")
        email = inst.contact("official_email")
        phone = inst.contact("phone")
        rows.append({
            "code": inst.code, "name": inst.name, "ownership": getattr(inst.ownership, "name", ""),
            "type": getattr(inst.institution_type, "name", ""), "category": getattr(inst.category, "name", ""),
            "state": inst.state_name, "lga": inst.lga, "address": addr.line1 if addr else "",
            "town": addr.town if addr else "", "official_email": email.value if email else "",
            "phone": phone.value if phone else "", "head": head.name if head else "",
            "year_established": inst.year_established or "", "website": inst.website,
            "cac_rc_number": inst.cac_rc_number, "status": "",
        })
    return _workbook("institutions", rows)


def export_programmes_xlsx(programmes):
    rows = [{"institution_code": p.institution.code, "name": p.name,
             "level": p.level.code.upper() if p.level_id else "", "category": getattr(p.category, "name", ""),
             "status": p.status, "approved_streams": p.approved_streams,
             "commencement_date": p.commencement_date.isoformat() if p.commencement_date else "",
             "expiry_date": p.expiry_date.isoformat() if p.expiry_date else ""} for p in programmes]
    return _workbook("programmes", rows)


# ===========================================================================
# Parse + validate
# ===========================================================================
def read_rows(upload, kind):
    from openpyxl import load_workbook

    try:
        upload.seek(0)
        wb = load_workbook(upload, read_only=True, data_only=True)
    except Exception:
        raise ValidationError("The file could not be read as an Excel workbook (.xlsx).") from None
    ws = wb.worksheets[0]
    columns = COLUMNS[kind]
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None) or ()
    positions = {}
    for idx, value in enumerate(header_row):
        key = _norm(str(value or "").replace("*", ""))
        for header, field, _req, _help in columns:
            if key == _norm(header):
                positions[field] = idx
    missing = [h for h, f, req, _x in columns if req and f not in positions]
    if missing:
        raise ValidationError(f"Missing column(s): {', '.join(missing)}. Download the template and keep its headers.")
    rows = []
    for number, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        data = {field: _cell(values[pos]) if pos < len(values) else "" for field, pos in positions.items()}
        if not any(data.values()):
            continue
        data["_row"] = number
        rows.append(data)
    wb.close()
    if not rows:
        raise ValidationError("The sheet has no data rows.")
    if len(rows) > 2000:
        raise ValidationError("Import at most 2,000 rows at a time.")
    return rows


def validate_rows(kind, rows):
    """Return (clean_rows, errors). Clean rows carry ids and an action."""
    return (_validate_institutions if kind == "institutions" else _validate_programmes)(rows)


def _validate_institutions(rows):
    clean, errors = [], []
    seen_codes, seen_names = {}, {}
    url = URLValidator()
    for raw in rows:
        n = raw["_row"]
        problems = []
        name = raw.get("name", "").strip()
        code = raw.get("code", "").strip().upper()
        if len(name) < 4:
            problems.append("Name is required.")
        if code and not re.fullmatch(r"[A-Z0-9]{3,6}", code):
            problems.append(f"Code '{code}' must be 3–6 uppercase letters or digits.")
        if raw.get("status", "").strip() and raw["status"].strip().lower() != Institution.PROPOSED:
            problems.append("Status cannot be set or changed by import (leave it blank).")
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
            try:
                services.validate_lga(state, lga)
            except ValidationError as exc:
                problems += exc.messages
        email = raw.get("official_email", "").strip()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                problems.append(f"'{email}' is not a valid e-mail address.")
        year = raw.get("year_established", "").strip()
        if year:
            if not year.isdigit() or not 1900 <= int(year) <= timezone.localdate().year + 1:
                problems.append(f"Year established '{year}' is not a year.")
        website = raw.get("website", "").strip()
        if website:
            try:
                url(website)
            except ValidationError:
                problems.append(f"Website '{website}' is not a URL (start with https://).")
        # Matching: code first, then name.
        existing = Institution.unscoped.filter(code=code).first() if code else None
        by_name = Institution.unscoped.filter(name__iexact=name).first() if name else None
        if existing is None and by_name is not None:
            if code and by_name.code != code:
                problems.append(f"An institution named '{name}' already exists with code {by_name.code}.")
            existing = by_name
        if existing is not None and by_name is not None and by_name.pk != existing.pk:
            problems.append(f"The name '{name}' belongs to another institution ({by_name.code}).")
        if existing is not None and existing.status == Institution.DELISTED:
            problems.append(f"{existing.code} is delisted; relist it before updating.")
        dup_key = code or _norm(name)
        if dup_key in seen_codes:
            problems.append(f"Duplicate of row {seen_codes[dup_key]}.")
        seen_codes[dup_key] = n
        if _norm(name) in seen_names and seen_names[_norm(name)] != n and dup_key != _norm(name):
            problems.append(f"Same name as row {seen_names[_norm(name)]}.")
        seen_names[_norm(name)] = n
        if problems:
            errors.append({"row": n, "label": name or code or "(blank)", "errors": problems})
            continue
        row = {"_row": n, "name": name, "code": code, "ownership_id": own.pk, "type_id": itype.pk,
               "category_id": cat.pk, "state_id": state.pk, "lga": lga, "address": raw.get("address", "").strip(),
               "town": raw.get("town", "").strip(), "official_email": email.lower(), "phone": raw.get("phone", "").strip(),
               "head": raw.get("head", "").strip(), "year_established": int(year) if year else None,
               "website": website, "cac_rc_number": raw.get("cac_rc_number", "").strip(), "_label": name}
        if existing is None:
            row["_action"] = "create"
            row["_after"] = f"new · {own.name} · {cat.name} · {state.name}"
        else:
            row["_id"] = existing.pk
            row["code"] = existing.code
            changes = _institution_changes(existing, row)
            row["_action"] = "update" if changes else "unchanged"
            row["_before"] = "; ".join(f"{k}: {b}" for k, (b, _a) in changes.items())[:250]
            row["_after"] = "; ".join(f"{k}: {a}" for k, (_b, a) in changes.items())[:250] or "no change"
        clean.append(row)
    return clean, errors


def _institution_changes(inst, row):
    out = {}

    def cmp(label, before, after):
        if (before or "") != (after or ""):
            out[label] = (before or "—", after or "—")

    cmp("name", inst.name, row["name"])
    cmp("ownership", getattr(inst.ownership, "name", ""), InstitutionOwnership.objects.get(pk=row["ownership_id"]).name)
    cmp("type", getattr(inst.institution_type, "name", ""), InstitutionType.objects.get(pk=row["type_id"]).name)
    cmp("category", getattr(inst.category, "name", ""), InstitutionCategory.objects.get(pk=row["category_id"]).name)
    from staff.models import StateOfOrigin

    cmp("state", inst.state_name, StateOfOrigin.objects.get(pk=row["state_id"]).name)
    cmp("lga", inst.lga, row["lga"])
    cmp("year", str(inst.year_established or ""), str(row["year_established"] or ""))
    cmp("website", inst.website, row["website"])
    cmp("CAC", inst.cac_rc_number, row["cac_rc_number"])
    for kind, key, attr in (("official_email", "official_email", "value"), ("phone", "phone", "value"),
                            ("head_of_institution", "head", "name")):
        if row.get(key):
            current = inst.contact(kind)
            cmp(kind.replace("_", " "), getattr(current, attr, "") if current else "", row[key])
    if row.get("address"):
        addr = inst.address("permanent_site")
        cmp("address", addr.line1 if addr else "", row["address"])
    return out


def _validate_programmes(rows):
    clean, errors, seen = [], [], {}
    for raw in rows:
        n = raw["_row"]
        problems = []
        code = raw.get("institution_code", "").strip().upper()
        inst = Institution.unscoped.filter(code=code).first() if code else None
        if not code:
            problems.append("Institution code is required.")
        elif inst is None:
            problems.append(f"No institution has the code '{code}'.")
        name = raw.get("name", "").strip()
        if len(name) < 2:
            problems.append("Programme name is required.")
        level = _lookup(ProgrammeLevel, raw.get("level"))
        if level is None:
            problems.append(f"Level '{raw.get('level', '')}' is not recognised.")
        category = None
        if raw.get("category"):
            category = ProgrammeCategory.objects.filter(name__iexact=raw["category"].strip()).first()
            if category is None:
                problems.append(f"Category '{raw['category']}' is not recognised.")
        status = (raw.get("status") or "proposed").strip().lower()
        if status not in PROGRAMME_STATUSES:
            problems.append(f"Status '{status}' is not valid ({', '.join(PROGRAMME_STATUSES)}).")
        streams = raw.get("approved_streams", "").strip() or "0"
        if not streams.isdigit():
            problems.append(f"Approved streams '{streams}' must be a whole number.")
        dates = {}
        for key in ("commencement_date", "expiry_date"):
            try:
                dates[key] = _date(raw.get(key))
            except ValueError as exc:
                problems.append(str(exc))
        if dates.get("commencement_date") and dates.get("expiry_date") and \
                dates["expiry_date"] <= dates["commencement_date"]:
            problems.append("Expiry date must be after the commencement date.")
        if inst is not None and inst.status == Institution.DELISTED:
            problems.append(f"{code} is delisted.")
        key = (code, _norm(name), getattr(level, "pk", None))
        if key in seen:
            problems.append(f"Duplicate of row {seen[key]}.")
        seen[key] = n
        if problems:
            errors.append({"row": n, "label": f"{code} {name}".strip() or "(blank)", "errors": problems})
            continue
        existing = Programme.unscoped.filter(institution=inst, name__iexact=name, level=level).first()
        row = {"_row": n, "institution_id": inst.pk, "name": name, "level_id": level.pk,
               "category_id": category.pk if category else None, "status": status, "approved_streams": int(streams),
               "commencement_date": dates["commencement_date"].isoformat() if dates.get("commencement_date") else "",
               "expiry_date": dates["expiry_date"].isoformat() if dates.get("expiry_date") else "",
               "_label": f"{code} · {level.code.upper()} {name}"}
        if existing is None:
            row["_action"], row["_after"] = "create", f"new · {status}"
        else:
            row["_id"] = existing.pk
            changes = []
            if existing.status != status:
                changes.append(f"status {existing.status}→{status}")
            if (existing.category_id or None) != row["category_id"]:
                changes.append("category")
            if existing.approved_streams != row["approved_streams"]:
                changes.append(f"streams {existing.approved_streams}→{row['approved_streams']}")
            for key in ("commencement_date", "expiry_date"):
                before = getattr(existing, key)
                if (before.isoformat() if before else "") != row[key]:
                    changes.append(key.replace("_", " "))
            row["_action"] = "update" if changes else "unchanged"
            row["_after"] = ", ".join(changes) or "no change"
        clean.append(row)
    return clean, errors


def summarise(rows):
    out = {"create": 0, "update": 0, "unchanged": 0}
    for row in rows:
        out[row["_action"]] = out.get(row["_action"], 0) + 1
    return out


@transaction.atomic
def stage_batch(upload, kind, *, user):
    """Validate an uploaded sheet and keep it as a batch (invalid or validated)."""
    if kind == "legacy":  # Phase 8: migrate from the previous system (institutions/migration.py)
        from . import migration

        sheets = migration.read_workbook(upload)
        raw_rows = [r for rows in sheets.values() for r in rows]
        clean, errors = migration.validate(sheets)
    else:
        raw_rows = read_rows(upload, kind)
        clean, errors = validate_rows(kind, raw_rows)
    batch = InstitutionImportBatch(kind=kind, created_by=user, department=getattr(user, "department", None),
                                   rows=clean if not errors else [], errors=errors,
                                   summary={**summarise(clean), "errors": len(errors), "total": len(raw_rows)},
                                   status="invalid" if errors else "validated")
    upload.seek(0)
    batch.file = upload
    batch.save()
    return batch


def revalidate(batch):
    """Run the stored rows through validation again (at confirm time)."""
    if batch.kind == "legacy":
        from . import migration

        return migration.revalidate(batch)
    columns = COLUMNS[batch.kind]
    fields = [f for _h, f, _r, _x in columns]
    raw = []
    for row in batch.rows or []:
        item = {"_row": row["_row"]}
        if batch.kind == "institutions":
            item.update({
                "code": row.get("code", ""), "name": row.get("name", ""),
                "ownership": InstitutionOwnership.objects.filter(pk=row["ownership_id"]).values_list("name", flat=True).first() or "",
                "type": InstitutionType.objects.filter(pk=row["type_id"]).values_list("name", flat=True).first() or "",
                "category": InstitutionCategory.objects.filter(pk=row["category_id"]).values_list("name", flat=True).first() or "",
                "state": _state_name(row["state_id"]), "lga": row["lga"], "address": row.get("address", ""),
                "town": row.get("town", ""), "official_email": row.get("official_email", ""),
                "phone": row.get("phone", ""), "head": row.get("head", ""),
                "year_established": str(row.get("year_established") or ""), "website": row.get("website", ""),
                "cac_rc_number": row.get("cac_rc_number", ""), "status": "",
            })
        else:
            inst = Institution.unscoped.filter(pk=row["institution_id"]).first()
            level = ProgrammeLevel.objects.filter(pk=row["level_id"]).first()
            cat = ProgrammeCategory.objects.filter(pk=row["category_id"]).first() if row.get("category_id") else None
            item.update({"institution_code": inst.code if inst else "", "name": row["name"],
                         "level": level.code if level else "", "category": cat.name if cat else "",
                         "status": row["status"], "approved_streams": str(row["approved_streams"]),
                         "commencement_date": row.get("commencement_date", ""),
                         "expiry_date": row.get("expiry_date", "")})
        raw.append({k: v for k, v in item.items() if k in fields or k == "_row"})
    clean, errors = validate_rows(batch.kind, raw)
    if errors:
        lines = [f"Row {e['row']}: {'; '.join(e['errors'])}" for e in errors[:10]]
        raise ValidationError(["The data changed since validation — upload the sheet again."] + lines)
    return clean


def _state_name(pk):
    from staff.models import StateOfOrigin

    return StateOfOrigin.objects.filter(pk=pk).values_list("name", flat=True).first() or ""


# ===========================================================================
# Apply (inside the PendingAction's transaction)
# ===========================================================================
def apply_batch(batch, *, actor, options=None):
    if batch.kind == "legacy":
        from . import migration

        return migration.apply(batch, actor=actor, send_invites=bool((options or {}).get("send_invites")))
    rows = revalidate(batch)
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    created_codes = []
    for row in rows:
        if row["_action"] == "unchanged":
            counts["unchanged"] += 1
            continue
        if batch.kind == "institutions":
            code = _apply_institution(row, actor)
            if row["_action"] == "create":
                created_codes.append(code)
        else:
            _apply_programme(row, actor)
        counts["created" if row["_action"] == "create" else "updated"] += 1
    batch.status = "applied"
    batch.result = {**counts, "created_codes": created_codes, "applied_at": timezone.now().isoformat()}
    batch.save(update_fields=["status", "result", "updated_at"])
    return counts


def _apply_institution(row, actor):
    from staff.models import StateOfOrigin

    state = StateOfOrigin.objects.get(pk=row["state_id"])
    if row["_action"] == "create":
        inst = services.create_institution({
            "name": row["name"], "code": row.get("code", ""),
            "ownership": InstitutionOwnership.objects.get(pk=row["ownership_id"]),
            "institution_type": InstitutionType.objects.get(pk=row["type_id"]),
            "category": InstitutionCategory.objects.get(pk=row["category_id"]), "state": state, "lga": row["lga"],
            "year_established": row.get("year_established"), "website": row.get("website", ""),
            "cac_rc_number": row.get("cac_rc_number", ""),
            "contacts": {"official_email": {"value": row.get("official_email", "")},
                         "phone": {"value": row.get("phone", "")},
                         "head_of_institution": {"name": row.get("head", ""), "value": ""}},
            "address": {"line1": row.get("address", ""), "town": row.get("town", ""), "lga": row["lga"], "state": state},
        }, actor=actor, origin="import")
        return inst.code
    inst = Institution.unscoped.select_for_update().get(pk=row["_id"])
    changes = _institution_changes(inst, row)
    if "name" in changes:
        inst.former_names = list(inst.former_names or []) + [{"name": inst.name,
                                                              "until": timezone.localdate().isoformat()}]
        inst.name = row["name"]
    inst.ownership_id, inst.institution_type_id, inst.category_id = row["ownership_id"], row["type_id"], row["category_id"]
    inst.state, inst.lga = state, row["lga"]
    inst.recompute_zone()
    inst.year_established = row.get("year_established")
    inst.website, inst.cac_rc_number = row.get("website", ""), row.get("cac_rc_number", "")
    old_dept = inst.handling_department
    if inst.status in (Institution.APPROVED, Institution.SUSPENDED):
        inst.handling_department = inst.derive_handling_department() or old_dept
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
    note = "; ".join(f"{k}: {b} → {a}" for k, (b, a) in changes.items())
    if inst.handling_department != old_dept:
        note += f"; department transfer {old_dept} → {inst.handling_department}"
    timeline.record(inst, "institution.imported", actor=actor, visibility="internal",
                    internal_text=f"Updated by bulk import: {note}"[:2000])
    return inst.code


def _apply_programme(row, actor):
    data = {"category_id": row.get("category_id"), "status": row["status"], "approved_streams": row["approved_streams"],
            "commencement_date": row.get("commencement_date") or None, "expiry_date": row.get("expiry_date") or None}
    if row["_action"] == "create":
        Programme.unscoped.create(institution_id=row["institution_id"], name=row["name"], level_id=row["level_id"], **data)
    else:
        Programme.unscoped.filter(pk=row["_id"]).update(updated_at=timezone.now(), **data)
