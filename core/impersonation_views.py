"""Views for starting / stopping impersonation."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User

from .impersonation import IMPERSONATE_SESSION_KEY


@login_required
def pick_user(request):
    """List active users so a superuser can pick someone to impersonate."""
    if not request.user.is_superuser:
        raise PermissionDenied("Only superusers can impersonate.")
    users = (
        User.objects.filter(is_active=True)
        .select_related("role", "department")
        .order_by("first_name", "last_name", "email")
    )
    return render(request, "core/impersonate.html", {"users": users})


@login_required
@require_POST
def start(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied("Only superusers can impersonate.")
    target = get_object_or_404(User, pk=pk, is_active=True)
    if target.pk == request.user.pk:
        messages.error(request, "You are already yourself.")
        return redirect("core:impersonate_pick")

    request.session[IMPERSONATE_SESSION_KEY] = target.pk
    messages.success(
        request,
        f"Now acting as {target.get_full_name() or target.email}. "
        f"Use the banner at the top to return."
    )
    return redirect("core:home")


@login_required
@require_POST
def stop(request):
    request.session.pop(IMPERSONATE_SESSION_KEY, None)
    messages.success(request, "Returned to your own account.")
    return redirect("core:home")