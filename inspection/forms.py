"""Phase 5 forms. Errors are attached to their fields, so the shared
``_field.html`` partial shows them inline next to the field (§20)."""
from __future__ import annotations

import datetime

from django import forms
from django.core.exceptions import ValidationError

from institutions.forms import QAForm

from .models import PostVisitReport, ResourcePerson, ResourcePersonFile

REPORT_SECTIONS = [  # key, label, help
    ("facilities", "Facilities and equipment", "Classrooms, laboratories, workshops and equipment against the SSQ."),
    ("staffing", "Staffing", "Academic and technical staff seen; vacancies and acting positions."),
    ("curriculum", "Curriculum and delivery", "Curriculum in use, practicals, logbooks, assessment records."),
    ("governance", "Governance and records", "Admission records, examination and academic board records."),
]


def _discipline_choices():
    from institutions.models import Discipline

    return Discipline.objects.filter(is_active=True).order_by("sort_order", "name")


class NominationForm(QAForm):
    full_name = forms.CharField(max_length=150, label="Full name (with title)")
    email = forms.EmailField(label="E-mail")
    phone = forms.CharField(max_length=40, required=False)
    source = forms.ChoiceField(choices=ResourcePerson.SOURCE_CHOICES)
    rank = forms.CharField(max_length=60, required=False, label="Rank code",
                           help_text="External persons: a code from qa.resource_person.required_designations, "
                                     "e.g. chief-lecturer.")
    staff = forms.ModelChoiceField(queryset=None, required=False, label="NBTE staff member",
                                   help_text="For source “NBTE staff”: their role and designation decide eligibility.")
    home_institution = forms.ModelChoiceField(queryset=None, required=False, label="Home institution",
                                              help_text="Conflict of interest: never sent to their own institution.")
    professional_body = forms.CharField(max_length=150, required=False)
    disciplines = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple,
                                                 label="Disciplines")
    primary_discipline = forms.ModelChoiceField(queryset=None, required=False,
                                                help_text="Blank = the first ticked.")
    qualifications = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from institutions.models import Institution
        from staff.models import Staff

        self.fields["disciplines"].queryset = _discipline_choices()
        self.fields["primary_discipline"].queryset = _discipline_choices()
        self.fields["staff"].queryset = Staff.objects.filter(user__isnull=False, user__is_active=True).order_by(
            "first_name", "last_name")
        self.fields["home_institution"].queryset = (Institution.objects.for_user(user).order_by("name")
                                                    if user is not None else Institution.objects.none())

    def clean(self):
        data = super().clean()
        if data.get("source") == "nbte_staff" and not data.get("staff"):
            self.add_error("staff", "Choose the member of staff.")
        if data.get("source") != "nbte_staff" and not (data.get("rank") or "").strip():
            self.add_error("rank", "Enter the person's rank code.")
        return data

    def service_data(self):
        d = self.cleaned_data
        return {"full_name": d["full_name"], "email": d["email"], "phone": d.get("phone", ""), "source": d["source"],
                "rank": d.get("rank", ""), "staff": d["staff"].pk if d.get("staff") else None,
                "home_institution": d["home_institution"].pk if d.get("home_institution") else None,
                "professional_body": d.get("professional_body", ""),
                "disciplines": [x.pk for x in d.get("disciplines") or []],
                "primary_discipline": d["primary_discipline"].pk if d.get("primary_discipline") else None,
                "qualifications": d.get("qualifications", "")}


class FileForm(QAForm):
    kind = forms.ChoiceField(choices=ResourcePersonFile.KIND_CHOICES, label="Document")
    file = forms.FileField(help_text="PDF, Word or image.")


class VetForm(QAForm):
    decision = forms.ChoiceField(choices=[("approve", "Approve — make active"), ("reject", "Do not approve")],
                                 widget=forms.RadioSelect)
    remark = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False,
                             help_text="Required when you do not approve.")


class StatusForm(QAForm):
    status = forms.ChoiceField(choices=[("active", "Active"), ("suspended", "Suspended"), ("retired", "Retired")])
    remark = forms.CharField(max_length=255, required=False)


