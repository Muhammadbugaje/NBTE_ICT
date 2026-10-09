"""Staff + Department import/export specs, aligned with staff/models.py.

Design decisions:
- Lookup columns (Department, Office, Category, State, Reports-to) declare a
  `lookup` callable that returns REAL values from the database. The template
  generator uses those to build dropdowns and a Reference sheet, so a user
  can only pick values that exist. No invented examples.
- Date columns parse Excel date cells (openpyxl returns `datetime`) and ISO
  strings. `birth_month` / `birth_day` accept integers 1-12 / 1-31.
- `photo` is a binary field and is intentionally NOT part of the import. Add
  photos from the staff record page after import.
- `user` (the login account link) is also not imported; assign it from the
  user's admin page.
"""
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_date

from core.importexport import Column, ExportSpec, ImportSpec, register

from .models import (
    Department,
    Office,
    Staff,
    StaffCategory,
    StaffState,
)


# ---------------------------------------------------------------------------
# Parse helpers (friendlier error messages than the built-ins)
# ---------------------------------------------------------------------------
def _parse_int(value):
    if isinstance(value, bool):
        raise ValidationError(f"'{value}' is not a whole number.")
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValidationError(f"'{value}' is not a whole number.")


def _parse_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_date(text)
    if parsed is not None:
        return parsed
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValidationError(f"'{value}' is not a valid date (use YYYY-MM-DD).")


def _parse_month(value):
    n = _parse_int(value)
    if not 1 <= n <= 12:
        raise ValidationError("Month must be between 1 and 12.")
    return n


def _parse_day(value):
    n = _parse_int(value)
    if not 1 <= n <= 31:
        raise ValidationError("Day must be between 1 and 31.")
    return n


def _parse_contact_preference(value):
    text = str(value).strip().lower()
    valid = {"email", "phone", "inapp"}
    if text in valid:
        return text
    raise ValidationError(f"'{value}' is not valid. Choose one of: email, phone, inapp.")


def _yes_no(value):
    if value in (None, ""):
        return True
    return str(value).strip().lower() in ("yes", "true", "1", "y", "on")


# ---------------------------------------------------------------------------
# Lookup value providers (hit the DB at template-build time)
# ---------------------------------------------------------------------------
def _department_codes():
    return list(
        Department.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .values_list("code", flat=True)
    )


def _office_names():
    return list(
        Office.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .values_list("name", flat=True)
    )


def _category_names():
    return list(
        StaffCategory.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .values_list("name", flat=True)
    )


def _state_names():
    return list(
        StaffState.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .values_list("name", flat=True)
    )


def _existing_employee_numbers():
    return list(
        Staff.objects.order_by("employee_no")
        .values_list("employee_no", flat=True)
    )


# ---------------------------------------------------------------------------
# Resolvers used by validate_row / save_row
# ---------------------------------------------------------------------------
def _resolve_department(code):
    if not code:
        return None
    return Department.objects.filter(code=str(code).strip().lower()).first()


def _resolve_office(name):
    if not name:
        return None
    return Office.objects.filter(name__iexact=str(name).strip()).first()


def _resolve_category(name):
    if not name:
        return None
    return StaffCategory.objects.filter(name__iexact=str(name).strip()).first()


def _resolve_state(name):
    if not name:
        return None
    return StaffState.objects.filter(name__iexact=str(name).strip()).first()


def _resolve_reports_to(employee_no):
    if not employee_no:
        return None
    return Staff.objects.filter(employee_no=str(employee_no).strip()).first()


def _valid_values_hint(callable_):
    values = callable_() or []
    if not values:
        return "no values exist in the system yet — leave blank"
    if len(values) > 6:
        return ", ".join(values[:6]) + f" … ({len(values)} total)"
    return ", ".join(values)


# ===========================================================================
# DEPARTMENTS
# ===========================================================================
@register
class DepartmentImport(ImportSpec):
    name = "departments"
    label = "Departments"
    description = "Organisational departments — code, name, active flag, order."
    key_fields = ("code",)
    columns = [
        Column("Code", "code", required=True,
               help="Stable short code, e.g. 'ict'. Used in permission scoping and "
                    "cannot be changed after the department has been referenced.",
               example="ict"),
        Column("Name", "name", required=True,
               help="Human-readable department name shown in the UI.",
               example="ICT"),
        Column("Sort order", "sort_order", parse=_parse_int,
               help="Integer; lower numbers appear first. Default 0.",
               example="0"),
        Column("Active", "is_active",
               help="Yes / No. Leave blank to default to Yes.",
               example="Yes"),
    ]

    def validate_row(self, cleaned):
        if len(cleaned.get("code") or "") < 2:
            raise ValidationError("Code is too short (minimum 2 characters).")
        if len(cleaned.get("name") or "") < 2:
            raise ValidationError("Name is too short (minimum 2 characters).")

    def save_row(self, cleaned):
        code = cleaned["code"].strip().lower()
        obj, created = Department.objects.update_or_create(
            code=code,
            defaults={
                "name": cleaned["name"].strip(),
                "sort_order": cleaned.get("sort_order") or 0,
                "is_active": _yes_no(cleaned.get("is_active")),
            },
        )
        return "created" if created else "updated"


