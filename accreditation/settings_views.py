"""Settings → SSQ templates (builder) and Settings → Curriculum catalogue
(Phase 3), namespace "qa_set", mounted under /qa/settings/.

Templates and catalogue lines are global configuration (the same for every
institution), so these screens are not institution-scoped (§14). Editing
needs ``accreditation.manage_ssq_templates`` / ``manage_catalogue``;
publishing needs ``accreditation.publish_ssq_template``.
"""
from __future__ import annotations

import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from qa_core.permissions import can_view_qa_settings, require
from qa_core.ui import render_qa, toast

from . import builder
from . import ssq as engine
from .models import CatalogueItem, QAType, SSQTemplate


def _msg(exc):
    return " ".join(getattr(exc, "messages", None) or [str(exc)])


def _ctx(request, tab, **extra):
    from qa_core.views import _settings_context

    return _settings_context(request, tab, **extra)


# ===========================================================================
# SSQ templates
# ===========================================================================
def _can_edit(user):
    return user.is_superuser or user.has_perm("accreditation.manage_ssq_templates")


def _can_publish(user):
    return user.is_superuser or user.has_perm("accreditation.publish_ssq_template")


def _templates_ctx(request, **extra):
    rows = []
    for qa_type in QAType.objects.order_by("sort_order", "name"):
        versions = list(SSQTemplate.objects.filter(qa_type=qa_type).order_by("-version"))
        rows.append({"type": qa_type, "versions": versions,
                     "current": next((v for v in versions if v.is_current), None),
                     "draft": next((v for v in versions if v.status == SSQTemplate.DRAFT), None)})
    ctx = {"type_rows": rows, "can_edit": _can_edit(request.user), "can_publish": _can_publish(request.user)}
    ctx.update(extra)
    return _ctx(request, "ssq_templates", **ctx)


@require(can_view_qa_settings)
@require_GET
def templates(request):
    return render_qa(request, "accreditation/settings/ssq_templates.html", _templates_ctx(request),
                     {"qa-panel": "accreditation/settings/_ssq_templates_panel.html"})


