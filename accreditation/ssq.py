"""The dynamic SSQ engine (Master Plan §5.4, Phase 3) — system code.

A template's ``schema`` is plain JSON, edited in Settings → SSQ templates::

    {"sections": [
        {"key": "governance", "title": "Governance", "description": "…",
         "department": true,                      # may be sent to an academic dept
         "fields": [
            {"key": "council_chair", "type": "text", "label": "Chairman of Council",
             "required": true, "help": "…", "prefill_from": "institution.contacts.head_of_institution.name",
             "validation": {"max_length": 150}},
            {"key": "organogram", "type": "file", "label": "Organogram", "document_kind": "organogram"},
            {"key": "workshop_equipment", "type": "catalogue", "label": "Workshop equipment",
             "catalogue": {"kind": "equipment"}},
            {"key": "academic_staff", "type": "table", "label": "Academic staff", "min_rows": 1,
             "columns": [{"key": "name", "label": "Name", "type": "text"}, …]},
         ]},
    ]}

This module validates schemas, computes prefill (accepted documents only —
never a draft or a returned document), cleans submitted values field by field,
binds catalogue fields (names come from the catalogue, the institution only
enters quantities: R-22), and renders the immutable submission snapshot.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.utils import timezone

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
FIELD_TYPES = {
    "text": "Short text", "textarea": "Long text", "email": "E-mail", "phone": "Phone", "url": "Web address",
    "number": "Number", "integer": "Whole number", "date": "Date", "choice": "One choice",
    "multi_choice": "Several choices", "yes_no": "Yes / No", "file": "File (evidence)",
    "catalogue": "Catalogue quantities", "table": "Table",
}
TABLE_COLUMN_TYPES = ("text", "number", "integer", "date", "choice", "yes_no", "email")
VALIDATION_KEYS = {"max_length", "min_length", "min", "max", "pattern", "min_date", "max_date", "max_rows",
                   "min_rows", "max_files"}
FIELD_KEYS = {"key", "type", "label", "required", "help", "prefill_from", "validation", "options", "document_kind",
              "catalogue", "columns", "min_rows", "max_rows", "width", "placeholder", "multiple", "scoring"}
SECTION_KEYS = {"key", "title", "description", "fields", "department", "scoring"}
# v2.4: report templates (visit / ICT verification) carry NBTE-only scoring
# rules; SSQs never do. See accreditation/report_scoring.py.
SCORING_FIELD_KEYS = {"weight", "points", "on", "group", "evidence_field", "mandatory", "observed_field"}
SCORING_OUTCOMES = ("interim", "deny", "critical", "major", "minor", "not_met")
WIDTHS = ("full", "half")

CONTACT_KINDS = ("official_email", "phone", "head_of_institution", "registrar", "bursar", "librarian")
INSTITUTION_ATTRS = ("name", "code", "ownership", "type", "category", "state", "lga", "zone", "year_established",
                     "website", "cac_rc_number", "address")
PROGRAMME_ATTRS = ("name", "level", "category", "approved_streams", "commencement_date", "expiry_date", "status")


def prefill_sources():
    """Every prefill source the builder offers (for its picker and the validator)."""
    out = [f"institution.{a}" for a in INSTITUTION_ATTRS]
    for kind in CONTACT_KINDS:
        out += [f"institution.contacts.{kind}", f"institution.contacts.{kind}.name"]
    out += [f"programme.{a}" for a in PROGRAMME_ATTRS]
    try:
        from institutions.models import DocumentKind

        out += [f"document.{code}" for code in DocumentKind.objects.filter(is_active=True)
                .values_list("code", flat=True)]
    except Exception:  # pragma: no cover - during migrations
        pass
    return out


# ===========================================================================
# Schema validation
# ===========================================================================
def validate_schema(schema):
    """Return a list of human-readable problems (empty = valid)."""
    errors = []
    if not isinstance(schema, dict):
        return ["The schema must be a JSON object with a \"sections\" list."]
    unknown = set(schema) - {"sections", "intro", "scoring", "form"}
    if unknown:
        errors.append(f"Unknown top-level keys: {', '.join(sorted(unknown))}.")
    sections = schema.get("sections")
    if not isinstance(sections, list) or not sections:
        return errors + ["Add at least one section."]
    section_keys, field_keys = set(), set()
    sources = set(prefill_sources())
    doc_kinds = {s.split(".", 1)[1] for s in sources if s.startswith("document.")}
    for si, section in enumerate(sections, start=1):
        where = f"Section {si}"
        if not isinstance(section, dict):
            errors.append(f"{where} must be an object.")
            continue
        key = section.get("key", "")
        if not isinstance(key, str) or not KEY_RE.match(key):
            errors.append(f"{where}: key '{key}' must be lower-case letters, digits or underscores.")
        elif key in section_keys:
            errors.append(f"{where}: the key '{key}' is used twice.")
        section_keys.add(key)
        where = f"Section '{key or si}'"
        if not str(section.get("title", "")).strip():
            errors.append(f"{where} needs a title.")
        extra = set(section) - SECTION_KEYS
        if extra:
            errors.append(f"{where}: unknown keys {', '.join(sorted(extra))}.")
        fields = section.get("fields")
        if not isinstance(fields, list) or not fields:
            errors.append(f"{where} needs at least one field.")
            continue
        for fi, field in enumerate(fields, start=1):
            errors += _validate_field(field, f"{where}, field {fi}", field_keys, sources, doc_kinds)
    return errors


def _validate_field(field, where, field_keys, sources, doc_kinds):
    errors = []
    if not isinstance(field, dict):
        return [f"{where} must be an object."]
    key = field.get("key", "")
    if not isinstance(key, str) or not KEY_RE.match(key):
        errors.append(f"{where}: key '{key}' must be lower-case letters, digits or underscores.")
    elif key in field_keys:
        errors.append(f"{where}: the key '{key}' is already used in this template.")
    field_keys.add(key)
    where = f"Field '{key or where}'"
    ftype = field.get("type")
    if ftype not in FIELD_TYPES:
        errors.append(f"{where}: unknown type '{ftype}'. Use one of: {', '.join(FIELD_TYPES)}.")
        return errors
    if not str(field.get("label", "")).strip():
        errors.append(f"{where} needs a label.")
    extra = set(field) - FIELD_KEYS
    if extra:
        errors.append(f"{where}: unknown keys {', '.join(sorted(extra))}.")
    if "required" in field and not isinstance(field["required"], bool):
        errors.append(f"{where}: 'required' must be true or false.")
    if field.get("width") and field["width"] not in WIDTHS:
        errors.append(f"{where}: width must be 'full' or 'half'.")
    validation = field.get("validation") or {}
    if not isinstance(validation, dict):
        errors.append(f"{where}: 'validation' must be an object.")
    else:
        bad = set(validation) - VALIDATION_KEYS
        if bad:
            errors.append(f"{where}: unknown validation rules {', '.join(sorted(bad))}.")
        if "pattern" in validation:
            try:
                re.compile(str(validation["pattern"]))
            except re.error:
                errors.append(f"{where}: the pattern is not a valid regular expression.")
        for num in ("max_length", "min_length", "min", "max", "max_rows", "min_rows", "max_files"):
            if num in validation and not isinstance(validation[num], (int, float)):
                errors.append(f"{where}: '{num}' must be a number.")
    if ftype in ("choice", "multi_choice"):
        options = field.get("options")
        if not isinstance(options, list) or len(options) < 2:
            errors.append(f"{where}: give at least two options.")
        else:
            values = [_option_value(o) for o in options]
            if any(v in (None, "") for v in values):
                errors.append(f"{where}: every option needs a value.")
            elif len(set(values)) != len(values):
                errors.append(f"{where}: options must be unique.")
    if ftype == "file":
        kind = field.get("document_kind")
        if kind and kind not in doc_kinds:
            errors.append(f"{where}: document kind '{kind}' does not exist (Settings → Documents).")
    if ftype == "catalogue":
        binding = field.get("catalogue")
        if not isinstance(binding, dict) or binding.get("kind") not in ("facility", "equipment"):
            errors.append(f"{where}: a catalogue field needs \"catalogue\": {{\"kind\": \"facility\" or "
                          f"\"equipment\"}}.")
        if field.get("prefill_from"):
            errors.append(f"{where}: catalogue fields cannot be prefilled.")
    if ftype == "table":
        columns = field.get("columns")
        if not isinstance(columns, list) or not columns:
            errors.append(f"{where}: a table needs columns.")
        else:
            seen = set()
            for ci, col in enumerate(columns, start=1):
                ckey = col.get("key", "") if isinstance(col, dict) else ""
                if not KEY_RE.match(ckey or ""):
                    errors.append(f"{where}, column {ci}: key '{ckey}' must be lower-case letters, digits or "
                                  "underscores.")
                elif ckey in seen:
                    errors.append(f"{where}: column key '{ckey}' is used twice.")
                seen.add(ckey)
                if isinstance(col, dict):
                    if col.get("type", "text") not in TABLE_COLUMN_TYPES:
                        errors.append(f"{where}, column '{ckey}': type must be one of "
                                      f"{', '.join(TABLE_COLUMN_TYPES)}.")
                    if col.get("type") == "choice" and len(col.get("options") or []) < 2:
                        errors.append(f"{where}, column '{ckey}': give at least two options.")
                    if not str(col.get("label", "")).strip():
                        errors.append(f"{where}, column '{ckey}' needs a label.")
    if "scoring" in field:
        errors += _validate_scoring(field, where)
    source = field.get("prefill_from")
    if source:
        if ftype in ("table", "catalogue"):
            errors.append(f"{where}: {ftype} fields cannot be prefilled.")
        elif source not in sources:
            errors.append(f"{where}: unknown prefill source '{source}'.")
        elif source.startswith("document.") and ftype != "file":
            errors.append(f"{where}: a document prefill needs a file field.")
        elif ftype == "file" and not source.startswith("document."):
            errors.append(f"{where}: a file field can only be prefilled from a document.")
    return errors


def _validate_scoring(field, where):
    errors = []
    rule = field.get("scoring")
    if not isinstance(rule, dict):
        return [f"{where}: 'scoring' must be an object."]
    extra = set(rule) - SCORING_FIELD_KEYS
    if extra:
        errors.append(f"{where}: unknown scoring keys {', '.join(sorted(extra))}.")
    if "weight" in rule and (not isinstance(rule["weight"], (int, float)) or rule["weight"] < 0):
        errors.append(f"{where}: the scoring weight must be a number of 0 or more.")
    values = {v for v, _l in option_pairs(field)}
    points = rule.get("points")
    if points is not None:
        if field["type"] != "choice" or not isinstance(points, dict):
            errors.append(f"{where}: scoring points need a one-choice field and an object of option → points.")
        else:
            for value, pts in points.items():
                if value not in values:
                    errors.append(f"{where}: scoring names the unknown option '{value}'.")
                elif pts is not None and not isinstance(pts, (int, float)):
                    errors.append(f"{where}: points for '{value}' must be a number (or null = not applicable).")
    on = rule.get("on")
    if on is not None:
        if not isinstance(on, dict):
            errors.append(f"{where}: scoring 'on' must map option values to an outcome.")
        else:
            for value, outcome in on.items():
                if value not in values:
                    errors.append(f"{where}: scoring names the unknown option '{value}'.")
                if outcome not in SCORING_OUTCOMES:
                    errors.append(f"{where}: outcome '{outcome}' must be one of {', '.join(SCORING_OUTCOMES)}.")
    return errors


def _option_value(option):
    if isinstance(option, dict):
        return option.get("value")
    return option


def option_pairs(field):
    out = []
    for option in field.get("options") or []:
        if isinstance(option, dict):
            out.append((str(option.get("value")), str(option.get("label") or option.get("value"))))
        else:
            out.append((str(option), str(option)))
    return out


# ===========================================================================
# Prefill (Requirement 3) — accepted documents only
# ===========================================================================
def accepted_document(institution, kind_code):
    """The latest ACCEPTED document of a kind. Drafts, submitted-but-unreviewed,
    returned and superseded versions are never used."""
    from institutions.models import InstitutionDocument

    return (InstitutionDocument.unscoped.filter(institution=institution, kind__code=kind_code,
                                                status=InstitutionDocument.ACCEPTED)
            .select_related("kind").order_by("-version", "-id").first())


def resolve_source(source, institution, programme=None):
    """The value for a prefill source, or None when there is nothing on record."""
    parts = source.split(".")
    if parts[0] == "institution":
        if len(parts) >= 3 and parts[1] == "contacts":
            contact = institution.contact(parts[2])
            if contact is None:
                return None
            if len(parts) == 4 and parts[3] == "name":
                return contact.name or None
            return contact.value or contact.name or None
        attr = parts[1]
        mapping = {
            "name": institution.name, "code": institution.code,
            "ownership": getattr(institution.ownership, "name", None),
            "type": getattr(institution.institution_type, "name", None),
            "category": getattr(institution.category, "name", None),
            "state": getattr(institution.state, "name", None), "lga": institution.lga or None,
            "zone": institution.zone or None, "year_established": institution.year_established,
            "website": institution.website or None, "cac_rc_number": institution.cac_rc_number or None,
        }
        if attr == "address":
            addr = institution.address("permanent_site") or institution.address()
            return ", ".join(x for x in (addr.line1, addr.town, addr.lga) if x) if addr else None
        return mapping.get(attr)
    if parts[0] == "programme" and programme is not None:
        attr = parts[1]
        value = {
            "name": programme.name, "level": getattr(programme.level, "name", None),
            "category": getattr(programme.category, "name", None), "approved_streams": programme.approved_streams,
            "commencement_date": programme.commencement_date, "expiry_date": programme.expiry_date,
            "status": programme.get_status_display(),
        }.get(attr)
        if isinstance(value, (datetime.date,)):
            return value.isoformat()
        return value
    if parts[0] == "document":
        return accepted_document(institution, parts[1])
    return None


def compute_prefill(template, institution, programme=None):
    """(data, evidence_docs, prefilled_keys). File fields prefilled from the
    accepted permanent record come back as InstitutionDocument objects for the
    caller to attach as re-used evidence."""
    data, docs, keys = {}, {}, []
    for field in template.field_map().values():
        source = field.get("prefill_from")
        if not source:
            continue
        value = resolve_source(source, institution, programme)
        if value in (None, ""):
            continue
        if field["type"] == "file":
            docs[field["key"]] = value
        else:
            data[field["key"]] = str(value) if field["type"] not in ("integer", "number") else str(value)
        keys.append(field["key"])
    return data, docs, keys


# ===========================================================================
# Catalogue binding (R-22)
# ===========================================================================
def catalogue_items(field, programme):
    """Current catalogue items for this field and programme (latest version of
    each named item, effective today)."""
    from .models import CatalogueItem

    kind = (field.get("catalogue") or {}).get("kind", "equipment")
    today = timezone.localdate()
    qs = CatalogueItem.objects.filter(kind=kind, is_active=True, effective_from__lte=today)
    if programme is not None:
        # v2.4: the programme's list comes from the facilities register when
        # the register has it (level-aware, refreshed when the register changes).
        from . import register_link

        register_link.ensure(programme)
        by_master = (qs.filter(programme_master_id=programme.master_id)
                     if getattr(programme, "master_id", None) else qs.none())
        by_name = qs.filter(programme_name__iexact=programme.name)
        if by_master.exists():
            qs = by_master
        elif by_name.exists():
            qs = by_name
        elif programme.category_id:
            qs = qs.filter(programme_name="", category_id=programme.category_id)
        else:
            qs = qs.none()
    latest = {}
    for item in qs.order_by("sort_order", "name", "-version"):
        ident = (item.programme_name.lower(), item.category_id, item.name.lower())
        if ident not in latest:
            latest[ident] = item
    return list(latest.values())


# ===========================================================================
# Cleaning values
# ===========================================================================
def _num(value, integer=False):
    text = str(value).replace(",", "").strip()
    if integer:
        if not re.fullmatch(r"-?\d+", text):
            raise ValidationError("Enter a whole number.")
        return int(text)
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValidationError("Enter a number.") from None


def clean_value(field, raw, *, programme=None):
    """Clean one field's raw form value. Returns the JSON-ready value ('' or
    [] when empty). Raises ValidationError with a message for the field. The
    ``required`` rule is applied at submission, not while saving drafts."""
    ftype = field["type"]
    rules = field.get("validation") or {}
    if ftype in ("text", "textarea", "email", "phone", "url"):
        value = str(raw or "").strip()
        if not value:
            return ""
        limit = int(rules.get("max_length") or (5000 if ftype == "textarea" else 255))
        if len(value) > limit:
            raise ValidationError(f"Keep it under {limit} characters.")
        if rules.get("min_length") and len(value) < int(rules["min_length"]):
            raise ValidationError(f"Write at least {int(rules['min_length'])} characters.")
        if ftype == "email":
            validate_email(value)
        if ftype == "url":
            URLValidator()(value)
        if ftype == "phone" and not re.fullmatch(r"[+0-9 ()\-]{7,20}", value):
            raise ValidationError("Enter a phone number (digits, spaces, + ( ) -).")
        if rules.get("pattern") and not re.fullmatch(str(rules["pattern"]), value):
            raise ValidationError("This value is not in the expected format.")
        return value
    if ftype in ("number", "integer"):
        if str(raw or "").strip() == "":
            return ""
        num = _num(raw, integer=ftype == "integer")
        if "min" in rules and num < Decimal(str(rules["min"])):
            raise ValidationError(f"The smallest allowed value is {rules['min']}.")
        if "max" in rules and num > Decimal(str(rules["max"])):
            raise ValidationError(f"The largest allowed value is {rules['max']}.")
        return str(num)
    if ftype == "date":
        text = str(raw or "").strip()
        if not text:
            return ""
        try:
            value = datetime.date.fromisoformat(text)
        except ValueError:
            raise ValidationError("Enter a date (YYYY-MM-DD).") from None
        if rules.get("min_date") and text < str(rules["min_date"]):
            raise ValidationError(f"The date cannot be before {rules['min_date']}.")
        if rules.get("max_date") and text > str(rules["max_date"]):
            raise ValidationError(f"The date cannot be after {rules['max_date']}.")
        return value.isoformat()
    if ftype == "choice":
        value = str(raw or "").strip()
        if not value:
            return ""
        if value not in {v for v, _l in option_pairs(field)}:
            raise ValidationError("Choose one of the options.")
        return value
    if ftype == "multi_choice":
        values = [str(v) for v in (raw if isinstance(raw, (list, tuple)) else [raw]) if str(v or "").strip()]
        allowed = {v for v, _l in option_pairs(field)}
        if any(v not in allowed for v in values):
            raise ValidationError("Choose from the options.")
        return values
    if ftype == "yes_no":
        value = str(raw or "").strip().lower()
        if not value:
            return ""
        if value not in ("yes", "no"):
            raise ValidationError("Choose yes or no.")
        return value
    if ftype == "catalogue":
        return clean_catalogue(field, raw, programme=programme)
    if ftype == "table":
        return clean_table(field, raw)
    if ftype == "file":
        return ""  # files are Evidence rows, not data
    raise ValidationError("Unknown field type.")


def clean_catalogue(field, raw, *, programme=None):
    """Quantities only, keyed by catalogue item id. Anything else posted —
    an item name, an unknown id — is ignored (R-22)."""
    items = {str(item.pk): item for item in catalogue_items(field, programme)}
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for item_id, qty in raw.items():
        item_id = str(item_id)
        if item_id not in items:
            continue
        text = str(qty if qty is not None else "").strip()
        if text == "":
            continue
        try:
            number = _num(text, integer=False)
        except ValidationError:
            raise ValidationError(f"{items[item_id].name}: enter a quantity.") from None
        if number < 0:
            raise ValidationError(f"{items[item_id].name}: the quantity cannot be negative.")
        out[item_id] = str(number)
    return out


def clean_table(field, raw):
    rows = raw if isinstance(raw, list) else []
    rules = field.get("validation") or {}
    max_rows = int(field.get("max_rows") or rules.get("max_rows") or 200)
    columns = field.get("columns") or []
    out = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        clean_row = {}
        for col in columns:
            ctype = col.get("type", "text")
            sub = {"key": col["key"], "type": ctype, "label": col.get("label", col["key"]),
                   "options": col.get("options"), "validation": col.get("validation") or {}}
            try:
                clean_row[col["key"]] = clean_value(sub, row.get(col["key"], ""))
            except ValidationError as exc:
                raise ValidationError(f"Row {index}, {sub['label']}: {' '.join(exc.messages)}") from None
        if any(v not in ("", None, []) for v in clean_row.values()):
            out.append(clean_row)
    if len(out) > max_rows:
        raise ValidationError(f"At most {max_rows} rows.")
    return out


def is_empty(field, value, evidence_count=0):
    if field["type"] == "file":
        return evidence_count == 0
    return value in (None, "", [], {})


def required_problems(template, data, evidence_counts, *, sections=None):
    """{field_key: message} for required fields still empty (and table
    min_rows), limited to `sections` when given."""
    problems = {}
    for section in template.sections():
        if sections is not None and section["key"] not in sections:
            continue
        for field in section.get("fields", []):
            value = data.get(field["key"])
            if field.get("required") and is_empty(field, value, evidence_counts.get(field["key"], 0)):
                problems[field["key"]] = "Required."
            elif field["type"] == "table":
                min_rows = int(field.get("min_rows") or (field.get("validation") or {}).get("min_rows") or 0)
                if field.get("required") and min_rows and len(value or []) < min_rows:
                    problems[field["key"]] = f"Add at least {min_rows} row(s)."
    return problems


def section_progress(template, data, evidence_counts):
    """[(section, filled_required, total_required, complete)]."""
    out = []
    for section in template.sections():
        required = [f for f in section.get("fields", []) if f.get("required")]
        filled = [f for f in required if not is_empty(f, data.get(f["key"]), evidence_counts.get(f["key"], 0))]
        out.append({"section": section, "filled": len(filled), "total": len(required),
                    "complete": len(filled) == len(required)})
    return out


# ===========================================================================
# Rendering (review screens and the submission snapshot)
# ===========================================================================
def display_value(field, value, *, programme=None):
    ftype = field["type"]
    if value in (None, "", [], {}):
        return ""
    if ftype in ("choice",):
        return dict(option_pairs(field)).get(str(value), str(value))
    if ftype == "multi_choice":
        labels = dict(option_pairs(field))
        return ", ".join(labels.get(v, v) for v in value)
    if ftype == "yes_no":
        return "Yes" if value == "yes" else "No"
    if ftype == "catalogue":
        return value  # rendered as rows by render_snapshot
    if ftype == "table":
        return value
    return str(value)


# X35 slice (Phase 0, S-11): what an institution must never see — NBTE's
# required quantities and the "Short" flags (Phase R adds weights/critical).
INSTITUTION_HIDDEN_ROW_KEYS = ("required", "short", "weight", "critical")
AUDIENCES = ("staff", "institution")


def for_audience(sections, audience="staff"):
    """A copy of rendered snapshot ``sections`` fit for ``audience``. Stored
    submission snapshots keep the staff view; every portal page, partial and
    portal API response passes them through here with ``institution``."""
    if audience not in AUDIENCES:
        raise ValueError(f"Unknown audience {audience!r}")
    if audience == "staff":
        return sections
    out = []
    for section in sections or []:
        fields = []
        for field in section.get("fields", []):
            field = dict(field)
            if field.get("type") == "catalogue":
                field["rows"] = [{k: v for k, v in row.items() if k not in INSTITUTION_HIDDEN_ROW_KEYS}
                                 for row in field.get("rows") or []]
            fields.append(field)
        out.append({**section, "fields": fields})
    return out


def render_snapshot(template, data, *, programme=None, audience="staff"):
    """Sections with labels and display values, frozen into a Submission.
    Catalogue rows carry the item name and required quantity at submission
    time, so later catalogue edits never change what was submitted. The
    ``institution`` audience strips required quantities and "Short" flags."""
    sections = []
    for section in template.sections():
        rows = []
        for field in section.get("fields", []):
            value = data.get(field["key"])
            entry = {"key": field["key"], "label": field["label"], "type": field["type"]}
            if field["type"] == "catalogue":
                qty = value or {}
                entry["rows"] = []
                for item in catalogue_items(field, programme):
                    have = qty.get(str(item.pk), "")
                    try:
                        short = have != "" and Decimal(str(have)) < item.required_quantity
                    except (InvalidOperation, ValueError):
                        short = False
                    entry["rows"].append({"item_id": item.pk, "name": item.name, "unit": item.unit,
                                          "required": str(item.required_quantity), "quantity": have,
                                          "version": item.version, "short": short,
                                          "weight": str(item.weight), "critical": item.is_critical,
                                          "min_spec": item.min_spec})
            elif field["type"] == "table":
                entry["columns"] = [{"key": c["key"], "label": c.get("label", c["key"])}
                                    for c in field.get("columns", [])]
                entry["rows"] = value or []
            else:
                entry["value"] = display_value(field, value, programme=programme)
            rows.append(entry)
        sections.append({"key": section["key"], "title": section["title"], "fields": rows})
    return for_audience(sections, audience)


def blank_field(ftype="text"):
    """A new field for the builder."""
    field = {"key": "", "type": ftype, "label": "", "required": False, "help": ""}
    if ftype in ("choice", "multi_choice"):
        field["options"] = ["Option A", "Option B"]
    if ftype == "catalogue":
        field["catalogue"] = {"kind": "equipment"}
    if ftype == "table":
        field["columns"] = [{"key": "name", "label": "Name", "type": "text"}]
    return field
