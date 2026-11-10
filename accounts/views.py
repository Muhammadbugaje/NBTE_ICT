from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.forms.utils import ErrorDict
from django.shortcuts import redirect, render

from core import ratelimit

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

    # Review M18: after 5 failed sign-ins for the same email from the same
    # address, further attempts are refused for 15 minutes (the password is
    # not even checked). A successful sign-in clears the counter.
    def _ratelimit_ident(self):
        email = (self.request.POST.get("username") or "").strip().lower()
        return f"{email}|{ratelimit.client_ip(self.request)}"

    def post(self, request, *args, **kwargs):
        limit, _window = ratelimit.LOGIN_FAILURES
        if ratelimit.is_limited("login", self._ratelimit_ident(), limit):
            form = self.get_form()
            form.cleaned_data = {}
            form._errors = ErrorDict()
            form.add_error(None, "Too many failed sign-in attempts. Please wait 15 minutes "
                                 "and try again, or reset your password.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        ratelimit.hit("login", self._ratelimit_ident(), ratelimit.LOGIN_FAILURES[1])
        return super().form_invalid(form)

    def form_valid(self, form):
        ratelimit.reset("login", self._ratelimit_ident())
        return super().form_valid(form)


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

    from .security_views import security_context  # Phase 9: devices, passkeys, deletion
    from config.appearance_views import appearance_card_context

    return render(request, "accounts/profile.html", {
        "form": form,
        "completeness": completeness,
        "threshold": user.profile_threshold,
        "is_complete": user.profile_complete,
        "missing": user.missing_profile_items(),
        **security_context(request),
        **appearance_card_context(request),  # v2.1 A10: personal Appearance card
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

# ---------------------------------------------------------------------------
# Profile nudge: "Continue anyway" (Phase 8, accounts/profile_nudges.py)
# ---------------------------------------------------------------------------
@login_required
def profile_nudge_continue(request):
    """Log that the user chose to start a request with an incomplete
    profile. POST only; answers 204 (the browser then follows the link)."""
    from django.http import HttpResponse, HttpResponseNotAllowed

    from core import audit

    from . import profile_nudges

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = (request.POST.get("target") or "")[:200]
    if profile_nudges.applies_to(request.user):
        st = profile_nudges.status(request.user)
        audit.log("other", request.user, actor=request.user,
                  note=f"continued with an incomplete profile ({st['percent']}%) to {target}"[:255],
                  changes={"percent": st["percent"], "threshold": st["threshold"], "target": target})
    return HttpResponse(status=204)