class QAPersonnelForm(QAForm):
    staff = forms.ModelChoiceField(queryset=None, label="Member of staff")
    disciplines = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple)
    is_verification_officer = forms.BooleanField(required=False, initial=True, label="Verification officer")
    is_team_lead_eligible = forms.BooleanField(required=False, label="Can lead a visit")
    active = forms.BooleanField(required=False, initial=True)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5,
                             help_text="Shown to the officer who confirms.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from staff.models import Staff

        self.fields["staff"].queryset = Staff.objects.filter(user__isnull=False, user__is_active=True).select_related(
            "designation").order_by("first_name", "last_name")
        self.fields["disciplines"].queryset = _discipline_choices()


class CompleteVisitForm(QAForm):
    actual_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="First day of the visit")
    actual_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Last day of the visit")
    absent = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple,
                                       label="Did NOT attend", help_text="Tick anyone on the plan who did not go.")

    def __init__(self, *args, team=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["absent"].choices = [(str(m.pk), f"{m.qa_mapping.person if m.qa_mapping_id else m.staff} — "
                                                     f"{m.role_on_team}") for m in team]

    def clean(self):
        data = super().clean()
        start, end = data.get("actual_start"), data.get("actual_end")
        if start and end and end < start:
            self.add_error("actual_end", "The visit cannot end before it starts.")
        if end and end > datetime.date.today():
            self.add_error("actual_end", "The visit has not ended yet; record it when it has.")
        return data


class AttendanceForm(QAForm):
    """Override Post-Visit Personnel: one switch per person + a reason, and an
    optional unplanned attendee."""

    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), min_length=5,
                             help_text="Why the record changes (shown to the confirming officer).")
    extra_staff = forms.ModelChoiceField(queryset=None, required=False, label="Add someone who attended but was "
                                                                              "not on the plan")

    def __init__(self, *args, attendance=(), **kwargs):
        super().__init__(*args, **kwargs)
        from staff.models import Staff

        self.rows = list(attendance)
        for pvp in self.rows:
            self.fields[f"att_{pvp.pk}"] = forms.BooleanField(required=False, initial=pvp.attended,
                                                              label=f"{pvp.person} — attended")
        self.fields["extra_staff"].queryset = Staff.objects.filter(user__isnull=False).order_by("first_name")
        self.order_fields([f"att_{p.pk}" for p in self.rows] + ["extra_staff", "reason"])

    def changes(self):
        d = self.cleaned_data
        out = []
        for pvp in self.rows:
            now = bool(d.get(f"att_{pvp.pk}"))
            if now != pvp.attended:
                out.append({"pvp_id": pvp.pk, "attended": now, "reason": d["reason"]})
        if d.get("extra_staff"):
            out.append({"staff_id": d["extra_staff"].pk, "attended": True, "reason": d["reason"]})
        return out

    def clean(self):
        data = super().clean()
        if not self.errors and not self.changes():
            raise ValidationError("Nothing changed: switch someone's attendance or add a person.")
        return data


class ReportForm(QAForm):
    summary = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False, label="Summary of findings")
    recommendation = forms.ChoiceField(choices=[("", "—")] + PostVisitReport.RECOMMENDATION_CHOICES, required=False,
                                       label="Team recommendation")

    def __init__(self, *args, report=None, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label, help_text in REPORT_SECTIONS:
            self.fields[f"f_{key}"] = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False,
                                                      label=label, help_text=help_text)
            if report is not None and not self.is_bound:
                self.initial[f"f_{key}"] = (report.findings or {}).get(key, "")
        if report is not None and not self.is_bound:
            self.initial["summary"] = report.summary
            self.initial["recommendation"] = report.recommendation
        self.order_fields([f"f_{k}" for k, _l, _h in REPORT_SECTIONS] + ["summary", "recommendation"])

    def findings(self):
        return {k: self.cleaned_data.get(f"f_{k}", "") for k, _l, _h in REPORT_SECTIONS}


class EndorseForm(QAForm):
    action = forms.ChoiceField(choices=[("endorse", "Endorse"), ("add_content", "Add content"),
                                        ("return", "Return")], widget=forms.HiddenInput)
    content = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False,
                              label="Comment / content to add",
                              help_text="Required to add content or to return (pinned on the returned task).")

    def clean(self):
        data = super().clean()
        if data.get("action") in ("add_content", "return") and len((data.get("content") or "").strip()) < 3:
            self.add_error("content", "Write the content to add." if data["action"] == "add_content"
                           else "A return needs a remark.")
        return data


class RecommendationForm(QAForm):
    recommendation = forms.ChoiceField(choices=PostVisitReport.RECOMMENDATION_CHOICES)
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False, label="Directorate note")
