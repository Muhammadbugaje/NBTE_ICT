"""Internal QA screens (NBTE host, `base.html` shell) — Phase 1.

    Settings → QA      rules, formulas (with test panel), reference formats,
                       workflows (read-only), notification templates
    Pending confirmations (two-step items in the user's scope)

Every list honours URL filters and the Show-all limit; every view renders a
full page for normal requests and a partial for HTMX (§20). Object views look
the object up inside the user's scope and answer 404 — never 403 — when it is
outside it (Master Plan §4, layer 3).
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from config.models import ConfigEntry
from core.models import AuditLogEntry

from . import actions, notify, references, settings_service
from .formula import FormulaError, evaluate
from .models import (
    ActionPolicy,
    FlowDefinition,
    FlowTransition,
    NotificationRule,
    NotificationTemplate,
    PendingAction,
    ReferenceFormat,
)
from .permissions import can_view_pending, can_view_qa_settings, require
from .settings_catalogue import GROUP_ORDER
from .ui import paginate, render_qa, toast

TABS = [
    ("rules", "Rules & thresholds", "qa:settings_rules"),
    ("formulas", "Formulas", "qa:settings_formulas"),
    ("fees", "Fees & bank accounts", "qa_set:fees"),
    ("methods", "Payment methods", "qa_set:methods"),
    ("documents", "Document rules", "qa_set:documents"),
    ("lookups", "Lookups", "qa_set:lookups"),
    ("ssq_templates", "SSQ templates", "qa_set:ssq_templates"),
    ("catalogue", "Curriculum catalogue", "qa_set:catalogue"),
    ("rates", "Rate table", "qa_set:rates"),
    ("complaints", "Complaints", "qa_set:complaints"),  # Phase 7
    ("references", "Reference formats", "qa:settings_references"),
    ("workflows", "Workflows", "qa:settings_workflows"),
    ("templates", "Notification templates", "qa:settings_templates"),
]


def _impersonating(request):
    return bool(getattr(request, "is_impersonating", False))


def _settings_context(request, tab, **extra):
    qa = settings_service.qa_entries()
    ctx = {
        "tabs": [{"key": k, "label": label, "url": reverse(name), "active": k == tab} for k, label, name in TABS],
        "active_tab": tab,
        "pending_mine": PendingAction.objects.filter(initiated_by=request.user,
                                                     status=PendingAction.PENDING).count(),
        "hero": {
            "settings": qa.count(),
            "dual": qa.filter(dual_control=True).count(),
            "locked": qa.filter(editable_by="config.locked_by_policy").count(),
            "flows": FlowDefinition.objects.filter(published_at__isnull=False).count(),
        },
    }
    ctx.update(extra)
    return ctx


# ===========================================================================
# Settings → QA: rules and thresholds
# ===========================================================================
@require(can_view_qa_settings)
@require_GET
def settings_home(request):
    return redirect("qa:settings_rules")


def _rules_queryset(request):
    qs = settings_service.qa_entries().exclude(value_type="formula")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(key__icontains=q) | Q(description__icontains=q))
    group = request.GET.get("group") or ""
    if group:
        qs = qs.filter(group=group)
    vtype = request.GET.get("type") or ""
    if vtype:
        qs = qs.filter(value_type=vtype)
    control = request.GET.get("control") or ""
    if control == "dual":
        qs = qs.filter(dual_control=True)
    elif control == "direct":
        qs = qs.filter(dual_control=False).exclude(editable_by="config.locked_by_policy")
    elif control == "locked":
        qs = qs.filter(editable_by="config.locked_by_policy")
    entries = list(qs.order_by("group", "key"))
    if request.GET.get("editable") == "1":
        entries = [e for e in entries if settings_service.can_edit(request.user, e)[0]]
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    entries.sort(key=lambda e: (order.get(e.group, 99), e.key))
    return entries


def _decorate(entries, user):
    pending = dict(
        PendingAction.objects.filter(
            policy_id="config.update_setting", status=PendingAction.PENDING,
            content_type=ContentType.objects.get_for_model(ConfigEntry),
            object_id__in=[str(e.pk) for e in entries],
        ).values_list("object_id", "reference")
    )
    for e in entries:
        e.can_edit, e.cannot_edit_reason = settings_service.can_edit(user, e)
        e.control = settings_service.control_label(e)
        e.display = settings_service.display_value(e.value_type, e.value)
        e.pending_reference = pending.get(str(e.pk), "")
    return entries


@require(can_view_qa_settings)
@require_GET
def settings_rules(request):
    entries = _rules_queryset(request)
    # Phase 7: with no filter, the portal registration settings sit in their
    # own card at the top of the page instead of in the list.
    unfiltered = not any(request.GET.get(k) for k in ("q", "group", "type", "control", "editable"))
    featured = []
    if unfiltered:
        featured = [e for e in entries if e.key in FEATURED_KEYS]
        featured.sort(key=lambda e: FEATURED_KEYS.index(e.key))
        entries = [e for e in entries if e.key not in FEATURED_KEYS]
        _decorate(featured, request.user)
    page = paginate(request, entries)
    _decorate(page["rows"], request.user)
    all_entries = settings_service.qa_entries().exclude(value_type="formula")
    ctx = _settings_context(
        request, "rules", page=page, filters=request.GET,
        groups=[g for g in GROUP_ORDER if all_entries.filter(group=g).exists()],
        types=sorted(set(all_entries.values_list("value_type", flat=True))),
        featured=featured,
        registration_on=any(e.key == FEATURED_KEYS[0] and e.display == "Yes" for e in featured),
        counts={
            "total": all_entries.count(),
            "dual": all_entries.filter(dual_control=True).count(),
            "locked": all_entries.filter(editable_by="config.locked_by_policy").count(),
        },
    )
    return render_qa(request, "qa_core/settings/rules.html", ctx, {
        "qa-panel": "qa_core/settings/_rules_panel.html",
        "qa-rows": "qa_core/settings/_rules_rows.html",
    })


FEATURED_KEYS = ["qa.portal.self_registration_enabled", "qa.portal.registration_contact_email",
                 "qa.portal.registration_contact_phone"]


def _entry_or_404(pk):
    entry = settings_service.qa_entries().filter(pk=pk).first()
    if entry is None:
        raise Http404("No such QA setting.")
    return entry


@require(can_view_qa_settings)
@require_GET
def setting_row(request, pk):
    entry = _decorate([_entry_or_404(pk)], request.user)[0]
    return render(request, "qa_core/settings/_rule_row.html", {"e": entry})


@require(can_view_qa_settings)
@require_GET
def setting_edit(request, pk):
    entry = _decorate([_entry_or_404(pk)], request.user)[0]
    if not entry.can_edit:
        raise PermissionDenied(entry.cannot_edit_reason)
    return render(request, "qa_core/settings/_rule_edit.html", {"e": entry, "value": entry.value})


@require(can_view_qa_settings)
@require_POST
def setting_save(request, pk):
    entry = _entry_or_404(pk)
    posted = request.POST.get("value", "")
    if entry.value_type == "bool":
        posted = "true" if request.POST.get("value") in ("true", "on", "1") else "false"
    new_value = settings_service.normalise_input(entry, posted)
    reason = (request.POST.get("reason") or "").strip()
    try:
        outcome, obj = settings_service.save_setting(request.user, entry, new_value, reason=reason,
                                                     impersonating=_impersonating(request))
    except (ValidationError, PermissionDenied) as exc:
        entry = _decorate([entry], request.user)[0]
        messages = getattr(exc, "messages", None) or [str(exc)]
        flat = []
        for m in messages:
            flat.extend(m if isinstance(m, list) else [m])
        return render(request, "qa_core/settings/_rule_edit.html",
                      {"e": entry, "value": posted, "errors": flat, "reason": reason})
    entry = _decorate([_entry_or_404(pk)], request.user)[0]
    response = render(request, "qa_core/settings/_rule_row.html", {"e": entry, "just_saved": outcome})
    if entry.key == FEATURED_KEYS[0] and outcome != "pending":  # keep the registration card's state line in step
        on = entry.display == "Yes"
        response.content += (
            '<span id="sh-registration-state" hx-swap-oob="true" class="sh-feature__state%s">%s</span>'
            % (" is-on" if on else "", "Registration is open on the portal" if on
               else "Registration is managed by NBTE")).encode()
    if outcome == "pending":
        return toast(response, "info", f"Sent for confirmation — {obj.reference}",
                     "A second officer must confirm this change before it applies.")
    return toast(response, "success", f"Saved {entry.key}")


@require(can_view_qa_settings)
@require_GET
def setting_history(request, pk):
    entry = _entry_or_404(pk)
    history = AuditLogEntry.objects.filter(model_label="config.ConfigEntry", object_pk=str(entry.pk)) \
        .order_by("-timestamp")[:20]
    pending = PendingAction.objects.filter(
        policy_id="config.update_setting", content_type=ContentType.objects.get_for_model(ConfigEntry),
        object_id=str(entry.pk)).select_related("initiated_by", "decided_by")[:10]
    return render(request, "qa_core/settings/_rule_history.html",
                  {"e": entry, "history": history, "pending": pending})


# ===========================================================================
# Formulas (with the "test with sample values" panel)
# ===========================================================================
def _formula_entries():
    entries = list(settings_service.qa_entries().filter(value_type="formula").order_by("group", "key"))
    for e in entries:
        data = e.parsed()
        e.expr = data["expr"]
        e.vars = data.get("vars", [])
        e.returns = data.get("returns", "decimal")
    return entries


@require(can_view_qa_settings)
@require_GET
def settings_formulas(request):
    entries = _formula_entries()
    q = (request.GET.get("q") or "").strip().lower()
    if q:
        entries = [e for e in entries if q in e.key.lower() or q in e.description.lower() or q in e.expr.lower()]
    _decorate(entries, request.user)
    ctx = _settings_context(request, "formulas", entries=entries, filters=request.GET)
    return render_qa(request, "qa_core/settings/formulas.html", ctx,
                     {"qa-panel": "qa_core/settings/_formulas_panel.html"})


def _sample_value(raw):
    raw = (raw or "").strip()
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return raw


@require(can_view_qa_settings)
@require_POST
def formula_test(request, pk):
    entry = _entry_or_404(pk)
    if entry.value_type != "formula":
        raise Http404
    data = entry.parsed()
    expr = (request.POST.get("expr") or data["expr"]).strip()
    variables = {}
    for name in data.get("vars", []):
        variables[name] = _sample_value(request.POST.get(f"var_{name}", ""))
    ctx = {"e": entry, "expr": expr, "inputs": variables}
    try:
        result, used = evaluate(expr, variables, declared=data.get("vars", []), returns=data.get("returns"),
                                _cfg_stack=[entry.key])
    except FormulaError as exc:
        ctx["error"] = str(exc)
    else:
        ctx["result"] = result
        ctx["used_cfg"] = used
        if isinstance(result, Decimal):
            ctx["result_display"] = f"{result:,.2f}" if result == result.quantize(Decimal("0.01")) else f"{result:,}"
    return render(request, "qa_core/settings/_formula_result.html", ctx)


@require(can_view_qa_settings)
@require_POST
def formula_save(request, pk):
    entry = _entry_or_404(pk)
    if entry.value_type != "formula":
        raise Http404
    expr = (request.POST.get("expr") or "").strip()
    reason = (request.POST.get("reason") or "").strip()
    new_value = settings_service.normalise_input(entry, expr)
    error = ""
    pa = None
    try:
        outcome, obj = settings_service.save_setting(request.user, entry, new_value, reason=reason,
                                                     impersonating=_impersonating(request))
        pa = obj if outcome == "pending" else None
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        error = "; ".join(str(m) for m in msgs)
    e = [x for x in _decorate(_formula_entries(), request.user) if x.pk == entry.pk][0]
    response = render(request, "qa_core/settings/_formula_card.html",
                      {"e": e, "error": error, "draft_expr": expr if error else "", "reason": reason})
    if pa is not None:
        return toast(response, "info", f"Sent for confirmation — {pa.reference}",
                     "The formula changes only when a second officer confirms it.")
    if not error:
        return toast(response, "success", f"Saved {entry.key}")
    return response


# ===========================================================================
# Reference formats
# ===========================================================================
def _reference_rows(user):
    rows = list(ReferenceFormat.objects.order_by("code"))
    ct = ContentType.objects.get_for_model(ReferenceFormat)
    pending = dict(PendingAction.objects.filter(
        policy_id="qa_core.update_reference_format", status=PendingAction.PENDING, content_type=ct,
    ).values_list("object_id", "reference"))
    can_manage = user.has_perm("qa_core.manage_reference_formats")
    for f in rows:
        try:
            f.example = references.example(f)
        except references.ReferenceError_ as exc:
            f.example = f"⚠ {exc}"
        f.pending_reference = pending.get(str(f.pk), "")
        f.can_edit = can_manage and not f.pending_reference
        last = f.counters.order_by("-period_key").first()
        f.last_issued = last.last_value if last else 0
    return rows


@require(can_view_qa_settings)
@require_GET
def settings_references(request):
    ctx = _settings_context(request, "references", rows=_reference_rows(request.user))
    return render_qa(request, "qa_core/settings/references.html", ctx,
                     {"qa-panel": "qa_core/settings/_references_panel.html"})


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def reference_edit(request, pk):
    fmt = get_object_or_404(ReferenceFormat, pk=pk)
    row = [r for r in _reference_rows(request.user) if r.pk == fmt.pk][0]
    if request.method == "GET":
        if request.GET.get("cancel"):
            return render(request, "qa_core/settings/_reference_row.html", {"f": row})
        if not row.can_edit:
            raise PermissionDenied("You cannot change this reference format.")
        return render(request, "qa_core/settings/_reference_edit.html", {"f": row, "data": row,
                                                                         "choices": _ref_choices()})
    payload = {
        "pattern": (request.POST.get("pattern") or "").strip(),
        "reset_period": request.POST.get("reset_period") or fmt.reset_period,
        "check_digit": request.POST.get("check_digit") or fmt.check_digit,
        "allowed_chars": (request.POST.get("allowed_chars") or "").strip(),
        "sequence_scope": request.POST.get("sequence_scope") or fmt.sequence_scope,
        "is_active": request.POST.get("is_active") == "on",
    }
    try:
        pa = actions.initiate("qa_core.update_reference_format", actor=request.user, target=fmt,
                              payload=payload, reason=(request.POST.get("reason") or "").strip(),
                              impersonating=_impersonating(request))
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        return render(request, "qa_core/settings/_reference_edit.html",
                      {"f": row, "data": payload, "errors": msgs, "choices": _ref_choices()})
    row = [r for r in _reference_rows(request.user) if r.pk == fmt.pk][0]
    response = render(request, "qa_core/settings/_reference_row.html", {"f": row})
    return toast(response, "info", f"Sent for confirmation — {pa.reference}",
                 "The new pattern applies to new references once a second officer confirms it.")


def _ref_choices():
    return {
        "reset": ReferenceFormat.RESET_CHOICES,
        "check": ReferenceFormat.CHECK_CHOICES,
        "scope": ReferenceFormat.SCOPE_CHOICES,
    }


@require(can_view_qa_settings)
@require_POST
def reference_check(request):
    value = (request.POST.get("reference") or "").strip()
    ctx = {"value": value}
    if value:
        ctx["valid"] = references.verify(value, "mod97")
    return render(request, "qa_core/settings/_reference_check.html", ctx)


# ===========================================================================
# Workflows (read-only in Phase 1)
# ===========================================================================
@require(can_view_qa_settings)
@require_GET
def settings_workflows(request):
    flows = (FlowDefinition.objects.annotate(n_stages=Count("stages", distinct=True),
                                             n_transitions=Count("transitions", distinct=True))
             .order_by("code", "-version"))
    ctx = _settings_context(request, "workflows", flows=flows)
    return render_qa(request, "qa_core/settings/workflows.html", ctx,
                     {"qa-panel": "qa_core/settings/_workflows_panel.html"})


@require(can_view_qa_settings)
@require_GET
def workflow_detail(request, pk):
    flow = get_object_or_404(FlowDefinition, pk=pk)
    stages = list(flow.stages.all())
    transitions = list(FlowTransition.objects.filter(flow=flow).select_related("from_stage", "to_stage"))
    by_from = {}
    for t in transitions:
        by_from.setdefault(t.from_stage_id, []).append(t)
    terminal_success = [s for s in stages if s.is_terminal_success]
    guarded = {}
    for s in terminal_success:
        guarded[s.code] = [t for t in transitions if t.to_stage_id == s.pk]
    for s in stages:
        s.out = by_from.get(s.pk, [])
    ctx = _settings_context(request, "workflows", flow=flow, stages=stages, transitions=transitions,
                            into_success=guarded)
    return render_qa(request, "qa_core/settings/workflow_detail.html", ctx,
                     {"qa-panel": "qa_core/settings/_workflow_detail_panel.html"})


# ===========================================================================
# Notification templates
# ===========================================================================
def _can_manage_templates(user):
    return user.has_perm("qa_core.manage_notification_templates")


@require(can_view_qa_settings)
@require_GET
def settings_templates(request):
    qs = NotificationTemplate.objects.annotate(n_rules=Count("rules")).order_by("code", "channel")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(subject__icontains=q) | Q(description__icontains=q))
    channel = request.GET.get("channel") or ""
    if channel:
        qs = qs.filter(channel=channel)
    active = request.GET.get("active") or ""
    if active in ("1", "0"):
        qs = qs.filter(is_active=active == "1")
    event = request.GET.get("event") or ""
    if event:
        qs = qs.filter(rules__event_code=event).distinct()
    page = paginate(request, qs)
    ctx = _settings_context(
        request, "templates", page=page, filters=request.GET,
        channels=NotificationTemplate.CHANNEL_CHOICES,
        events=sorted(set(NotificationRule.objects.values_list("event_code", flat=True))),
        can_manage=_can_manage_templates(request.user),
    )
    return render_qa(request, "qa_core/settings/templates.html", ctx, {
        "qa-panel": "qa_core/settings/_templates_panel.html",
        "qa-rows": "qa_core/settings/_templates_rows.html",
    })


SAMPLE_CONTEXT = {
    "reference": "PA-2026-000123", "action": "Change a dual-control QA setting",
    "target": "qa.fee.programme_processing", "status": "Applied", "initiator": "Amina Officer",
    "decider": "Bello Director", "remark": "Matches the approved fee memo.", "reason": "Annex A update",
    "expires_at": "19 Oct 2026, 15:00", "failure": "", "institution": "Demo Polytechnic, Kaduna",
}


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def template_edit(request, pk):
    template = get_object_or_404(NotificationTemplate, pk=pk)
    can_manage = _can_manage_templates(request.user)
    if request.method == "GET":
        if request.GET.get("cancel"):
            return render(request, "qa_core/settings/_template_row.html", {"t": template, "can_manage": can_manage})
        return render(request, "qa_core/settings/_template_edit.html",
                      {"t": template, "subject": template.subject, "body": template.body,
                       "can_manage": can_manage, "sample": SAMPLE_CONTEXT})
    if not can_manage:
        raise PermissionDenied("You cannot edit notification templates.")
    subject = request.POST.get("subject", "")
    body = request.POST.get("body", "")
    try:
        notify.validate_template(subject, body)
    except notify.TemplateRenderError as exc:
        return render(request, "qa_core/settings/_template_edit.html",
                      {"t": template, "subject": subject, "body": body, "errors": [str(exc)],
                       "can_manage": can_manage, "sample": SAMPLE_CONTEXT})
    template.subject = subject
    template.body = body
    template.save()
    response = render(request, "qa_core/settings/_template_row.html", {"t": template, "can_manage": can_manage})
    return toast(response, "success", f"Saved template {template.code}")


@require(can_view_qa_settings)
@require_POST
def template_preview(request, pk):
    template = get_object_or_404(NotificationTemplate, pk=pk)
    subject, body, error = "", "", ""
    try:
        subject = notify.render_string(request.POST.get("subject", template.subject), SAMPLE_CONTEXT)
        body = notify.render_string(request.POST.get("body", template.body), SAMPLE_CONTEXT)
    except notify.TemplateRenderError as exc:
        error = str(exc)
    return render(request, "qa_core/settings/_template_preview.html",
                  {"t": template, "subject": subject, "body": body, "error": error})


@require(can_view_qa_settings)
@require_POST
def template_toggle(request, pk):
    """Optimistic quick action (§20): the switch flips at once in the browser;
    this answers with the server's truth and the UI reconciles."""
    template = get_object_or_404(NotificationTemplate, pk=pk)
    if not _can_manage_templates(request.user):
        raise PermissionDenied("You cannot change notification templates.")
    template.is_active = request.POST.get("is_active") in ("on", "true", "1")
    template.save(update_fields=["is_active", "updated_at", "updated_by"])
    response = HttpResponse(status=204)
    return toast(response, "success",
                 f"{template.code} [{template.get_channel_display()}] {'on' if template.is_active else 'off'}")


