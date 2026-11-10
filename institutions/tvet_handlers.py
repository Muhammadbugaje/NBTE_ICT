"""Pending-action handlers for professional bodies and programmes (Part 1c, X47).

Each change is stored with its form data, previewed field by field, and the
same form is bound again and saved when the last layer (confirm or approve)
passes — so the website, the API and the confirm queue share one rule set.

    institutions.edit_professional_body   op "form" (add / edit), op "account" (portal account)
    institutions.edit_programme_mapping   op "form" (add / edit), op "toggle" (on / off)
    institutions.edit_programme_master    op "map" (hand-typed programme -> master row, or a new row)
"""
from __future__ import annotations

from django.core.exceptions import ValidationError

from qa_core.actions import ActionHandler, DiffRow, register


def _clean_data(data):
    return {k: v for k, v in (data or {}).items() if k not in ("csrfmiddlewaretoken",)}


def form_payload(request, *, op="form", **extra):
    """The payload a view stores: the posted fields (lists kept) plus extras."""
    data = {}
    for key in request.POST:
        if key == "csrfmiddlewaretoken":
            continue
        values = request.POST.getlist(key)
        data[key] = values if len(values) > 1 else values[0]
    return {"op": op, "data": data, **extra}


def _value(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if hasattr(v, "all"):
        return ", ".join(str(x) for x in v.all()) or "—"
    return str(v)


class _FormHandler(ActionHandler):
    form_class = None
    target_model = None

    def form(self, target, payload):
        return self.form_class(_clean_data(payload.get("data")), instance=target)

    def check_initiator(self, target, payload, actor):
        pass

    def validate(self, target, payload):
        if payload.get("op", "form") != "form":
            return self.validate_op(target, payload)
        form = self.form(target, payload)
        if not form.is_valid():
            raise ValidationError([f"{k}: {' '.join(v)}" if k != "__all__" else " ".join(v)
                                   for k, v in form.errors.items()])

    def validate_op(self, target, payload):
        raise ValidationError("Unknown change.")

    def snapshot(self, target):
        if target is None:
            return {}
        return {f.name: _value(getattr(target, f.name, "")) for f in target._meta.concrete_fields
                if f.name not in ("id", "created_at", "updated_at", "search_key")}

    def preview(self, target, payload):
        if payload.get("op", "form") != "form":
            return self.preview_op(target, payload)
        form = self.form(target, payload)
        if not form.is_valid():
            return [DiffRow("Problem", "", "; ".join(" ".join(v) for v in form.errors.values()))]
        rows = []
        for name in form.fields:
            new = form.cleaned_data.get(name)
            old = getattr(target, name, None) if target is not None else None
            if hasattr(old, "all"):
                old = ", ".join(str(x) for x in old.all())
                new = ", ".join(str(x) for x in (new or []))
            if _value(old) != _value(new) or target is None:
                rows.append(DiffRow(form.fields[name].label or name.replace("_", " ").capitalize(),
                                    _value(old) if target is not None else "", _value(new)))
        return rows

    def preview_op(self, target, payload):
        return []

    def apply(self, target, payload, actor):
        if payload.get("op", "form") != "form":
            return self.apply_op(target, payload, actor)
        form = self.form(target, payload)
        if not form.is_valid():
            raise ValidationError("The change is no longer valid.")
        saved = form.save(commit=False)
        if target is None and hasattr(saved, "created_by_id") and not saved.created_by_id:
            saved.created_by = actor
        saved.save()
        if hasattr(form, "save_m2m"):
            form.save_m2m()
        return saved

    def apply_op(self, target, payload, actor):
        raise ValidationError("Unknown change.")

    def scope_department(self, target, initiator):
        return getattr(initiator, "department", None)


@register
class ProfessionalBodyHandler(_FormHandler):
    code = "institutions.edit_professional_body"

    @property
    def form_class(self):
        from .pb_forms import ProfessionalBodyForm
        return ProfessionalBodyForm

    @property
    def target_model(self):
        from .models import ProfessionalBody
        return ProfessionalBody

    def payload_label(self, payload):
        data = payload.get("data") or {}
        if payload.get("op") == "account":
            return f"Portal account {data.get('email', '')}"
        return f"New body: {data.get('name', '')}".strip()

    def target_label(self, target):
        return f"{target.acronym or target.name}" if target is not None else ""

    def validate_op(self, target, payload):
        if payload.get("op") == "account":
            data = payload.get("data") or {}
            if target is None or not (data.get("email") or "").strip():
                raise ValidationError("Give the e-mail address of the body's portal account.")
            return
        raise ValidationError("Unknown change.")

    def preview_op(self, target, payload):
        data = payload.get("data") or {}
        return [DiffRow("Portal account", "", f"{data.get('first_name', '')} {data.get('last_name', '')} "
                                              f"<{data.get('email', '')}> ({data.get('role', 'pb-admin')})")]

    def apply_op(self, target, payload, actor):
        from .pb_portal import create_body_user
        data = payload.get("data") or {}
        primary = data.get("primary_contact") in (True, "on", "true", "True", "1")
        return create_body_user(target, email=data.get("email", ""), first_name=data.get("first_name", ""),
                                last_name=data.get("last_name", ""), job_title=data.get("job_title", ""),
                                role_code=data.get("role", "pb-admin"), primary_contact=primary,
                                actor=actor, request=None)


@register
class ProgrammeMappingHandler(_FormHandler):
    code = "institutions.edit_programme_mapping"

    @property
    def target_model(self):
        from .models import ProgrammeBodyMapping
        return ProgrammeBodyMapping

    def form(self, target, payload):
        from .models import ProfessionalBody
        from .pb_forms import MappingForm
        body = ProfessionalBody.objects.filter(pk=payload.get("fixed_body")).first() if payload.get("fixed_body") \
            else None
        return MappingForm(_clean_data(payload.get("data")), instance=target, body=body)

    def payload_label(self, payload):
        return f"Map {(payload.get('data') or {}).get('programme_name', '')}".strip()

    def validate_op(self, target, payload):
        if payload.get("op") == "toggle" and target is not None:
            return
        raise ValidationError("Unknown change.")

    def preview_op(self, target, payload):
        return [DiffRow("Active", _value(target.is_active), _value(not target.is_active))]

    def apply_op(self, target, payload, actor):
        target.is_active = not target.is_active
        target.save(update_fields=["is_active", "updated_at"])
        return target


@register
class ProgrammeMasterHandler(ActionHandler):
    code = "institutions.edit_programme_master"

    @property
    def target_model(self):
        from .models import Programme
        return Programme

    def target_label(self, target):
        return f"{target} ({target.institution.name})" if target is not None else ""

    def snapshot(self, target):
        return {"master": _value(getattr(target, "master", None))} if target is not None else {}

    def validate(self, target, payload):
        choice = str(payload.get("master") or "")
        if target is None or not target.not_in_master:
            raise ValidationError("This programme is already on the master list.")
        if choice != "new" and not choice.isdigit():
            raise ValidationError("Choose a master-list programme.")

    def preview(self, target, payload):
        from .models import ProgrammeMaster
        choice = str(payload.get("master") or "")
        after = f"New master row: {target.name}" if choice == "new" else \
            str(ProgrammeMaster.objects.filter(pk=choice).first() or "—")
        return [DiffRow("Master-list programme", _value(getattr(target, "master", None)), after)]

    def apply(self, target, payload, actor):
        from .models import ProgrammeMaster
        from .programme_master import map_programme
        choice = str(payload.get("master") or "")
        if choice == "new":
            master = (ProgrammeMaster.objects.filter(name__iexact=target.name, level=target.level).first()
                      or ProgrammeMaster.objects.create(
                          name=target.name, level=target.level, programme_category=target.category,
                          discipline=target.category.discipline if target.category_id else None,
                          professional_body=target.professional_body,
                          notes=f"Added from {target.institution.name}'s hand-typed programme."))
        else:
            master = ProgrammeMaster.objects.get(pk=choice)
        map_programme(target, master, user=actor)
        return master

    def scope_department(self, target, initiator):
        return getattr(initiator, "department", None)
