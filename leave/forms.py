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
