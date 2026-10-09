"""Forms for the workflow app."""
from django import forms

from accounts.models import User

from .models import RequestType


PRIORITY_CHOICES = [
    ("low", "Low"),
    ("normal", "Normal"),
    ("high", "High"),
    ("urgent", "Urgent"),
]


class RaiseRequestForm(forms.Form):
    """Raise a new request.

    All active request types are shown to all users. Cross-department routing
    is handled by the chain, not by hiding options from the requester.
    """

    request_type = forms.ModelChoiceField(
        queryset=RequestType.objects.none(),
        empty_label="— Select a request type —",
        widget=forms.Select(attrs={"class": "input"}),
    )
    subject = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Short summary of your request",
        }),
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 6,
            "placeholder": "Describe what you need. Include any relevant details.",
        }),
    )
    priority = forms.ChoiceField(
        choices=PRIORITY_CHOICES,
        initial="normal",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, fixed_type=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)

        qs = (
            RequestType.objects
            .filter(is_active=True)
            .select_related("category", "handling_department")
            .order_by("category__sort_order", "sort_order", "name")
        )
        self.fields["request_type"].queryset = qs

        if fixed_type is not None:
            self.fields["request_type"].initial = fixed_type
            self.fields["request_type"].widget = forms.HiddenInput()

    def clean_subject(self):
        s = (self.cleaned_data.get("subject") or "").strip()
        if len(s) < 3:
            raise forms.ValidationError("Give your request a short but meaningful subject.")
        return s

    def clean_body(self):
        b = (self.cleaned_data.get("body") or "").strip()
        if len(b) < 10:
            raise forms.ValidationError("Please describe your request in a bit more detail.")
        return b


class ApproveForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Optional comment",
        }),
    )


class ReturnForm(forms.Form):
    comment = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Explain what needs to change",
        }),
    )

    def clean_comment(self):
        c = (self.cleaned_data.get("comment") or "").strip()
        if not c:
            raise forms.ValidationError("A comment is required when returning a request.")
        return c


class RejectForm(forms.Form):
    comment = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Explain why this is being rejected",
        }),
    )

    def clean_comment(self):
        c = (self.cleaned_data.get("comment") or "").strip()
        if not c:
            raise forms.ValidationError("A comment is required when rejecting a request.")
        return c


class AssignForm(forms.Form):
    assignee = forms.ModelChoiceField(
        queryset=User.objects.none(),
        empty_label="— Select a staff member —",
        widget=forms.Select(attrs={"class": "input"}),
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 2,
            "placeholder": "Optional instructions",
        }),
    )

    def __init__(self, *args, handling_department=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(is_active=True)
        if handling_department is not None:
            scoped = qs.filter(department=handling_department)
            if scoped.exists():
                qs = scoped
        self.fields["assignee"].queryset = qs.order_by("first_name", "last_name", "email")


class FulfilledForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Optional notes on the work completed",
        }),
    )


class CloseForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Optional confirmation note",
        }),
    )


class CancelForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Why are you cancelling this request?",
        }),
    )


class DeleteRequestForm(forms.Form):
    """Deleting a request is destructive — require a reason and typed
    confirmation."""

    reason = forms.CharField(
        required=True,
        max_length=500,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Why are you deleting this request?",
        }),
    )
    confirm = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Type DELETE to confirm",
            "autocomplete": "off",
        }),
    )

    def clean_reason(self):
        r = (self.cleaned_data.get("reason") or "").strip()
        if len(r) < 5:
            raise forms.ValidationError("Please give a reason for deletion (at least 5 characters).")
        return r

    def clean_confirm(self):
        c = (self.cleaned_data.get("confirm") or "").strip().upper()
        if c != "DELETE":
            raise forms.ValidationError("Type DELETE exactly to confirm.")
        return c


class CommentForm(forms.Form):
    body = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={
            "class": "input", "rows": 3,
            "placeholder": "Add a comment",
        }),
    )
    is_internal = forms.BooleanField(
        required=False,
        label="Internal note (only staff can see this)",
    )


class AttachmentForm(forms.Form):
    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    description = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Optional description",
        }),
    )
    
class ResubmitForm(forms.Form):
    """Requester edits a returned request and resubmits it."""

    subject = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Short summary of your request",
        }),
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 6,
            "placeholder": "Update your request with what the reviewer asked for.",
        }),
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            "class": "input",
            "rows": 3,
            "placeholder": "Optional note to the reviewer (what did you change?)",
        }),
    )
    file = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    file_description = forms.CharField(
        required=False, max_length=255,
        widget=forms.TextInput(attrs={
            "class": "input",
            "placeholder": "Optional description of the attached file",
        }),
    )

    def __init__(self, *args, request_obj=None, **kwargs):
        super().__init__(*args, **kwargs)
        if request_obj is not None:
            self.fields["subject"].initial = request_obj.subject
            self.fields["body"].initial = request_obj.body    