# ===========================================================================
# Pending confirmations
# ===========================================================================
AGE_FILTERS = {"24h": 1, "3d": 3, "7d": 7}


@require(can_view_pending)
@require_GET
def pending_list(request):
    qs = actions.visible_to(request.user)
    status = request.GET.get("status", "pending")
    if status and status != "all":
        qs = qs.filter(status=status)
    action = request.GET.get("action") or ""
    if action:
        qs = qs.filter(policy_id=action)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(target_label__icontains=q) | Q(reason__icontains=q)
                       | Q(initiated_by__email__icontains=q) | Q(initiated_by__first_name__icontains=q)
                       | Q(initiated_by__last_name__icontains=q))
    who = request.GET.get("who") or ""
    if who == "mine":
        qs = qs.filter(initiated_by=request.user)
    elif who == "others":
        qs = qs.exclude(initiated_by=request.user)
    age = request.GET.get("age") or ""
    if age in AGE_FILTERS:
        qs = qs.filter(initiated_at__gte=timezone.now() - datetime.timedelta(days=AGE_FILTERS[age]))
    qs = qs.order_by("expires_at" if status == "pending" else "-initiated_at", "-id")
    page = paginate(request, qs)
    impersonating = _impersonating(request)
    for pa in page["rows"]:
        pa.can_decide, pa.decide_reason = actions.can_decide(pa, request.user, impersonating=impersonating)
    visible = actions.visible_to(request.user)
    ctx = {
        "page": page, "filters": request.GET, "status": status,
        "policies": ActionPolicy.objects.order_by("label"),
        "status_choices": PendingAction.STATUS_CHOICES,
        "counts": {
            "pending": visible.filter(status=PendingAction.PENDING).count(),
            "mine": visible.filter(status=PendingAction.PENDING, initiated_by=request.user).count(),
            "overdue": visible.filter(status=PendingAction.PENDING, expires_at__lte=timezone.now()).count(),
        },
    }
    return render_qa(request, "qa_core/pending/list.html", ctx, {
        "qa-panel": "qa_core/pending/_list_panel.html",
        "qa-rows": "qa_core/pending/_rows.html",
    })


