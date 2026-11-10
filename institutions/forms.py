"""Forms for the institution management actions (Master Plan §6) and the
institution portal. Every queryset of an institution-bearing model is
scoped with ``.for_user(user)`` (Master Plan §4)."""
from __future__ import annotations

import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from core.forms_dynamic import DynamicFormMixin
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
        else:  # keep the list's spelling
            match = next((o for o in services.lgas_for(state) if o.lower() == lga.lower()), None)
            if match and match != lga:
                lga = form.cleaned_data[lga_field] = match
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


class AddTvetForm(DynamicFormMixin, ReasonMixin, InstitutionProfileFields):
    """v2.1 R.7 (X46, design 33): code generated automatically; status at
    registration (+ approval letter when Approved); type and category in one
    field; RC/CAC number for private institutions only; physical + postal
    address; State -> LGA dropdown; chief executive and head of ICT."""

    STATUS_CHOICES = [("proposed", "Proposed: not yet operating"),
                      ("existing", "Existing: already operating, approved earlier"),
                      ("approved", "Approved: NBTE approval letter issued")]
    status_at_add = forms.ChoiceField(choices=STATUS_CHOICES, initial="proposed", label="Status at registration",
                                      widget=forms.RadioSelect)
    approval_letter = forms.FileField(required=True, label="Approval letter (PDF)",
                                      help_text="Required when the institution is already approved. Up to 10 MB.")
    postal_address = forms.CharField(max_length=255, required=False, label="Postal address")
    ce_email = forms.EmailField(label="Chief executive e-mail")
    ce_phone = forms.CharField(max_length=30, required=False, label="Chief executive phone")
    ict_name = forms.CharField(max_length=150, required=False, label="Head of ICT (name)")
    ict_email = forms.EmailField(required=False, label="Head of ICT e-mail")
    ict_phone = forms.CharField(max_length=30, required=False, label="Head of ICT phone")
    admin_first_name = forms.CharField(max_length=150, label="Administrator first name")
    admin_last_name = forms.CharField(max_length=150, label="Administrator last name")
    admin_email = forms.EmailField(label="Administrator e-mail",
                                   help_text="The portal account. Credentials are sent separately (Send Login "
                                             "Credentials) once the institution is confirmed.")
    admin_phone = forms.CharField(max_length=30, required=False, label="Administrator phone")
    admin_job_title = forms.CharField(max_length=120, required=False, label="Administrator job title")
    admin_department = forms.CharField(max_length=120, required=False, label="Administrator's department",
                                       help_text="Checked against qa.institution.admin_required_department.")

    field_order = ["name", "status_at_add", "approval_letter", "ownership", "category", "cac_rc_number",
                   "year_established", "website", "state", "lga", "address_line1", "address_town", "postal_address",
                   "official_email", "phone", "head_name", "ce_email", "ce_phone", "ict_name", "ict_email",
                   "ict_phone", "admin_first_name", "admin_last_name", "admin_email", "admin_phone",
                   "admin_job_title", "admin_department", "reason"]
    fieldsets = [
        ("Institution", ["name", "status_at_add", "approval_letter", "ownership", "category", "cac_rc_number",
                         "year_established", "website"]),
        ("Location", ["state", "lga", "address_line1", "address_town", "postal_address"]),
        ("Contacts", ["official_email", "phone"]),
        ("Chief executive and head of ICT", ["head_name", "ce_email", "ce_phone", "ict_name", "ict_email",
                                             "ict_phone"]),
        ("Portal administrator", ["admin_first_name", "admin_last_name", "admin_email", "admin_phone",
                                  "admin_job_title", "admin_department"]),
        ("Why", ["reason"]),
    ]
    lga_select = True

    def __init__(self, *args, **kwargs):
        private = list(InstitutionOwnership.objects.filter(is_active=True, is_public=False)
                       .values_list("pk", flat=True))
        self.show_if = {"approval_letter": {"status_at_add": ["approved"]},
                        "cac_rc_number": {"ownership": [str(pk) for pk in private]}}
        super().__init__(*args, **kwargs)
        del self.fields["institution_type"]
        cat = self.fields["category"]
        cat.label = "Type"
        cat.queryset = InstitutionCategory.objects.filter(is_active=True).select_related("institution_type")
        cat.label_from_instance = lambda c: (f"{c.institution_type.name} · {c.name}"
                                             if c.institution_type_id and c.institution_type.name != c.name
                                             else c.name)
        self.fields["cac_rc_number"].label = "RC / CAC number"
        self.fields["cac_rc_number"].help_text = "Private institutions only."
        self.fields["address_line1"].label = "Physical address"
        self.fields["head_name"].label = "Chief executive (name)"
        # State -> LGA: a real dropdown, refilled through HTMX when the state changes
        state_id = (self.data.get(self.add_prefix("state")) if self.is_bound else None) or self.initial.get("state")
        lgas = services.lgas_for(StateOfOrigin.objects.filter(pk=state_id).first()) if state_id else []
        self.fields["lga"].widget = forms.Select(
            choices=[("", "Choose the LGA" if lgas else "Choose the state first")] + [(x, x) for x in lgas],
            attrs={"class": "input"})
        self.fields["approval_letter"].widget.attrs["accept"] = ".pdf"

    def clean_approval_letter(self):
        f = self.cleaned_data.get("approval_letter")
        if f:
            from qa_core.files import validate_upload

            validate_upload(f, extensions=("pdf",), max_mb=10, label="approval letter")
        return f

    def clean(self):
        data = super().clean()
        cat = data.get("category")
        if cat is not None:
            if not cat.institution_type_id:
                self.add_error("category", "This category has no institution type. Choose another.")
            else:
                data["institution_type"] = cat.institution_type
        own = data.get("ownership")
        if own is not None and own.is_public:
            data["cac_rc_number"] = ""
        elif own is not None and not (data.get("cac_rc_number") or "").strip():
            self.add_error("cac_rc_number", "Enter the RC / CAC number of the private institution.")
        return data

    def payload(self):
        d = self.cleaned_data
        out = self.profile_payload()
        out.update({"code": "", "status_at_add": d.get("status_at_add", "proposed"),
                    "postal_address": d.get("postal_address", ""), "ce_email": (d.get("ce_email") or "").lower(),
                    "ce_phone": d.get("ce_phone", ""), "ict_name": d.get("ict_name", ""),
                    "ict_email": (d.get("ict_email") or "").lower(), "ict_phone": d.get("ict_phone", ""),
                    "admin_first_name": d["admin_first_name"],
                    "admin_last_name": d["admin_last_name"], "admin_email": d["admin_email"].lower(),
                    "admin_phone": d.get("admin_phone", ""), "admin_job_title": d.get("admin_job_title", ""),
                    "admin_department": d.get("admin_department", "")})
        letter = d.get("approval_letter")
        if letter and out["status_at_add"] == "approved":
            out.update(stage_upload(letter))
        return out


