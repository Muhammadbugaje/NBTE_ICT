"""Stage 3 — Group & Permission Management views.

Kept out of accounts/user_views.py on purpose (that module belongs to Stage 1).
Every view:

* checks a Django permission (auth.*_group or accounts.assign_groups),
* enforces the rank/ownership rules server-side through
  accounts.permissions_service (never by hiding a checkbox),
* writes one audit entry per change with actor, target, before and after,
  through `core.audit.log` — the same API Stage 1 used.
"""
from collections import OrderedDict

from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core import audit

from . import permissions_service as ps
from .group_forms import (
    GroupForm, RoleGroupForm, UserDirectPermissionForm, UserGroupForm, permission_queryset,
)
from .group_signals import views_are_logging
from .models import Role, User

PAGE_SIZE = 12


# ---------------------------------------------------------------------------
# access helpers
# ---------------------------------------------------------------------------
def _can_view(user):
    return user.is_superuser or any(
        user.has_perm(p) for p in (
            "auth.view_group", "auth.change_group", "auth.add_group",
            "accounts.assign_groups", "accounts.manage_roles",
        )
    )


def _require(user, *perms, any_of=False):
    if user.is_superuser:
        return
    checks = [user.has_perm(p) for p in perms]
    if (any_of and not any(checks)) or (not any_of and not all(checks)):
        raise PermissionDenied("You don't have permission to do that.")


def _deny(request, message, back_url=None):
    """403 with a clear, specific explanation (rank / ownership rules)."""
    return render(request, "accounts/groups/denied.html",
                  {"message": message, "back_url": back_url}, status=403)


def _visible_users(actor):
    """Same department scoping Stage 1 uses for user management (review M7)."""
    from .scoping import scope_to_department
    return scope_to_department(User.objects.all(), actor)


