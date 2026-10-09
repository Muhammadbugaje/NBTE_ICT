from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from .forms import (
    EmailAuthenticationForm,
    ForcePasswordChangeForm,
    ProfileForm,
    VoluntaryPasswordChangeForm,
)


class PortalLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True


@login_required
def force_password_change(request):
    """Serves the password-change page.

    If the user MUST change (temporary password), no current password required.
    If the user is just changing voluntarily, ask for the current password.
    The correct form is chosen transparently.
    """
    user = request.user
    forced = bool(user.must_change_password)

    FormClass = ForcePasswordChangeForm if forced else VoluntaryPasswordChangeForm
    form = FormClass(user, request.POST or None)

    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password updated successfully.")
        return redirect("core:home")

    return render(request, "accounts/password_change.html", {
        "form": form,
        "forced": forced,
    })


# Alias for a cleaner URL name (kept alongside the old one for compat)
password_change = force_password_change


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
@login_required
def profile_view(request):
    """User's own profile page: read + edit + completeness."""
    user = request.user

    if request.method == "POST":
        form = ProfileForm(user, request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile saved.")
            # Fire a notification the moment the threshold is crossed
            _maybe_notify_profile_complete(request, user)
            return redirect("accounts:profile")           # ← FIXED: was "accounts:profile"
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = ProfileForm(user)

    completeness = user.profile_completeness()

    return render(request, "accounts/profile.html", {
        "form": form,
        "completeness": completeness,
        "threshold": user.profile_threshold,
        "is_complete": user.profile_complete,
        "missing": user.missing_profile_items(),
    })


def _maybe_notify_profile_complete(request, user):
    """If the user just crossed the completeness threshold, send one
    celebratory notification. Suppress if they'd already crossed it."""
    from notifications.models import Notification
    from notifications.services import notify

    threshold = user.profile_threshold
    percent = user.profile_percent()
    if percent < threshold:
        return

    # Avoid spamming on every save: only fire if a recent notification for
    # this milestone doesn't already exist.
    recent = Notification.objects.filter(
        recipient=user, category=Notification.SYSTEM,
        title__icontains="profile is complete",
    ).exists()
    if recent:
        return

    notify(
        recipient=user,
        title="Your profile is complete",
        body=f"You've reached {percent}% completion. All features are now unlocked.",
        url="/accounts/profile/",
        category=Notification.SYSTEM,
        level=Notification.SUCCESS,
    )