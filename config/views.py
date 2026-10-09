from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import ConfigEntry


@login_required
@permission_required("config.change_configentry", raise_exception=True)
def settings_index(request):
    entries = list(ConfigEntry.objects.order_by("group", "key", "scope"))
    grouped = [(g or "General", list(items)) for g, items in groupby(entries, key=lambda e: e.group)]
    from notifications import engine

    quota = [{"provider": p, "used": engine.used_today(p), "limit": engine.daily_limit(p)} for p in engine.PROVIDERS]
    return render(request, "config/settings.html", {"grouped": grouped, "quota": quota})


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def settings_save(request, pk):
    entry = get_object_or_404(ConfigEntry, pk=pk)
    entry.value = request.POST.get("value", "")
    try:
        entry.full_clean()
    except ValidationError as exc:
        messages.error(request, f"{entry.key}: " + "; ".join(exc.messages))
    else:
        entry.save()  # post_save signal invalidates the cache -> effective immediately
        messages.success(request, f"Saved {entry.key}.")
    return redirect("config:settings")


@login_required
@permission_required("config.change_configentry", raise_exception=True)
@require_POST
def send_test_email(request):
    """Synchronous on purpose so the admin sees which provider actually delivered."""
    from django.conf import settings

    from notifications import engine

    try:
        provider = engine.send(
            f"{settings.SITE_NAME} test email", [request.user.email],
            "<p>This is a test email from the settings page.</p>", "Test email.", category="test")
    except engine.DeliveryError as exc:
        messages.error(request, f"Test email failed: {exc}")
    else:
        messages.success(request, f"Test email sent to {request.user.email} via {provider}.")
    return redirect("config:settings")