def _pending_or_404(request, pk):
    pa = actions.visible_to(request.user).filter(pk=pk).first()
    if pa is None:
        raise Http404("No such pending confirmation in your scope.")
    return pa


def _detail_context(request, pa, **extra):
    handler = actions.get_handler(pa.policy_id)
    target = actions._load_target(pa)
    try:
        # Create-type actions (Add TVET, bulk import) have no target yet; their
        # handlers preview from the payload alone.
        diff = handler.preview(target, pa.payload) if (target is not None or not pa.object_id) else []
    except Exception:
        diff = []
    if pa.status != PendingAction.PENDING and pa.before_snapshot:
        # Show what was proposed against what it replaced, not today's value.
        diff = [actions.DiffRow(row.label, _snapshot_before(pa, row), row.after) for row in diff]
    can, why = actions.can_decide(pa, request.user, impersonating=_impersonating(request))
    stale = bool(target is not None and pa.target_version and pa.is_open
                 and actions._version_of(target) != pa.target_version)
    ctx = {"pa": pa, "diff": diff, "can_decide": can, "decide_reason": why, "target": target,
           "stale": stale, "can_withdraw": pa.is_open and pa.initiated_by_id == request.user.pk,
           "history": AuditLogEntry.objects.filter(model_label="qa_core.PendingAction", object_pk=str(pa.pk))
           .order_by("timestamp")[:30]}
    ctx.update(extra)
    return ctx