@register
class DepartmentExport(ExportSpec):
    name = "departments"
    label = "Departments"
    description = "All departments with codes, names, order, and active state."
    columns = [
        Column("Code", "code"),
        Column("Name", "name"),
        Column("Sort order", "sort_order"),
        Column("Active", "is_active"),
    ]

    def get_queryset(self):
        return Department.objects.order_by("sort_order", "name")

    def value(self, obj, column):
        if column.field == "is_active":
            return "Yes" if obj.is_active else "No"
        return getattr(obj, column.field)


# ===========================================================================
# STAFF
# ===========================================================================
@register
class StaffImport(ImportSpec):
    name = "staff"
    label = "Staff"
    description = (
        "The staff directory. Matches existing records by Employee No and updates "
        "them; unknown Employee No values create new records. Photos and login "
        "accounts are assigned separately from the staff page after import."
    )
    key_fields = ("employee_no",)
    columns = [
        Column("Employee No", "employee_no", required=True,
               help="Unique staff identifier. Used to match existing records.",
               example="ICT-001"),
        Column("First name", "first_name", required=True, example="Ada"),
        Column("Last name", "last_name", required=True, example="Lovelace"),
        Column("Email", "email",
               help="Official email address (used for notifications).",
               example="ada@nbte.gov.ng"),
        Column("Phone", "phone", example="08012345678"),

        # ---- position ----
        Column("Designation", "designation", example="Senior Officer"),
        Column("Grade level", "grade_level",
               help="Free-text. e.g. 12, or 'GL-12'.",
               example="12"),
        Column("Department code", "department_code",
               lookup=_department_codes,
               help="Must match an existing department code. Leave blank to leave unchanged."),
        Column("Office", "office_name",
               lookup=_office_names,
               help="Office name (e.g. Technical Support). Leave blank if not assigned."),
        Column("Category", "category_name",
               lookup=_category_names,
               help="Staff category. Leave blank if not applicable."),
        Column("Subsection", "subsection", example="Networking"),
        Column("Office location", "office_location", example="Block B, Room 12"),
        Column("Reports to", "reports_to_no",
               lookup=_existing_employee_numbers,
               help="Employee No of this staff member's supervisor. Must already exist. "
                    "Blank for top-level staff."),
        Column("Duties", "duties",
               help="Free-text description of responsibilities."),

        # ---- state ----
        Column("State", "state_name",
               lookup=_state_names,
               help="Current staff state (Active, On Leave, etc.). Leave blank to leave unchanged."),
        Column("State start date", "state_start", parse=_parse_date,
               help="YYYY-MM-DD. Defaults to today when a new state is set."),
        Column("State end date", "state_end", parse=_parse_date,
               help="YYYY-MM-DD. Leave blank for open-ended states."),

        # ---- contact preferences & emergencies ----
        Column("Contact preference", "contact_preference", parse=_parse_contact_preference,
               help="One of: email, phone, inapp. Blank defaults to email.",
               example="email"),
        Column("Emergency contact name", "emergency_contact_name"),
        Column("Emergency contact phone", "emergency_contact_phone"),

        # ---- personal (month/day only; no birth year, per doc 30 Q1) ----
        Column("Birth month", "birth_month", parse=_parse_month,
               help="1-12. Only month and day are stored; not a full birth date.",
               example="6"),
        Column("Birth day", "birth_day", parse=_parse_day,
               help="1-31.",
               example="15"),
        Column("Date joined service", "date_joined_service", parse=_parse_date,
               help="YYYY-MM-DD."),
    ]

    def validate_row(self, cleaned):
        code = cleaned.get("department_code")
        if code and not _resolve_department(code):
            raise ValidationError(
                f"Department code '{code}' is not valid. "
                f"Available: {_valid_values_hint(_department_codes)}. "
                f"Leave blank if not applicable."
            )
        office = cleaned.get("office_name")
        if office and not _resolve_office(office):
            raise ValidationError(
                f"Office '{office}' is not valid. "
                f"Available: {_valid_values_hint(_office_names)}. "
                f"Leave blank if not applicable."
            )
        cat = cleaned.get("category_name")
        if cat and not _resolve_category(cat):
            raise ValidationError(
                f"Category '{cat}' is not valid. "
                f"Available: {_valid_values_hint(_category_names)}. "
                f"Leave blank if not applicable."
            )
        state = cleaned.get("state_name")
        if state and not _resolve_state(state):
            raise ValidationError(
                f"State '{state}' is not valid. "
                f"Available: {_valid_values_hint(_state_names)}. "
                f"Leave blank if not applicable."
            )
        rt = cleaned.get("reports_to_no")
        if rt and str(rt).strip() != str(cleaned.get("employee_no") or "").strip():
            if not _resolve_reports_to(rt):
                raise ValidationError(
                    f"Reports-to employee '{rt}' does not exist. "
                    f"Import that staff record first, or leave blank."
                )
        elif rt and str(rt).strip() == str(cleaned.get("employee_no") or "").strip():
            raise ValidationError("A staff member cannot report to themselves.")

    def save_row(self, cleaned):
        employee_no = str(cleaned["employee_no"]).strip()
        defaults = {
            "first_name": str(cleaned["first_name"]).strip(),
            "last_name": str(cleaned["last_name"]).strip(),
            "email": cleaned.get("email") or "",
            "phone": cleaned.get("phone") or "",
            "designation": cleaned.get("designation") or "",
            "grade_level": cleaned.get("grade_level") or "",
            "subsection": cleaned.get("subsection") or "",
            "office_location": cleaned.get("office_location") or "",
            "duties": cleaned.get("duties") or "",
            "contact_preference": cleaned.get("contact_preference") or "email",
            "emergency_contact_name": cleaned.get("emergency_contact_name") or "",
            "emergency_contact_phone": cleaned.get("emergency_contact_phone") or "",
            "birth_month": cleaned.get("birth_month"),
            "birth_day": cleaned.get("birth_day"),
            "date_joined_service": cleaned.get("date_joined_service"),
        }

        dept = _resolve_department(cleaned.get("department_code"))
        if dept is not None:
            defaults["department"] = dept
        office = _resolve_office(cleaned.get("office_name"))
        if office is not None:
            defaults["office"] = office
        cat = _resolve_category(cleaned.get("category_name"))
        if cat is not None:
            defaults["category"] = cat
        state = _resolve_state(cleaned.get("state_name"))
        if state is not None:
            defaults["state"] = state
        rt = _resolve_reports_to(cleaned.get("reports_to_no"))
        if rt is not None:
            defaults["reports_to"] = rt

        # State dates: only apply if a state is being set in this row.
        if state is not None:
            if cleaned.get("state_start") is not None:
                defaults["state_start"] = cleaned["state_start"]
            if cleaned.get("state_end") is not None:
                defaults["state_end"] = cleaned["state_end"]

        obj, created = Staff.objects.update_or_create(
            employee_no=employee_no,
            defaults=defaults,
        )
        return "created" if created else "updated"


