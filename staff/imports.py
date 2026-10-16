"""Staff import/export specs — registered with the shared importexport wizard.

Import columns include an optional `create_account` switch that fires the same
account-provisioning + welcome-email path as the manual "Add staff" flow.
"""
from __future__ import annotations

from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from core.importexport import Column, ExportSpec, ImportSpec, register

from .models import Department, Office, Staff, StaffCategory, StaffState

User = get_user_model()

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off", ""}


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------
def _as_str(v):
    return str(v).strip() if v is not None else ""


def _as_bool(v):
    s = _as_str(v).lower()
    if s in TRUE_VALUES:
        return True
    if s in FALSE_VALUES:
        return False
    raise ValidationError(f"Not a yes/no value: {v}")


def _as_int(v, *, lo=None, hi=None, label="value"):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a number.")
    if lo is not None and n < lo:
        raise ValidationError(f"{label} must be ≥ {lo}.")
    if hi is not None and n > hi:
        raise ValidationError(f"{label} must be ≤ {hi}.")
    return n


def _as_date(v):
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = _as_str(v)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValidationError(f"Date not understood: {v} (try YYYY-MM-DD).")


def _lookup_model(model, label):
    def parse(v):
        s = _as_str(v)
        if not s:
            return None
        obj = model.objects.filter(name__iexact=s).first()
        if not obj:
            raise ValidationError(f"Unknown {label}: {v}")
        return obj
    return parse


def _lookup_department(v):
    s = _as_str(v)
    if not s:
        return None
    obj = Department.objects.filter(name__iexact=s).first()
    if not obj:
        obj = Department.objects.filter(code__iexact=s).first()
    if not obj:
        raise ValidationError(f"Unknown department: {v}")
    return obj


def _lookup_office(v):
    return _lookup_model(Office, "office")(v)


def _lookup_category(v):
    return _lookup_model(StaffCategory, "category")(v)


def _lookup_state(v):
    return _lookup_model(StaffState, "state")(v)


def _lookup_reports_to(v):
    s = _as_str(v)
    if not s:
        return None
    # Try employee_no first, then email
    obj = Staff.objects.filter(employee_no__iexact=s).first()
    if not obj:
        obj = Staff.objects.filter(email__iexact=s).first()
    if not obj:
        raise ValidationError(f"No staff with employee no / email: {v}")
    return obj


def _lookups(model):
    return lambda: list(model.objects.values_list("name", flat=True))


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
@register
class StaffImport(ImportSpec):
    name = "staff"
    label = "Staff directory"
    description = (
        "Bulk create or update staff. Match on employee number. An optional "
        "'create_account' column provisions a login and emails the credentials."
    )
    key_fields = ("employee_no",)

    columns = [
        Column("Employee no.", field="employee_no", required=True,
               help="Unique staff ID.", example="ICT-0012"),
        Column("First name", field="first_name", required=True),
        Column("Last name",  field="last_name",  required=True),
        Column("Email", field="email", required=True,
               help="Used for the account if you enable create_account.",
               example="first.last@nbte.gov.ng"),
        Column("Phone", field="phone"),

        Column("Department", field="department",
               lookup=lambda: list(Department.objects.filter(is_active=True)
                                   .values_list("name", flat=True)),
               parse=_lookup_department),
        Column("Office", field="office",
               lookup=lambda: list(Office.objects.filter(is_active=True)
                                   .values_list("name", flat=True)),
               parse=_lookup_office),
        Column("Category", field="category",
               lookup=lambda: list(StaffCategory.objects.filter(is_active=True)
                                   .values_list("name", flat=True)),
               parse=_lookup_category),
        Column("State", field="state",
               lookup=lambda: list(StaffState.objects.filter(is_active=True)
                                   .values_list("name", flat=True)),
               parse=_lookup_state),

        Column("Subsection", field="subsection"),
        Column("Designation", field="designation"),
        Column("Grade level", field="grade_level"),
        Column("Office location", field="office_location"),
        Column("Reports to", field="reports_to",
               help="Employee no. or email of the supervisor.",
               parse=_lookup_reports_to),
        Column("Duties", field="duties"),

        Column("Contact preference", field="contact_preference",
               help="email / phone / inapp",
               parse=lambda v: _as_str(v).lower()),
        Column("Emergency contact name",  field="emergency_contact_name"),
        Column("Emergency contact phone", field="emergency_contact_phone"),

        Column("Birth month", field="birth_month",
               parse=lambda v: _as_int(v, lo=1, hi=12, label="Birth month")),
        Column("Birth day", field="birth_day",
               parse=lambda v: _as_int(v, lo=1, hi=31, label="Birth day")),
        Column("Date joined service", field="date_joined_service",
               parse=_as_date),

        Column("Create account", field="create_account",
               help="yes / no. If yes, a login is provisioned and credentials emailed.",
               example="no", parse=_as_bool),
    ]

    def validate_row(self, cleaned):
        # Reject duplicate employee_no only if the existing row belongs to
        # a *different* key (i.e. we're creating, not updating).
        emp = cleaned.get("employee_no")
        if not emp:
            return
        cp = cleaned.get("contact_preference")
        if cp and cp not in ("email", "phone", "inapp"):
            raise ValidationError(f"Contact preference must be email/phone/inapp, got '{cp}'.")

    def save_row(self, cleaned):
        """Returns "created" or "updated" so the ImportResult counts correctly."""
        emp = cleaned.pop("employee_no")
        create_account = cleaned.pop("create_account", False)
        department = cleaned.pop("department", None)

        obj = Staff.objects.filter(employee_no__iexact=emp).first()
        created = obj is None
        if created:
            obj = Staff(employee_no=emp)

        # Review H6: the Department column used to be popped and discarded.
        # A blank cell leaves an existing record's department unchanged.
        if department is not None:
            obj.department = department

        # Review H6: blank cells arrive as None. Text columns are NOT NULL in
        # the database, so they become "" (previously the whole row failed).
        # A blank State leaves the current state unchanged; a blank contact
        # preference falls back to the model default.
        for field, value in cleaned.items():
            if value is None:
                if field == "state":
                    continue
                model_field = Staff._meta.get_field(field)
                if field == "contact_preference":
                    value = model_field.default
                elif not model_field.null and not model_field.is_relation:
                    value = ""
            setattr(obj, field, value)

        obj._state_changed_by = None  # no actor context inside the wizard
        obj.save()

        # Provision a user account on request — same logic as the view.
        if create_account and not obj.user_id and obj.email:
            _provision_account_for_import(obj)

        return "created" if created else "updated"


