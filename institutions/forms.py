"""Forms for the institution management actions (Master Plan §6) and the
institution portal. Every queryset of an institution-bearing model is
scoped with ``.for_user(user)`` (Master Plan §4)."""
from __future__ import annotations

import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from staff.models import StateOfOrigin

from . import services
from .models import (
    Discipline,
    DocumentKind,
    InstitutionAddress,
    InstitutionCategory,
    InstitutionContact,
    InstitutionOwnership,
    InstitutionRequest,
    InstitutionRequestType,
    InstitutionType,
    Programme,
    ProgrammeCategory,
    ProgrammeLevel,
)

REASON_HELP = "Why this change? The second officer reads it before confirming."


def _input(field, placeholder="", **attrs):
    field.widget.attrs.setdefault("class", "input")
    if placeholder:
        field.widget.attrs.setdefault("placeholder", placeholder)
    for k, v in attrs.items():
        field.widget.attrs[k] = v
    return field


class QAForm(forms.Form):
    """Adds the NBTE input class to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            css = widget.attrs.get("class", "")
            if "input" not in css:
                widget.attrs["class"] = (css + " input").strip()


class ReasonMixin(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "maxlength": 2000}), min_length=5,
                             help_text=REASON_HELP)


def state_choices():
    return StateOfOrigin.objects.filter(is_active=True).order_by("name")


def _lga_clean(form, state_field="state", lga_field="lga"):
    state = form.cleaned_data.get(state_field)
    lga = (form.cleaned_data.get(lga_field) or "").strip()
    if state and lga:
        try:
            services.validate_lga(state, lga)
        except ValidationError as exc:
            form.add_error(lga_field, exc)
    return lga


# ---------------------------------------------------------------------------
# New Institution
# ---------------------------------------------------------------------------
class InstitutionProfileFields(QAForm):
    name = forms.CharField(max_length=255, label="Institution name")
    ownership = forms.ModelChoiceField(InstitutionOwnership.objects.filter(is_active=True))
    institution_type = forms.ModelChoiceField(InstitutionType.objects.filter(is_active=True), label="Type")
    category = forms.ModelChoiceField(InstitutionCategory.objects.filter(is_active=True))
    state = forms.ModelChoiceField(StateOfOrigin.objects.none())
    lga = forms.CharField(max_length=100, label="LGA", widget=forms.TextInput(attrs={"list": "qa-lga-options",
                                                                                       "autocomplete": "off"}))
    year_established = forms.IntegerField(required=False, min_value=1900, max_value=datetime.date.today().year + 1)
    cac_rc_number = forms.CharField(max_length=40, required=False, label="CAC RC number")
    website = forms.URLField(required=False)
    official_email = forms.EmailField(label="Official e-mail")
    phone = forms.CharField(max_length=30, label="Phone")
    head_name = forms.CharField(max_length=150, label="Head of institution (name)")
    address_line1 = forms.CharField(max_length=255, label="Address")
    address_town = forms.CharField(max_length=100, required=False, label="Town")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state"].queryset = state_choices()

    def clean_lga(self):
        return (self.cleaned_data.get("lga") or "").strip()

    def clean(self):
        data = super().clean()
        _lga_clean(self)
        category, itype = data.get("category"), data.get("institution_type")
        if category and itype and category.institution_type_id and category.institution_type_id != itype.pk:
            self.add_error("category", f"{category.name} is a category of {category.institution_type.name}.")
        return data

    def profile_payload(self):
        d = self.cleaned_data
        return {
            "name": d["name"].strip(), "ownership_id": d["ownership"].pk, "institution_type_id": d["institution_type"].pk,
            "category_id": d["category"].pk, "state_id": d["state"].pk, "lga": d["lga"],
            "year_established": d.get("year_established"), "cac_rc_number": d.get("cac_rc_number", ""),
            "website": d.get("website", ""), "official_email": d["official_email"].lower(), "phone": d["phone"],
            "head_name": d["head_name"], "address_line1": d["address_line1"], "address_town": d.get("address_town", ""),
        }


class AddTvetForm(ReasonMixin, InstitutionProfileFields):
    code = forms.CharField(max_length=6, required=False, label="Code (optional)",
                           help_text="3–6 letters/digits. Leave blank to allocate one at confirmation.")
    admin_first_name = forms.CharField(max_length=150, label="Administrator first name")
    admin_last_name = forms.CharField(max_length=150, label="Administrator last name")
    admin_email = forms.EmailField(label="Administrator e-mail",
                                   help_text="The portal account. Credentials are sent separately (Send Login "
                                             "Credentials) once the institution is confirmed.")
    admin_phone = forms.CharField(max_length=30, required=False, label="Administrator phone")
    admin_job_title = forms.CharField(max_length=120, required=False, label="Administrator job title")
    admin_department = forms.CharField(max_length=120, required=False, label="Administrator's department",
                                       help_text="Checked against qa.institution.admin_required_department.")

    field_order = ["name", "code", "ownership", "institution_type", "category", "state", "lga", "address_line1",
                   "address_town", "year_established", "cac_rc_number", "website", "official_email", "phone",
                   "head_name", "admin_first_name", "admin_last_name", "admin_email", "admin_phone",
                   "admin_job_title", "admin_department", "reason"]
    fieldsets = [
        ("Institution", ["name", "code", "ownership", "institution_type", "category", "year_established",
                         "cac_rc_number", "website"]),
        ("Location", ["state", "lga", "address_line1", "address_town"]),
        ("Contacts", ["official_email", "phone", "head_name"]),
        ("Portal administrator", ["admin_first_name", "admin_last_name", "admin_email", "admin_phone",
                                  "admin_job_title", "admin_department"]),
        ("Why", ["reason"]),
    ]

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def payload(self):
        d = self.cleaned_data
        out = self.profile_payload()
        out.update({"code": d.get("code", ""), "admin_first_name": d["admin_first_name"],
                    "admin_last_name": d["admin_last_name"], "admin_email": d["admin_email"].lower(),
                    "admin_phone": d.get("admin_phone", ""), "admin_job_title": d.get("admin_job_title", ""),
                    "admin_department": d.get("admin_department", "")})
        return out


class ReasonOnlyForm(ReasonMixin, QAForm):
    def __init__(self, *args, institution=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)

    def payload(self):
        return {}


# ---------------------------------------------------------------------------
# TVET Support
# ---------------------------------------------------------------------------
class InstitutionActionForm(ReasonMixin, QAForm):
    def __init__(self, *args, institution=None, user=None, **kwargs):
        self.institution = institution
        self.user = user
        super().__init__(*args, **kwargs)

    def payload(self):
        return {}


class DropProgrammesForm(InstitutionActionForm):
    programmes = forms.ModelMultipleChoiceField(Programme.objects.none(), widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["programmes"].queryset = (Programme.objects.for_user(self.user)
                                              .filter(institution=self.institution).exclude(status="dropped")
                                              .select_related("level").order_by("name"))

    def payload(self):
        return {"programme_ids": [p.pk for p in self.cleaned_data["programmes"]]}


class UpdateStateLgaForm(InstitutionActionForm):
    state = forms.ModelChoiceField(StateOfOrigin.objects.none())
    lga = forms.CharField(max_length=100, label="LGA", widget=forms.TextInput(attrs={"list": "qa-lga-options"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state"].queryset = state_choices()
        if self.institution is not None and not self.is_bound:
            self.initial.setdefault("state", self.institution.state_id)
            self.initial.setdefault("lga", self.institution.lga)

    def clean(self):
        data = super().clean()
        _lga_clean(self)
        return data

    def payload(self):
        return {"state_id": self.cleaned_data["state"].pk, "lga": self.cleaned_data["lga"].strip()}


class WipeRequestsForm(InstitutionActionForm):
    request_types = forms.ModelMultipleChoiceField(InstitutionRequestType.objects.filter(is_enabled=True),
                                                   widget=forms.CheckboxSelectMultiple, label="Request types")

    def payload(self):
        return {"type_codes": [t.code for t in self.cleaned_data["request_types"]]}


class CancelRequestForm(InstitutionActionForm):
    request = forms.ModelChoiceField(InstitutionRequest.objects.none(), label="Request to cancel")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["request"].queryset = (InstitutionRequest.objects.for_user(self.user)
                                           .filter(institution=self.institution, request_type__is_enabled=True)
                                           .exclude(status__in=("cancelled", "closed")).select_related("request_type"))

    def payload(self):
        return {"request_id": self.cleaned_data["request"].pk}


class AddProgrammeToQAForm(InstitutionActionForm):
    """Phase 3: add one programme to an open QA request (draft, submitted or fee due)."""

    qa_request = forms.ModelChoiceField(None, label="QA request")
    programme = forms.ModelChoiceField(Programme.objects.none(), label="Programme to add")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accreditation.models import QARequest

        self.fields["qa_request"].queryset = (QARequest.objects.for_user(self.user)
                                              .filter(institution=self.institution,
                                                      status__in=("draft", "submitted", "fee_due"))
                                              .select_related("qa_type"))
        self.fields["programme"].queryset = (Programme.objects.for_user(self.user)
                                             .filter(institution=self.institution).exclude(status="dropped")
                                             .select_related("level").order_by("name"))

    def payload(self):
        return {"qa_request_id": self.cleaned_data["qa_request"].pk,
                "programme_id": self.cleaned_data["programme"].pk}


class SendBackToAuditForm(InstitutionActionForm):
    """Phase 4: return a QA case at stage 7 or later to stage 5 (Audit)."""

    case = forms.ChoiceField(label="QA case")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accreditation import case_services

        choices = []
        if self.institution is not None and self.user is not None:
            for case in case_services.visible_cases(self.user).filter(institution=self.institution).order_by("-pk"):
                if case_services.can_send_back_to_audit(case):
                    labels = ", ".join(s.label for s in case_services.stage_objects(case))
                    choices.append((str(case.pk), f"{case.reference} — {labels}"))
        self.fields["case"].choices = choices

    def payload(self):
        return {"case_id": int(self.cleaned_data["case"])}


class RevertHistoryForm(InstitutionActionForm):
    stage = forms.ChoiceField(label="Move the application back to")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accreditation.models import Application

        self.application = (Application.objects.for_user(self.user).filter(institution=self.institution)
                            .exclude(status="withdrawn").order_by("-created_at").first())
        choices = []
        if self.application is not None:
            from qa_core import flow

            instance = flow.instance_for(self.application, "onboarding")
            if instance is not None:
                seen, current = [], set(instance.current_stages)
                stages = {s.code: s for s in instance.flow_version.stages.all()}
                for event in flow.history(instance):
                    code = event.to_stage
                    if code in stages and code not in current and code not in seen \
                            and stages[code].kind in ("start", "action"):
                        seen.append(code)
                choices = [(c, stages[c].label) for c in seen]
        self.fields["stage"].choices = choices

    def clean(self):
        data = super().clean()
        if self.application is None:
            raise ValidationError("This institution has no application to revert.")
        return data

    def payload(self):
        return {"application_id": self.application.pk, "stage": self.cleaned_data["stage"]}


class UpdateContactForm(InstitutionActionForm):
    kind = forms.ChoiceField(choices=InstitutionContact.KIND_CHOICES, label="Contact")
    name = forms.CharField(max_length=150, required=False, help_text="For people (head, registrar, bursar …).")
    value = forms.CharField(max_length=255, label="E-mail or phone")

    def clean(self):
        data = super().clean()
        kind, value = data.get("kind"), (data.get("value") or "").strip()
        if kind == "official_email" and value:
            try:
                validate_email(value)
            except ValidationError:
                self.add_error("value", "Enter a valid e-mail address.")
        if kind in ("head_of_institution", "registrar", "bursar", "librarian") and not (data.get("name") or "").strip():
            self.add_error("name", "Enter the person's name.")
        return data

    def payload(self):
        d = self.cleaned_data
        return {"kind": d["kind"], "value": d["value"].strip(), "name": (d.get("name") or "").strip()}


class UpdateAddressForm(InstitutionActionForm):
    kind = forms.ChoiceField(choices=InstitutionAddress.KIND_CHOICES, label="Address type")
    line1 = forms.CharField(max_length=255, label="Address")
    town = forms.CharField(max_length=100, required=False)
    state = forms.ModelChoiceField(StateOfOrigin.objects.none())
    lga = forms.CharField(max_length=100, label="LGA", widget=forms.TextInput(attrs={"list": "qa-lga-options"}))
    gps = forms.CharField(max_length=60, required=False, label="GPS (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["state"].queryset = state_choices()
        if self.institution is not None and not self.is_bound:
            self.initial.setdefault("state", self.institution.state_id)

    def clean(self):
        data = super().clean()
        _lga_clean(self)
        return data

    def payload(self):
        d = self.cleaned_data
        return {"kind": d["kind"], "line1": d["line1"].strip(), "town": d.get("town", "").strip(),
                "state_id": d["state"].pk, "lga": d["lga"].strip(), "gps": d.get("gps", "").strip()}


class UpdateCategoryForm(InstitutionActionForm):
    category = forms.ModelChoiceField(InstitutionCategory.objects.filter(is_active=True)
                                      .select_related("routing_department"))

    def payload(self):
        return {"category_id": self.cleaned_data["category"].pk}


class UpdateNameForm(InstitutionActionForm):
    name = forms.CharField(max_length=255, label="New name")

    def payload(self):
        return {"name": self.cleaned_data["name"].strip()}


class UpdateOwnershipForm(InstitutionActionForm):
    ownership = forms.ModelChoiceField(InstitutionOwnership.objects.filter(is_active=True))

    def payload(self):
        return {"ownership_id": self.cleaned_data["ownership"].pk}


class ImpersonateForm(InstitutionActionForm):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5,
                             help_text="Recorded with the session (for example the complaint or phone call).")


class SendCredentialsForm(InstitutionActionForm):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Optional note for the audit trail.")


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------
class ImportUploadForm(QAForm):
    kind = forms.ChoiceField(choices=[("institutions", "Institutions"), ("programmes", "Programmes")])
    file = forms.FileField(label="Excel file (.xlsx)")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        from qa_core.files import validate_upload

        validate_upload(upload, extensions=["xlsx"], max_mb=10, label="spreadsheet")
        return upload


# ---------------------------------------------------------------------------
# Lookups (Settings → QA → Lookups)
# ---------------------------------------------------------------------------
class OwnershipForm(forms.ModelForm):
    class Meta:
        model = InstitutionOwnership
        fields = ["code", "name", "is_public", "is_active", "sort_order"]


class TypeForm(forms.ModelForm):
    class Meta:
        model = InstitutionType
        fields = ["code", "name", "default_department", "is_active", "sort_order"]


class CategoryForm(forms.ModelForm):
    class Meta:
        model = InstitutionCategory
        fields = ["code", "name", "institution_type", "routing_department", "is_active", "sort_order"]


class DisciplineForm(forms.ModelForm):
    class Meta:
        model = Discipline
        fields = ["code", "name", "is_active", "sort_order"]


class LevelForm(forms.ModelForm):
    class Meta:
        model = ProgrammeLevel
        fields = ["code", "name", "is_active", "sort_order"]


class ProgrammeCategoryForm(forms.ModelForm):
    class Meta:
        model = ProgrammeCategory
        fields = ["name", "discipline", "students_per_stream", "is_technology_based", "is_active"]


class DocumentKindForm(forms.ModelForm):
    accepted_extensions = forms.CharField(help_text="Comma-separated, e.g. pdf, jpg, png")

    class Meta:
        model = DocumentKind
        fields = ["code", "name", "description", "accepted_extensions", "max_mb", "is_active", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["accepted_extensions"] = ", ".join(self.instance.accepted_extensions or [])

    def clean_accepted_extensions(self):
        raw = self.cleaned_data["accepted_extensions"]
        exts = [e.strip().lower().lstrip(".") for e in raw.replace(";", ",").split(",") if e.strip()]
        allowed = {"pdf", "jpg", "jpeg", "png", "docx", "xlsx"}
        bad = [e for e in exts if e not in allowed]
        if bad:
            raise ValidationError(f"Not allowed: {', '.join(bad)}. Use {', '.join(sorted(allowed))}.")
        return exts or ["pdf"]


class DocumentRequirementForm(forms.ModelForm):
    class Meta:
        from .models import DocumentRequirement

        model = DocumentRequirement
        fields = ["process", "ownership", "institution_type", "document_kind", "is_mandatory",
                  "reviewing_department", "refresh_after_months", "notes", "is_active"]


LOOKUP_FORMS = {
    "ownership": (InstitutionOwnership, OwnershipForm, "Ownership"),
    "type": (InstitutionType, TypeForm, "Institution types"),
    "category": (InstitutionCategory, CategoryForm, "Categories → department"),
    "discipline": (Discipline, DisciplineForm, "Disciplines"),
    "level": (ProgrammeLevel, LevelForm, "Programme levels"),
    "programme-category": (ProgrammeCategory, ProgrammeCategoryForm, "Programme categories"),
    "document-kind": (DocumentKind, DocumentKindForm, "Document kinds"),
}
