"""Forms for the workflow management UI. Kept separate from `forms.py`
(which handles the runtime raise/approve flows).
"""
from __future__ import annotations

from django import forms

from accounts.models import Role, User
from staff.models import Department

from .models import (
    ApprovalChain,
    ApprovalStep,
    RequestCategory,
    RequestType,
)

INPUT = "input"


class ApprovalChainForm(forms.ModelForm):
    class Meta:
        model = ApprovalChain
        fields = ["name", "code", "description", "is_active"]
        widgets = {
            "name":        forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "code":        forms.TextInput(attrs={"class": INPUT,
                                                   "placeholder": "e.g. ict-standard"}),
            "description": forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "is_active":   forms.CheckboxInput(),
        }


class ApprovalStepForm(forms.ModelForm):
    class Meta:
        model = ApprovalStep
        fields = [
            "position", "name", "kind",
            "approver_kind", "approver_role", "approver_user",
            "scope", "is_optional", "is_hierarchical", "instructions",
        ]
        widgets = {
            "position":      forms.NumberInput(attrs={"class": INPUT, "min": 1}),
            "name":          forms.TextInput(attrs={"class": INPUT, "autofocus": True,
                                                     "placeholder": "e.g. Head of Office approval"}),
            "kind":          forms.Select(attrs={"class": INPUT}),
            "approver_kind": forms.Select(attrs={"class": INPUT}),
            "approver_role": forms.Select(attrs={"class": INPUT}),
            "approver_user": forms.Select(attrs={"class": INPUT}),
            "scope":         forms.TextInput(attrs={"class": INPUT,
                                                     "placeholder": "requester / receiving / finance"}),
            "instructions":  forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "is_optional":   forms.CheckboxInput(),
            "is_hierarchical": forms.CheckboxInput(),
        }
        help_texts = {
            "approver_role":    "Required when approver kind is “role_in_department”.",
            "approver_user":    "Required when approver kind is “specific_user”.",
            "scope":            "Who the role/user resolves against: “requester”, "
                                 "“receiving”, or a Department code.",
            "is_optional":      "Skip this step automatically if no one matches the role.",
            "is_hierarchical":  "Leave on for org-chain steps (rank-aware). Turn OFF "
                                 "for mandatory gates like Finance / Audit / HR / ES / Records.",
            "instructions":     "Shown to the acting user as guidance on the request page.",
        }

    def __init__(self, *args, chain=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.chain = chain
        self.fields["approver_role"].queryset = Role.objects.order_by("-rank", "name")
        self.fields["approver_user"].queryset = (
            User.objects.filter(is_active=True)
            .order_by("first_name", "last_name")
        )

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("approver_kind")
        role = cleaned.get("approver_role")
        user = cleaned.get("approver_user")

        if kind == ApprovalStep.APPROVER_ROLE and role is None:
            self.add_error("approver_role",
                           "This approver kind needs a role to be selected.")
        if kind == ApprovalStep.APPROVER_SPECIFIC and user is None:
            self.add_error("approver_user",
                           "This approver kind needs a specific user.")

        # Optional/hierarchical sanity: synthetic gates must not be optional
        if cleaned.get("is_optional") and not cleaned.get("is_hierarchical"):
            self.add_error(
                "is_optional",
                "Mandatory gates (is_hierarchical=False) cannot be marked optional.",
            )
        return cleaned


class RequestTypeForm(forms.ModelForm):
    class Meta:
        model = RequestType
        fields = [
            "name", "code", "category", "handling_department", "description",
            "approval_chain", "requester_chain", "is_cross_department",
            "requires_es_approval", "requires_finance_approval",
            "requires_audit_approval", "requires_hr_approval",
            "requires_records_filing",
            "is_active", "sort_order", "icon",
        ]
        widgets = {
            "name":               forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "code":               forms.TextInput(attrs={"class": INPUT}),
            "category":           forms.Select(attrs={"class": INPUT}),
            "handling_department": forms.Select(attrs={"class": INPUT}),
            "description":        forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "approval_chain":     forms.Select(attrs={"class": INPUT}),
            "requester_chain":    forms.Select(attrs={"class": INPUT}),
            "is_cross_department": forms.CheckboxInput(),
            "requires_es_approval":      forms.CheckboxInput(),
            "requires_finance_approval": forms.CheckboxInput(),
            "requires_audit_approval":   forms.CheckboxInput(),
            "requires_hr_approval":      forms.CheckboxInput(),
            "requires_records_filing":   forms.CheckboxInput(),
            "is_active":          forms.CheckboxInput(),
            "sort_order":         forms.NumberInput(attrs={"class": INPUT, "min": 0}),
            "icon":               forms.TextInput(attrs={"class": INPUT,
                                                          "placeholder": "e.g. wrench"}),
        }
        help_texts = {
            "approval_chain":  "The main chain that runs (or the receiving chain "
                               "for cross-department types).",
            "requester_chain": "Cross-department only: the chain that runs in the "
                               "requester's own department first.",
            "is_cross_department": "When on, requests route from the requester's "
                                    "department to the handling department.",
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_cross_department") and not cleaned.get("requester_chain"):
            self.add_error(
                "requester_chain",
                "Cross-department types need a requester-side chain "
                "(or the default will be used — pick one explicitly).",
            )
        return cleaned


class RequestCategoryForm(forms.ModelForm):
    class Meta:
        model = RequestCategory
        fields = ["name", "code", "description", "icon", "sort_order", "is_active"]
        widgets = {
            "name":        forms.TextInput(attrs={"class": INPUT, "autofocus": True}),
            "code":        forms.TextInput(attrs={"class": INPUT,
                                                   "placeholder": "e.g. ict"}),
            "description": forms.Textarea(attrs={"class": INPUT, "rows": 3}),
            "icon":        forms.TextInput(attrs={"class": INPUT}),
            "sort_order":  forms.NumberInput(attrs={"class": INPUT, "min": 0}),
            "is_active":   forms.CheckboxInput(),
        }