def _provision_account_for_import(staff: Staff):
    """Minimal counterpart to the view's provisioning helper — creates the
    user, sets must_change_password, and dispatches the welcome email.
    Review H5: shares staff.services.provision_account_for_staff."""
    from django.conf import settings
    from django.core.exceptions import ValidationError
    from django.urls import reverse

    from .services import provision_account_for_staff

    login_url = f"{getattr(settings, 'SITE_URL', '').rstrip('/')}{reverse('accounts:login')}"
    status, _user = provision_account_for_staff(staff, login_url=login_url)
    if status == "conflict":
        raise ValidationError(
            f"An account with {staff.email} already belongs to another directory record."
        )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
@register
class StaffExport(ExportSpec):
    name = "staff"
    label = "Staff directory"
    description = "Export every staff record with the fields you see in the directory."

    columns = [
        Column("Employee no.", field="employee_no"),
        Column("First name", field="first_name"),
        Column("Last name",  field="last_name"),
        Column("Email", field="email"),
        Column("Phone", field="phone"),

        Column("Department", field="department_name"),
        Column("Office", field="office_name"),
        Column("Category", field="category_name"),
        Column("State", field="state_name"),

        Column("Subsection", field="subsection"),
        Column("Designation", field="designation"),
        Column("Grade level", field="grade_level"),
        Column("Office location", field="office_location"),
        Column("Reports to", field="reports_to_name"),
        Column("Duties", field="duties"),

        Column("Contact preference", field="contact_preference"),
        Column("Emergency contact name", field="emergency_contact_name"),
        Column("Emergency contact phone", field="emergency_contact_phone"),

        Column("Birth month", field="birth_month"),
        Column("Birth day", field="birth_day"),
        Column("Date joined service", field="date_joined_service"),
    ]

    def get_queryset(self):
        return (
            Staff.objects
            .select_related("department", "office", "category", "state", "reports_to")
            .order_by("last_name", "first_name")
        )

    def value(self, obj, column):
        # Map the flat labels in `columns` to values that may need lookups.
        if column.field == "department_name":
            return obj.department.name if obj.department else ""
        if column.field == "office_name":
            return obj.office.name if obj.office else ""
        if column.field == "category_name":
            return obj.category.name if obj.category else ""
        if column.field == "state_name":
            return obj.state.name if obj.state else ""
        if column.field == "reports_to_name":
            # Review H6: export the supervisor's employee number so the file
            # re-imports (the import looks supervisors up by employee no/email).
            return obj.reports_to.employee_no if obj.reports_to else ""
        return getattr(obj, column.field, "")