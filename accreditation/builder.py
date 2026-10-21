"""Settings → SSQ templates and Settings → Curriculum catalogue (Phase 3).

Both follow Requirement 16: a published template or a catalogue line is never
overwritten. Editing a published template starts a new draft version; editing
a catalogue line writes a new version and retires the old one. SSQs already
under way keep the template version they were opened with.
"""
# scoped-by-caller
from __future__ import annotations

import copy
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from qa_core import timeline  # noqa: F401  (kept for parity with other services)

from . import ssq as engine
from .models import CatalogueItem, QAType, SSQTemplate

EMPTY_SCHEMA = {"sections": [{"key": "section_1", "title": "Section 1", "description": "", "fields": []}]}


def _need(user, perm):
    if not user.has_perm(perm):
        raise PermissionDenied(f"You do not hold {perm}.")


# ===========================================================================
# SSQ templates
# ===========================================================================
def current_template(qa_type):
    return SSQTemplate.objects.filter(qa_type=qa_type, status=SSQTemplate.PUBLISHED, is_current=True).first()


def open_draft(qa_type):
    return SSQTemplate.objects.filter(qa_type=qa_type, status=SSQTemplate.DRAFT).order_by("-version").first()


@transaction.atomic
def new_draft(qa_type, *, user, base=None):
    """A draft version copying `base` (default: the current published one)."""
    _need(user, "accreditation.manage_ssq_templates")
    existing = open_draft(qa_type)
    if existing is not None:
        raise ValidationError(f"Version {existing.version} is already a draft — edit or discard it first.")
    base = base or current_template(qa_type)
    version = (SSQTemplate.objects.filter(qa_type=qa_type).aggregate(m=Max("version"))["m"] or 0) + 1
    return SSQTemplate.objects.create(
        qa_type=qa_type, version=version, name=(base.name if base else f"{qa_type.name} SSQ"),
        schema=copy.deepcopy(base.schema) if base else copy.deepcopy(EMPTY_SCHEMA), status=SSQTemplate.DRAFT,
        created_by=user, notes=f"Copied from version {base.version}." if base else "")


@transaction.atomic
def save_draft(template, *, user, schema=None, name=None, notes=None):
    """Store the draft as it is (it may still be invalid); returns the
    validator's errors so the builder can show them."""
    _need(user, "accreditation.manage_ssq_templates")
    template = SSQTemplate.objects.select_for_update().get(pk=template.pk)
    if template.status != SSQTemplate.DRAFT:
        raise ValidationError("A published template is never edited. Start a new version.")
    if schema is not None:
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except ValueError as exc:
                raise ValidationError(f"The JSON could not be read: {exc}") from None
        if not isinstance(schema, dict):
            raise ValidationError("The schema must be a JSON object with a 'sections' list.")
        template.schema = schema
    if name is not None:
        template.name = name.strip()[:150] or template.name
    if notes is not None:
        template.notes = notes
    template.save()
    return engine.validate_schema(template.schema)


@transaction.atomic
def publish(template, *, user, system=False):
    """Publish a draft: it becomes current for its QA type; the previous
    version is retired (kept, never changed)."""
    if not system:
        _need(user, "accreditation.publish_ssq_template")
    template = SSQTemplate.objects.select_for_update().get(pk=template.pk)
    if template.status != SSQTemplate.DRAFT:
        raise ValidationError("Only a draft can be published.")
    errors = engine.validate_schema(template.schema)
    if errors:
        raise ValidationError(["The template has errors:"] + errors[:20])
    now = timezone.now()
    SSQTemplate.objects.filter(qa_type=template.qa_type, is_current=True).exclude(pk=template.pk).update(
        is_current=False, status=SSQTemplate.RETIRED, updated_at=now)
    template.status, template.is_current = SSQTemplate.PUBLISHED, True
    template.published_at, template.published_by = now, (user if getattr(user, "pk", None) else None)
    template.save()
    QAType.objects.filter(pk=template.qa_type_id).update(ssq_template=template, updated_at=now)
    return template


def usage_count(template):
    """How many SSQs (any institution) were opened on this version."""
    from .models import SSQ

    return SSQ.unscoped.filter(template=template).count()