def _internal_group_ids():
    """Groups carried by at least one NBTE internal (staff-audience) role."""
    from . import audience
    staff_roles = audience.roles_for(audience.STAFF)
    return set(Group.objects.filter(roles__in=staff_roles).values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# presentation helpers
# ---------------------------------------------------------------------------
def human_label(codename):
    """add_invoice -> "Add invoice"; view_staffstatushistory -> "View staffstatushistory"."""
    text = codename.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _app_name(app_label):
    try:
        return str(django_apps.get_app_config(app_label).verbose_name).capitalize()
    except LookupError:
        return app_label.replace("_", " ").capitalize()


def build_picker(perms, selected=(), locked=lambda p: False, inherited=frozenset(), field="permissions"):
    """Group Permission rows by app for the accordion picker template."""
    selected = {getattr(p, "pk", p) for p in selected}
    by_app = OrderedDict()
    for p in perms:
        app = p.content_type.app_label
        entry = by_app.setdefault(app, {
            "app_label": app, "app_name": _app_name(app),
            "perms": [], "selected": 0, "total": 0,
        })
        label = ps.perm_label(p)
        is_sel = p.pk in selected
        entry["perms"].append({
            "id": p.pk, "codename": p.codename, "label": human_label(p.codename),
            "full": label, "name": p.name, "checked": is_sel,
            "locked": locked(p), "inherited": label in inherited, "field": field,
        })
        entry["total"] += 1
        entry["selected"] += 1 if is_sel else 0
    return sorted(by_app.values(), key=lambda a: a["app_name"])


def _apps_of(labels):
    out = OrderedDict()
    for lab in sorted(labels):
        app, code = lab.split(".", 1)
        out.setdefault(app, []).append(code)
    return [{"app_label": a, "app_name": _app_name(a), "codes": c, "count": len(c)} for a, c in out.items()]


def _diff(before, after):
    before, after = set(before), set(after)
    return {"added": sorted(after - before), "removed": sorted(before - after)}


def _audit(action, instance, actor, before, after, note=""):
    audit.log(action, instance, actor=actor, changes={
        "before": sorted(before), "after": sorted(after), **_diff(before, after),
    }, note=note)


# ---------------------------------------------------------------------------
# Group list
# ---------------------------------------------------------------------------
@login_required
def group_list(request):
    if not _can_view(request.user):
        raise PermissionDenied
    q = (request.GET.get("q") or "").strip()
    app = (request.GET.get("app") or "").strip()
    groups = (Group.objects.annotate(
                  member_count=Count("user", distinct=True),
                  perm_count=Count("permissions", distinct=True))
              .prefetch_related(Prefetch("roles", queryset=Role.objects.order_by("-rank", "name")))
              .select_related("profile")
              .order_by("name"))
    if q:
        groups = groups.filter(Q(name__icontains=q) | Q(profile__description__icontains=q))
    if app:
        groups = groups.filter(permissions__content_type__app_label=app).distinct()
    page = Paginator(groups, PAGE_SIZE).get_page(request.GET.get("page"))
    app_choices = sorted(
        {(a, _app_name(a)) for a in Permission.objects.filter(group__isnull=False)
         .values_list("content_type__app_label", flat=True)},
        key=lambda t: t[1],
    )
    return render(request, "accounts/groups/list.html", {
        "page": page, "q": q, "app": app, "app_choices": app_choices,
        "can_add": request.user.has_perm("auth.add_group"),
        "can_change": request.user.has_perm("auth.change_group"),
        "can_delete": request.user.has_perm("auth.delete_group"),
    })


# ---------------------------------------------------------------------------
# Group create / update
# ---------------------------------------------------------------------------
def _group_form_page(request, group=None):
    actor = request.user
    form = GroupForm(request.POST or None, instance=group, actor=actor)
    before = ps.group_permissions(group) if group else set()
    if request.method == "POST" and form.is_valid():
        with transaction.atomic(), views_are_logging():
            saved = form.save()
            after = ps.group_permissions(saved)
            _audit("create" if group is None else "update", saved, actor, before, after,
                   note=f"group {'created' if group is None else 'edited'} via groups UI")
        messages.success(request, f"Group “{saved.name}” saved.")
        if "save_add" in request.POST:
            return redirect("accounts:groups:create")
        return redirect("accounts:groups:detail", pk=saved.pk)

    if form.is_bound:   # re-show what the user ticked, not what is saved
        selected = [int(x) for x in request.POST.getlist("permissions") if x.isdigit()]
    else:
        selected = list(group.permissions.values_list("pk", flat=True)) if group else []
    picker = build_picker(
        permission_queryset(), selected=selected,
        locked=lambda p: not ps.can_edit_group_permission(actor, p),
    )
    return render(request, "accounts/groups/form.html", {
        "form": form, "group": group, "picker": picker,
        "selected_total": sum(a["selected"] for a in picker),
        "selected_apps": sum(1 for a in picker if a["selected"]),
    })


@login_required
def group_create(request):
    _require(request.user, "auth.add_group")
    return _group_form_page(request)


@login_required
def group_update(request, pk):
    _require(request.user, "auth.change_group")
    group = get_object_or_404(Group, pk=pk)
    if ps.group_outranks(request.user, group):
        return _deny(request, f"“{group.name}” is held by someone ranked above you, so you can't change it.",
                     reverse("accounts:groups:detail", args=[group.pk]))
    return _group_form_page(request, group)


# ---------------------------------------------------------------------------
# Group delete (POST; GET shows a no-JS confirmation page)
# ---------------------------------------------------------------------------
@login_required
def group_delete(request, pk):
    _require(request.user, "auth.delete_group")
    group = get_object_or_404(Group, pk=pk)
    if ps.group_outranks(request.user, group):
        return _deny(request, f"“{group.name}” is held by someone ranked above you, so you can't delete it.",
                     reverse("accounts:groups:detail", args=[group.pk]))
    if request.method == "POST":
        name, before = group.name, ps.group_permissions(group)
        roles = sorted(group.roles.values_list("code", flat=True))
        members = group.user_set.count()
        with transaction.atomic(), views_are_logging():
            audit.log("delete", group, actor=request.user, changes={
                "before": sorted(before), "after": [], "removed": sorted(before), "added": [],
                "roles": roles, "direct_members": members,
            }, note="group deleted via groups UI")
            group.delete()
        messages.success(request, f"Group “{name}” deleted.")
        return redirect("accounts:groups:list")
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    return render(request, "accounts/groups/confirm_delete.html", {"group": group})


# ---------------------------------------------------------------------------
# Group detail
# ---------------------------------------------------------------------------
@login_required
def group_detail(request, pk):
    if not _can_view(request.user):
        raise PermissionDenied
    group = get_object_or_404(Group.objects.select_related("profile"), pk=pk)
    roles = group.roles.order_by("-rank", "name").annotate(holders=Count("users", distinct=True))
    direct_members = _visible_users(request.user).filter(groups=group).select_related("role", "department")
    via_role = (_visible_users(request.user).filter(role__groups=group)
                .exclude(groups=group).select_related("role", "department"))
    activity = []
    if request.user.has_perm("core.view_auditlogentry"):
        from core.models import AuditLogEntry
        activity = AuditLogEntry.objects.filter(model_label="auth.Group", object_pk=str(group.pk))[:10]
    return render(request, "accounts/groups/detail.html", {
        "group": group, "roles": roles,
        "direct_members": direct_members.order_by("first_name", "last_name"),
        "via_role": via_role.order_by("first_name", "last_name"),
        "perm_apps": _apps_of(ps.group_permissions(group)),
        "perm_total": group.permissions.count(),
        "activity": activity,
        "can_change": request.user.has_perm("auth.change_group") and not ps.group_outranks(request.user, group),
        "can_delete": request.user.has_perm("auth.delete_group") and not ps.group_outranks(request.user, group),
    })


# ---------------------------------------------------------------------------
# Role -> groups
# ---------------------------------------------------------------------------
@login_required
def role_groups_edit(request, pk=None):
    _require(request.user, "accounts.assign_groups")
    roles = Role.objects.order_by("-rank", "name").annotate(
        group_count=Count("groups", distinct=True), holders=Count("users", distinct=True))
    if pk is None:
        first = roles.first()
        if first is None:
            return render(request, "accounts/roles/groups.html", {"roles": roles, "role": None})
        return redirect("accounts:groups:role_groups", pk=first.pk)
    role = get_object_or_404(Role, pk=pk)
    actor = request.user
    can_edit_role = ps.can_act_on_role(actor, role)
    if request.method == "POST":
        if not can_edit_role:
            return _deny(request, f"“{role.name}” (rank {role.rank}) is ranked above you, so you can't change its groups.",
                         reverse("accounts:groups:role_groups", args=[role.pk]))
        form = RoleGroupForm(request.POST, role=role, actor=actor)
        if form.is_valid():
            before_groups = set(role.groups.values_list("name", flat=True))
            before_perms = ps.role_permissions(role)
            with transaction.atomic(), views_are_logging():
                role.groups.set(form.cleaned_data["groups"])
                after_perms = ps.role_permissions(role)
                after_groups = set(role.groups.values_list("name", flat=True))
                audit.log("update", role, actor=actor, changes={
                    "groups_before": sorted(before_groups), "groups_after": sorted(after_groups),
                    "permissions_before": sorted(before_perms), "permissions_after": sorted(after_perms),
                    **_diff(before_perms, after_perms),
                }, note="role groups edited via groups UI")
            messages.success(request, f"Groups for “{role.name}” saved.")
            return redirect("accounts:groups:role_groups", pk=role.pk)
    else:
        form = RoleGroupForm(role=role, actor=actor)

    current_ids = set(role.groups.values_list("pk", flat=True))
    if form.is_bound:
        current_ids = {int(x) for x in request.POST.getlist("groups") if x.isdigit()}
    groups = Group.objects.order_by("name").annotate(perm_count=Count("permissions", distinct=True))
    group_rows = [{
        "group": g, "checked": g.pk in current_ids,
        "locked": not can_edit_role or not ps.can_assign_group(actor, role, g),
        "perms": sorted(ps.group_permissions(g)),
    } for g in groups]
    baseline = {ps.perm_label(p) for p in role.permissions.select_related("content_type")}
    return render(request, "accounts/roles/groups.html", {
        "roles": roles, "role": role, "form": form, "group_rows": group_rows,
        "can_edit_role": can_edit_role, "baseline": sorted(baseline),
        "baseline_apps": _apps_of(baseline),
        "grants": _apps_of(ps.role_permissions(role)),
        "grant_total": len(ps.role_permissions(role)),
    })


# ---------------------------------------------------------------------------
# User -> direct groups & permissions
# ---------------------------------------------------------------------------
@login_required
def user_groups_edit(request, pk):
    _require(request.user, "accounts.assign_groups")
    actor = request.user
    target = get_object_or_404(_visible_users(actor).select_related("role", "department"), pk=pk)
    can_act = ps.can_act_on_user(actor, target)
    if request.method == "POST":
        if not can_act:
            return _deny(request, f"{target.get_full_name() or target.email} is ranked above you (or is a superuser), so you can't change their access.",
                         reverse("accounts:groups:user_groups", args=[target.pk]))
        gform = UserGroupForm(request.POST, target=target, actor=actor)
        pform = UserDirectPermissionForm(request.POST, target=target, actor=actor)
        if gform.is_valid() and target.user_type != User.STAFF:
            # Strict separation: a portal account never gets a group that an
            # NBTE internal role carries (that would hand it internal access).
            internal = sorted(g.name for g in gform.cleaned_data["groups"] if g.pk in _internal_group_ids())
            if internal:
                gform.add_error("groups", "These groups belong to NBTE internal roles and cannot be given to a "
                                          f"portal account: {', '.join(internal)}.")
        if gform.is_valid() and pform.is_valid():
            before_groups = set(target.groups.values_list("name", flat=True))
            before_direct = {ps.perm_label(p) for p in target.user_permissions.select_related("content_type")}
            before_eff = ps.effective_permissions(target)
            with transaction.atomic(), views_are_logging():
                target.groups.set(gform.cleaned_data["groups"])
                target.user_permissions.set(pform.cleaned_data["permissions"])
                fresh = User.objects.get(pk=target.pk)
                after_eff = ps.effective_permissions(fresh)
                audit.log("update", target, actor=actor, changes={
                    "groups_before": sorted(before_groups),
                    "groups_after": sorted(fresh.groups.values_list("name", flat=True)),
                    "direct_permissions_before": sorted(before_direct),
                    "direct_permissions_after": sorted(
                        ps.perm_label(p) for p in fresh.user_permissions.select_related("content_type")),
                    "effective_added": sorted(after_eff - before_eff),
                    "effective_removed": sorted(before_eff - after_eff),
                }, note="user groups/permissions edited via groups UI")
            messages.success(request, f"Access for {target.get_full_name() or target.email} saved.")
            return redirect("accounts:groups:user_groups", pk=target.pk)
    else:
        gform = UserGroupForm(target=target, actor=actor)
        pform = UserDirectPermissionForm(target=target, actor=actor)

    inherited = ps.inherited_permissions(target)
    role_groups = list(target.role.groups.order_by("name")) if target.role_id else []
    if gform.is_bound:
        group_ids = {int(x) for x in request.POST.getlist("groups") if x.isdigit()}
        perm_ids = {int(x) for x in request.POST.getlist("permissions") if x.isdigit()}
    else:
        group_ids = set(target.groups.values_list("pk", flat=True))
        perm_ids = set(target.user_permissions.values_list("pk", flat=True))
    role_group_ids = {g.pk for g in role_groups}
    internal_ids = _internal_group_ids() if target.user_type != User.STAFF else set()
    group_rows = [{
        "group": g, "checked": g.pk in group_ids, "inherited": g.pk in role_group_ids,
        "internal": g.pk in internal_ids,
        "locked": not can_act or not ps.can_assign_group(actor, target, g)
                  or (g.pk in internal_ids and g.pk not in group_ids),
        "perm_count": g.perm_count,
    } for g in Group.objects.order_by("name").annotate(perm_count=Count("permissions", distinct=True))]
    picker = build_picker(
        permission_queryset(), selected=perm_ids, inherited=inherited,
        locked=lambda p: not can_act or not ps.can_assign_permission(actor, target, p),
    )
    from . import audience
    segment = audience.segment_of(target)
    return render(request, "accounts/users/groups.html", {
        "u": target, "gform": gform, "pform": pform, "can_act": can_act,
        "segment": segment, "seg": audience.SEGMENTS[segment],
        "role_groups": [{"group": g, "apps": _apps_of(ps.group_permissions(g))} for g in role_groups],
        "role_baseline_apps": _apps_of({ps.perm_label(p) for p in target.role.permissions.select_related("content_type")})
        if target.role_id else [],
        "group_rows": group_rows, "picker": picker,
        "effective_total": len(ps.effective_permissions(target)),
        "inherited_total": len(inherited),
        "direct_total": len(ps.direct_permissions(target)),
    })


# ---------------------------------------------------------------------------
# Permission matrix (read-only)
# ---------------------------------------------------------------------------
@login_required
def permission_matrix(request):
    if not _can_view(request.user):
        raise PermissionDenied
    roles = list(Role.objects.order_by("-rank", "name"))
    role_sets = {r.pk: ps.role_permissions(r) for r in roles}
    apps = sorted({lab.split(".", 1)[0] for s in role_sets.values() for lab in s},
                  key=_app_name)
    rows, detail = [], {}
    for r in roles:
        cells = []
        for a in apps:
            codes = sorted(lab.split(".", 1)[1] for lab in role_sets[r.pk] if lab.startswith(a + "."))
            key = f"{r.pk}:{a}"
            if codes:
                detail[key] = {
                    "role": r.name, "app": _app_name(a),
                    "perms": [{"code": c, "label": human_label(c)} for c in codes],
                }
            cells.append({"app": a, "count": len(codes), "key": key})
        rows.append({"role": r, "cells": cells, "total": len(role_sets[r.pk])})
    from . import audience
    aud = request.GET.get("audience") or ""
    if aud in audience.AUDIENCE_LABELS:
        rows = [r for r in rows if audience.role_audience(r["role"]) == aud]
    return render(request, "accounts/permissions/matrix.html", {
        "rows": rows, "audience": aud, "role_total": len(roles),
        "nbte_roles": sum(1 for r in roles if audience.role_audience(r) == audience.STAFF), "apps": [{"label": a, "name": _app_name(a)} for a in apps],
        "detail": detail,
    })


# ---------------------------------------------------------------------------
# Users (entry point to each user's groups & permissions page)
# ---------------------------------------------------------------------------
@login_required
def user_access_list(request):
    """Scoped user list for access management. Kept here (not in
    user_views.py) so Stage 1's user list is untouched; each row links to the
    existing user detail page and to the new groups & permissions page."""
    _require(request.user, "accounts.view_user", "accounts.assign_groups", any_of=True)
    from . import audience
    from .user_views import segment_counts
    segment = audience.clean_segment(request.GET.get("segment"))
    visible = _visible_users(request.user)
    qs = (visible.filter(audience.segment_q(segment)).select_related("role", "department", "institution")
          .annotate(group_count=Count("groups", distinct=True),
                    direct_perm_count=Count("user_permissions", distinct=True))
          .order_by("first_name", "last_name", "email"))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    role = (request.GET.get("role") or "").strip()
    if role.isdigit():
        qs = qs.filter(role_id=int(role))
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "accounts/users/access_list.html", {
        "page": page, "q": q, "role": role,
        "segment": segment, "seg": audience.SEGMENTS[segment], "segment_counts": segment_counts(visible),
        "roles": audience.roles_for_segment(segment).order_by("-rank", "name"),
        "can_assign": request.user.has_perm("accounts.assign_groups"),
    })
