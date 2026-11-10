"""Staff forms — create/edit, search, state change."""
from __future__ import annotations

from django import forms
from django.db import models

from .models import (
    Department, Office, Staff, StaffCategory, StaffState,
)


INPUT_CLASS = "input"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
class StaffSearchForm(forms.Form):
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": INPUT_CLASS, "placeholder": "Search name, email, employee no…",
        }),
    )
    department = forms.ModelChoiceField(
        required=False, queryset=Department.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    state = forms.ModelChoiceField(
        required=False, queryset=StaffState.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .permissions import sees_all_departments
        if user and not sees_all_departments(user):
            dept_id = getattr(user, "department_id", None)
            self.fields["department"].queryset = Department.objects.filter(pk=dept_id)


# ---------------------------------------------------------------------------
# Create / edit
# ---------------------------------------------------------------------------
class StaffForm(forms.ModelForm):
    """Create/edit a Staff record.

    Includes the optional 'create a user account' switch that fires the
    account-provisioning + welcome-email flow. If `initial_password` is left
    blank, a random one is generated and emailed.
    """

    create_account = forms.BooleanField(
        required=False, initial=False,
        label="Create a user account",
        help_text="Generate a temporary password and email the login details.",
    )

    initial_password = forms.CharField(
        required=False,
        label="Initial password",
        help_text="Leave blank to generate a random one. Emailed with the login details.",
        widget=forms.TextInput(attrs={
            "class": INPUT_CLASS,
            "autocomplete": "new-password",
            "placeholder": "Optional — leave blank for random",
        }),
    )

    role = forms.ModelChoiceField(
        queryset=None, required=False, label="Role",
        empty_label="No role yet",
        help_text="What they can see and do once the account exists. Only roles you may grant are listed.",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    class Meta:
        model = Staff
        fields = [
            "employee_no", "first_name", "last_name", "email", "phone",
            "photo",
            "department", "office", "subsection", "designation",
            "grade_level", "office_location", "category", "reports_to",
            "duties",
            "state", "state_start", "state_end",
            "contact_preference",
            "emergency_contact_name", "emergency_contact_phone",
            "birth_month", "birth_day", "date_joined_service",
            # Nominal roll fields
            "gender", "state_of_origin", "lga", "date_of_birth",
            "date_first_appointment", "date_joined_board",
            "date_present_appointment", "date_confirmation", "qualifications",
        ]
        widgets = {
            "employee_no":     forms.TextInput(attrs={"class": INPUT_CLASS}),
            "first_name":      forms.TextInput(attrs={"class": INPUT_CLASS}),
            "last_name":       forms.TextInput(attrs={"class": INPUT_CLASS}),
            "email":           forms.EmailInput(attrs={"class": INPUT_CLASS}),
            "phone":           forms.TextInput(attrs={"class": INPUT_CLASS}),
            "subsection":      forms.TextInput(attrs={"class": INPUT_CLASS}),
            "designation":     forms.Select(attrs={"class": INPUT_CLASS}),
            "gender":          forms.Select(attrs={"class": INPUT_CLASS}),
            "state_of_origin": forms.Select(attrs={"class": INPUT_CLASS}),
            "lga":             forms.TextInput(attrs={"class": INPUT_CLASS, "list": "nbr-lga-options",
                                                      "autocomplete": "off"}),
            "date_of_birth":   forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "date_first_appointment":   forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "date_joined_board":        forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "date_present_appointment": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "date_confirmation":        forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}, format="%Y-%m-%d"),
            "qualifications":  forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
            "grade_level":     forms.TextInput(attrs={"class": INPUT_CLASS}),
            "office_location": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "duties":          forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 3}),
            "state_start":     forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "state_end":       forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "date_joined_service": forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
            "emergency_contact_name":  forms.TextInput(attrs={"class": INPUT_CLASS}),
            "emergency_contact_phone": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "birth_month":     forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 12}),
            "birth_day":       forms.NumberInput(attrs={"class": INPUT_CLASS, "min": 1, "max": 31}),
            "department":      forms.Select(attrs={"class": INPUT_CLASS}),
            "office":          forms.Select(attrs={"class": INPUT_CLASS}),
            "category":        forms.Select(attrs={"class": INPUT_CLASS}),
            "reports_to":      forms.Select(attrs={"class": INPUT_CLASS}),
            "state":           forms.Select(attrs={"class": INPUT_CLASS}),
            "contact_preference": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["photo"].required = False
        self.fields["email"].required = True
        self.fields["email"].help_text = "For linked accounts, this is also the sign-in email."

        # B.8 (C4): role on create, limited to what the actor may grant.
        from . import role_ui
        if self.instance.pk or not role_ui.can_manage_roles(user):
            self.fields.pop("role", None)
        else:
            self.fields["role"].queryset = role_ui.grantable_roles(user)

        # Department heads can't move staff out of their own department.
        # Review H7: someone without a department gets no choices at all
        # (previously they got the full, unrestricted list).
        if user and not (user.is_superuser or user.has_perm("staff.change_staff")):
            dept = getattr(user, "department", None)
            if dept:
                self.fields["department"].queryset = Department.objects.filter(pk=dept.pk)
                self.initial.setdefault("department", dept.pk)
            else:
                self.fields["department"].queryset = Department.objects.none()

        # Nominal roll lookups: active designations (most senior first) and states.
        from .models import Designation, StateOfOrigin
        if "designation" in self.fields:
            qs = Designation.objects.filter(is_active=True)
            if self.instance.pk and self.instance.designation_id:
                qs = Designation.objects.filter(models.Q(is_active=True) | models.Q(pk=self.instance.designation_id))
            self.fields["designation"].queryset = qs.order_by("-rank", "name")
        if "state_of_origin" in self.fields:
            self.fields["state_of_origin"].queryset = StateOfOrigin.objects.filter(is_active=True)

        # Sensible default: only active states are selectable for a new record.
        if not self.instance.pk:
            self.fields["state"].queryset = StaffState.objects.filter(is_active=True)

        # Hide "create account" once the record is already linked to a user.
        if self.instance.pk and self.instance.user_id:
            self.fields.pop("create_account", None)
            self.fields.pop("initial_password", None)

    def clean(self):
        cleaned = super().clean()

        email = (cleaned.get("email") or "").strip().lower()
        if self.instance.user_id and email:
            from django.contrib.auth import get_user_model

            if get_user_model().objects.filter(email__iexact=email).exclude(
                    pk=self.instance.user_id).exists():
                self.add_error("email", "Another account already uses this email address.")
            else:
                cleaned["email"] = email

        # -- Birth month/day range -------------------------------------------
        m = cleaned.get("birth_month")
        d = cleaned.get("birth_day")
        if m is not None and (m < 1 or m > 12):
            self.add_error("birth_month", "Month must be 1–12.")
        if d is not None and (d < 1 or d > 31):
            self.add_error("birth_day", "Day must be 1–31.")

        # -- Account provisioning checks -------------------------------------
        if cleaned.get("create_account"):
            if not cleaned.get("email"):
                self.add_error("email", "Email is required to create an account.")
            pw = cleaned.get("initial_password") or ""
            # Only enforce the length rule if a password was actually typed;
            # blank means "generate a random one", which is handled downstream.
            if pw and len(pw) < 8:
                self.add_error("initial_password", "Must be at least 8 characters.")

        return cleaned