# ===========================================================================
# Dynamic form builder — per-request-type form schemas
# ===========================================================================
import json as _json  # noqa: E402

from django.core.exceptions import ValidationError as _ValidationError  # noqa: E402


# Names the schema cannot use — they're set by the caller
_RESERVED_KEYS = {
    "request_type", "subject", "body", "priority", "comment",
    "file", "file_description", "csrfmiddlewaretoken",
}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
def validate_schema(schema):
    """Raise ValidationError if the schema is malformed. Returns the parsed
    list of field definitions."""
    if not isinstance(schema, dict):
        raise _ValidationError("Schema must be a JSON object.")
    fields = schema.get("fields") or []
    if not isinstance(fields, list):
        raise _ValidationError("'fields' must be a list.")
    seen = set()
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise _ValidationError(f"Field {i} is not an object.")
        key = f.get("key")
        label = f.get("label")
        ftype = f.get("type")
        if not key or not isinstance(key, str):
            raise _ValidationError(f"Field {i}: missing or invalid 'key'.")
        if key in _RESERVED_KEYS:
            raise _ValidationError(
                f"Field {i}: '{key}' is a reserved name and cannot be used in the schema."
            )
        if key in seen:
            raise _ValidationError(f"Field {i}: duplicate key '{key}'.")
        seen.add(key)
        if not label or not isinstance(label, str):
            raise _ValidationError(f"Field '{key}': missing or invalid 'label'.")
        if ftype not in (
            "text", "textarea", "number", "date", "select",
            "checkbox", "asset_picker", "user_picker",
        ):
            raise _ValidationError(
                f"Field '{key}': unknown type '{ftype}'."
            )
        if ftype == "select" and not f.get("options"):
            raise _ValidationError(
                f"Field '{key}': type 'select' requires an 'options' list."
            )
    return fields


# ---------------------------------------------------------------------------
# Choice providers
# ---------------------------------------------------------------------------
def _asset_choices(requester, category_codes=None):
    """Return [(asset_tag, display), …] scoped to what the requester can see.

    Regular users see only assets assigned to them. Users with
    `accounts.access_all_departments` (or superusers) see every asset.
    """
    try:
        from assets.models import Asset
    except ImportError:
        return []
    qs = Asset.objects.filter(is_deleted=False)
    if requester is not None \
            and not requester.is_superuser \
            and not requester.has_perm("accounts.access_all_departments"):
        qs = qs.filter(current_custodian=requester)
    if category_codes:
        qs = qs.filter(category__code__in=category_codes)
    qs = qs.select_related("category").order_by("asset_tag")
    return [
        (a.asset_tag, f"{a.asset_tag} · {a.display_name}")
        for a in qs
    ]


def _user_choices(requester, scope=None):
    """Return [(email, display), …] scoped to the requester's department when
    appropriate."""
    from accounts.models import User
    qs = User.objects.filter(is_active=True)
    if requester is not None \
            and not requester.is_superuser \
            and not requester.has_perm("accounts.access_all_departments"):
        if requester.department_id:
            qs = qs.filter(department_id=requester.department_id)
    qs = qs.order_by("first_name", "last_name", "email")
    return [(u.email, f"{u.get_full_name() or u.email}") for u in qs]


# ---------------------------------------------------------------------------
# Field builders
# ---------------------------------------------------------------------------
def _build_field(defn, requester):
    """Return (field_name, django_field) for one schema definition."""
    key = defn["key"]
    label = defn["label"]
    required = bool(defn.get("required"))
    help_text = defn.get("help") or ""
    ftype = defn["type"]

    common = {
        "label": label,
        "required": required,
        "help_text": help_text,
    }

    if ftype == "text":
        return key, forms.CharField(
            max_length=int(defn.get("max_length") or 255),
            widget=forms.TextInput(attrs={"class": "input", "placeholder": defn.get("placeholder", "")}),
            **common,
        )
    if ftype == "textarea":
        return key, forms.CharField(
            widget=forms.Textarea(attrs={"class": "input", "rows": int(defn.get("rows") or 4),
                                         "placeholder": defn.get("placeholder", "")}),
            **common,
        )
    if ftype == "number":
        return key, forms.IntegerField(
            widget=forms.NumberInput(attrs={"class": "input", "step": defn.get("step", "1"),
                                            "placeholder": defn.get("placeholder", "")}),
            **common,
        )
    if ftype == "date":
        return key, forms.DateField(
            widget=forms.DateInput(attrs={"class": "input", "type": "date"}),
            **common,
        )
    if ftype == "checkbox":
        f = forms.BooleanField(
            label=label, required=required, help_text=help_text,
            widget=forms.CheckboxInput(),
        )
        return key, f
    if ftype == "select":
        choices = [(str(c), str(c)) for c in (defn.get("options") or [])]
        return key, forms.ChoiceField(
            choices=[("", "— Select —")] + choices if not required else choices,
            widget=forms.Select(attrs={"class": "input"}),
            **common,
        )
    if ftype == "asset_picker":
        codes = defn.get("category_codes") or None
        choices = _asset_choices(requester, codes)
        return key, forms.ChoiceField(
            choices=[("", "— Select an asset —")] + choices,
            widget=forms.Select(attrs={"class": "input"}),
            **common,
        )
    if ftype == "user_picker":
        choices = _user_choices(requester)
        return key, forms.ChoiceField(
            choices=[("", "— Select a person —")] + choices,
            widget=forms.Select(attrs={"class": "input"}),
            **common,
        )
    raise _ValidationError(f"Unsupported field type: {ftype}")


