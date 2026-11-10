"""Dynamic forms helper (v2.1 X49; used by register, Add TVET, setup forms and
visit logistics from Phase F).

1. Conditional fields: declare ``show_if`` on the form

       class TVETForm(DynamicFormMixin, forms.Form):
           show_if = {"cac_rc_number": {"ownership_is_public": False},
                      "approval_letter": {"status": ["approved"]}}

   * the widget gets ``data-show-if='{"ownership_is_public": false}'`` and the
     template wraps the field with ``{% dynamic_field form.cac_rc_number %}``
     (or any element carrying the same attribute); static/js/forms-dynamic.js
     hides/shows it as the controlling fields change. Conditionally required
     widgets also carry ``data-dynamic-required`` so the browser and shared
     field template can expose required state only while the field is shown;
   * ``clean()`` applies the SAME predicates on the server (the source of
     truth): a hidden field is not required and its value is dropped.

2. Dependent dropdowns: ``dependent_select(form, "state", "lga", url)`` makes
   the parent select refill the child ``<select>`` through HTMX
   (``hx-get=url``, ``?state=<id>``) - e.g. State -> LGA with
   ``qa_portal:public_lga_options``. Server validation (``validate_lga``) keeps
   the pair consistent whatever the browser sends.
"""
import json

from django import forms


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def predicate_matches(expected, actual):
    """expected: a value, a list of allowed values, or a bool."""
    if isinstance(expected, bool):
        return _truthy(actual) == expected
    if isinstance(expected, (list, tuple, set)):
        return str(actual) in {str(e) for e in expected}
    return str(actual) == str(expected)


def is_visible(rules, data):
    """True when every {controlling field: expected} rule holds for ``data``."""
    return all(predicate_matches(exp, data.get(field)) for field, exp in (rules or {}).items())


class DynamicFormMixin:
    show_if = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, rules in (self.show_if or {}).items():
            if name in self.fields:
                self.fields[name].widget.attrs["data-show-if"] = json.dumps(rules)
                self.fields[name]._dynamic_required = self.fields[name].required
                if self.fields[name].required:
                    self.fields[name].widget.attrs["data-dynamic-required"] = "true"
                # required-ness is decided in clean() once we know visibility
                self.fields[name].required = False

    def _raw(self, name):
        field = self.fields.get(name)
        if field is None:
            return self.data.get(name)
        value = field.widget.value_from_datadict(self.data, self.files, self.add_prefix(name))
        if isinstance(field, forms.BooleanField):
            return field.clean(value) if value not in (None, "") else False
        return value

    def visible_fields_map(self):
        data = {name: self._raw(name) for name in self.fields}
        return {name: is_visible(rules, data) for name, rules in (self.show_if or {}).items()}

    def clean(self):
        cleaned = super().clean()
        for name, visible in self.visible_fields_map().items():
            field = self.fields.get(name)
            if field is None:
                continue
            if not visible:
                cleaned.pop(name, None)
                if name in self._errors:
                    del self._errors[name]
            elif getattr(field, "_dynamic_required", False) and cleaned.get(name) in (None, "", [], False):
                self.add_error(name, "This field is required.")
        return cleaned


def dependent_select(form, parent, child, url, *, target=None, param=None):
    """Wire ``form[parent]`` to refill ``form[child]`` (a select) via HTMX."""
    widget = form.fields[parent].widget
    widget.attrs.update({
        "hx-get": url,
        "hx-trigger": "change",
        "hx-target": target or f"#id_{form.add_prefix(child)}",
        "hx-swap": "innerHTML",
        "hx-include": "this",
        "data-dependent-child": form.add_prefix(child),
    })
    if param and param != parent:
        widget.attrs["hx-vals"] = json.dumps({param: ""})
    return form
