"""Import diff specs for professional bodies and the programme master list
(v2.1 R.5 X42 / R.4 X44, through core/importdiff.py).

* ``professional_bodies`` - one row per body, matched by name then acronym,
  with a *Programmes* column: the master-list codes the body regulates
  (separated by ";"). Applying sets ``ProgrammeMaster.professional_body`` for
  those codes, so the export -> edit -> import round trip carries programmes.
* ``programme_master`` - the master list. Its export doubles as the mapping
  template: download it, fill the *Professional body* column, upload, check
  the field-level preview, apply.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError

from core.importdiff import DiffSpec, register_diff
from core.importexport import Column

from .models import (Discipline, Institution, InstitutionAddress, InstitutionCategory, InstitutionContact,
                     InstitutionOwnership, ManagementRole, ProfessionalBody, Programme, ProgrammeCategory,
                     ProgrammeLevel, ProgrammeMaster)


def _yes(v):
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s in ("", "yes", "y", "true", "1", "active"):
        return True
    if s in ("no", "n", "false", "0", "inactive"):
        return False
    raise ValidationError("Use Yes or No.")


def _level(v):
    obj = ProgrammeLevel.objects.filter(code__iexact=str(v).strip()).first() or \
        ProgrammeLevel.objects.filter(name__iexact=str(v).strip()).first()
    if obj is None:
        raise ValidationError(f"Unknown level '{v}'. Use ND, HND, NID or NVC.")
    return obj


def _body(v):
    s = str(v).strip()
    obj = ProfessionalBody.objects.filter(acronym__iexact=s).first() or \
        ProfessionalBody.objects.filter(name__iexact=s).first()
    if obj is None:
        raise ValidationError(f"Unknown professional body '{v}'. Use the acronym or full name of a body on file.")
    return obj


def _category(v):
    obj = ProgrammeCategory.objects.filter(name__iexact=str(v).strip()).first()
    if obj is None:
        raise ValidationError(f"Unknown programme category '{v}'.")
    return obj


def _discipline(v):
    s = str(v).strip()
    obj = Discipline.objects.filter(name__iexact=s).first() or Discipline.objects.filter(code__iexact=s).first()
    if obj is None:
        raise ValidationError(f"Unknown discipline '{v}'.")
    return obj


def _codes(v):
    return sorted({c.strip().lower() for c in str(v or "").replace(",", ";").replace("\n", ";").split(";")
                   if c.strip()})


def _names_list(model, field="name"):
    return lambda: list(model.objects.filter(is_active=True).values_list(field, flat=True))


@register_diff
class ProfessionalBodyDiff(DiffSpec):
    name = "professional_bodies"
    label = "Professional bodies"
    version = 1
    # Part 1b/1c: an import applies at once, so it needs the approve layer (or the ES / super admin).
    permission = "institutions.approve_edit_professional_bodies"

    def can_use(self, user):
        from .tvet_access import is_top
        return bool(user and user.is_authenticated and (is_top(user) or user.has_perm(self.permission)))
    key_fields = ("name",)
    alt_keys = (("acronym",),)
    match_note = "Matched by name, then acronym"
    compare_fields = ("acronym", "address", "contact_person", "email", "phone", "registrar_name", "registrar_email",
                      "registrar_phone", "website", "requires_letter_before_commencement", "is_active", "programmes")
    allow_deactivate_missing = True
    columns = [
        Column("Name", "name", required=True),
        Column("Acronym", "acronym"),
        Column("Address", "address"),
        Column("Contact person", "contact_person"),
        Column("E-mail", "email"),
        Column("Phone", "phone"),
        Column("Registrar", "registrar_name"),
        Column("Registrar e-mail", "registrar_email"),
        Column("Registrar phone", "registrar_phone"),
        Column("Website", "website"),
        Column("Letter before commencement", "requires_letter_before_commencement", parse=_yes),
        Column("Active", "is_active", parse=_yes),
        Column("Programmes", "programmes", parse=_codes,
               help="Programme master-list codes, separated by ';' (e.g. nd-computer-science; hnd-computer-science)."),
    ]

    def can_use(self, user):
        return bool(user and user.is_authenticated and (user.is_superuser or user.has_perm(self.permission)))

    def queryset(self):
        return ProfessionalBody.objects.order_by("name")

    def validate_row(self, clean):
        codes = clean.get("programmes") or []
        if codes:
            known = set(ProgrammeMaster.objects.filter(code__in=codes).values_list("code", flat=True))
            missing = [c for c in codes if c not in known]
            if missing:
                raise ValidationError("Unknown programme code(s): " + ", ".join(missing))

    def current(self, obj, field):
        if field == "programmes":
            return sorted(obj.master_programmes.values_list("code", flat=True))
        return getattr(obj, field, None)

    def normalise(self, field, value):
        if field == "programmes":
            return ";".join(sorted(value or []))
        return super().normalise(field, value)

    def display(self, field, value):
        if field == "programmes":
            return "; ".join(value or [])
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return super().display(field, value)

    def export_value(self, obj, column):
        v = self.current(obj, column.field)
        return self.display(column.field, v) if column.field == "programmes" or isinstance(v, bool) else v

    def row_sublabel(self, clean, obj=None):
        return (obj.acronym if obj is not None else clean.get("acronym")) or ""

    def apply_row(self, obj, clean, fields, actor):
        if obj is None:
            obj = ProfessionalBody(created_by=actor)
            fields = [c.field for c in self.columns]
        codes = None
        for f in fields:
            if f not in clean:
                continue
            v = clean.get(f)
            if f == "programmes":
                codes = v or []
                continue
            if v is None:
                v = True if f == "is_active" else (False if isinstance(getattr(obj, f), bool) else "")
            setattr(obj, f, v)
        obj.full_clean(exclude=["created_by"])
        obj.save()
        if codes is not None:
            ProgrammeMaster.objects.filter(professional_body=obj).exclude(code__in=codes).update(
                professional_body=None)
            ProgrammeMaster.objects.filter(code__in=codes).update(professional_body=obj)
        return obj

    def deactivate(self, obj, actor):
        obj.is_active = False
        obj.save(update_fields=["is_active", "updated_at"])


@register_diff
class ProgrammeMasterDiff(DiffSpec):
    name = "programme_master"
    label = "Programme master list"
    version = 1
    permission = "institutions.approve_edit_programmes"  # Part 1b/1c: imports apply at once
    key_fields = ("name", "level")
    alt_keys = (("code",),)
    match_note = "Matched by programme name + level, then code"
    compare_fields = ("code", "programme_category", "discipline", "professional_body", "aliases", "curriculum_ref",
                      "nos_sector", "is_active")
    allow_deactivate_missing = True
    columns = [
        Column("Programme", "name", required=True),
        Column("Level", "level", required=True, parse=_level,
               lookup=lambda: [c.upper() for c in ProgrammeLevel.objects.exclude(code="admin")
                               .values_list("code", flat=True)]),
        Column("Code", "code", help="Leave blank for new rows: it is generated."),
        Column("Category", "programme_category", parse=_category, lookup=_names_list(ProgrammeCategory)),
        Column("Discipline", "discipline", parse=_discipline, lookup=_names_list(Discipline)),
        Column("Professional body", "professional_body", parse=_body,
               lookup=lambda: [b.acronym or b.name for b in ProfessionalBody.objects.filter(is_active=True)],
               help="Acronym or full name. Blank = no change."),
        Column("Aliases", "aliases", help="Other names, separated by ';'."),
        Column("Curriculum reference", "curriculum_ref"),
        Column("NOS sector", "nos_sector"),
        Column("Active", "is_active", parse=_yes),
    ]

    def can_use(self, user):
        from .tvet_access import is_top
        return bool(user and user.is_authenticated and (is_top(user) or user.has_perm(self.permission)))

    def queryset(self):
        return ProgrammeMaster.objects.select_related("level", "professional_body", "programme_category",
                                                      "discipline").order_by("name", "level__sort_order")

    def clean_row(self, raw):
        clean = super().clean_row(raw)
        if clean.get("aliases"):
            clean["aliases"] = "\n".join(a.strip() for a in str(clean["aliases"]).replace("\n", ";").split(";")
                                         if a.strip())
        if clean.get("code"):
            clean["code"] = str(clean["code"]).strip().lower()
        return clean

    def display(self, field, value):
        if field == "level" and hasattr(value, "code"):
            return value.code.upper()
        if field == "professional_body" and hasattr(value, "acronym"):
            return value.acronym or value.name
        if field == "aliases" and value:
            return "; ".join(str(value).splitlines())
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if hasattr(value, "name"):
            return value.name
        return super().display(field, value)

    def export_value(self, obj, column):
        return self.display(column.field, self.current(obj, column.field))

    def row_label(self, clean, obj=None):
        if obj is not None:
            return str(obj)
        lv = clean.get("level")
        return f"{lv.code.upper() if hasattr(lv, 'code') else ''} {clean.get('name') or ''}".strip()

    def row_sublabel(self, clean, obj=None):
        body = obj.professional_body if obj is not None else clean.get("professional_body")
        return (body.acronym or body.name) if body else "No professional body"

    def apply_row(self, obj, clean, fields, actor):
        if obj is None:
            obj = ProgrammeMaster()
            fields = [c.field for c in self.columns]
        for f in fields:
            if f not in clean:
                continue
            v = clean.get(f)
            if v is None:
                if f in ("programme_category", "discipline", "professional_body"):
                    continue  # blank = no change
                v = True if f == "is_active" else ""
            setattr(obj, f, v)
        if obj.programme_category_id and not obj.discipline_id:
            obj.discipline = obj.programme_category.discipline
        obj.save()
        if obj.professional_body_id:
            from .models import Programme

            Programme.unscoped.filter(master=obj, professional_body__isnull=True).update(
                professional_body=obj.professional_body, body_source="master")
        return obj

    def deactivate(self, obj, actor):
        obj.is_active = False
        obj.save(update_fields=["is_active", "updated_at"])


# ===========================================================================
# v2.1 X45: institutions, programmes and management roles
# ===========================================================================
def _named(model, label):
    def parse(value):
        text = " ".join(str(value).split())
        obj = model.objects.filter(name__iexact=text).first()
        if obj is None and hasattr(model, "code"):
            obj = model.objects.filter(code__iexact=text).first()
        if obj is None:
            raise ValidationError(f"Unknown {label} '{value}'.")
        return obj
    return parse


def _state(value):
    from staff.models import StateOfOrigin

    text = " ".join(str(value).split())
    obj = (StateOfOrigin.objects.filter(name__iexact=text).first()
           or StateOfOrigin.objects.filter(code__iexact=text).first())
    if obj is None:
        raise ValidationError(f"Unknown state '{value}'.")
    return obj


def _institution_status(value):
    text = str(value or "").strip()
    for key, label in Institution.STATUS_LABELS.items():
        if text.lower() in (key, label.lower()):
            return key
    raise ValidationError(f"Unknown institution status '{value}'.")


def _programme_status(value):
    text = str(value or "").strip().lower().replace("_", " ")
    for key, label in Programme.STATUS_CHOICES:
        if text in (key, key.replace("_", " "), label.lower()):
            return key
    raise ValidationError(f"Unknown programme status '{value}'.")


def _approval_type(value):
    from accreditation.models import ApprovalType

    text = " ".join(str(value).split())
    obj = (ApprovalType.objects.filter(name__iexact=text).first()
           or ApprovalType.objects.filter(code__iexact=text).first())
    if obj is None:
        raise ValidationError(f"Unknown approval type '{value}'.")
    return obj


def _date_value(value):
    import datetime

    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            pass
    raise ValidationError(f"Unknown date '{value}'. Use YYYY-MM-DD.")


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"'{value}' is not a whole number.")


@register_diff
class InstitutionDiff(DiffSpec):
    """v2.1 X45: institutions with their current address and official contact.
    Matched by code. New rows are created as proposed; an approved row keeps
    its approval date. Importing applies at once, so it needs the approve
    permission (or the ES / super admin)."""

    name = "institutions"
    label = "Institutions"
    version = 1
    permission = "institutions.approve_bulk_import"
    key_fields = ("code",)
    match_note = "Matched by institution code"
    compare_fields = ("name", "ownership", "category", "state", "lga", "address_line1", "town",
                      "official_email", "phone", "year_established", "cac_rc_number", "website", "status")
    columns = [
        Column("Code", "code", required=True, help="The institution's short code (e.g. KDP). Never changes."),
        Column("Name", "name", required=True),
        Column("Ownership", "ownership", parse=_named(InstitutionOwnership, "ownership")),
        Column("Category", "category", parse=_named(InstitutionCategory, "category")),
        Column("State", "state", parse=_state),
        Column("LGA", "lga"),
        Column("Address", "address_line1", help="Permanent-site address."),
        Column("Town", "town"),
        Column("Official e-mail", "official_email"),
        Column("Phone", "phone"),
        Column("Year established", "year_established", parse=_int_or_none),
        Column("Website", "website"),
        Column("CAC RC number", "cac_rc_number"),
        Column("Status", "status", parse=_institution_status,
               lookup=lambda: [Institution.STATUS_LABELS[k] for k in Institution.STATUS_LABELS]),
    ]

    def can_use(self, user):
        from .tvet_access import is_top

        return bool(user and user.is_authenticated
                    and (is_top(user) or user.has_perm(self.permission)))

    def queryset(self):
        return Institution.unscoped.select_related("ownership", "category", "state").order_by("code")

    def current(self, obj, field):
        if field in ("address_line1", "town"):
            row = InstitutionAddress.unscoped.filter(institution=obj, kind="permanent_site",
                                                     is_current=True).first()
            return getattr(row, "line1" if field == "address_line1" else "town") if row else ""
        if field in ("official_email", "phone"):
            row = InstitutionContact.unscoped.filter(institution=obj, kind=field, is_current=True).first()
            return row.value if row else ""
        return getattr(obj, field, None)

    def display(self, field, value):
        if field in ("ownership", "category", "state") and value is not None and hasattr(value, "name"):
            return value.name
        if field == "status":
            return Institution.STATUS_LABELS.get(value, value or "")
        return super().display(field, value)

    def row_label(self, clean, obj=None):
        return (obj.code + " — " + obj.name) if obj is not None else \
            f"{clean.get('code') or ''} — {clean.get('name') or ''}".strip(" —")

    def row_sublabel(self, clean, obj=None):
        if obj is not None:
            return f"{obj.category.name if obj.category_id else ''} · {obj.state.name if obj.state_id else ''}"
        cat = clean.get("category")
        state = clean.get("state")
        return " · ".join(b for b in (getattr(cat, "name", ""), getattr(state, "name", "")) if b)

    def apply_row(self, obj, clean, fields, actor):
        from . import services

        if obj is None:
            required = [f for f in ("name", "ownership", "category", "state") if not clean.get(f)]
            if required:
                raise ValidationError("A new institution needs name, ownership, category and state.")
            obj = services.create_institution({
                "code": clean.get("code"), "name": clean["name"], "ownership": clean.get("ownership"),
                "category": clean.get("category"), "state": clean.get("state"), "lga": clean.get("lga") or "",
                "year_established": clean.get("year_established"), "cac_rc_number": clean.get("cac_rc_number") or "",
                "website": clean.get("website") or "",
                "address": {"line1": clean.get("address_line1") or "", "town": clean.get("town") or "",
                            "lga": clean.get("lga") or "", "state": clean.get("state")},
                "contacts": {"official_email": {"value": clean.get("official_email") or ""},
                             "phone": {"value": clean.get("phone") or ""}},
            }, actor=actor, origin="import")
            if clean.get("status") and clean["status"] != Institution.PROPOSED:
                obj.status = clean["status"]
                if obj.status == Institution.APPROVED:
                    from django.utils import timezone

                    obj.approved_at = obj.approved_at or timezone.now()
                obj.save(update_fields=["status", "approved_at", "updated_at"])
            return obj

        changed = []
        for field in ("name", "ownership", "category", "state", "lga", "year_established", "cac_rc_number",
                      "website", "status"):
            if field not in fields or field not in clean:
                continue
            value = clean.get(field)
            if value is None and field not in ("year_established",):
                value = "" if field not in ("ownership", "category", "state") else getattr(obj, field)
            setattr(obj, field, value)
            changed.append(field)
        if "status" in changed and obj.status == Institution.APPROVED and obj.approved_at is None:
            from django.utils import timezone

            obj.approved_at = timezone.now()
            changed.append("approved_at")
        if changed:
            obj.save()
        address = {}
        for field, attr in (("address_line1", "line1"), ("town", "town")):
            if field in fields and field in clean:
                address[attr] = clean.get(field) or ""
        if address:
            row = InstitutionAddress.unscoped.filter(institution=obj, kind="permanent_site",
                                                     is_current=True).first()
            if row is None:
                InstitutionAddress.unscoped.create(institution=obj, kind="permanent_site", **address)
            else:
                for attr, value in address.items():
                    setattr(row, attr, value)
                row.save(update_fields=list(address) + ["updated_at"])
        for field in ("official_email", "phone"):
            if field in fields and field in clean:
                value = clean.get(field) or ""
                row = InstitutionContact.unscoped.filter(institution=obj, kind=field, is_current=True).first()
                if row is None:
                    if value:
                        InstitutionContact.unscoped.create(institution=obj, kind=field, value=value)
                else:
                    row.value = value
                    row.save(update_fields=["value", "updated_at"])
        return obj


@register_diff
class ProgrammeDiff(DiffSpec):
    """v2.1 X45: institution programmes (status, approval type, streams, dates
    and professional body). Matched by institution code + programme + level."""

    name = "programmes"
    label = "Institution programmes"
    version = 1
    permission = "institutions.approve_bulk_import"
    key_fields = ("institution_code", "name", "level")
    match_note = "Matched by institution code + programme name + level"
    compare_fields = ("category", "status", "approval_type", "approved_streams", "commencement_date",
                      "expiry_date", "professional_body")
    columns = [
        Column("Institution code", "institution_code", required=True),
        Column("Programme", "name", required=True),
        Column("Level", "level", required=True, parse=_level,
               lookup=lambda: [c.upper() for c in ProgrammeLevel.objects.exclude(code="admin")
                               .values_list("code", flat=True)]),
        Column("Category", "category", parse=_category, lookup=_names_list(ProgrammeCategory)),
        Column("Status", "status", parse=_programme_status),
        Column("Approval type", "approval_type", parse=_approval_type),
        Column("Approved streams", "approved_streams", parse=_int_or_none),
        Column("Commencement date", "commencement_date", parse=_date_value),
        Column("Expiry date", "expiry_date", parse=_date_value, help="Blank = derived from the approval type."),
        Column("Professional body", "professional_body", parse=_body,
               lookup=lambda: [b.acronym or b.name for b in ProfessionalBody.objects.filter(is_active=True)]),
    ]

    def can_use(self, user):
        from .tvet_access import is_top

        return bool(user and user.is_authenticated
                    and (is_top(user) or user.has_perm(self.permission)))

    def queryset(self):
        return (Programme.unscoped.select_related("institution", "level", "category", "approval_type",
                                                  "professional_body").order_by("institution__code", "name"))

    def lookup(self, clean):
        inst = Institution.unscoped.filter(code__iexact=str(clean.get("institution_code") or "").strip()).first()
        level = clean.get("level")
        if inst is None or level is None or not clean.get("name"):
            return None, None
        found = Programme.unscoped.filter(institution=inst, name__iexact=str(clean["name"]).strip(),
                                          level=level).first()
        return (found, ("institution_code", "name", "level")) if found else (None, None)

    def object_key(self, obj):
        return "|".join((obj.institution.code.casefold(), " ".join(obj.name.split()).casefold(),
                         (obj.level.code if obj.level_id else "").casefold()))

    def current(self, obj, field):
        if field == "institution_code":
            return obj.institution.code
        return getattr(obj, field, None)

    def display(self, field, value):
        if field == "level" and value is not None and hasattr(value, "code"):
            return value.code.upper()
        if field in ("category", "approval_type", "professional_body") and value is not None \
                and hasattr(value, "name"):
            return value.name
        if field == "status":
            return dict(Programme.STATUS_CHOICES).get(value, value or "")
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return super().display(field, value)

    def row_label(self, clean, obj=None):
        if obj is not None:
            return f"{obj.institution.code} · {obj}"
        level = clean.get("level")
        return f"{clean.get('institution_code') or ''} · " \
               f"{getattr(level, 'code', '').upper()} {clean.get('name') or ''}".strip()

    def row_sublabel(self, clean, obj=None):
        body = obj.professional_body if obj is not None else clean.get("professional_body")
        return (body.acronym or body.name) if body else ""

    def apply_row(self, obj, clean, fields, actor):
        inst = Institution.unscoped.filter(code__iexact=str(clean.get("institution_code") or "").strip()).first()
        if inst is None:
            raise ValidationError(f"Unknown institution code '{clean.get('institution_code')}'.")
        if obj is None:
            obj = Programme(institution=inst, name=" ".join(str(clean["name"]).split()),
                            level=clean.get("level"))
            fields = [c.field for c in self.columns if c.field not in ("institution_code",)]
        for field in fields:
            if field not in clean or field in ("institution_code", "name", "level"):
                continue
            value = clean.get(field)
            if field in ("category", "approval_type", "professional_body") and value is None:
                continue
            setattr(obj, field, value)
        if obj.expiry_date is None and obj.commencement_date and obj.approval_type_id:
            obj.expiry_date = obj.approval_type.expiry_for(obj.commencement_date)
        obj.save()
        return obj


@register_diff
class ManagementRoleDiff(DiffSpec):
    """v2.1 X45: the NBTE-defined management roles list."""

    name = "management_roles"
    label = "Management roles"
    version = 1
    permission = "institutions.manage_management_roles"
    key_fields = ("code",)
    match_note = "Matched by role code"
    compare_fields = ("name", "sort_order", "is_mandatory", "max_holders", "requires_email",
                      "requires_phone", "is_chief_executive", "is_ict_head", "is_active")
    columns = [
        Column("Code", "code", required=True),
        Column("Name", "name", required=True),
        Column("Sort order", "sort_order", parse=_int_or_none),
        Column("Mandatory", "is_mandatory", parse=_yes),
        Column("Max holders", "max_holders", parse=_int_or_none),
        Column("Requires e-mail", "requires_email", parse=_yes),
        Column("Requires phone", "requires_phone", parse=_yes),
        Column("Chief executive", "is_chief_executive", parse=_yes),
        Column("Head of ICT", "is_ict_head", parse=_yes),
        Column("Active", "is_active", parse=_yes),
    ]

    def can_use(self, user):
        from .tvet_access import is_top

        return bool(user and user.is_authenticated
                    and (is_top(user) or user.has_perm(self.permission)))

    def queryset(self):
        return ManagementRole.objects.order_by("sort_order", "name")

    def row_label(self, clean, obj=None):
        return f"{obj.code} — {obj.name}" if obj is not None else \
            f"{clean.get('code') or ''} — {clean.get('name') or ''}"

    def apply_row(self, obj, clean, fields, actor):
        if obj is None:
            obj = ManagementRole()
            fields = [c.field for c in self.columns]
        for field in fields:
            if field not in clean:
                continue
            value = clean.get(field)
            if value is None:
                if field in ("max_holders", "sort_order"):
                    value = 0
                elif field.startswith("is_") or field.startswith("requires_"):
                    value = False
                else:
                    value = ""
            setattr(obj, field, value)
        obj.full_clean()
        obj.save()
        return obj
