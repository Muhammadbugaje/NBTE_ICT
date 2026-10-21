"""Memo route management UI.

Mounted at `/manage/messages/`. Requires
`messaging.change_memoroute` or `messaging.manage_memoroute`, or
superuser.
"""
from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import Role, User

from .management_forms import (
    GrantBroadcastForm,
    MemoRouteForm,
    MemoRouteRuleForm,
    MemoRouteStepForm,
)
from .models import Memo, MemoRoute, MemoRouteRule, MemoRouteStep

logger = logging.getLogger(__name__)


BROADCAST_PERMISSION_CODENAME = "send_memo_broadcast"


# ===========================================================================
# Permissions
# ===========================================================================
def _can_manage(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return (
        user.has_perm("messaging.change_memoroute")
        or user.has_perm("messaging.manage_memoroute")
    )


def _require_manage(user):
    if not _can_manage(user):
        raise PermissionDenied("You don't have permission to manage memo routes.")


def _wants_json(request):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return "application/json" in (request.headers.get("Accept") or "")


# ===========================================================================
# Position helpers — identical pattern to workflow admin
# ===========================================================================
def _next_position(route):
    last = route.steps.aggregate(m=Max("position"))["m"]
    return (last or 0) + 1


def _normalize_positions(route_id):
    """Renumber steps densely, using a two-phase update to avoid
    UNIQUE(route_id, position) violations on SQLite."""
    steps = list(
        MemoRouteStep.objects.filter(route_id=route_id)
        .order_by("position", "pk")
        .values_list("pk", "position")
    )
    if not steps:
        return
    if all(pos == idx + 1 for idx, (_pk, pos) in enumerate(steps)):
        return

    park_base = (
        MemoRouteStep.objects.filter(route_id=route_id)
        .aggregate(m=Max("position"))["m"] or 0
    ) + 1000

    for offset, (pk, _old) in enumerate(steps):
        MemoRouteStep.objects.filter(pk=pk).update(position=park_base + offset)
    for final_pos, (pk, _old) in enumerate(steps, start=1):
        MemoRouteStep.objects.filter(pk=pk).update(position=final_pos)


# ===========================================================================
# Overview
# ===========================================================================
@login_required
def home(request):
    _require_manage(request.user)

    routes = MemoRoute.objects.annotate(
        step_count=Count("steps", distinct=True),
        rule_count=Count("rules", distinct=True),
    )
    routes_recent = routes.order_by("-updated_at")[:5]

    rules_recent = (
        MemoRouteRule.objects
        .filter(is_active=True)
        .select_related("route")
        .order_by("-priority", "-updated_at")[:6]
    )

    counts = {
        "routes":          MemoRoute.objects.count(),
        "active_routes":   MemoRoute.objects.filter(is_active=True).count(),
        "rules":           MemoRouteRule.objects.count(),
        "active_rules":    MemoRouteRule.objects.filter(is_active=True).count(),
        "steps":           MemoRouteStep.objects.count(),
        "broadcast_users": _broadcast_users_qs().count(),
    }

    # Warnings — things the admin should look at.
    warnings = []

    routes_no_steps = routes.filter(is_active=True, step_count=0).count()
    if routes_no_steps:
        warnings.append({
            "level": "warn",
            "title": "Active routes without steps",
            "text": (
                f"{routes_no_steps} active route"
                f"{'s' if routes_no_steps != 1 else ''} "
                f"{'have' if routes_no_steps != 1 else 'has'} no steps. "
                "Memos routed through them will publish immediately."
            ),
            "url": reverse("messaging_manage:route_list"),
            "link_label": "Review routes",
        })

    rules_broken = MemoRouteRule.objects.filter(
        is_active=True, route__is_active=False,
    ).count()
    if rules_broken:
        warnings.append({
            "level": "warn",
            "title": "Active rules pointing at inactive routes",
            "text": (
                f"{rules_broken} rule"
                f"{'s' if rules_broken != 1 else ''} "
                f"{'point' if rules_broken != 1 else 'points'} at a route "
                "that's been switched off."
            ),
            "url": reverse("messaging_manage:rule_list"),
            "link_label": "Review rules",
        })

    if counts["routes"] == 0:
        warnings.append({
            "level": "info",
            "title": "Start by creating your first route",
            "text": (
                "A route is a sequence of approval steps. Once you have one, "
                "create rules to decide when it applies."
            ),
            "url": reverse("messaging_manage:route_create"),
            "link_label": "Create a route",
        })

    return render(request, "manage/messages/home.html", {
        "counts":   counts,
        "routes":   routes_recent,
        "rules":    rules_recent,
        "warnings": warnings,
    })


# ===========================================================================
# Routes
# ===========================================================================
@login_required
def route_list(request):
    _require_manage(request.user)

    q = (request.GET.get("q") or "").strip()

    qs = (
        MemoRoute.objects
        .annotate(
            step_count=Count("steps", distinct=True),
            rule_count=Count("rules", distinct=True),
        )
        .order_by("name")
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))

    return render(request, "manage/messages/route_list.html", {
        "routes": qs,
        "q":      q,
    })


