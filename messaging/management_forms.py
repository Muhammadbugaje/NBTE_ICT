"""Forms for the memo route management UI."""
from __future__ import annotations

from django import forms

from accounts.models import Role, User
from accounts.selectors import staff_users

from .models import MemoRoute, MemoRouteRule, MemoRouteStep


# ===========================================================================
# Target-kind choices — module-level constant.
# ===========================================================================
TARGET_KIND_CHOICES = [
    ("specific_user", "One specific person"),
    ("all_staff",     "All staff"),
    ("all_directors", "All Directors"),
    ("department",    "A department"),
    ("role",          "Specific roles"),
    ("individuals",   "A list of people"),
]


# ===========================================================================
# Route
# ===========================================================================
class MemoRouteForm(forms.ModelForm):
    class Meta:
        model = MemoRoute
        fields = ["name", "code", "description", "same_rank_behaviour", "is_active"]
        widgets = {
            "name":        forms.TextInput(attrs={"class": "mm-input", "autofocus": True}),
            "code":        forms.TextInput(attrs={"class": "mm-input",
                                                   "placeholder": "e.g. staff-to-director"}),
            "description": forms.Textarea(attrs={"class": "mm-input mm-input-textarea", "rows": 3}),
            "same_rank_behaviour": forms.Select(attrs={"class": "mm-input"}),
            "is_active":   forms.CheckboxInput(),
        }


