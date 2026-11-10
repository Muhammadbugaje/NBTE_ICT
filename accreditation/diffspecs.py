"""Import diff specs for the curriculum catalogue (v2.1 X45, R.6).

The catalogue is the list of facilities and equipment a programme must have.
Importing applies at once, so it needs the catalogue permission (or the ES /
super admin), matching the rule used by the professional-body import.
"""
from __future__ import annotations

import decimal

from django.core.exceptions import ValidationError

from core.importdiff import DiffSpec, register_diff
from core.importexport import Column

from .models import CatalogueItem


def _yes(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("", "yes", "y", "true", "1", "active"):
        return True
    if text in ("no", "n", "false", "0", "inactive"):
        return False
    raise ValidationError("Use Yes or No.")


def _kind(value):
    text = str(value or "").strip().lower()
    if text in ("facility", "facilities"):
        return CatalogueItem.FACILITY
    if text in ("equipment", "item", "items"):
        return CatalogueItem.EQUIPMENT
    raise ValidationError(f"Unknown kind '{value}'. Use facility or equipment.")


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return decimal.Decimal(str(value).strip())
    except (decimal.InvalidOperation, ValueError):
        raise ValidationError(f"'{value}' is not a number.")


def _integer(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"'{value}' is not a whole number.")


def _master(value):
    from institutions.models import ProgrammeMaster

    text = " ".join(str(value or "").split())
    if not text:
        return None
    obj = (ProgrammeMaster.objects.filter(code__iexact=text).first()
           or ProgrammeMaster.objects.filter(name__iexact=text).first())
    if obj is None:
        raise ValidationError(f"Unknown programme master '{value}'.")
    return obj


def _category(value):
    from institutions.models import ProgrammeCategory

    text = " ".join(str(value or "").split())
    if not text:
        return None
    obj = ProgrammeCategory.objects.filter(name__iexact=text).first()
    if obj is None:
        raise ValidationError(f"Unknown programme category '{value}'.")
    return obj


@register_diff
class CurriculumRequirementDiff(DiffSpec):
    """v2.1 X45: curriculum catalogue rows (required facilities/equipment)."""

    name = "curriculum_requirements"
    label = "Curriculum requirements"
    version = 1
    permission = "accreditation.manage_catalogue"
    key_fields = ("programme_master", "name", "kind")
    match_note = "Matched by programme master + item name + kind"
    compare_fields = ("programme_name", "category", "required_quantity", "unit", "weight", "is_critical",
                      "min_spec", "is_active", "sort_order")
    columns = [
        Column("Programme master", "programme_master", parse=_master,
               help="Master-list code or name. Blank = category-wide item."),
        Column("Programme name", "programme_name", help="Legacy free-text programme name (optional)."),
        Column("Category", "category", parse=_category),
        Column("Item", "name", required=True),
        Column("Kind", "kind", parse=_kind, lookup=lambda: ["facility", "equipment"]),
        Column("Required quantity", "required_quantity", parse=_decimal),
        Column("Unit", "unit"),
        Column("Weight", "weight", parse=_decimal, help="Relative weight in the shortfall score (internal)."),
        Column("Critical", "is_critical", parse=_yes, help="Counts double in the shortfall score (internal)."),
        Column("Minimum specification", "min_spec"),
        Column("Active", "is_active", parse=_yes),
        Column("Sort order", "sort_order", parse=_integer),
    ]

    def can_use(self, user):
        from institutions.tvet_access import is_top

        return bool(user and user.is_authenticated
                    and (is_top(user) or user.has_perm(self.permission)))

    def queryset(self):
        return CatalogueItem.objects.select_related("programme_master", "category").order_by(
            "programme_master__name", "kind", "sort_order", "name")

    def current(self, obj, field):
        if field == "programme_master":
            return obj.programme_master.code if obj.programme_master_id else ""
        return getattr(obj, field, None)

    def lookup(self, clean):
        master = clean.get("programme_master")
        kind = clean.get("kind") or CatalogueItem.EQUIPMENT
        name = " ".join(str(clean.get("name") or "").split())
        if not name:
            return None, None
        qs = CatalogueItem.objects.filter(name__iexact=name, kind=kind)
        qs = qs.filter(programme_master=master) if master is not None else qs.filter(programme_master__isnull=True)
        found = qs.first()
        return (found, ("programme_master", "name", "kind")) if found else (None, None)

    def object_key(self, obj):
        master = obj.programme_master.code if obj.programme_master_id else ""
        return "|".join((master.casefold(), " ".join(obj.name.split()).casefold(), obj.kind))

    def display(self, field, value):
        if field == "programme_master" and value is not None and hasattr(value, "code"):
            return value.code
        if field == "category" and value is not None and hasattr(value, "name"):
            return value.name
        if field == "kind":
            return dict(CatalogueItem.KIND_CHOICES).get(value, value or "")
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return super().display(field, value)

    def row_label(self, clean, obj=None):
        if obj is not None:
            return obj.name
        return str(clean.get("name") or "")

    def row_sublabel(self, clean, obj=None):
        master = obj.programme_master if obj is not None else clean.get("programme_master")
        return master.code if master is not None else (clean.get("programme_name") or "Category-wide")

    def apply_row(self, obj, clean, fields, actor):
        if obj is None:
            obj = CatalogueItem()
            fields = [c.field for c in self.columns]
        for field in fields:
            if field not in clean:
                continue
            value = clean.get(field)
            if value is None:
                if field in ("required_quantity", "weight", "sort_order"):
                    value = decimal.Decimal("1") if field != "sort_order" else 0
                elif field in ("is_critical", "is_active"):
                    value = field == "is_active"
                elif field in ("programme_master", "category"):
                    continue
                else:
                    value = ""
            setattr(obj, field, value)
        if obj.programme_master_id and not obj.programme_name:
            obj.programme_name = obj.programme_master.name
        if obj.programme_master_id and not obj.category_id:
            obj.category = obj.programme_master.programme_category
        obj.full_clean()
        obj.save()
        return obj
