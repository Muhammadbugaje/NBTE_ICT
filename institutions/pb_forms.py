"""Forms for professional bodies and their programme mappings (Phase 6)."""
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .forms import QAForm
from .models import ProfessionalBody, ProgrammeBodyMapping, ProgrammeLevel, programme_key


class _Styled:
    def _style(self):
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            css = field.widget.attrs.get("class", "")
            if "input" not in css:
                field.widget.attrs["class"] = (css + " input").strip()


class ProfessionalBodyForm(_Styled, forms.ModelForm):
    class Meta:
        model = ProfessionalBody
        fields = ["name", "acronym", "contact_person", "email", "phone", "address", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}
        labels = {"email": "E-mail", "is_active": "Active"}
        help_texts = {"is_active": "Inactive bodies are kept but no longer flag joint QA."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields["name"].widget.attrs.setdefault("placeholder", "Pharmacy Council of Nigeria")
        self.fields["acronym"].widget.attrs.setdefault("placeholder", "PCN")
        self.fields["email"].widget.attrs.setdefault("autocomplete", "email")
        self.fields["phone"].widget.attrs.setdefault("autocomplete", "tel")

    def clean_name(self):
        name = " ".join((self.cleaned_data.get("name") or "").split())
        clash = ProfessionalBody.objects.filter(name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("A professional body with this name is already on file.")
        return name

    def clean_acronym(self):
        acronym = (self.cleaned_data.get("acronym") or "").strip().upper()
        if acronym and ProfessionalBody.objects.filter(acronym__iexact=acronym).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Another professional body already uses this acronym.")
        return acronym


class MappingForm(_Styled, forms.ModelForm):
    class Meta:
        model = ProgrammeBodyMapping
        fields = ["body", "programme_name", "level", "note", "is_active"]
        labels = {"body": "Professional body", "programme_name": "Programme", "is_active": "Active"}
        help_texts = {"programme_name": "As named in the curriculum. Matches every institution's programme of "
                                        "this name.",
                      "level": "Leave blank to cover every level (ND and HND)."}

    def __init__(self, *args, body=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields["body"].queryset = ProfessionalBody.objects.order_by("name")
        self.fields["level"].queryset = ProgrammeLevel.objects.filter(is_active=True).exclude(code="admin")
        self.fields["level"].empty_label = "Every level"
        self.fields["programme_name"].widget.attrs.setdefault("placeholder", "Pharmacy Technician")
        self.fields["programme_name"].widget.attrs["list"] = "pb-programme-names"
        if body is not None:
            self.fields["body"].initial = body
            self.fields["body"].widget = forms.HiddenInput()

    def clean(self):
        data = super().clean()
        body, name, level = data.get("body"), data.get("programme_name"), data.get("level")
        if body and name:
            clash = ProgrammeBodyMapping.objects.filter(body=body, programme_key=programme_key(name), level=level)
            if clash.exclude(pk=self.instance.pk).exists():
                self.add_error("programme_name", f"{name} is already mapped to {body.acronym or body.name}"
                                                 f"{' at this level' if level else ''}.")
        return data


class PBImportForm(QAForm):
    file = forms.FileField(label="Excel file (.xlsx)",
                           help_text="Use the template: a “Professional bodies” sheet and a “Programme mappings” "
                                     "sheet.")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        from qa_core.files import validate_upload

        validate_upload(upload, extensions=["xlsx"], max_mb=10, label="spreadsheet")
        return upload