def build_schema_fields(request_type, requester):
    """Return a dict {name: Field} for the request_type's form_schema."""
    if request_type is None:
        return {}
    schema = request_type.form_schema or {}
    if not schema:
        return {}
    try:
        definitions = validate_schema(schema)
    except _ValidationError:
        # A malformed schema in the DB should not break the raise page.
        # Log and skip — validation happens on save in admin.
        import logging
        logging.getLogger(__name__).exception(
            "Malformed form_schema for request type %s", request_type.code
        )
        return {}
    fields = {}
    for defn in definitions:
        name, field = _build_field(defn, requester)
        fields[name] = field
    return fields


# ---------------------------------------------------------------------------
# The dynamic form
# ---------------------------------------------------------------------------
def build_request_form(request_type, requester, data=None, files=None):
    """Build a form that includes the common fields plus the schema fields
    for `request_type`.

    Common fields are: subject, body, priority (plus a hidden request_type).
    Schema fields are added dynamically.
    """
    base = {
        "subject": forms.CharField(
            max_length=255,
            widget=forms.TextInput(attrs={
                "class": "input",
                "placeholder": "Short summary of your request",
            }),
        ),
        "body": forms.CharField(
            widget=forms.Textarea(attrs={
                "class": "input", "rows": 5,
                "placeholder": "Describe what you need in a bit more detail.",
            }),
        ),
        "priority": forms.ChoiceField(
            choices=PRIORITY_CHOICES,
            initial="normal",
            widget=forms.Select(attrs={"class": "input"}),
        ),
    }
    # Merge in the schema fields
    base.update(build_schema_fields(request_type, requester))

    DynamicForm = type("DynamicRequestForm", (forms.Form,), base)
    form = DynamicForm(data, files)
    return form


def build_resubmit_form(request_obj, requester, data=None, files=None):
    """Same as build_request_form, but for the resubmit flow — includes
    subject/body/comment/attachment plus the schema fields."""
    base = {
        "subject": forms.CharField(
            max_length=255,
            widget=forms.TextInput(attrs={"class": "input"}),
        ),
        "body": forms.CharField(
            widget=forms.Textarea(attrs={"class": "input", "rows": 5}),
        ),
        "comment": forms.CharField(
            required=False,
            widget=forms.Textarea(attrs={
                "class": "input", "rows": 2,
                "placeholder": "Optional note to the reviewer (what did you change?)",
            }),
        ),
        "file": forms.FileField(
            required=False,
            widget=forms.ClearableFileInput(attrs={"class": "input"}),
        ),
        "file_description": forms.CharField(
            required=False, max_length=255,
            widget=forms.TextInput(attrs={
                "class": "input",
                "placeholder": "Optional description of the attached file",
            }),
        ),
    }
    base.update(build_schema_fields(request_obj.request_type, requester))

    DynamicForm = type("DynamicResubmitForm", (forms.Form,), base)
    form = DynamicForm(data, files)

    # Pre-fill
    form.fields["subject"].initial = request_obj.subject
    form.fields["body"].initial = request_obj.body
    for key, value in (request_obj.form_data or {}).items():
        if key in form.fields:
            form.fields[key].initial = value
    return form


def extract_form_data(form, request_type):
    """Return the dict of schema-field values from a submitted form.
    Ignores the common fields (subject, body, priority, comment, file)."""
    if request_type is None or not request_type.form_schema:
        return {}
    schema = request_type.form_schema
    try:
        definitions = validate_schema(schema)
    except _ValidationError:
        return {}
    out = {}
    for defn in definitions:
        key = defn["key"]
        if key in form.cleaned_data:
            out[key] = form.cleaned_data[key]
    return out
    
    
    