@login_required
def route_detail(request, pk):
    _require_manage(request.user)

    route = get_object_or_404(
        MemoRoute.objects.prefetch_related(
            Prefetch(
                "steps",
                queryset=MemoRouteStep.objects.select_related(
                    "approver_role", "approver_user",
                ).order_by("position", "pk"),
            ),
        ),
        pk=pk,
    )

    using = (
        MemoRouteRule.objects
        .filter(route=route)
        .order_by("-priority", "name")
    )

    return render(request, "manage/messages/route_detail.html", {
        "route": route,
        "steps": list(route.steps.all()),
        "using": using,
        "approver_kinds": MemoRouteStep.APPROVER_CHOICES,
    })


@login_required
def route_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = MemoRouteForm(request.POST)
        if form.is_valid():
            route = form.save()
            messages.success(request, f"Route “{route.name}” created.")
            return redirect("messaging_manage:route_detail", pk=route.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = MemoRouteForm()
    return render(request, "manage/messages/route_form.html", {
        "form": form, "mode": "create", "route": None,
    })


@login_required
def route_update(request, pk):
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=pk)
    if request.method == "POST":
        form = MemoRouteForm(request.POST, instance=route)
        if form.is_valid():
            form.save()
            messages.success(request, "Route updated.")
            return redirect("messaging_manage:route_detail", pk=route.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = MemoRouteForm(instance=route)
    return render(request, "manage/messages/route_form.html", {
        "form": form, "mode": "update", "route": route,
    })


@login_required
def route_delete(request, pk):
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=pk)

    if request.method == "POST":
        if route.rules.exists():
            messages.error(
                request,
                "This route is still used by a rule. Reassign or delete "
                "those rules first.",
            )
            return redirect("messaging_manage:route_detail", pk=route.pk)

        name = route.name
        route.delete()
        messages.success(request, f"Route “{name}” deleted.")
        return redirect("messaging_manage:route_list")

    return render(request, "manage/messages/route_confirm_delete.html",
                  {"route": route})


# ===========================================================================
# Steps
# ===========================================================================
@login_required
def step_create(request, route_pk):
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=route_pk)

    if request.method == "POST":
        form = MemoRouteStepForm(request.POST, route=route)
        if form.is_valid():
            step = form.save(commit=False)
            step.route = route
            if not step.position:
                step.position = _next_position(route)
            step.save()
            messages.success(request, f"Step “{step.name}” added.")
            return redirect("messaging_manage:route_detail", pk=route.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = MemoRouteStepForm(
            route=route,
            initial={"position": _next_position(route)},
        )

    return render(request, "manage/messages/step_form.html", {
        "form": form, "route": route, "step": None, "mode": "create",
    })


@login_required
def step_update(request, route_pk, step_pk):
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=route_pk)
    step = get_object_or_404(MemoRouteStep, pk=step_pk, route=route)

    if request.method == "POST":
        form = MemoRouteStepForm(request.POST, instance=step, route=route)
        if form.is_valid():
            form.save()
            messages.success(request, "Step updated.")
            return redirect("messaging_manage:route_detail", pk=route.pk)
        messages.error(request, "Please fix the errors below.")
    else:
        form = MemoRouteStepForm(instance=step, route=route)

    return render(request, "manage/messages/step_form.html", {
        "form": form, "route": route, "step": step, "mode": "update",
    })