def stage_upload(upload):
    """Keep an upload in restricted storage until the pending action is
    confirmed (the payload is JSON, so it carries the stored name)."""
    import uuid

    from qa_core.files import extension_of, restricted_storage, sha256_of

    name = restricted_storage().save(f"qa/staged/{uuid.uuid4().hex}.{extension_of(upload.name) or 'pdf'}", upload)
    return {"approval_letter_path": name, "approval_letter_name": (upload.name or "")[:255],
            "approval_letter_sha256": sha256_of(upload)}


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


class UpdateProgrammeForm(InstitutionActionForm):
    """v2.1 R.7 (X48): inline Edit on the institution page (second officer confirms)."""
    programme = forms.ModelChoiceField(Programme.objects.none())
    name = forms.CharField(max_length=200, label="Programme name")
    level = forms.ModelChoiceField(ProgrammeLevel.objects.filter(is_active=True))
    category = forms.ModelChoiceField(ProgrammeCategory.objects.filter(is_active=True), required=False)
    professional_body = forms.ModelChoiceField(None, required=False, label="Professional body")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import ProfessionalBody

        self.fields["programme"].queryset = (Programme.objects.for_user(self.user)
                                             .filter(institution=self.institution).exclude(status="dropped")
                                             .select_related("level").order_by("name"))
        self.fields["professional_body"].queryset = ProfessionalBody.objects.filter(is_active=True)
        pk = self.initial.get("programme")
        if pk and not self.is_bound:
            p = self.fields["programme"].queryset.filter(pk=pk).first()
            if p is not None:
                self.initial.update({"name": p.name, "level": p.level_id, "category": p.category_id,
                                     "professional_body": p.professional_body_id})

    def payload(self):
        d = self.cleaned_data
        return {"programme_id": d["programme"].pk, "name": " ".join(d["name"].split()), "level_id": d["level"].pk,
                "category_id": d["category"].pk if d.get("category") else None,
                "professional_body_id": d["professional_body"].pk if d.get("professional_body") else None}


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
    kind = forms.ChoiceField(choices=[("institutions", "Institutions"), ("programmes", "Programmes"),
                                      ("legacy", "Migrate from the previous system (institutions, programmes, "
                                                 "history)")])
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


class ManagementRoleForm(forms.ModelForm):
    """v2.1 X13: NBTE's list of management roles (Settings → QA → Lookups)."""

    class Meta:
        from .models import ManagementRole

        model = ManagementRole
        fields = ["code", "name", "is_mandatory", "max_holders", "requires_email", "requires_phone",
                  "is_chief_executive", "is_ict_head", "is_active", "sort_order"]

    def clean(self):
        from .models import ManagementRole

        data = super().clean()
        for flag, label in (("is_chief_executive", "Chief Executive"), ("is_ict_head", "Head of ICT")):
            if data.get(flag) and ManagementRole.objects.filter(**{flag: True}).exclude(pk=self.instance.pk).exists():
                self.add_error(flag, f"Another role is already marked as the {label}.")
        return data


LOOKUP_FORMS = {
    "ownership": (InstitutionOwnership, OwnershipForm, "Ownership"),
    "type": (InstitutionType, TypeForm, "Institution types"),
    "category": (InstitutionCategory, CategoryForm, "Categories → department"),
    "discipline": (Discipline, DisciplineForm, "Disciplines"),
    "level": (ProgrammeLevel, LevelForm, "Programme levels"),
    "programme-category": (ProgrammeCategory, ProgrammeCategoryForm, "Programme categories"),
    "document-kind": (DocumentKind, DocumentKindForm, "Document kinds"),
    "management-role": (ManagementRoleForm.Meta.model, ManagementRoleForm, "Management roles"),  # v2.1 X13
}
