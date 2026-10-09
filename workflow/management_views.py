"""Workflow management UI — the custom admin panel for chains, types,
categories, and roles.

Mounted under `/manage/workflow/`. Requires `workflow.change_approvalchain`
(or superuser) for every view.

Kept separate from `workflow/views.py` (which powers the runtime request
detail pages) so the two don't tangle.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import Role, User
from staff.models import Department

from .management_forms import (
    ApprovalChainForm,
    ApprovalStepForm,
    RequestCategoryForm,
    RequestTypeForm,
)
from .models import (
    ApprovalChain,
    ApprovalStep,
    RequestCategory,
    RequestType,
)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
def _can_manage_workflow(user):
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.has_perm("workflow.change_approvalchain")


def _require_manage(user):
    if not _can_manage_workflow(user):
        raise PermissionDenied(
            "You don't have permission to manage workflows."
        )


def _wants_json(request):
    """True if the caller wants JSON (fetch/XHR), False for a plain form POST."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return "application/json" in (request.headers.get("Accept") or "")

# ---------------------------------------------------------------------------
# Position helpers — keep ApprovalStep.position dense and unique-safe
# ---------------------------------------------------------------------------
def _next_position(chain):
    """Return one past the highest position currently in the chain."""
    last = chain.steps.aggregate(m=Max("position"))["m"]
    return (last or 0) + 1


def _normalize_positions(chain_id):
    """Renumber every step in a chain to a dense 1..N sequence.

    Uses a two-phase update (park at high temporary values, then write
    final values) so it never transiently violates
    UNIQUE(chain_id, position) on backends that lack deferred unique
    constraints (SQLite in particular).
    """
    steps = list(
        ApprovalStep.objects.filter(chain_id=chain_id)
        .order_by("position", "pk")
        .values_list("pk", "position")
    )
    if not steps:
        return

    # Already dense? Nothing to do.
    if all(pos == idx + 1 for idx, (_pk, pos) in enumerate(steps)):
        return

    # Park everything out of the way, then write final values.
    park_base = (
        ApprovalStep.objects.filter(chain_id=chain_id)
        .aggregate(m=Max("position"))["m"] or 0
    ) + 1000

    for offset, (pk, _old) in enumerate(steps):
        ApprovalStep.objects.filter(pk=pk).update(position=park_base + offset)

    for final_pos, (pk, _old) in enumerate(steps, start=1):
        ApprovalStep.objects.filter(pk=pk).update(position=final_pos)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