@login_required
@require_POST
def step_delete(request, route_pk, step_pk):
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=route_pk)
    step  = get_object_or_404(MemoRouteStep, pk=step_pk, route=route)
    ajax  = _wants_json(request)

    with transaction.atomic():
        name = step.name
        step.delete()
        _normalize_positions(route.pk)

    if ajax:
        return JsonResponse({"ok": True})
    messages.success(request, f"Step “{name}” deleted.")
    return redirect("messaging_manage:route_detail", pk=route.pk)


@login_required
@require_POST
def step_move(request, route_pk, step_pk):
    """Swap one step with its neighbour. AJAX-friendly."""
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=route_pk)
    step  = get_object_or_404(MemoRouteStep, pk=step_pk, route=route)
    ajax  = _wants_json(request)

    def _ok(moved=None, noop=False):
        if ajax:
            payload = {"ok": True}
            if moved: payload["moved"] = moved
            if noop:  payload["noop"] = True
            return JsonResponse(payload)
        if noop:
            messages.info(request, "Already at the edge — nothing to move.")
        else:
            messages.success(request, f"Step “{step.name}” moved {moved}.")
        return redirect("messaging_manage:route_detail", pk=route.pk)

    def _err(msg, status=400):
        if ajax:
            return JsonResponse({"ok": False, "error": msg}, status=status)
        messages.error(request, msg)
        return redirect("messaging_manage:route_detail", pk=route.pk)

    direction = (request.POST.get("direction") or "").lower()
    if direction not in {"up", "down"}:
        return _err("Direction must be 'up' or 'down'.")

    with transaction.atomic():
        ordered = list(
            MemoRouteStep.objects
            .select_for_update()
            .filter(route=route)
            .order_by("position", "pk")
        )
        idx = next((i for i, s in enumerate(ordered) if s.pk == step.pk), None)
        if idx is None:
            return _err("Step not in route.", status=404)

        if direction == "up" and idx == 0:
            return _ok(noop=True)
        if direction == "down" and idx == len(ordered) - 1:
            return _ok(noop=True)

        other_idx = idx - 1 if direction == "up" else idx + 1
        other = ordered[other_idx]

        _normalize_positions(route.pk)
        step.refresh_from_db(fields=["position"])
        other.refresh_from_db(fields=["position"])

        pos_self, pos_other = step.position, other.position
        temp = _next_position(route)

        MemoRouteStep.objects.filter(pk=step.pk).update(position=temp)
        MemoRouteStep.objects.filter(pk=other.pk).update(position=pos_self)
        MemoRouteStep.objects.filter(pk=step.pk).update(position=pos_other)

    return _ok(moved=direction)


@login_required
@require_POST
def route_reorder(request, pk):
    """Drag-drop reorder. Body JSON: {"order": [step_pk, ...]}"""
    _require_manage(request.user)
    route = get_object_or_404(MemoRoute, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    order = payload.get("order") or []
    if not isinstance(order, list):
        return JsonResponse({"ok": False, "error": "order must be a list"}, status=400)

    try:
        order = [int(x) for x in order]
    except (TypeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "order must contain integer step ids"},
            status=400,
        )

    valid_pks = set(route.steps.values_list("pk", flat=True))
    if set(order) != valid_pks or len(order) != len(valid_pks):
        return JsonResponse(
            {"ok": False, "error": "order must contain exactly the route's steps"},
            status=400,
        )

    with transaction.atomic():
        park_base = (
            MemoRouteStep.objects.filter(route=route)
            .aggregate(m=Max("position"))["m"] or 0
        ) + 1000

        for offset, s_pk in enumerate(order):
            MemoRouteStep.objects.filter(pk=s_pk).update(position=park_base + offset)
        for i, s_pk in enumerate(order, start=1):
            MemoRouteStep.objects.filter(pk=s_pk).update(position=i)

    return JsonResponse({"ok": True})


