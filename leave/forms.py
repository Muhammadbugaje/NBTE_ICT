from django import forms


class LeaveRoleEntitlementForm(forms.Form):
    def __init__(self, *args, roles, values=None, **kwargs):
        super().__init__(*args, **kwargs)
        values = values or {}
        for role in roles:
            self.fields[f"role_{role.pk}"] = forms.DecimalField(
                label=role.name,
                required=False,
                min_value=0,
                max_digits=5,
                decimal_places=2,
                initial=values.get(role.code, ""),
                widget=forms.NumberInput(attrs={
                    "class": "input",
                    "min": "0",
                    "step": "0.5",
                    "inputmode": "decimal",
                }),
            )


class LeaveEntitlementForm(forms.Form):
    staff_id = forms.IntegerField(widget=forms.HiddenInput)
    year = forms.IntegerField(min_value=1900, max_value=9999, widget=forms.HiddenInput)
    days = forms.DecimalField(
        label="Annual entitlement",
        required=False,
        min_value=0,
        max_digits=5,
        decimal_places=2,
        widget=forms.NumberInput(attrs={
            "class": "input",
            "min": "0",
            "step": "0.5",
            "inputmode": "decimal",
        }),
    )


# ===========================================================================
# v2.1 Phase D: leave types, application and holidays
# ===========================================================================
from core.uploads import validate_attachment_upload  # noqa: E402

from .models import LeaveType  # noqa: E402


def _input(**attrs):
    return {"class": "input", **attrs}


class LeaveTypeForm(forms.ModelForm):
    """Settings -> Leave types (F2)."""

    class Meta:
        model = LeaveType
        fields = ["name", "code", "description", "days", "input_mode", "counting", "paid", "gender_restriction",
                  "needs_document", "notice_days", "max_days_per_application", "max_per_year", "carry_over_days",
                  "sort_order", "is_active"]
        labels = {
            "days": "Days awarded per year",
            "input_mode": "How staff apply",
            "counting": "Days counted as",
            "paid": "Paid leave",
            "gender_restriction": "Who may apply",
            "needs_document": "Supporting document required",
            "notice_days": "Notice (days before the first day)",
            "max_days_per_application": "Longest single application (days)",
            "max_per_year": "Applications per year (most)",
            "carry_over_days": "Days that may be carried over",
            "sort_order": "Order in lists",
            "is_active": "Open for applications",
        }
        widgets = {
            "name": forms.TextInput(attrs=_input(placeholder="e.g. Maternity leave")),
            "code": forms.TextInput(attrs=_input(placeholder="e.g. maternity")),
            "description": forms.Textarea(attrs=_input(rows=2)),
            "days": forms.NumberInput(attrs=_input(min="0", inputmode="numeric", placeholder="No yearly limit")),
            "input_mode": forms.Select(attrs=_input()),
            "counting": forms.Select(attrs=_input()),
            "gender_restriction": forms.Select(attrs=_input()),
            "notice_days": forms.NumberInput(attrs=_input(min="0", inputmode="numeric")),
            "max_days_per_application": forms.NumberInput(attrs=_input(min="1", inputmode="numeric",
                                                                        placeholder="No limit")),
            "max_per_year": forms.NumberInput(attrs=_input(min="1", inputmode="numeric", placeholder="No limit")),
            "carry_over_days": forms.NumberInput(attrs=_input(min="0", inputmode="numeric")),
            "sort_order": forms.NumberInput(attrs=_input(min="0", inputmode="numeric")),
        }
        help_texts = {"code": "Short unique name used in imports and the API (letters, numbers, hyphens)."}

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().lower()
        if self.instance.pk and self.instance.code == LeaveType.ANNUAL and code != LeaveType.ANNUAL:
            raise forms.ValidationError("The annual leave code cannot change: entitlements and roles use it.")
        return code

    def clean(self):
        data = super().clean()
        if self.instance.pk and self.instance.code == LeaveType.ANNUAL and data.get("is_active") is False:
            self.add_error("is_active", "Annual leave cannot be closed.")
        mx, days = data.get("max_days_per_application"), data.get("days")
        if mx and days and mx > days:
            self.add_error("max_days_per_application", "A single application cannot be longer than the yearly days.")
        return data