def _snapshot_before(pa, row):
    if row.label == "Value" and "value" in pa.before_snapshot:
        return settings_service.display_value(pa.before_snapshot.get("value_type", "str"),
                                              pa.before_snapshot["value"])
    key = row.label.lower().replace(" ", "_")
    return str(pa.before_snapshot.get(key, row.before))


@require(can_view_pending)
@require_GET
def pending_detail(request, pk):
    pa = _pending_or_404(request, pk)
    return render_qa(request, "qa_core/pending/detail.html", _detail_context(request, pa),
                     {"qa-panel": "qa_core/pending/_detail_panel.html"})


def _decide(request, pk, verb):
    pa = _pending_or_404(request, pk)
    remark = (request.POST.get("remark") or "").strip()
    error = ""
    try:
        if verb == "confirm":
            pa = actions.confirm(pa, request.user, remark=remark, impersonating=_impersonating(request))
        elif verb == "reject":
            pa = actions.reject(pa, request.user, remark=remark, impersonating=_impersonating(request))
        else:
            pa = actions.withdraw(pa, request.user)
    except (ValidationError, PermissionDenied) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        error = "; ".join(str(m) for m in msgs)
    pa.refresh_from_db()
    ctx = _detail_context(request, pa, error=error, remark=remark if error else "")
    response = render_qa(request, "qa_core/pending/detail.html", ctx,
                         {"qa-panel": "qa_core/pending/_detail_panel.html"})
    if error:
        return toast(response, "error", "Not done", error)
    titles = {
        PendingAction.APPLIED: ("success", f"{pa.reference} confirmed and applied"),
        PendingAction.FAILED: ("error", f"{pa.reference} could not be applied"),
        PendingAction.REJECTED: ("warning", f"{pa.reference} rejected"),
        PendingAction.WITHDRAWN: ("info", f"{pa.reference} withdrawn"),
    }
    kind, title = titles.get(pa.status, ("info", f"{pa.reference}: {pa.get_status_display()}"))
    return toast(response, kind, title, pa.failure_detail)