# ===========================================================================
# Rules
# ===========================================================================
@login_required
def rule_list(request):
    _require_manage(request.user)

    q = (request.GET.get("q") or "").strip()
    route_filter = (request.GET.get("route") or "").strip()

    qs = (
        MemoRouteRule.objects
        .select_related("route")
        .order_by("-priority", "name")
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(route__name__icontains=q))
    if route_filter:
        qs = qs.filter(route__code=route_filter)

    return render(request, "manage/messages/rule_list.html", {
        "rules":   qs,
        "q":       q,
        "route_filter": route_filter,
        "routes":  MemoRoute.objects.order_by("name"),
    })


@login_required
def rule_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = MemoRouteRuleForm(request.POST)
        if form.is_valid():
            rule = form.save()
            messages.success(request, f"Rule “{rule.name}” created.")
            return redirect("messaging_manage:rule_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = MemoRouteRuleForm()
    return render(request, "manage/messages/rule_form.html", {
        "form": form, "mode": "create", "rule": None,
    })


@login_required
def rule_update(request, pk):
    _require_manage(request.user)
    rule = get_object_or_404(MemoRouteRule, pk=pk)
    if request.method == "POST":
        form = MemoRouteRuleForm(request.POST, instance=rule)
        if form.is_valid():
            form.save()
            messages.success(request, f"Rule “{rule.name}” updated.")
            return redirect("messaging_manage:rule_list")
        messages.error(request, "Please fix the errors below.")
    else:
        form = MemoRouteRuleForm(instance=rule)
    return render(request, "manage/messages/rule_form.html", {
        "form": form, "mode": "update", "rule": rule,
    })


@login_required
def rule_delete(request, pk):
    _require_manage(request.user)
    rule = get_object_or_404(MemoRouteRule, pk=pk)
    if request.method == "POST":
        name = rule.name
        rule.delete()
        messages.success(request, f"Rule “{name}” deleted.")
        return redirect("messaging_manage:rule_list")
    return render(request, "manage/messages/rule_confirm_delete.html",
                  {"rule": rule})


# ===========================================================================
# Permissions — grant/revoke the broadcast permission
# ===========================================================================
def _broadcast_permission():
    """Return the messaging.send_memo_broadcast Permission, or None."""
    try:
        ct = ContentType.objects.get(app_label="messaging", model="memo")
        return Permission.objects.get(
            content_type=ct, codename=BROADCAST_PERMISSION_CODENAME,
        )
    except (ContentType.DoesNotExist, Permission.DoesNotExist):
        return None


def _broadcast_users_qs():
    """Users who currently hold the broadcast permission directly."""
    perm = _broadcast_permission()
    if perm is None:
        return User.objects.none()
    return (
        User.objects
        .filter(user_permissions=perm, is_active=True)
        .select_related("role", "department")
        .order_by("first_name", "last_name", "email")
    )


@login_required
def permission_panel(request):
    """Grant and revoke the `send_memo_broadcast` permission.

    This is the one-stop place to give individual Directors (or any
    other user) the ability to address memos to All Staff, All Directors,
    or a whole department.
    """
    _require_manage(request.user)

    perm = _broadcast_permission()
    if perm is None:
        messages.error(
            request,
            "The broadcast permission isn't registered yet. "
            "Run `python manage.py migrate messaging` first.",
        )
        return redirect("messaging_manage:home")

    # Currently-granted users.
    granted_qs = _broadcast_users_qs()
    granted_ids = list(granted_qs.values_list("pk", flat=True))

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "grant":
            form = GrantBroadcastForm(request.POST, exclude_users=granted_ids)
            if form.is_valid():
                target = form.cleaned_data["user"]
                target.user_permissions.add(perm)
                messages.success(
                    request,
                    f"{target.get_full_name() or target.email} can now "
                    f"address memos broadly.",
                )
                return redirect("messaging_manage:permission_panel")
            # Fall through with form errors on the page.

        elif action == "revoke":
            try:
                user_id = int(request.POST.get("user_id") or 0)
            except (TypeError, ValueError):
                user_id = 0
            target = User.objects.filter(pk=user_id, is_active=True).first()
            if target is not None:
                target.user_permissions.remove(perm)
                messages.success(
                    request,
                    f"Revoked broadcast privileges from "
                    f"{target.get_full_name() or target.email}.",
                )
            return redirect("messaging_manage:permission_panel")

        else:
            messages.error(request, "Unknown action.")
            return redirect("messaging_manage:permission_panel")
    else:
        form = GrantBroadcastForm(exclude_users=granted_ids)

    # Superusers and the ES always have broadcast power — surface them
    # as "implicit" so the admin understands.
    implicit_qs = User.objects.filter(
        Q(is_superuser=True) | Q(role__code="executive-secretary"),
        is_active=True,
    ).select_related("role", "department").distinct()

    return render(request, "manage/messages/permissions.html", {
        "form":           form,
        "granted":        granted_qs,
        "implicit":       implicit_qs,
        "permission":     perm,
    })


# ===========================================================================
# Resolution tester — see what route + chain a memo would get
# ===========================================================================
@login_required
def resolution_tester(request):
    """Pick a sender + target kind + target; see the resulting chain."""
    _require_manage(request.user)

    from . import routing

    sender_id = (request.GET.get("sender") or "").strip()
    target_kind = (request.GET.get("target_kind") or "").strip()
    target_user_id = (request.GET.get("target_user") or "").strip()
    target_dept_id = (request.GET.get("target_dept") or "").strip()

    result = None
    if sender_id and target_kind:
        sender = User.objects.filter(pk=sender_id, is_active=True).first()
        target_user = User.objects.filter(pk=target_user_id).first() if target_user_id else None
        target_dept = None
        if target_dept_id:
            from staff.models import Department
            target_dept = Department.objects.filter(pk=target_dept_id).first()

        if sender is not None:
            # Build a lightweight, unsaved memo for the resolver.
            preview = Memo(
                sender=sender,
                subject="(preview)",
                body="(preview)",
                visibility=target_kind,
                target_user=target_user,
                department=target_dept,
            )
            try:
                route = routing.route_for_memo(preview)
            except Exception as exc:
                result = {"error": str(exc)}
            else:
                preview.route = route
                steps_preview = []
                if route is not None:
                    # Same rules as the engine and the memo page (routing.evaluate_step).
                    steps_preview = routing.simulate_chain(preview)

                result = {
                    "route":  route,
                    "steps":  steps_preview,
                    "sender": sender,
                    "target_kind": target_kind,
                }

    return render(request, "manage/messages/test.html", {
        "result":      result,
        "sender_id":   sender_id,
        "target_kind": target_kind,
        "target_user_id": target_user_id,
        "target_dept_id": target_dept_id,
        "target_kinds": Memo.VISIBILITY_CHOICES,
        "users": (
            User.objects.filter(is_active=True)
            .select_related("department", "role")
            .order_by("first_name", "last_name", "email")[:500]
        ),
        "departments": _departments(),
    })


def _departments():
    try:
        from staff.models import Department
        return Department.objects.filter(is_active=True).order_by("name")
    except Exception:
        from django.db.models import QuerySet
        return []


# ===========================================================================
# Help
# ===========================================================================
@login_required
def help_page(request):
    _require_manage(request.user)
    return render(request, "manage/messages/help.html", {
        "same_rank_choices": MemoRoute.SAME_RANK_CHOICES,
        "approver_kinds":    MemoRouteStep.APPROVER_CHOICES,
        "target_kinds":      Memo.VISIBILITY_CHOICES,
        "roles":             Role.objects.order_by("-rank", "name"),
    })