class StaffSelfForm(forms.ModelForm):
    """What a person may edit on their OWN directory record (review H7):
    contact details only. HR-controlled fields (employee no, department,
    office, designation, grade, reports-to, state, birthday, duties) stay
    with HR / department managers via `StaffForm`."""

    class Meta:
        model = Staff
        fields = [
            "phone", "photo", "office_location", "contact_preference",
            "emergency_contact_name", "emergency_contact_phone",
        ]
        widgets = {
            "phone":           forms.TextInput(attrs={"class": INPUT_CLASS}),
            "office_location": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "contact_preference": forms.Select(attrs={"class": INPUT_CLASS}),
            "emergency_contact_name":  forms.TextInput(attrs={"class": INPUT_CLASS}),
            "emergency_contact_phone": forms.TextInput(attrs={"class": INPUT_CLASS}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["photo"].required = False


# ---------------------------------------------------------------------------
# State change
# ---------------------------------------------------------------------------
class StaffStateChangeForm(forms.Form):
    """Writes through to Staff.change_state() — the only correct path
    (permission-checked, history + audit)."""

    state = forms.ModelChoiceField(
        queryset=StaffState.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": INPUT_CLASS, "type": "date"}),
    )
    reason = forms.CharField(
        required=False, max_length=255,
        widget=forms.Textarea(attrs={"class": INPUT_CLASS, "rows": 2}),
    )

    def clean(self):
        cleaned = super().clean()
        s = cleaned.get("start_date")
        e = cleaned.get("end_date")
        if s and e and e < s:
            self.add_error("end_date", "End date can't be before the start date.")
        return cleaned

from django import forms
from django.utils import timezone

from .models import ExecutivePosition, ExecutiveTenure


INPUT = "input"


class ExecutivePositionForm(forms.ModelForm):
    class Meta:
        model = ExecutivePosition
        fields = ["name", "scope", "description", "department",
                  "order", "is_active"]
        widgets = {
            "name":        forms.TextInput(attrs={"class": INPUT,
                                                   "placeholder": "e.g. Director of ICT"}),
            "scope":       forms.Select(attrs={"class": INPUT}),
            "description": forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "department":  forms.Select(attrs={"class": INPUT}),
            "order":       forms.NumberInput(attrs={"class": INPUT, "min": 1}),
            "is_active":   forms.CheckboxInput(),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        qs = ExecutivePosition.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A position with this name already exists.")
        return name


class ExecutiveTenureForm(forms.ModelForm):
    class Meta:
        model = ExecutiveTenure
        fields = [
            "position", "staff", "display_name", "title", "is_placeholder",
            "started_on", "ended_on", "is_acting", "appointment_reference",
            "photo", "qualifications", "bio",
            "is_public", "is_featured", "display_order",
        ]
        widgets = {
            "position":              forms.Select(attrs={"class": INPUT}),
            "staff":                 forms.Select(attrs={"class": INPUT}),
            "display_name":          forms.TextInput(attrs={"class": INPUT,
                                                             "placeholder": "Name, if not a staff record"}),
            "title":                 forms.TextInput(attrs={"class": INPUT, "placeholder": "Dr., Engr., Prof."}),
            "is_placeholder":        forms.CheckboxInput(),
            "started_on":            forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "ended_on":              forms.DateInput(attrs={"class": INPUT, "type": "date"}),
            "appointment_reference": forms.TextInput(attrs={"class": INPUT,
                                                             "placeholder": "Board memo / ref no."}),
            "qualifications":        forms.Textarea(attrs={"class": INPUT, "rows": 4}),
            "bio":                   forms.Textarea(attrs={"class": INPUT, "rows": 5}),
            "display_order":         forms.NumberInput(attrs={"class": INPUT, "min": 1}),
            "is_acting":             forms.CheckboxInput(),
            "is_public":             forms.CheckboxInput(),
            "is_featured":           forms.CheckboxInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import Staff  # local to avoid circular import

        # Only show active staff.
        self.fields["staff"].queryset = (
            Staff.objects.filter(is_deleted=False)
            .order_by("last_name", "first_name")
        )
        self.fields["position"].queryset = (
            ExecutivePosition.objects.filter(is_active=True)
            .order_by("order", "name")
        )

        if not self.instance.pk:
            self.fields["started_on"].initial = timezone.localdate()
            self.fields["display_order"].initial = 100

        # If a position is already taken, hint it in the form.
        # (Not enforced — a position can legitimately have two concurrent
        # acting + substantive holders, or temporary overlaps.)

    def clean(self):
        cleaned = super().clean()
        started = cleaned.get("started_on")
        ended = cleaned.get("ended_on")

        # v2.1 A4: a past leader may have no staff record, but then needs a name.
        if not cleaned.get("staff") and not (cleaned.get("display_name") or "").strip():
            self.add_error("display_name", "Choose a staff member or type the leader's name.")

        if started and ended and ended < started:
            self.add_error("ended_on", "End date cannot be before the start date.")

        # If this tenure is being marked as "featured" and it's not public,
        # reject — featured is a public-page concept.
        if cleaned.get("is_featured") and not cleaned.get("is_public"):
            self.add_error("is_featured",
                           "Only public tenures can be featured on the public page.")

        return cleaned