@require(can_view_pending)
@require_POST
def pending_confirm(request, pk):
    return _decide(request, pk, "confirm")


@require(can_view_pending)
@require_POST
def pending_reject(request, pk):
    return _decide(request, pk, "reject")


@require(can_view_pending)
@require_POST
def pending_withdraw(request, pk):
    return _decide(request, pk, "withdraw")


# ===========================================================================
# QA e-mail outbox (Phase 2: e-mails are logged, not sent)
# ===========================================================================
def _can_read_outbox(user):
    """Superusers, the oversight board and QA settings administrators (they
    configure e-mail delivery). The log holds one-time set-password links, so
    it is never shown more widely."""
    from .permissions import can_read_outbox

    return can_read_outbox(user)


@require(_can_read_outbox)
@require_GET
def outbox(request):
    from .mail import delivery_mode
    from .models import OutboundEmail

    qs = OutboundEmail.objects.all()
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(recipients__icontains=q) | Q(body_text__icontains=q))
    category = request.GET.get("category", "")
    if category:
        qs = qs.filter(category=category)
    days = {"1d": 1, "7d": 7, "30d": 30}.get(request.GET.get("period", ""))
    if days:
        import datetime

        from django.utils import timezone as tz

        qs = qs.filter(created_at__gte=tz.now() - datetime.timedelta(days=days))
    to = (request.GET.get("to") or "").strip().lower()
    if to:
        qs = qs.filter(recipients__icontains=to)
    page = paginate(request, qs.order_by("-created_at", "-id"))
    ctx = {"page": page, "filters": request.GET, "mode": delivery_mode(),
           "categories": sorted(set(OutboundEmail.objects.values_list("category", flat=True)))}
    return render_qa(request, "qa_core/outbox.html", ctx, {"qa-panel": "qa_core/_outbox_panel.html",
                                                           "qa-rows": "qa_core/_outbox_rows.html"})