# ===========================================================================
# Step
# ===========================================================================
class MemoRouteStepForm(forms.ModelForm):
    class Meta:
        model = MemoRouteStep
        fields = [
            "position", "name", "approver_kind",
            "approver_role", "approver_user", "scope",
            "is_optional", "is_hierarchical", "instructions",
        ]
        widgets = {
            "position":      forms.NumberInput(attrs={"class": "mm-input", "min": 1}),
            "name":          forms.TextInput(attrs={"class": "mm-input", "autofocus": True,
                                                     "placeholder": "e.g. Director approval"}),
            "approver_kind": forms.Select(attrs={"class": "mm-input", "data-mm-step-kind": ""}),
            "approver_role": forms.Select(attrs={"class": "mm-input"}),
            "approver_user": forms.Select(attrs={"class": "mm-input"}),
            "scope":         forms.TextInput(attrs={"class": "mm-input", "placeholder": "requester"}),
            "is_optional":   forms.CheckboxInput(),
            "is_hierarchical": forms.CheckboxInput(),
            "instructions":  forms.Textarea(attrs={"class": "mm-input mm-input-textarea", "rows": 3,
                                                    "placeholder": "Optional guidance for the acting user"}),
        }

    def __init__(self, *args, route=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.route = route
        self.fields["approver_role"].queryset = Role.objects.order_by("-rank", "name")
        self.fields["approver_user"].queryset = (
            staff_users()  # Phase 0 (M9)
            .select_related("department", "role")
            .order_by("first_name", "last_name", "email")
        )
        self.fields["approver_role"].required = False
        self.fields["approver_user"].required = False
        self.fields["approver_role"].empty_label = "— Select a role —"
        self.fields["approver_user"].empty_label = "— Select a user —"

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("approver_kind")
        if kind == MemoRouteStep.APPROVER_ROLE_IN_DEPT and cleaned.get("approver_role") is None:
            self.add_error("approver_role", "This approver kind needs a role.")
        if kind == MemoRouteStep.APPROVER_SPECIFIC and cleaned.get("approver_user") is None:
            self.add_error("approver_user", "This approver kind needs a specific user.")
        return cleaned


# ===========================================================================
# Rule
# ===========================================================================
class MemoRouteRuleForm(forms.ModelForm):
    """Route selection rule.

    Both `sender_role_codes` and `target_role_codes` are stored on the
    model as JSON lists of role codes, but presented as checkbox grids
    of `Role` objects. The `clean_*` methods translate back to codes.
    """
    sender_role_codes = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Sender roles",
    )
    target_role_codes = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Recipient roles",
    )
    target_kind = forms.ChoiceField(
        choices=[("", "— Select a target kind —")] + TARGET_KIND_CHOICES,
        required=True,
        widget=forms.Select(attrs={"class": "mm-input", "data-mm-target-kind": ""}),
        label="Target kind",
    )

    class Meta:
        model = MemoRouteRule
        fields = [
            "name",
            "route",
            "target_kind",
            "sender_role_codes",
            "target_role_codes",
            "department_relation",
            "priority",
            "is_active",
        ]
        widgets = {
            "department_relation": forms.Select(attrs={"class": "mm-input"}),
            "name": forms.TextInput(attrs={"class": "mm-input", "autofocus": True,
                                           "placeholder": "e.g. Staff memo to a Director"}),
            "route": forms.Select(attrs={"class": "mm-input"}),
            "priority": forms.NumberInput(attrs={"class": "mm-input", "min": 0}),
            "is_active": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ---- CRITICAL FIX -------------------------------------------
        # Django's ModelForm metaclass regenerates a field for
        # `target_kind` from the model, and the model field's
        # `choices=[]` makes that regenerated field have almost no
        # valid options. We override the choices here, unconditionally,
        # after super().__init__() has finished. This is what stops the
        # "not one of the available choices" error.
        tk = self.fields.get("target_kind")
        if tk is not None:
            tk.choices = [("", "— Select a target kind —")] + TARGET_KIND_CHOICES
            tk.required = True

        # ---- Route queryset -----------------------------------------
        self.fields["route"].queryset = (
            MemoRoute.objects.filter(is_active=True).order_by("name")
        )
        self.fields["route"].empty_label = "— Select a route —"

        # ---- Role querysets for the checkbox grids ------------------
        role_qs = list(Role.objects.order_by("-rank", "name"))
        self.fields["sender_role_codes"].queryset = Role.objects.filter(
            pk__in=[r.pk for r in role_qs],
        ).order_by("-rank", "name")
        self.fields["target_role_codes"].queryset = Role.objects.filter(
            pk__in=[r.pk for r in role_qs],
        ).order_by("-rank", "name")

        # ---- Pre-fill for edit mode ---------------------------------
        # Because our checkbox fields hold Role objects while the model
        # stores codes, we can't rely on Django's automatic initial
        # population. Set them explicitly by pk.
        if self.instance and self.instance.pk:
            sender_codes = set(self.instance.sender_role_codes or [])
            if sender_codes:
                self.initial["sender_role_codes"] = list(
                    Role.objects.filter(code__in=sender_codes).values_list("pk", flat=True)
                )

            target_codes = set(self.instance.target_role_codes or [])
            if target_codes:
                self.initial["target_role_codes"] = list(
                    Role.objects.filter(code__in=target_codes).values_list("pk", flat=True)
                )

            if self.instance.target_kind:
                self.initial["target_kind"] = self.instance.target_kind

    def clean_sender_role_codes(self):
        roles = self.cleaned_data.get("sender_role_codes") or []
        return [r.code for r in roles]

    def clean_target_role_codes(self):
        roles = self.cleaned_data.get("target_role_codes") or []
        return [r.code for r in roles]

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("target_kind")
        if kind and kind != "specific_user":
            cleaned["target_role_codes"] = []
        return cleaned


# ===========================================================================
# Broadcast grant
# ===========================================================================
class GrantBroadcastForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        widget=forms.Select(attrs={"class": "mm-input", "autofocus": True}),
        label="Who",
        help_text="Pick the Director or other user to grant broadcast privileges to.",
    )
    reason = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={"class": "mm-input",
                                       "placeholder": "Optional — why are you granting this?"}),
    )

    def __init__(self, *args, exclude_users=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = staff_users()  # Phase 0 (M9)
        if exclude_users:
            qs = qs.exclude(pk__in=list(exclude_users))
        self.fields["user"].queryset = qs.select_related(
            "role", "department",
        ).order_by("first_name", "last_name", "email")
        self.fields["user"].empty_label = "— Select a user —"