@login_required
def manage_home(request):
    _require_manage(request.user)

    # ---- Chains with counts --------------------------------------------
    chains_with_counts = ApprovalChain.objects.annotate(
        step_count=Count("steps", distinct=True),
        type_count=Count("request_types", distinct=True),
    )
    chains_recent = chains_with_counts.order_by("-updated_at")[:5]

    # ---- Active request types -------------------------------------------
    types_recent = (
        RequestType.objects
        .filter(is_active=True)
        .select_related("category", "handling_department", "approval_chain")
        .order_by("category__sort_order", "sort_order", "name")[:6]
    )

    # ---- Counts ---------------------------------------------------------
    counts = {
        "chains":         ApprovalChain.objects.count(),
        "active_chains":  ApprovalChain.objects.filter(is_active=True).count(),
        "types":          RequestType.objects.count(),
        "active_types":   RequestType.objects.filter(is_active=True).count(),
        "categories":     RequestCategory.objects.count(),
        "active_categories": RequestCategory.objects.filter(is_active=True).count(),
        "roles":          Role.objects.count(),
    }

    # ---- Warnings — things the admin should look at ---------------------
    warnings = []

    chains_no_steps = chains_with_counts.filter(is_active=True, step_count=0).count()
    if chains_no_steps:
        warnings.append({
            "level": "warn",
            "title": "Active chains without steps",
            "text": (
                f"{chains_no_steps} active chain"
                f"{'s' if chains_no_steps != 1 else ''} "
                f"{'have' if chains_no_steps != 1 else 'has'} no steps. "
                "Requests using those chains will fail when they try to advance."
            ),
            "url": reverse("workflow_manage:chain_list"),
            "link_label": "Review chains",
        })

    types_broken = RequestType.objects.filter(
        is_active=True, approval_chain__is_active=False,
    ).count()
    if types_broken:
        warnings.append({
            "level": "warn",
            "title": "Request types pointing at inactive chains",
            "text": (
                f"{types_broken} active request type"
                f"{'s' if types_broken != 1 else ''} "
                f"{'point' if types_broken != 1 else 'points'} at a chain "
                "that's been switched off."
            ),
            "url": reverse("workflow_manage:type_list"),
            "link_label": "Review types",
        })

    if counts["chains"] == 0:
        warnings.append({
            "level": "info",
            "title": "Start by creating your first chain",
            "text": (
                "A chain is a sequence of approval steps. Once you have one, "
                "you can create request types that use it."
            ),
            "url": reverse("workflow_manage:chain_create"),
            "link_label": "Create a chain",
        })

    return render(request, "manage/workflow/home.html", {
        "counts":   counts,
        "chains":   chains_recent,
        "types":    types_recent,
        "warnings": warnings,
    })


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------
@login_required
def chain_list(request):
    _require_manage(request.user)

    q = (request.GET.get("q") or "").strip()

    qs = (
        ApprovalChain.objects
        .annotate(
            step_count=Count("steps", distinct=True),
            type_count=Count("request_types", distinct=True),
        )
        .order_by("name")
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))

    return render(request, "manage/workflow/chain_list.html", {
        "chains": qs,
        "q":      q,
    })


@login_required
def chain_detail(request, pk):
    _require_manage(request.user)

    chain = get_object_or_404(
        ApprovalChain.objects.prefetch_related(
            Prefetch(
                "steps",
                queryset=ApprovalStep.objects.select_related(
                    "approver_role", "approver_user",
                ).order_by("position", "pk"),
            ),
        ),
        pk=pk,
    )

    # Which request types use this chain (either side)?
    using = (
        RequestType.objects
        .filter(Q(approval_chain=chain) | Q(requester_chain=chain))
        .select_related("category", "handling_department")
        .order_by("category__sort_order", "name")
    )

    return render(request, "manage/workflow/chain_detail.html", {
        "chain":      chain,
        "steps":      list(chain.steps.all()),
        "using":      using,
        "step_kinds": ApprovalStep.KIND_CHOICES,
    })