@transaction.atomic
def discard_draft(template, *, user):
    _need(user, "accreditation.manage_ssq_templates")
    if template.status != SSQTemplate.DRAFT:
        raise ValidationError("Only a draft can be discarded.")
    if usage_count(template):
        raise ValidationError("This draft is in use.")
    template.delete()


# --- structured edits used by the visual builder (all on drafts) ---------------
def _schema(template):
    template.refresh_from_db(fields=["schema", "status"])  # the caller's object may be stale
    schema = copy.deepcopy(template.schema or {})
    schema.setdefault("sections", [])
    return schema


def _find_section(schema, key):
    for section in schema["sections"]:
        if section.get("key") == key:
            return section
    raise ValidationError("Unknown section.")


def add_section(template, *, user, key, title, description="", department=True):
    schema = _schema(template)
    key = (key or "").strip()
    if any(s.get("key") == key for s in schema["sections"]):
        raise ValidationError(f"A section with key '{key}' already exists.")
    schema["sections"].append({"key": key, "title": title.strip(), "description": description.strip(),
                               "department": bool(department), "fields": []})
    return save_draft(template, user=user, schema=schema)


def update_section(template, *, user, key, title, description="", department=True):
    schema = _schema(template)
    section = _find_section(schema, key)
    section.update({"title": title.strip(), "description": description.strip(), "department": bool(department)})
    return save_draft(template, user=user, schema=schema)


def remove_section(template, *, user, key):
    schema = _schema(template)
    _find_section(schema, key)
    schema["sections"] = [s for s in schema["sections"] if s.get("key") != key]
    return save_draft(template, user=user, schema=schema)


def move_section(template, *, user, key, delta):
    schema = _schema(template)
    keys = [s.get("key") for s in schema["sections"]]
    if key not in keys:
        raise ValidationError("Unknown section.")
    i = keys.index(key)
    j = max(0, min(len(keys) - 1, i + delta))
    schema["sections"].insert(j, schema["sections"].pop(i))
    return save_draft(template, user=user, schema=schema)


