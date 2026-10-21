"""Settings → Complaints (Master Plan §11, §13): layers, routing rules,
escalation rules and the knowledge base. Rows only — nothing about a
complaint's path is in code. Mounted in the ``qa_set`` namespace.

Each section needs its own permission (§12): manage_complaint_layers,
manage_routing_rules, manage_escalation_rules, manage_knowledge_base. Others
see the tables read-only.
"""
from __future__ import annotations

import json

from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from qa_core.permissions import can_view_qa_settings, require
from qa_core.ui import toast

from .models import ComplaintLayer, EscalationRule, KnowledgeArticle, RoutingRule

SECTIONS = [
    ("layers", "Layers", "complaints.manage_complaint_layers"),
    ("routing", "Routing", "complaints.manage_routing_rules"),
    ("escalation", "Escalation", "complaints.manage_escalation_rules"),
    ("kb", "Knowledge base", "complaints.manage_knowledge_base"),
]
PERMS = {k: p for k, _l, p in SECTIONS}


def _can(user, section):
    return user.is_superuser or user.has_perm(PERMS[section])


class _Form(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            css = "input"
            widget.attrs.setdefault("class", css)


class LayerForm(_Form):
    applies_to = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple,
        choices=[("institution", "Institutions"), ("resource_person", "Resource persons"), ("staff", "NBTE staff")],
        help_text="None ticked = everyone may raise it.")
    custom_fields_json = forms.CharField(
        required=False, label="Extra questions (JSON)", widget=forms.Textarea(attrs={"rows": 4}),
        help_text='[{"key": "amount", "label": "Amount involved", "type": "text", "required": false}] — '
                  "types: text, textarea, choice (with \"options\"), date.")

    class Meta:
        model = ComplaintLayer
        fields = ["name", "code", "parent", "description", "default_priority", "flow", "is_active", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from qa_core.models import FlowDefinition

        self.fields["code"].required = False
        self.fields["code"].help_text = "Blank = made from the name."
        self.fields["parent"].queryset = ComplaintLayer.objects.filter(parent__isnull=True).exclude(
            pk=self.instance.pk or 0)
        self.fields["flow"].queryset = FlowDefinition.objects.filter(
            subject_model="complaints.complaint", published_at__isnull=False).order_by("code", "-version")
        self.fields["flow"].help_text = "Blank = the default complaint flow. Build new flows under Workflows."
        if self.instance.pk:
            self.initial["applies_to"] = self.instance.applies_to_user_types or []
            self.initial["custom_fields_json"] = json.dumps(self.instance.custom_fields or [], indent=1)

    def clean_code(self):
        code = slugify(self.cleaned_data.get("code") or self.cleaned_data.get("name") or "")[:60]
        if not code:
            raise ValidationError("Give the layer a name.")
        clash = ComplaintLayer.objects.filter(code=code).exclude(pk=self.instance.pk or 0)
        if clash.exists():
            raise ValidationError(f"The code {code} is already used.")
        return code

    def clean_custom_fields_json(self):
        raw = (self.cleaned_data.get("custom_fields_json") or "").strip() or "[]"
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ValidationError(f"Not valid JSON: {exc}") from None
        if not isinstance(data, list) or not all(isinstance(f, dict) and f.get("key") for f in data):
            raise ValidationError("A list of questions, each with at least a \"key\".")
        for f in data:
            if f.get("type", "text") not in ("text", "textarea", "choice", "date"):
                raise ValidationError(f"Question {f['key']}: type must be text, textarea, choice or date.")
        return data

    def clean(self):
        data = super().clean()
        parent = data.get("parent")
        if parent is not None and parent.parent_id:
            raise ValidationError("Layers nest two deep at most: choose a top-level parent.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.applies_to_user_types = list(self.cleaned_data.get("applies_to") or [])
        obj.custom_fields = self.cleaned_data.get("custom_fields_json") or []
        if commit:
            obj.save()
        return obj


class RoutingForm(_Form):
    class Meta:
        model = RoutingRule
        fields = ["order", "layer", "stage_code", "condition", "target_kind", "target_department", "target_role",
                  "target_user", "note", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User

        self.fields["target_user"].queryset = User.objects.filter(is_active=True, user_type="staff").order_by(
            "first_name", "last_name")

    def clean_condition(self):
        from qa_core.formula import FormulaError, evaluate_value

        cond = (self.cleaned_data.get("condition") or "").strip()
        if not cond:
            return ""
        sample = {"priority": "normal", "layer": "x", "parent_layer": "", "raiser_type": "institution", "stage": "x",
                  "institution_code": "X", "institution_zone": "NW", "institution_state": "Kaduna",
                  "handling_department": "dpap", "reopened": 0}
        try:
            evaluate_value(cond, sample, declared=list(sample), returns="bool")
        except FormulaError as exc:
            raise ValidationError(f"The condition cannot be checked: {exc}") from None
        return cond

    def clean(self):
        data = super().clean()
        kind = data.get("target_kind")
        if kind == "department" and not data.get("target_department"):
            self.add_error("target_department", "Choose the department.")
        if kind == "role" and not data.get("target_role"):
            self.add_error("target_role", "Choose the role.")
        if kind == "user" and not data.get("target_user"):
            self.add_error("target_user", "Choose the person.")
        return data


class EscalationForm(_Form):
    class Meta:
        model = EscalationRule
        fields = ["level", "after_breach_minutes", "target_kind", "target_role", "target_user", "only_if", "flow",
                  "stage_code", "notify_template", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User
        from qa_core.models import FlowDefinition

        self.fields["target_user"].queryset = User.objects.filter(is_active=True, user_type="staff").order_by(
            "first_name", "last_name")
        self.fields["flow"].queryset = FlowDefinition.objects.filter(subject_model="complaints.complaint")

    def clean(self):
        data = super().clean()
        if data.get("target_kind") == "role" and not data.get("target_role"):
            self.add_error("target_role", "Choose the role.")
        if data.get("target_kind") == "user" and not data.get("target_user"):
            self.add_error("target_user", "Choose the person.")
        return data


class ArticleForm(_Form):
    class Meta:
        model = KnowledgeArticle
        fields = ["title", "slug", "summary", "body", "keywords", "layer", "audience", "is_published"]
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["slug"].help_text = "Blank = made from the title."

    def clean_slug(self):
        slug = slugify(self.cleaned_data.get("slug") or self.cleaned_data.get("title") or "")[:200]
        if not slug:
            raise ValidationError("Give the article a title.")
        if KnowledgeArticle.objects.filter(slug=slug).exclude(pk=self.instance.pk or 0).exists():
            raise ValidationError(f"The address {slug} is already used.")
        return slug


FORMS = {"layers": LayerForm, "routing": RoutingForm, "escalation": EscalationForm, "kb": ArticleForm}
MODELS = {"layers": ComplaintLayer, "routing": RoutingRule, "escalation": EscalationRule, "kb": KnowledgeArticle}


def _rows(section):
    if section == "layers":
        return list(ComplaintLayer.objects.select_related("parent", "flow").order_by("sort_order", "name"))
    if section == "routing":
        return list(RoutingRule.objects.select_related("layer", "target_department", "target_role", "target_user")
                    .order_by("order", "id"))
    if section == "escalation":
        return list(EscalationRule.objects.select_related("target_role", "target_user", "flow"))
    return list(KnowledgeArticle.objects.select_related("layer").order_by("title"))


def _ctx(request, section, **extra):
    from qa_core.views import _settings_context

    ctx = {"sections": [{"key": k, "label": label, "active": k == section} for k, label, _p in SECTIONS],
           "section": section, "rows": _rows(section), "editable": _can(request.user, section),
           "perm": PERMS[section]}
    ctx.update(extra)
    return _settings_context(request, "complaints", **ctx)


def _section(request):
    section = request.GET.get("section") or request.POST.get("section") or "layers"
    return section if section in PERMS else "layers"


def _render(request, ctx, status=200):
    target = request.headers.get("HX-Target", "") if request.headers.get("HX-Request") else ""
    template = "complaints/settings/_panel.html" if target == "qa-panel" else "complaints/settings/page.html"
    return render(request, template, ctx, status=status)


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def complaints_settings(request):
    """List a section; POST creates a row."""
    section = _section(request)
    form = None
    if request.method == "POST":
        if not _can(request.user, section):
            raise PermissionDenied(f"You need {PERMS[section]}.")
        form = FORMS[section](request.POST)
        if form.is_valid():
            obj = form.save()
            if not request.headers.get("HX-Request"):
                return redirect(reverse("qa_set:complaints") + f"?section={section}")
            response = _render(request, _ctx(request, section, form=FORMS[section](), saved=obj))
            return toast(response, "success", "Added", str(obj))
        response = _render(request, _ctx(request, section, form=form, open_new=True), status=422)
        return toast(response, "error", "Not added", "Check the highlighted fields.")
    return _render(request, _ctx(request, section, form=FORMS[section]() if _can(request.user, section) else None))


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def complaints_settings_edit(request, section, pk):
    if section not in PERMS:
        raise Http404
    obj = MODELS[section].objects.filter(pk=pk).first()
    if obj is None:
        raise Http404
    editable = _can(request.user, section)
    if request.method == "POST":
        if not editable:
            raise PermissionDenied(f"You need {PERMS[section]}.")
        form = FORMS[section](request.POST, instance=obj)
        if form.is_valid():
            form.save()
            if not request.headers.get("HX-Request"):
                return redirect(reverse("qa_set:complaints") + f"?section={section}")
            response = render(request, "complaints/settings/_panel.html", _ctx(request, section,
                                                                                form=FORMS[section]()))
            response["HX-Retarget"] = "#qa-panel"
            return toast(response, "success", "Saved", str(obj))
        response = render(request, "complaints/settings/_edit.html",
                          _ctx(request, section, edit_form=form, obj=obj), status=422)
        return toast(response, "error", "Not saved", "Check the highlighted fields.")
    form = FORMS[section](instance=obj) if editable else None
    return render(request, "complaints/settings/_edit.html", _ctx(request, section, edit_form=form, obj=obj))