@login_required
def chain_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = ApprovalChainForm(request.POST)
        if form.is_valid():
            chain = form.save()
            messages.success(request, f"Chain “{chain.name}” created.")
            return redirect("workflow_manage:chain_detail", pk=chain.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = ApprovalChainForm()
    return render(request, "manage/workflow/chain_form.html", {
        "form": form, "mode": "create", "chain": None,
    })


@login_required
def chain_update(request, pk):
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=pk)
    if request.method == "POST":
        form = ApprovalChainForm(request.POST, instance=chain)
        if form.is_valid():
            form.save()
            messages.success(request, "Chain updated.")
            return redirect("workflow_manage:chain_detail", pk=chain.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = ApprovalChainForm(instance=chain)
    return render(request, "manage/workflow/chain_form.html", {
        "form": form, "mode": "update", "chain": chain,
    })


@login_required
def chain_delete(request, pk):
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=pk)
    if request.method == "POST":
        if chain.request_types.exists() or RequestType.objects.filter(
            requester_chain=chain,
        ).exists():
            messages.error(
                request,
                "This chain is still in use by a request type. "
                "Reassign those first.",
            )
            return redirect("workflow_manage:chain_detail", pk=chain.pk)
        name = chain.name
        chain.delete()
        messages.success(request, f"Chain “{name}” deleted.")
        return redirect("workflow_manage:chain_list")
    return render(request, "manage/workflow/chain_confirm_delete.html",
                  {"chain": chain})


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
@login_required
def step_create(request, chain_pk):
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=chain_pk)

    if request.method == "POST":
        form = ApprovalStepForm(request.POST, chain=chain)
        if form.is_valid():
            step = form.save(commit=False)
            step.chain = chain
            if not step.position:
                step.position = _next_position(chain)
            step.save()
            messages.success(request, f"Step “{step.name}” added.")
            return redirect("workflow_manage:chain_detail", pk=chain.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = ApprovalStepForm(
            chain=chain,
            initial={"position": _next_position(chain)},
        )

    return render(request, "manage/workflow/step_form.html", {
        "form": form, "chain": chain, "step": None, "mode": "create",
    })


@login_required
def step_update(request, chain_pk, step_pk):
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=chain_pk)
    step = get_object_or_404(ApprovalStep, pk=step_pk, chain=chain)

    if request.method == "POST":
        form = ApprovalStepForm(request.POST, instance=step, chain=chain)
        if form.is_valid():
            form.save()
            messages.success(request, "Step updated.")
            return redirect("workflow_manage:chain_detail", pk=chain.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = ApprovalStepForm(instance=step, chain=chain)

    return render(request, "manage/workflow/step_form.html", {
        "form": form, "chain": chain, "step": step, "mode": "update",
    })


@login_required
@require_POST
def step_delete(request, chain_pk, step_pk):
    """Delete one step, re-densify, then redirect (or JSON for AJAX)."""
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=chain_pk)
    step  = get_object_or_404(ApprovalStep, pk=step_pk, chain=chain)
    ajax  = _wants_json(request)

    with transaction.atomic():
        name = step.name
        step.delete()
        _normalize_positions(chain.pk)

    if ajax:
        return JsonResponse({"ok": True})
    messages.success(request, f"Step “{name}” deleted.")
    return redirect("workflow_manage:chain_detail", pk=chain.pk)



@login_required
@require_POST
def step_move(request, chain_pk, step_pk):
    """Swap one step with its immediate neighbour, then re-densify.

    Responds JSON to AJAX callers, redirects to the chain detail page
    for plain form submits.
    """
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=chain_pk)
    step  = get_object_or_404(ApprovalStep, pk=step_pk, chain=chain)
    ajax  = _wants_json(request)

    def _ok(moved=None, noop=False):
        if ajax:
            payload = {"ok": True}
            if moved:
                payload["moved"] = moved
            if noop:
                payload["noop"] = True
            return JsonResponse(payload)
        if noop:
            messages.info(request, "Already at the edge — nothing to move.")
        else:
            messages.success(request, f"Step “{step.name}” moved {moved}.")
        return redirect("workflow_manage:chain_detail", pk=chain.pk)

    def _err(msg, status=400):
        if ajax:
            return JsonResponse({"ok": False, "error": msg}, status=status)
        messages.error(request, msg)
        return redirect("workflow_manage:chain_detail", pk=chain.pk)

    direction = (request.POST.get("direction") or "").lower()
    if direction not in {"up", "down"}:
        return _err("Direction must be 'up' or 'down'.")

    with transaction.atomic():
        ordered = list(
            ApprovalStep.objects
            .select_for_update()
            .filter(chain=chain)
            .order_by("position", "pk")
        )
        idx = next((i for i, s in enumerate(ordered) if s.pk == step.pk), None)
        if idx is None:
            return _err("Step not in chain.", status=404)

        if direction == "up" and idx == 0:
            return _ok(noop=True)
        if direction == "down" and idx == len(ordered) - 1:
            return _ok(noop=True)

        other_idx = idx - 1 if direction == "up" else idx + 1
        other = ordered[other_idx]

        _normalize_positions(chain.pk)
        step.refresh_from_db(fields=["position"])
        other.refresh_from_db(fields=["position"])

        pos_self, pos_other = step.position, other.position
        temp = _next_position(chain)

        ApprovalStep.objects.filter(pk=step.pk).update(position=temp)
        ApprovalStep.objects.filter(pk=other.pk).update(position=pos_self)
        ApprovalStep.objects.filter(pk=step.pk).update(position=pos_other)

    return _ok(moved=direction)




