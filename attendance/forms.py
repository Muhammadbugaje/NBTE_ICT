"""Forms for attendance — check-in, upload, manual edit."""
from datetime import date as date_cls

from django import forms
from django.core.exceptions import ValidationError

from .models import AttendanceRecord


class CheckInForm(forms.Form):
    latitude = forms.DecimalField(
        max_digits=9, decimal_places=6,
        widget=forms.HiddenInput(),
    )
    longitude = forms.DecimalField(
        max_digits=9, decimal_places=6,
        widget=forms.HiddenInput(),
    )


class UploadMatrixForm(forms.Form):
    MONTHS = [(i, date_cls(2000, i, 1).strftime("%B")) for i in range(1, 13)]

    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={
            "class": "input",
            "accept": ".xlsx,.xls",
        }),
    )
    month = forms.ChoiceField(choices=MONTHS, widget=forms.Select(attrs={"class": "input"}))
    year = forms.IntegerField(
        min_value=2000, max_value=2100,
        widget=forms.NumberInput(attrs={"class": "input"}),
    )
    overwrite = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={"class": "check-input"}),
        help_text="If unchecked, existing records are left as-is.",
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".xlsx", ".xls")):
            raise ValidationError("Please upload an Excel file (.xlsx or .xls).")
        if f.size > 10 * 1024 * 1024:
            raise ValidationError("File must be smaller than 10 MB.")
        return f


class ManualEditForm(forms.Form):
    status = forms.ChoiceField(
        choices=AttendanceRecord.STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2,
            "placeholder": "Why was this changed? (kept in the audit log)",
        }),
    )


class ReportFilterForm(forms.Form):
    """Used by the director's report page."""
    start = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    end = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
    )
    user = forms.ModelChoiceField(
        queryset=None, required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    status = forms.ChoiceField(
        choices=[("", "Any status")] + list(AttendanceRecord.STATUS_CHOICES),
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        qs = User.objects.filter(is_active=True)
        if department is not None:
            qs = qs.filter(department=department)
        self.fields["user"].queryset = qs.order_by("first_name", "last_name", "email")