@require(can_view_qa_settings)
@require_POST
def template_new(request, type_id):
    qa_type = QAType.objects.filter(pk=type_id).first()
    if qa_type is None:
        raise Http404
    try:
        draft = builder.new_draft(qa_type, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        response = render(request, "accreditation/settings/_ssq_templates_panel.html",
                          _templates_ctx(request, error=_msg(exc)), status=422)
        return toast(response, "error", "Not started", _msg(exc))
    response = render(request, "accreditation/settings/_builder_panel.html", _builder_ctx(request, draft))
    response["HX-Push-Url"] = f"/qa/settings/ssq-templates/{draft.pk}/"
    return toast(response, "success", f"Version {draft.version} started", "Edit it, then publish.")


def _template_or_404(pk):
    template = SSQTemplate.objects.select_related("qa_type").filter(pk=pk).first()
    if template is None:
        raise Http404
    return template


def _builder_ctx(request, template, **extra):
    errors = engine.validate_schema(template.schema)
    sections = []
    for section in template.sections():
        fields = []
        for field in section.get("fields", []):
            f = dict(field)
            f["type_label"] = engine.FIELD_TYPES.get(field.get("type"), field.get("type"))
            fields.append(f)
        sections.append(dict(section, fields=fields))
    from institutions.models import DocumentKind

    ctx = {"template": template, "sections": sections, "errors": errors,
           "editable": template.status == SSQTemplate.DRAFT and _can_edit(request.user),
           "can_publish": _can_publish(request.user), "field_types": list(engine.FIELD_TYPES.items()),
           "prefill_sources": engine.prefill_sources(),
           "document_kinds": DocumentKind.objects.filter(is_active=True).order_by("name"),
           "schema_json": json.dumps(template.schema, indent=2, ensure_ascii=False),
           "in_use": builder.usage_count(template),
           "versions": list(SSQTemplate.objects.filter(qa_type=template.qa_type).order_by("-version"))}
    ctx.update(extra)
    return _ctx(request, "ssq_templates", **ctx)


@require(can_view_qa_settings)
@require_GET
def template_builder(request, pk):
    template = _template_or_404(pk)
    return render_qa(request, "accreditation/settings/builder.html", _builder_ctx(request, template),
                     {"qa-panel": "accreditation/settings/_builder_panel.html"})


def _builder_response(request, template, *, error="", ok=""):
    template.refresh_from_db()
    response = render(request, "accreditation/settings/_builder_panel.html",
                      _builder_ctx(request, template, error=error), status=422 if error else 200)
    if error:
        return toast(response, "error", "Not saved", error)
    return toast(response, "success", ok) if ok else response


@require(can_view_qa_settings)
@require_POST
def template_section(request, pk):
    """Add, update, move or remove a section (op=add|update|up|down|remove)."""
    template = _template_or_404(pk)
    p = request.POST
    op = p.get("op", "add")
    try:
        if op == "add":
            builder.add_section(template, user=request.user, key=p.get("key", ""), title=p.get("title", ""),
                                description=p.get("description", ""), department=p.get("department") == "on")
        elif op == "update":
            builder.update_section(template, user=request.user, key=p.get("key", ""), title=p.get("title", ""),
                                   description=p.get("description", ""), department=p.get("department") == "on")
        elif op in ("up", "down"):
            builder.move_section(template, user=request.user, key=p.get("key", ""), delta=-1 if op == "up" else 1)
        elif op == "remove":
            builder.remove_section(template, user=request.user, key=p.get("key", ""))
        else:
            raise ValidationError("Unknown operation.")
    except (ValidationError, PermissionDenied) as exc:
        return _builder_response(request, template, error=_msg(exc))
    return _builder_response(request, template, ok={"add": "Section added", "update": "Section saved",
                                                     "remove": "Section removed"}.get(op, ""))


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def template_field(request, pk):
    """GET: the field editor (new field of ?type= in ?section=, or ?key= to edit).
    POST: save it; op=remove|up|down act on ?key=."""
    template = _template_or_404(pk)
    section_key = request.GET.get("section") or request.POST.get("section", "")
    if request.method == "GET":
        key = request.GET.get("key", "")
        field = template.field_map().get(key) if key else engine.blank_field(request.GET.get("type", "text"))
        if field is None:
            raise Http404
        ctx = _builder_ctx(request, template, field=field, section_key=section_key, original_key=key,
                           options_text="\n".join(str(o.get("label", o.get("value")) if isinstance(o, dict) else o)
                                                  for o in field.get("options", []) or []),
                           columns_text="\n".join(f"{c.get('key')}|{c.get('label', '')}|{c.get('type', 'text')}"
                                                  for c in field.get("columns", []) or []))
        return render(request, "accreditation/settings/_field_form.html", ctx)
    op = request.POST.get("op", "save")
    try:
        if op == "remove":
            builder.remove_field(template, user=request.user, section_key=section_key, key=request.POST.get("key", ""))
        elif op in ("up", "down"):
            builder.move_field(template, user=request.user, section_key=section_key, key=request.POST.get("key", ""),
                               delta=-1 if op == "up" else 1)
        else:
            field = builder.field_from_form(request.POST)
            problems = engine._validate_field(field, "This field", set(), set(engine.prefill_sources()),
                                              {s.split(".", 1)[1] for s in engine.prefill_sources()
                                               if s.startswith("document.")})
            if problems:
                raise ValidationError(problems)
            builder.upsert_field(template, user=request.user, section_key=section_key, field=field,
                                 original_key=request.POST.get("original_key", ""))
    except (ValidationError, PermissionDenied) as exc:
        if op == "save":
            template.refresh_from_db()
            ctx = _builder_ctx(request, template, field=_safe_field(request.POST),
                               section_key=section_key, original_key=request.POST.get("original_key", ""),
                               options_text=request.POST.get("options", ""),
                               columns_text=request.POST.get("columns", ""), field_error=_msg(exc))
            response = render(request, "accreditation/settings/_field_form.html", ctx, status=422)
            return toast(response, "error", "Field not saved", _msg(exc))
        return _builder_response(request, template, error=_msg(exc))
    response = _builder_response(request, template, ok={"save": "Field saved", "remove": "Field removed"}.get(op, ""))
    response["HX-Retarget"] = "#qa-panel"
    return response


def _safe_field(data):
    try:
        return builder.field_from_form(data)
    except ValidationError:
        return {"key": data.get("key", ""), "type": data.get("type", "text"), "label": data.get("label", "")}


@require(can_view_qa_settings)
@require_POST
def template_json(request, pk):
    """Advanced: replace the whole draft schema with pasted JSON."""
    template = _template_or_404(pk)
    try:
        errors = builder.save_draft(template, user=request.user, schema=request.POST.get("schema", ""),
                                    name=request.POST.get("name"), notes=request.POST.get("notes"))
    except (ValidationError, PermissionDenied) as exc:
        return _builder_response(request, template, error=_msg(exc))
    return _builder_response(request, template, ok="Draft saved" + (f" — {len(errors)} problem(s) to fix"
                                                                    if errors else " — valid"))


@require(can_view_qa_settings)
@require_POST
def template_publish(request, pk):
    template = _template_or_404(pk)
    try:
        builder.publish(template, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _builder_response(request, template, error=_msg(exc))
    return _builder_response(request, template, ok=f"Version {template.version} published — new SSQs use it")


@require(can_view_qa_settings)
@require_POST
def template_discard(request, pk):
    template = _template_or_404(pk)
    try:
        builder.discard_draft(template, user=request.user)
    except (ValidationError, PermissionDenied) as exc:
        return _builder_response(request, template, error=_msg(exc))
    response = render(request, "accreditation/settings/_ssq_templates_panel.html", _templates_ctx(request))
    response["HX-Push-Url"] = "/qa/settings/ssq-templates/"
    return toast(response, "info", "Draft discarded")


@require(can_view_qa_settings)
@require_GET
def template_preview(request, pk):
    """What the institution will see (inputs disabled), for any version."""
    template = _template_or_404(pk)
    return render(request, "accreditation/settings/_preview.html",
                  {"template": template, "sections": template.sections(), "field_types": engine.FIELD_TYPES})


# ===========================================================================
# Curriculum catalogue
# ===========================================================================
def _can_manage_catalogue(user):
    return user.is_superuser or user.has_perm("accreditation.manage_catalogue")


def _catalogue_ctx(request, **extra):
    from institutions.models import ProgrammeCategory

    f = request.GET if request.method == "GET" else request.POST
    qs = CatalogueItem.objects.select_related("category").order_by("programme_name", "kind", "sort_order", "name")
    show = f.get("show", "active")
    if show == "active":
        qs = qs.filter(is_active=True)
    if f.get("programme"):
        qs = qs.filter(programme_name=f["programme"])
    if f.get("kind") in ("facility", "equipment"):
        qs = qs.filter(kind=f["kind"])
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    from qa_core.ui import paginate

    page = paginate(request, qs) if request.method == "GET" else None
    ctx = {"items": page["rows"] if page else list(qs), "page": page, "filters": f, "show": show,
           "editable": _can_manage_catalogue(request.user),
           "programmes": sorted(set(CatalogueItem.objects.exclude(programme_name="")
                                    .values_list("programme_name", flat=True))),
           "categories": ProgrammeCategory.objects.filter(is_active=True).order_by("name"),
           "kinds": CatalogueItem.KIND_CHOICES,
           "count_active": CatalogueItem.objects.filter(is_active=True).count()}
    ctx.update(extra)
    return _ctx(request, "catalogue", **ctx)


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def catalogue(request):
    if request.method == "POST":
        from institutions.models import ProgrammeCategory

        p = request.POST
        try:
            category = ProgrammeCategory.objects.filter(pk=p.get("category") or 0).first()
            item = builder.create_item(user=request.user, category=category, programme_name=p.get("programme_name", ""),
                                       kind=p.get("kind", ""), name=p.get("name", ""),
                                       required_quantity=p.get("required_quantity", ""), unit=p.get("unit", "no."),
                                       weight=p.get("weight", ""), min_spec=p.get("min_spec", ""),
                                       is_critical=p.get("is_critical") == "on")
        except (ValidationError, PermissionDenied) as exc:
            response = render(request, "accreditation/settings/_catalogue_panel.html",
                              _catalogue_ctx(request, error=_msg(exc), values=p), status=422)
            return toast(response, "error", "Not added", _msg(exc))
        response = render(request, "accreditation/settings/_catalogue_panel.html", _catalogue_ctx(request))
        return toast(response, "success", f"{item.name} added")
    return render_qa(request, "accreditation/settings/catalogue.html", _catalogue_ctx(request),
                     {"qa-panel": "accreditation/settings/_catalogue_panel.html",
                      "cat-rows": "accreditation/settings/_catalogue_rows.html"})


@require(can_view_qa_settings)
@require_POST
def catalogue_item(request, pk):
    """op=revise (new version with the new quantity/unit/name) or op=retire."""
    item = CatalogueItem.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    op = request.POST.get("op", "revise")
    try:
        if op == "retire":
            builder.retire_item(item, user=request.user)
            title = f"{item.name} retired"
        else:
            new = builder.revise_item(item, user=request.user, required_quantity=request.POST.get("required_quantity", ""),
                                      unit=request.POST.get("unit"), name=request.POST.get("name"),
                                      weight=request.POST.get("weight"), min_spec=request.POST.get("min_spec"),
                                      is_critical=request.POST.get("is_critical") == "on")
            title = f"{new.name}: version {new.version}"
    except (ValidationError, PermissionDenied) as exc:
        response = render(request, "accreditation/settings/_catalogue_panel.html",
                          _catalogue_ctx(request, error=_msg(exc)), status=422)
        return toast(response, "error", "Not saved", _msg(exc))
    response = render(request, "accreditation/settings/_catalogue_panel.html", _catalogue_ctx(request))
    return toast(response, "success", title, "SSQs already submitted keep the old figures.")


# ===========================================================================
# Rate table (Phase 4, SOP-03 cost sheets)
# ===========================================================================
def _can_manage_rates(user):
    return user.is_superuser or user.has_perm("accreditation.manage_rate_table")


def _rates_ctx(request, **extra):
    from staff.models import Department, StateOfOrigin

    from staff.models import StateOfOrigin

    from .models import RateTable

    f = request.GET if request.method == "GET" else request.POST
    qs = RateTable.objects.select_related("department", "state").order_by(
        "state__name", "department__name", "sort_order", "code")
    if f.get("show", "active") == "active":
        qs = qs.filter(is_active=True)
    import config

    confirmed = str(config.get("qa.cost.rates_confirmed", False)).strip().lower() in ("1", "true", "yes", "on")
    ctx = {"rates": list(qs), "filters": f, "show": f.get("show", "active"), "editable": _can_manage_rates(request.user),
           "kinds": RateTable.KIND_CHOICES, "units": RateTable.UNIT_CHOICES, "rates_confirmed": confirmed,
           "departments": Department.objects.filter(is_active=True).order_by("name")
           if hasattr(Department, "is_active") else Department.objects.order_by("name"),
           "states": StateOfOrigin.objects.filter(is_active=True).order_by("name")}
    ctx.update(extra)
    return _ctx(request, "rates", **ctx)


def _money(raw):
    from decimal import Decimal, InvalidOperation

    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError("Enter the amount in naira, e.g. 35000.") from None
    if value < 0 or value > Decimal("100000000"):
        raise ValidationError("Enter an amount between 0 and ₦100,000,000.")
    return value.quantize(Decimal("0.01"))


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def rates(request):
    from django.utils.text import slugify

    from staff.models import Department

    from staff.models import StateOfOrigin

    from .models import RateTable

    if request.method == "POST":
        p = request.POST
        try:
            if not _can_manage_rates(request.user):
                raise PermissionDenied("Only holders of accreditation.manage_rate_table can change rates.")
            code = slugify(p.get("code", ""))[:60]
            role = p.get("role", "").strip()
            if not code or not role:
                raise ValidationError("Enter a code and who the rate is for.")
            dept = Department.objects.filter(pk=p.get("department") or 0).first()
            state = StateOfOrigin.objects.filter(pk=p.get("state") or 0).first()
            if RateTable.objects.filter(code=code, department=dept, state=state).exists():
                raise ValidationError(f"A rate with code {code} already exists"
                                      f"{' for ' + dept.name if dept else ''}"
                                      f"{' in ' + state.name if state else ''}; edit it instead.")
            kind = p.get("allowance_type") if p.get("allowance_type") in dict(RateTable.KIND_CHOICES) else "other"
            unit = p.get("unit") if p.get("unit") in dict(RateTable.UNIT_CHOICES) else RateTable.PER_DAY
            rate = RateTable.objects.create(code=code, role=role[:120], allowance_type=kind, unit=unit,
                                            amount=_money(p.get("amount", "")), department=dept, state=state,
                                            sort_order=(RateTable.objects.count() + 1) * 10)
        except (ValidationError, PermissionDenied) as exc:
            response = render(request, "accreditation/settings/_rates_panel.html",
                              _rates_ctx(request, error=_msg(exc), values=p), status=422)
            return toast(response, "error", "Not added", _msg(exc))
        response = render(request, "accreditation/settings/_rates_panel.html", _rates_ctx(request))
        return toast(response, "success", f"{rate.role} added")
    return render_qa(request, "accreditation/settings/rates.html", _rates_ctx(request),
                     {"qa-panel": "accreditation/settings/_rates_panel.html"})


@require(can_view_qa_settings)
@require_POST
def rate_edit(request, pk):
    """op=save (amount / role / unit) or op=toggle (retire / restore). Lines
    already on a cost sheet keep the amount they were priced at."""
    from staff.models import StateOfOrigin

    from .models import RateTable

    rate = RateTable.objects.filter(pk=pk).first()
    if rate is None:
        raise Http404
    p = request.POST
    try:
        if not _can_manage_rates(request.user):
            raise PermissionDenied("Only holders of accreditation.manage_rate_table can change rates.")
        if p.get("op") == "toggle":
            rate.is_active = not rate.is_active
            rate.save(update_fields=["is_active", "updated_at"])
            title = f"{rate.role} {'restored' if rate.is_active else 'retired'}"
        else:
            rate.amount = _money(p.get("amount", rate.amount))
            if p.get("role", "").strip():
                rate.role = p["role"].strip()[:120]
            if p.get("unit") in dict(RateTable.UNIT_CHOICES):
                rate.unit = p["unit"]
            rate.save()
            title = f"{rate.role}: ₦{rate.amount:,.2f}"
    except (ValidationError, PermissionDenied) as exc:
        response = render(request, "accreditation/settings/_rates_panel.html",
                          _rates_ctx(request, error=_msg(exc)), status=422)
        return toast(response, "error", "Not saved", _msg(exc))
    response = render(request, "accreditation/settings/_rates_panel.html", _rates_ctx(request))
    return toast(response, "success", title, "Cost lines already priced keep their amounts.")


# ===========================================================================
# Phase 8: Settings → Approval types (B18; manage_approval_types)
# ===========================================================================
def _can_manage_types(user):
    return user.is_superuser or user.has_perm("accreditation.manage_approval_types")


def _types_ctx(request, **extra):
    from .models import ApprovalType

    ctx = {"types": list(ApprovalType.objects.order_by("sort_order", "name")),
           "editable": _can_manage_types(request.user), "statuses": ApprovalType.PROGRAMME_STATUS_CHOICES}
    ctx.update(extra)
    return _ctx(request, "approval_types", **ctx)


def _months(raw):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValidationError("Enter the duration in whole months.") from None
    if not 1 <= value <= 240:
        raise ValidationError("The duration must be 1 to 240 months.")
    return value


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def approval_types(request):
    """List and add approval types. Changing a duration affects decisions
    recorded from now on; a programme's expiry date, once applied, is kept."""
    from django.utils.text import slugify

    from .models import ApprovalType

    if request.method == "POST":
        p = request.POST
        try:
            if not _can_manage_types(request.user):
                raise PermissionDenied("Only holders of accreditation.manage_approval_types can change approval types.")
            code = slugify(p.get("code", "")).replace("-", "_")[:40]
            name = (p.get("name") or "").strip()[:120]
            if not code or not name:
                raise ValidationError("Enter a code and a name.")
            if ApprovalType.objects.filter(code=code).exists():
                raise ValidationError(f"An approval type with code {code} already exists; edit it instead.")
            status = p.get("programme_status") if p.get("programme_status") in dict(
                ApprovalType.PROGRAMME_STATUS_CHOICES) else "accredited"
            item = ApprovalType.objects.create(code=code, name=name, duration_months=_months(p.get("duration_months")),
                                               programme_status=status,
                                               description=(p.get("description") or "").strip()[:255],
                                               sort_order=(ApprovalType.objects.count() + 1) * 10)
        except (ValidationError, PermissionDenied) as exc:
            response = render(request, "accreditation/settings/_approval_types_panel.html",
                              _types_ctx(request, error=_msg(exc), values=p), status=422)
            return toast(response, "error", "Not added", _msg(exc))
        response = render(request, "accreditation/settings/_approval_types_panel.html", _types_ctx(request))
        return toast(response, "success", f"{item.name} added")
    return render_qa(request, "accreditation/settings/approval_types.html", _types_ctx(request),
                     {"qa-panel": "accreditation/settings/_approval_types_panel.html"})


@require(can_view_qa_settings)
@require_POST
def approval_type_edit(request, pk):
    from .models import ApprovalType

    item = ApprovalType.objects.filter(pk=pk).first()
    if item is None:
        raise Http404
    p = request.POST
    try:
        if not _can_manage_types(request.user):
            raise PermissionDenied("Only holders of accreditation.manage_approval_types can change approval types.")
        if p.get("op") == "toggle":
            item.is_active = not item.is_active
            item.save(update_fields=["is_active", "updated_at"])
            title = f"{item.name} {'switched on' if item.is_active else 'switched off'}"
        else:
            item.duration_months = _months(p.get("duration_months", item.duration_months))
            if (p.get("name") or "").strip():
                item.name = p["name"].strip()[:120]
            if p.get("programme_status") in dict(ApprovalType.PROGRAMME_STATUS_CHOICES):
                item.programme_status = p["programme_status"]
            item.save()
            title = f"{item.name}: {item.duration_months} months"
    except (ValidationError, PermissionDenied) as exc:
        response = render(request, "accreditation/settings/_approval_types_panel.html",
                          _types_ctx(request, error=_msg(exc)), status=422)
        return toast(response, "error", "Not saved", _msg(exc))
    response = render(request, "accreditation/settings/_approval_types_panel.html", _types_ctx(request))
    return toast(response, "success", title, "Programmes already decided keep their expiry dates.")