class LeaveApplyForm(forms.Form):
    """The dynamic application form (F4): the type decides the fields."""

    leave_type = forms.ModelChoiceField(queryset=LeaveType.objects.none(), to_field_name="code",
                                        empty_label=None, widget=forms.Select(attrs=_input()))
    start_date = forms.DateField(label="First day of leave", required=False,
                                 widget=forms.DateInput(attrs=_input(type="date")))
    end_date = forms.DateField(label="Last day of leave", required=False,
                               widget=forms.DateInput(attrs=_input(type="date")))
    days = forms.IntegerField(label="Number of days", required=False, min_value=1, max_value=366,
                              widget=forms.NumberInput(attrs=_input(min="1", max="366", inputmode="numeric")))
    reason = forms.CharField(label="Reason or note for your approvers", required=False, max_length=2000,
                             widget=forms.Textarea(attrs=_input(rows=3)))
    document = forms.FileField(label="Supporting document", required=False,
                               validators=[validate_attachment_upload])

    def __init__(self, *args, staff=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = LeaveType.objects.filter(is_active=True)
        if staff is not None and staff.gender:
            qs = qs.exclude(gender_restriction__in=[g for g, _ in LeaveType.GENDER_CHOICES
                                                    if g and g != staff.gender])
        self.fields["leave_type"].queryset = qs

    def selected_type(self):
        code = (self.data.get("leave_type") if self.is_bound else None) or self.initial.get("leave_type")
        qs = self.fields["leave_type"].queryset
        return (qs.filter(code=getattr(code, "code", code)).first() if code else None) or qs.first()

    def clean(self):
        data = super().clean()
        lt = data.get("leave_type")
        if lt is None:
            return data
        if not data.get("start_date"):
            self.add_error("start_date", "Enter the first day of leave.")
        if lt.is_days_mode:
            if not data.get("days"):
                self.add_error("days", "Enter how many days you want.")
        elif not data.get("end_date"):
            self.add_error("end_date", "Enter the last day of leave.")
        if lt.needs_document and not data.get("document"):
            self.add_error("document", f"{lt.name} needs a supporting document.")
        return data

    def form_data(self):
        """What the workflow request stores (and leave.services reads back)."""
        d = self.cleaned_data
        lt = d["leave_type"]
        out = {"leave_type": lt.code, "start_date": d["start_date"].isoformat()}
        if lt.is_days_mode:
            out["days"] = d["days"]
        else:
            out["end_date"] = d["end_date"].isoformat()
        return out


class HolidayForm(forms.ModelForm):
    """Settings -> Holidays (F7): awarded to all staff unless narrowed."""

    class Meta:
        from portal.models import Holiday

        model = Holiday
        fields = ["name", "date_start", "date_end", "kind", "to_confirm", "description", "is_active"]
        labels = {"date_start": "First day", "date_end": "Last day", "to_confirm": "Date to confirm",
                  "is_active": "Active (awarded to staff)"}
        widgets = {
            "name": forms.TextInput(attrs=_input(placeholder="e.g. Democracy Day")),
            "date_start": forms.DateInput(attrs=_input(type="date"), format="%Y-%m-%d"),
            "date_end": forms.DateInput(attrs=_input(type="date"), format="%Y-%m-%d"),
            "kind": forms.Select(attrs=_input()),
            "description": forms.Textarea(attrs=_input(rows=2)),
        }
        help_texts = {"to_confirm": "Tick for movable dates (Eid, Maulud) until the Federal Government declares them."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date_end"].required = False

    def clean(self):
        data = super().clean()
        start, end = data.get("date_start"), data.get("date_end")
        if start and not end:
            data["date_end"] = start
        elif start and end and end < start:
            self.add_error("date_end", "The last day cannot be before the first day.")
        return data
