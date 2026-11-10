"""Staff import with a diff preview (v2.1 X45 - the first DiffSpec).

Same columns as the older staff import (staff/imports.py, kept working from the
Import & export wizard until every entry point is switched). Matched by staff
number (employee no.), then e-mail. New staff are added; changed fields are
listed old -> new with a tick each; staff missing from the file are kept.
"""
from django.core.exceptions import ValidationError

from core.importdiff import DiffSpec, register_diff

from .imports import StaffImport
from .models import Staff

FK_FIELDS = ("department", "office", "category", "state", "reports_to")
NBTE_DOMAINS = ("nbte.gov.ng",)


@register_diff
class StaffDiff(DiffSpec, StaffImport):
    name = "staff"
    label = "Staff"
    version = 1
    permission = "staff.import_nominal_roll"
    key_fields = ("employee_no",)
    alt_keys = (("email",),)
    match_note = "Matched by staff number, then e-mail"
    compare_fields = (
        "first_name", "last_name", "email", "phone", "department", "office", "category", "state", "subsection",
        "designation", "grade_level", "office_location", "reports_to", "duties", "contact_preference",
        "emergency_contact_name", "emergency_contact_phone", "birth_month", "birth_day", "date_joined_service",
    )
    allow_deactivate_missing = False  # leaving NBTE is a lifecycle step on the profile, never an import

    columns = StaffImport.columns

    def can_use(self, user):
        return bool(user and user.is_authenticated and (user.has_perm("staff.import_nominal_roll")
                                                         or user.has_perm("staff.add_staff")))

    def queryset(self):
        return Staff.objects.select_related("department", "office", "category", "state", "designation",
                                            "reports_to").order_by("last_name", "first_name")

    def current(self, obj, field):
        if field == "designation":
            return obj.designation.name if obj.designation_id else ""
        if field == "reports_to":
            return obj.reports_to.employee_no if obj.reports_to_id else ""
        return getattr(obj, field, None)

    def normalise(self, field, value):
        if field == "reports_to" and hasattr(value, "employee_no"):
            value = value.employee_no
        if field == "designation":
            from .nbte_reference import designation_key

            return designation_key(str(value or "")) if value else ""
        return super().normalise(field, value)

    def display(self, field, value):
        if field == "reports_to" and hasattr(value, "employee_no"):
            return value.employee_no
        if field == "contact_preference" and value:
            return dict(Staff.CONTACT_CHOICES).get(value, value)
        if field == "create_account":
            return "yes" if value else ""
        return super().display(field, value)

    def row_label(self, clean, obj=None):
        if obj is not None:
            return obj.full_name
        return f"{clean.get('first_name') or ''} {clean.get('last_name') or ''}".strip()

    def row_sublabel(self, clean, obj=None):
        if obj is not None:
            bits = [obj.employee_no, obj.designation.name if obj.designation_id else "",
                    obj.department.name if obj.department_id else ""]
        else:
            dept = clean.get("department")
            bits = [clean.get("employee_no") or "", clean.get("designation") or "",
                    dept.name if hasattr(dept, "name") else ""]
        return " · ".join(b for b in bits if b)

    def field_warnings(self, field, old, new, obj, clean):
        if field == "email" and old and new:
            domain = str(new).rsplit("@", 1)[-1].lower()
            if domain not in NBTE_DOMAINS and str(old).lower().endswith(NBTE_DOMAINS):
                return "Not an NBTE address. Left unticked so the sign-in e-mail stays the same."
        return None

    def apply_row(self, obj, clean, fields, actor):
        if obj is None:
            return StaffImport.save_row(self, dict(clean))
        from .nominal_roll import LookupResolver

        changed = []
        for f in fields:
            if f not in clean:
                continue
            value = clean.get(f)
            if f == "designation":
                if value:
                    obj.designation = LookupResolver().designation(value) or obj.designation
                    changed.append(f)
                continue
            if f == "employee_no":
                if not value:
                    continue
                if Staff.all_objects.filter(employee_no__iexact=value).exclude(pk=obj.pk).exists():
                    raise ValidationError(f"Staff number {value} already belongs to someone else.")
            if value is None and f not in FK_FIELDS:
                model_field = Staff._meta.get_field(f)
                if not model_field.null:
                    value = model_field.default if f == "contact_preference" else ""
            setattr(obj, f, value)
            changed.append(f)
        if changed:
            obj._state_changed_by = actor
            obj.save()
        return obj

    def export_value(self, obj, column):
        f = column.field
        if f in ("department", "office", "category", "state"):
            rel = getattr(obj, f)
            return rel.name if rel is not None else ""
        if f == "create_account":
            return ""
        return self.current(obj, f)
