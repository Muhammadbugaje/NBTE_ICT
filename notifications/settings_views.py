"""Phase 9: internal "Notification settings" page."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .forms import NotificationPreferenceForm
from .preferences import get_preferences


def preference_form(user, door, data=None):
    """Bound when `data` is given; the instance is the user's row (or unsaved defaults)."""
    pref = get_preferences(user)
    if pref.user_id is None:
        pref.user = user
    return NotificationPreferenceForm(data, instance=pref, door=door)


@login_required
@require_http_methods(["GET", "POST"])
def notification_settings(request):
    form = preference_form(request.user, "internal", request.POST if request.method == "POST" else None)
    impersonating = bool(getattr(request, "is_impersonating", False))
    if request.method == "POST":
        if impersonating:
            messages.error(request, "Not available while you are viewing as another user.")
            return redirect("notifications:settings")
        if form.is_valid():
            form.save()
            messages.success(request, "Notification settings saved.")
            return redirect("notifications:settings")
        messages.error(request, "Please fix the highlighted settings.")
    return render(request, "notifications/settings.html", {"form": form, "impersonating": impersonating})