@register
class StaffExport(ExportSpec):
    name = "staff"
    label = "Staff"
    description = (
        "All active staff records. This export can be re-imported unchanged — "
        "it's a round-trip format for the staff module."
    )
    columns = [
        Column("Employee No", "employee_no"),
        Column("First name", "first_name"),
        Column("Last name", "last_name"),
        Column("Email", "email"),
        Column("Phone", "phone"),
        Column("Designation", "designation"),
        Column("Grade level", "grade_level"),
        Column("Department code", "department_code"),
        Column("Office", "office_name"),
        Column("Category", "category_name"),
        Column("Subsection", "subsection"),
        Column("Office location", "office_location"),
        Column("Reports to", "reports_to_no"),
        Column("Duties", "duties"),
        Column("State", "state_name"),
        Column("State start date", "state_start"),
        Column("State end date", "state_end"),
        Column("Contact preference", "contact_preference"),
        Column("Emergency contact name", "emergency_contact_name"),
        Column("Emergency contact phone", "emergency_contact_phone"),
        Column("Birth month", "birth_month"),
        Column("Birth day", "birth_day"),
        Column("Date joined service", "date_joined_service"),
    ]

    def get_queryset(self):
        return (
            Staff.objects
            .select_related("department", "office", "category", "state", "reports_to")
            .order_by("last_name", "first_name")
        )

    def value(self, obj, column):
        field = column.field
        if field == "department_code":
            return obj.department.code if obj.department_id else ""
        if field == "office_name":
            return obj.office.name if obj.office_id else ""
        if field == "category_name":
            return obj.category.name if obj.category_id else ""
        if field == "state_name":
            return obj.state.name if obj.state_id else ""
        if field == "reports_to_no":
            return obj.reports_to.employee_no if obj.reports_to_id else ""
        value = getattr(obj, field, None)
        if value is None:
            return ""
        # Dates render as ISO strings so the exported file re-imports cleanly.
        if isinstance(value, (date, datetime)):
            return value.isoformat()[:10]
        return value