def field_from_form(data):
    """Turn the builder's field form into a schema field dict."""
    ftype = data.get("type", "text")
    field = {"key": (data.get("key") or "").strip(), "type": ftype, "label": (data.get("label") or "").strip(),
             "required": bool(data.get("required"))}
    if (data.get("help") or "").strip():
        field["help"] = data["help"].strip()
    if ftype in ("choice", "multi_choice"):
        field["options"] = [line.strip() for line in (data.get("options") or "").splitlines() if line.strip()]
    validation = {}
    if ftype in ("number", "integer"):
        for bound in ("min", "max"):
            raw = (data.get(bound) or "").strip()
            if raw:
                try:
                    validation[bound] = float(raw) if ftype == "number" else int(raw)
                except ValueError:
                    raise ValidationError(f"'{bound}' must be a number.") from None
    if ftype in ("text", "textarea") and (data.get("max_length") or "").strip():
        try:
            validation["max_length"] = int(data["max_length"])
        except ValueError:
            raise ValidationError("'max length' must be a whole number.") from None
    if validation:
        field["validation"] = validation
    if ftype == "file":
        if (data.get("document_kind") or "").strip():
            field["document_kind"] = data["document_kind"].strip()
        field["multiple"] = bool(data.get("multiple"))
    if ftype == "catalogue":
        field["catalogue"] = {"kind": data.get("catalogue_kind") or "equipment"}
    if ftype == "table":
        columns = []
        for line in (data.get("columns") or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if parts and parts[0]:
                col = {"key": parts[0], "label": parts[1] if len(parts) > 1 and parts[1] else parts[0],
                       "type": parts[2] if len(parts) > 2 and parts[2] else "text"}
                columns.append(col)
        field["columns"] = columns
        if (data.get("min_rows") or "").strip().isdigit():
            field["min_rows"] = int(data["min_rows"])
    if (data.get("prefill_from") or "").strip():
        field["prefill_from"] = data["prefill_from"].strip()
    return field


def upsert_field(template, *, user, section_key, field, original_key=""):
    schema = _schema(template)
    section = _find_section(schema, section_key)
    fields = section.setdefault("fields", [])
    if original_key:
        for i, existing in enumerate(fields):
            if existing.get("key") == original_key:
                fields[i] = field
                break
        else:
            raise ValidationError("Unknown field.")
    else:
        if any(f.get("key") == field["key"] for s in schema["sections"] for f in s.get("fields", [])):
            raise ValidationError(f"A field with key '{field['key']}' already exists.")
        fields.append(field)
    return save_draft(template, user=user, schema=schema)


def remove_field(template, *, user, section_key, key):
    schema = _schema(template)
    section = _find_section(schema, section_key)
    section["fields"] = [f for f in section.get("fields", []) if f.get("key") != key]
    return save_draft(template, user=user, schema=schema)


def move_field(template, *, user, section_key, key, delta):
    schema = _schema(template)
    section = _find_section(schema, section_key)
    fields = section.get("fields", [])
    keys = [f.get("key") for f in fields]
    if key not in keys:
        raise ValidationError("Unknown field.")
    i = keys.index(key)
    j = max(0, min(len(keys) - 1, i + delta))
    fields.insert(j, fields.pop(i))
    return save_draft(template, user=user, schema=schema)


# ===========================================================================
# Curriculum catalogue (R-22)
# ===========================================================================
def _quantity(raw):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("The required quantity must be a number.") from None
    if value < 0:
        raise ValidationError("The required quantity cannot be negative.")
    return value


@transaction.atomic
def create_item(*, user, category, programme_name, kind, name, required_quantity, unit="no."):
    _need(user, "accreditation.manage_catalogue")
    name = (name or "").strip()
    if len(name) < 2:
        raise ValidationError("Name the facility or equipment.")
    if kind not in (CatalogueItem.FACILITY, CatalogueItem.EQUIPMENT):
        raise ValidationError("Choose facility or equipment.")
    if category is None and not (programme_name or "").strip():
        raise ValidationError("Choose a programme category or name a programme.")
    if CatalogueItem.objects.filter(category=category, programme_name=(programme_name or "").strip(),
                                    name__iexact=name, is_active=True).exists():
        raise ValidationError(f"'{name}' is already in this catalogue. Edit it instead.")
    order = (CatalogueItem.objects.aggregate(m=Max("sort_order"))["m"] or 0) + 10
    return CatalogueItem.objects.create(category=category, programme_name=(programme_name or "").strip(), kind=kind,
                                        name=name, required_quantity=_quantity(required_quantity),
                                        unit=(unit or "no.").strip()[:40], sort_order=order)


@transaction.atomic
def revise_item(item, *, user, required_quantity, unit=None, name=None):
    """A change writes version n+1 and retires version n (never overwritten:
    SSQ snapshots already taken keep the old quantities)."""
    _need(user, "accreditation.manage_catalogue")
    item = CatalogueItem.objects.select_for_update().get(pk=item.pk)
    if not item.is_active:
        raise ValidationError("This line was already replaced by a newer version.")
    quantity = _quantity(required_quantity)
    new_name = (name or item.name).strip()
    new_unit = (unit if unit is not None else item.unit).strip()[:40]
    if quantity == item.required_quantity and new_name == item.name and new_unit == item.unit:
        raise ValidationError("Nothing changed.")
    item.is_active = False
    item.save(update_fields=["is_active", "updated_at"])
    return CatalogueItem.objects.create(category=item.category, programme_name=item.programme_name, kind=item.kind,
                                        name=new_name, required_quantity=quantity, unit=new_unit,
                                        version=item.version + 1, sort_order=item.sort_order,
                                        effective_from=timezone.localdate())


@transaction.atomic
def retire_item(item, *, user):
    _need(user, "accreditation.manage_catalogue")
    if not item.is_active:
        raise ValidationError("Already retired.")
    item.is_active = False
    item.save(update_fields=["is_active", "updated_at"])
    return item


def item_history(item):
    return list(CatalogueItem.objects.filter(category=item.category, programme_name=item.programme_name,
                                             kind=item.kind, sort_order=item.sort_order).order_by("-version"))