@login_required
@require_POST
def chain_reorder(request, pk):
    """Drag-drop reorder endpoint. Body JSON: {"order": [step_pk, step_pk, …]}"""
    _require_manage(request.user)
    chain = get_object_or_404(ApprovalChain, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    order = payload.get("order") or []
    if not isinstance(order, list):
        return JsonResponse(
            {"ok": False, "error": "order must be a list"}, status=400,
        )

    # Normalise to ints so template/JSON string IDs still match.
    try:
        order = [int(x) for x in order]
    except (TypeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "order must contain integer step ids"},
            status=400,
        )

    valid_pks = set(chain.steps.values_list("pk", flat=True))
    if set(order) != valid_pks or len(order) != len(valid_pks):
        return JsonResponse(
            {"ok": False, "error": "order must contain exactly the chain's steps"},
            status=400,
        )

    with transaction.atomic():
        # Park at unique high values first — a shared temp value (e.g. 0)
        # would immediately violate UNIQUE(chain_id, position).
        park_base = (
            ApprovalStep.objects.filter(chain=chain)
            .aggregate(m=Max("position"))["m"] or 0
        ) + 1000

        for offset, s_pk in enumerate(order):
            ApprovalStep.objects.filter(pk=s_pk).update(
                position=park_base + offset,
            )
        for i, s_pk in enumerate(order, start=1):
            ApprovalStep.objects.filter(pk=s_pk).update(position=i)

    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# Request Types
