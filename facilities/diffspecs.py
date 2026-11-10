"""v2.2: Excel round trip for the facilities and equipment register.

One row per item. Matched by Programme + Facility + Sub-group + Item; the
preview shows New / Changed (old → new) / Unchanged / Missing before anything
is saved, like every other NBTE import (core.importdiff)."""
from __future__ import annotations

from django.core.exceptions import ValidationError

from core.importdiff import DiffSpec, register_diff
from core.importexport import Column

from .models import FacilityItem, FacilityProgramme, FacilitySection


def _text(value):
    return " ".join(str(value).split()) if value not in (None, "") else ""


def _qty(value):
    if value in (None, ""):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _text(value)


def _yes(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("", "yes", "y", "true", "1", "active"):
        return True
    if text in ("no", "n", "false", "0", "inactive"):
        return False
    raise ValidationError("Use Yes or No.")


def _int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise ValidationError(f"'{value}' is not a whole number.")


def _levels(value):
    text = str(value or "").upper().replace("/", ",")
    found = [lv for lv in ("ND", "HND") if lv in [p.strip() for p in text.split(",")]]
    return ", ".join(found)


@register_diff
class FacilityItemDiff(DiffSpec):
    name = "facilities_equipment"
    label = "Facilities and equipment (ND-HND)"
    version = 1
    permission = "facilities.manage_facilities"
    max_rows = 30000
    key_fields = ("programme", "facility", "group", "name")
    match_note = "Matched by programme + facility + sub-group + item"
    compare_fields = ("nd_qty", "hnd_qty", "remarks", "is_critical", "is_active", "sort_order")
    allow_deactivate_missing = True
    columns = [
        Column("Programme", "programme", required=True, parse=_text, help="Programme name, e.g. Statistics."),
        Column("Levels", "levels", parse=_levels, help="ND, HND or ND, HND (used when a programme is new)."),
        Column("Facility", "facility", required=True, parse=_text,
               help="Laboratory, workshop, studio or room, e.g. Electronics Laboratory."),
        Column("Sub-group", "group", parse=_text, help="Optional heading inside the facility."),
        Column("Item", "name", required=True, parse=_text),
        Column("ND quantity", "nd_qty", parse=_qty, help="Number, or text such as '1 set'. Blank or '-' = none."),
        Column("HND quantity", "hnd_qty", parse=_qty),
        Column("Remarks", "remarks", parse=_text),
        Column("Critical", "is_critical", parse=_yes, help="Yes = counts double in the SSQ shortfall score."),
        Column("Active", "is_active", parse=_yes),
        Column("Order", "sort_order", parse=_int),
    ]

    def can_use(self, user):
        import org

        return bool(user and user.is_authenticated and (user.is_superuser or org.is_es(user)
                                                         or org.is_super_admin(user)
                                                         or user.has_perm(self.permission)))

    def normalise_key_value(self, field, value):
        if field == "group":
            return " ".join(str(value or "").split()).casefold() or "-"
        return super().normalise_key_value(field, value)

    def key_for(self, clean, fields=None):
        fields = fields or self.key_fields
        parts = [self.normalise_key_value(f, clean.get(f)) for f in fields]
        return "|".join(parts) if all(parts) else ""

    def queryset(self):
        return FacilityItem.objects.select_related("programme", "section").order_by(
            "programme__name", "section__sort_order", "sort_order")

    def current(self, obj, field):
        if field == "programme":
            return obj.programme.name
        if field == "facility":
            return obj.section.name
        if field == "levels":
            return obj.programme.levels
        return getattr(obj, field, None)

    def lookup(self, clean):
        name = _text(clean.get("name"))
        if not (name and clean.get("programme") and clean.get("facility")):
            return None, None
        found = FacilityItem.objects.filter(
            programme__name__iexact=clean["programme"], section__name__iexact=clean["facility"],
            group__iexact=clean.get("group") or "", name__iexact=name).first()
        return (found, self.key_fields) if found else (None, None)

    def object_key(self, obj):
        return "|".join((self.normalise_key_value("programme", obj.programme.name),
                         self.normalise_key_value("facility", obj.section.name),
                         self.normalise_key_value("group", obj.group),
                         self.normalise_key_value("name", obj.name)))

    def row_label(self, clean, obj=None):
        return clean.get("name") or (obj.name if obj else "")

    def row_sublabel(self, clean, obj=None):
        return f"{clean.get('programme') or ''} · {clean.get('facility') or ''}"

    def apply_row(self, obj, clean, fields, actor):
        if obj is None:
            programme, _ = FacilityProgramme.objects.get_or_create(
                name__iexact=clean["programme"], defaults={"name": clean["programme"],
                                                          "levels": clean.get("levels") or ""})
            section = FacilitySection.objects.filter(programme=programme, name__iexact=clean["facility"]).first()
            if section is None:
                order = programme.sections.count() + 1
                section = FacilitySection.objects.create(programme=programme, name=clean["facility"],
                                                         sort_order=order, code=_letter(order))
            obj = FacilityItem(programme=programme, section=section, group=clean.get("group") or "",
                               name=clean["name"])
            fields = self.compare_fields
        for f in fields:
            if f in ("programme", "facility", "group", "name", "levels"):
                continue
            value = clean.get(f)
            if f in ("is_critical",) and value is None:
                value = False
            if f == "is_active" and value is None:
                value = True
            if f == "sort_order" and value is None:
                value = obj.sort_order or 0
            if f in ("nd_qty", "hnd_qty", "remarks") and value is None:
                value = ""
            setattr(obj, f, value)
        obj.updated_by = actor if getattr(actor, "pk", None) else None
        obj.save()
        return obj

    def deactivate(self, obj, actor):
        obj.is_active = False
        obj.updated_by = actor
        obj.save(update_fields=["is_active", "updated_by", "updated_at"])

    def export_value(self, obj, column):
        return self.current(obj, column.field)


def _letter(n):
    letters = ""
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return letters or "A"
