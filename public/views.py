"""Public history page and Settings → History (v2.1 A1, A2)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import HistoryMilestoneForm
from .hero import history_enabled
from .models import HistoryMilestone

HISTORY_KEYS = ("home.history.enabled", "home.title.history", "home.history.intro")


def _can_manage(user):
    return user.is_authenticated and (user.is_superuser or user.has_perm("public.manage_history")
                                      or user.has_perm("config.change_configentry"))


def history_page(request):
    """/history/ — the full timeline. Hidden from the public until
    home.history.enabled is on; administrators see a preview with a note."""
    enabled = history_enabled()
    can_manage = _can_manage(request.user)
    if not enabled and not can_manage:
        raise Http404("History is not published yet.")
    qs = HistoryMilestone.objects.all() if can_manage else HistoryMilestone.objects.filter(is_published=True)
    return render(request, "public/history.html", {
        "milestones": list(qs), "history_enabled": enabled, "can_manage": can_manage,
        "is_authed": request.user.is_authenticated,
    })


# ---------------------------------------------------------------------------
# Settings → History (HTMX: every action swaps #history-admin)
# ---------------------------------------------------------------------------
def _settings_values():
    import config

    from config.defaults import HOME_TEXT_DEFAULTS

    return {
        "enabled": history_enabled(),
        "title": config.get("home.title.history", "") or HOME_TEXT_DEFAULTS.get("home.title.history", ""),
        "intro": config.get("home.history.intro", "") or HOME_TEXT_DEFAULTS.get("home.history.intro", ""),
    }


def _admin_context(form=None, editing=None):
    return {"milestones": list(HistoryMilestone.objects.all()), "form": form or HistoryMilestoneForm(),
            "editing": editing, "history_settings": _settings_values()}


def _respond(request, form=None, editing=None, status=200):
    ctx = _admin_context(form, editing)
    if request.headers.get("HX-Request"):
        return render(request, "public/settings/_history_admin.html", ctx, status=status)
    if status >= 400:
        return render(request, "public/settings/history.html", ctx, status=status)
    return redirect("config:history")


@login_required
def history_settings(request):
    if not _can_manage(request.user):
        raise PermissionDenied
    return render(request, "public/settings/history.html", _admin_context())


@login_required
@require_POST
def history_create(request):
    if not _can_manage(request.user):
        raise PermissionDenied
    form = HistoryMilestoneForm(request.POST, request.FILES)
    if not form.is_valid():
        return _respond(request, form=form, status=422)
    obj = form.save(commit=False)
    last = HistoryMilestone.objects.order_by("-order").first()
    obj.order = (last.order + 10) if last else 10
    obj.save()
    messages.success(request, f"Added {obj.year} · {obj.title}.")
    return _respond(request)


@login_required
def history_edit(request, pk):
    if not _can_manage(request.user):
        raise PermissionDenied
    obj = get_object_or_404(HistoryMilestone, pk=pk)
    if request.method == "POST":
        form = HistoryMilestoneForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, f"Saved {obj.year} · {obj.title}.")
            return _respond(request)
        return _respond(request, form=form, editing=obj.pk, status=422)
    if not request.headers.get("HX-Request"):
        return render(request, "public/settings/history.html",
                      _admin_context(HistoryMilestoneForm(instance=obj), obj.pk))
    return render(request, "public/settings/_history_admin.html",
                  _admin_context(HistoryMilestoneForm(instance=obj), obj.pk))


@login_required
@require_POST
def history_move(request, pk, direction):
    """Swap with the neighbour above/below, then renumber 10, 20, 30 …"""
    if not _can_manage(request.user):
        raise PermissionDenied
    if direction not in ("up", "down"):
        raise Http404
    items = list(HistoryMilestone.objects.all())
    idx = next((i for i, m in enumerate(items) if m.pk == pk), None)
    if idx is None:
        raise Http404
    j = idx - 1 if direction == "up" else idx + 1
    if 0 <= j < len(items):
        items[idx], items[j] = items[j], items[idx]
        with transaction.atomic():
            for n, m in enumerate(items, start=1):
                if m.order != n * 10:
                    m.order = n * 10
                    m.save(update_fields=["order", "updated_at"])
    return _respond(request)


@login_required
@require_POST
def history_delete(request, pk):
    if not _can_manage(request.user):
        raise PermissionDenied
    obj = get_object_or_404(HistoryMilestone, pk=pk)
    label = str(obj)
    obj.delete()
    messages.success(request, f"Removed {label}.")
    return _respond(request)


@login_required
@require_POST
def history_settings_save(request):
    """The section switch, title and introduction (Settings rows, audited)."""
    if not _can_manage(request.user):
        raise PermissionDenied
    from config.models import ConfigEntry
    from config.registry import invalidate
    from core import audit

    values = {
        "home.history.enabled": ("true" if request.POST.get("enabled") else "false", "bool"),
        "home.title.history": ((request.POST.get("title") or "").strip()[:200], "str"),
        "home.history.intro": ((request.POST.get("intro") or "").strip()[:600], "str"),
    }
    for key, (value, vtype) in values.items():
        entry, _ = ConfigEntry.objects.get_or_create(
            key=key, scope="global", defaults={"value": value, "value_type": vtype, "group": "Homepage"})
        if entry.value != value:
            old = entry.value
            entry.value = value
            entry.full_clean()
            entry.save()
            invalidate(key, "global")
            audit.log("update", entry, actor=request.user, changes={"value": [old, value]}, note="history settings")
    messages.success(request, "History settings saved.")
    return _respond(request)