# ---------------------------------------------------------------------------
@login_required
def type_list(request):
    _require_manage(request.user)

    q = (request.GET.get("q") or "").strip()
    cat = (request.GET.get("category") or "").strip()

    qs = (
        RequestType.objects
        .select_related("category", "handling_department",
                        "approval_chain", "requester_chain")
        .order_by("category__sort_order", "sort_order", "name")
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
    if cat:
        qs = qs.filter(category__code=cat)

    return render(request, "manage/workflow/type_list.html", {
        "types":      qs,
        "q":          q,
        "category":   cat,
        "categories": RequestCategory.objects.order_by("sort_order", "name"),
    })


@login_required
def type_update(request, pk):
    _require_manage(request.user)
    rt = get_object_or_404(RequestType, pk=pk)
    if request.method == "POST":
        form = RequestTypeForm(request.POST, instance=rt)
        if form.is_valid():
            form.save()
            messages.success(request, f"Type “{rt.name}” updated.")
            return redirect("workflow_manage:type_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = RequestTypeForm(instance=rt)
    return render(request, "manage/workflow/type_form.html", {
        "form": form, "rt": rt, "mode": "update",
    })


@login_required
def type_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = RequestTypeForm(request.POST)
        if form.is_valid():
            rt = form.save()
            messages.success(request, f"Type “{rt.name}” created.")
            return redirect("workflow_manage:type_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = RequestTypeForm()
    return render(request, "manage/workflow/type_form.html", {
        "form": form, "rt": None, "mode": "create",
    })


# ---------------------------------------------------------------------------
# Categories (quick list + create)
# ---------------------------------------------------------------------------
@login_required
def category_list(request):
    _require_manage(request.user)
    qs = (
        RequestCategory.objects
        .annotate(type_count=Count("request_types"))
        .order_by("sort_order", "name")
    )
    return render(request, "manage/workflow/category_list.html", {"categories": qs})


@login_required
def category_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = RequestCategoryForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Category “{cat.name}” created.")
            return redirect("workflow_manage:category_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = RequestCategoryForm()
    return render(request, "manage/workflow/category_form.html", {
        "form": form, "cat": None, "mode": "create",
    })


@login_required
def category_update(request, pk):
    _require_manage(request.user)
    cat = get_object_or_404(RequestCategory, pk=pk)
    if request.method == "POST":
        form = RequestCategoryForm(request.POST, instance=cat)
        if form.is_valid():
            form.save()
            messages.success(request, "Category updated.")
            return redirect("workflow_manage:category_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = RequestCategoryForm(instance=cat)
    return render(request, "manage/workflow/category_form.html", {
        "form": form, "cat": cat, "mode": "update",
    })


# ---------------------------------------------------------------------------
# Roles & hierarchy reference
# ---------------------------------------------------------------------------
@login_required
def role_reference(request):
    _require_manage(request.user)

    roles = list(Role.objects.order_by("-rank", "name"))

    # Precompute: role_id → list of active users
    users_by_role = {}
    for u in (
        User.objects.filter(is_active=True, role__isnull=False)
        .select_related("role", "department")
        .order_by("department__name", "first_name", "last_name")
    ):
        users_by_role.setdefault(u.role_id, []).append(u)

    # Attach to each role object so the template can read them directly
    for r in roles:
        r.users_list = users_by_role.get(r.pk, [])
        r.user_count = len(r.users_list)

    return render(request, "manage/workflow/roles.html", {
        "roles":       roles,
        "departments": Department.objects.filter(is_active=True).order_by("name"),
    })


# ---------------------------------------------------------------------------
# Resolution tester
# ---------------------------------------------------------------------------
@login_required
def resolution_tester(request):
    """Pick a request type and a user; see which runtime chain gets built."""
    _require_manage(request.user)

    from .services import _build_runtime_chain, resolve_approver
    from .models import Request  # local to avoid circular import

    result = None
    form_data = {
        "request_type":  request.GET.get("request_type") or "",
        "requester":     request.GET.get("requester") or "",
    }

    if form_data["request_type"] and form_data["requester"]:
        rt = RequestType.objects.filter(pk=form_data["request_type"]).first()
        ru = User.objects.filter(pk=form_data["requester"]).first()
        if rt and ru:
            # Build a lightweight preview request object — not saved.
            preview = Request(
                request_type=rt,
                requester=ru,
                receiving_department=rt.handling_department,
            )
            try:
                steps, handoff_index = _build_runtime_chain(preview)
            except Exception as exc:
                result = {"error": str(exc)}
            else:
                rows = []
                for i, s in enumerate(steps):
                    actor = resolve_approver(s, preview)
                    rows.append({
                        "index":         i,
                        "step":          s,
                        "actor":         actor,
                        "is_handoff":    (handoff_index is not None and i == handoff_index),
                        "is_synthetic":  not s.pk,
                        "key":           f"db:{s.pk}" if s.pk else f"syn:{s.name}",
                    })
                result = {
                    "steps":         rows,
                    "handoff_index": handoff_index,
                    "request_type":  rt,
                    "requester":     ru,
                }

    return render(request, "manage/workflow/resolution_tester.html", {
        "result":       result,
        "form_data":    form_data,
        "request_types": (
            RequestType.objects
            .filter(is_active=True)
            .select_related("category", "handling_department")
            .order_by("category__sort_order", "name")
        ),
        "users": (
            User.objects.filter(is_active=True)
            .select_related("department", "role")
            .order_by("first_name", "last_name")
        ),
    })


# ---------------------------------------------------------------------------
# Help / documentation
# ---------------------------------------------------------------------------
@login_required
def help_page(request):
    _require_manage(request.user)
    return render(request, "manage/workflow/help.html", {
        "roles":       Role.objects.order_by("-rank", "name"),
        "approver_kinds": ApprovalStep.APPROVER_CHOICES,
        "step_kinds":     ApprovalStep.KIND_CHOICES,
    })