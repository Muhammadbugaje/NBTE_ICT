"""Forms for the tickets app."""
from django import forms

from .models import Ticket, TicketCategory


class TicketCreateForm(forms.Form):
    """Data-only form. The service layer handles creation."""

    subject = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Short summary — e.g. 'Printer jam on 2nd floor'",
            "autofocus": True,
        }),
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 6,
            "placeholder": "What's wrong? What have you tried? Any error messages?",
        }),
    )
    priority = forms.ChoiceField(
        choices=Ticket.PRIORITY_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
    current_location = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Where is this — office, floor, room?",
        }),
    )

    # --- Asset selection: dropdown of own assets + manual fallback ---
    asset_choice = forms.ChoiceField(
        required=False,
        label="Related asset",
        widget=forms.Select(attrs={"class": "input", "data-asset-select": True}),
    )
    asset_tag = forms.CharField(
        required=False,
        max_length=60,
        label="Or enter a tag manually",
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "e.g. ICT-COMPUT-00001",
            "data-asset-manual": True,
        }),
    )

    attachment = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )

    def __init__(self, *args, category=None, user=None, initial_asset_tag=None,
                 initial_subject=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.category = category

        # Priority floor
        if category is not None:
            order = ["low", "normal", "high", "urgent"]
            try:
                floor = order.index(category.minimum_priority)
                self.fields["priority"].choices = [
                    (p, l) for p, l in Ticket.PRIORITY_CHOICES
                    if order.index(p) >= floor
                ]
                self.fields["priority"].initial = category.minimum_priority
            except ValueError:
                pass

        # Asset dropdown from the user's current assignments
        choices = [("", "— None —")]
        if user and user.is_authenticated:
            try:
                from assets.models import AssetAssignment
                assignments = (
                    AssetAssignment.objects
                    .filter(assigned_to=user, returned_at__isnull=True)
                    .select_related("asset")
                    .order_by("asset__asset_tag")
                )
                for a in assignments:
                    if a.asset:
                        label = f"{a.asset.asset_tag} — {a.asset.display_name}"
                        choices.append((a.asset.asset_tag, label))
            except Exception:
                pass
        choices.append(("__other__", "Other (enter below)"))
        self.fields["asset_choice"].choices = choices

        # Pre-fills from query string (asset detail → raise ticket flow)
        if initial_asset_tag:
            # If the tag is in the user's dropdown, pre-select it
            tag_codes = [c[0] for c in choices]
            if initial_asset_tag in tag_codes:
                self.fields["asset_choice"].initial = initial_asset_tag
            else:
                self.fields["asset_choice"].initial = "__other__"
                self.fields["asset_tag"].initial = initial_asset_tag
        if initial_subject:
            self.fields["subject"].initial = initial_subject

    def clean_subject(self):
        s = (self.cleaned_data.get("subject") or "").strip()
        if len(s) < 4:
            raise forms.ValidationError("Subject is too short.")
        return s

    def clean_body(self):
        b = (self.cleaned_data.get("body") or "").strip()
        if len(b) < 10:
            raise forms.ValidationError(
                "Please describe the issue in a bit more detail."
            )
        return b

    def clean(self):
        data = super().clean()
        choice = (data.get("asset_choice") or "").strip()
        manual = (data.get("asset_tag") or "").strip()
        if choice and choice != "__other__":
            data["asset_tag"] = choice
        else:
            data["asset_tag"] = manual
        return data


class AssignTicketForm(forms.Form):
    assignee = forms.ModelChoiceField(
        queryset=None,
        widget=forms.Select(attrs={"class": "input"}),
        label="Assign to",
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Optional note for the assignee",
        }),
    )

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        qs = User.objects.filter(is_active=True)
        if department is not None:
            qs = qs.filter(department=department)
        self.fields["assignee"].queryset = qs.order_by("first_name", "last_name", "email")


class StartWorkForm(forms.Form):
    note = forms.CharField(required=False, widget=forms.TextInput(attrs={
        "class": "input",
        "placeholder": "Optional — what are you doing first?",
    }))


class AwaitingForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.TextInput(attrs={
        "class": "input",
        "placeholder": "What are you waiting on?",
    }))


class ResolveForm(forms.Form):
    resolution_notes = forms.CharField(
        label="What was done",
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 3,
            "placeholder": "What was done to fix this?",
        }),
    )
    collection_location = forms.CharField(
        required=False,
        max_length=255,
        label="Collection location",
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "e.g. ICT Office, 2nd floor, room 214",
        }),
    )
    collection_timeframe = forms.CharField(
        required=False,
        max_length=255,
        label="Collection timeframe",
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "e.g. Weekdays 9am–4pm, or 'by Friday'",
        }),
    )

    def clean_resolution_notes(self):
        n = (self.cleaned_data.get("resolution_notes") or "").strip()
        if len(n) < 5:
            raise forms.ValidationError("Please describe the resolution.")
        return n


class DirectorSignoffForm(forms.Form):
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 3,
            "placeholder": "Optional — any director comment",
        }),
    )


class CloseForm(forms.Form):
    RATINGS = [
        (5, "★★★★★  Excellent"),
        (4, "★★★★☆  Good"),
        (3, "★★★☆☆  Okay"),
        (2, "★★☆☆☆  Below expectations"),
        (1, "★☆☆☆☆  Poor"),
    ]
    rating = forms.ChoiceField(
        choices=RATINGS, required=False, initial=5,
        widget=forms.Select(attrs={"class": "input"}),
    )
    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={
        "class": "input",
        "rows": 2,
        "placeholder": "Optional feedback…",
    }))


class ReopenForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={
        "class": "input",
        "rows": 2,
        "placeholder": "What's still wrong?",
    }))

    def clean_reason(self):
        r = (self.cleaned_data.get("reason") or "").strip()
        if len(r) < 5:
            raise forms.ValidationError("Please explain what's still wrong.")
        return r


class CancelForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.TextInput(attrs={
        "class": "input",
        "placeholder": "Optional — why are you cancelling?",
    }))


class CommentForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={
        "class": "input",
        "rows": 3,
        "placeholder": "Write a comment…",
    }))

    def clean_body(self):
        b = (self.cleaned_data.get("body") or "").strip()
        if not b:
            raise forms.ValidationError("Comment cannot be empty.")
        return b


class InternalNoteForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={
        "class": "input",
        "rows": 3,
        "placeholder": "Internal note — only staff can see this.",
    }))


class AttachmentForm(forms.Form):
    file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "input"}))
    description = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Optional description",
        }),
    )