"""NBTE Quality Assurance Portal — Phase 1 views (portal host only).

    Sign in · Set password (from a link) · Forgot password
    Dashboard (empty in Phase 1) · Account settings

Institution data is always read through ``Model.objects.for_user(user)``
(Master Plan §4). The portal shell follows §16: NBTE tokens and components,
its own simpler layout. Every page works as a full page and as an HTMX
partial (`#qa-main`) for boosted navigation (§20).
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_http_methods, require_POST

import config
from core import ratelimit
from core.impersonation import IMPERSONATE_SESSION_KEY

from .hosts import internal_url, portal_url
from .portal_forms import (
    AccountProfileForm,
    PortalAuthenticationForm,
    PortalPasswordChangeForm,
    PortalPasswordResetForm,
    PortalSetPasswordForm,
)
from .portal_tokens import link_valid_hours, portal_token_generator
from .ui import is_htmx, timeout_ms

PORTAL_NAME = "NBTE Quality Assurance Portal"
FULL_BASE = "qa_portal/base_portal.html"
PARTIAL_BASE = "qa_portal/base_partial.html"


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------
def _institution_for(user):
    from institutions.models import Institution

    if getattr(user, "user_type", "") != "institution":
        return None
    return Institution.objects.for_user(user).select_related("handling_department").first()


def _shell_context(request, section, **extra):
    user = request.user
    ctx = {
        "portal_name": PORTAL_NAME,
        "section": section,
        "base_template": PARTIAL_BASE if (is_htmx(request) and request.headers.get("HX-Target") == "qa-main")
        else FULL_BASE,
        "qa_timeout_ms": timeout_ms(),
        "maintenance_message": str(config.get("qa.portal.maintenance_message", "") or ""),
        "internal_login_url": internal_url(request, "/accounts/login/"),
    }
    if user.is_authenticated:
        from notifications.models import Notification

        ctx["institution"] = _institution_for(user)
        if getattr(user, "user_type", "") == "staff":
            # Phase 3: staff on the portal see no institution data — their
            # notifications (which name institutions) stay on the internal system.
            ctx["unread_count"], ctx["recent_notifications"] = 0, []
        else:
            ctx["unread_count"] = Notification.objects.filter(recipient=user, is_read=False).count()
            ctx["recent_notifications"] = Notification.objects.filter(recipient=user).order_by("-created_at")[:6]
        ctx["portal_nav"] = portal_nav(user, ctx["institution"], section)
        ctx["impersonation"] = getattr(request, "qa_impersonation", None)
    ctx.update(extra)
    return ctx


NAV_ICONS = {
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/>'
                 '<rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "application": '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "documents": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    "payments": '<rect x="2" y="5" width="20" height="14" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/>',
    "institution": '<path d="M3 21h18"/><path d="M5 21V7l7-4 7 4v14"/><path d="M9 21v-6h6v6"/>',
    "timeline": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    "qa_requests": '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "queries": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "engagements": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "service": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    "profile": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',
    "inspections": '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "account": '<circle cx="12" cy="8" r="4"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
    "complaints": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
                  '<line x1="12" y1="7" x2="12" y2="11"/><line x1="12" y1="14" x2="12.01" y2="14"/>',
}


def portal_nav(user, institution, section):
    """Section links for the top bar (desktop) and tab bar (phones)."""
    items = [("dashboard", "Dashboard", reverse("qa_portal:dashboard"))]
    if getattr(user, "user_type", "") == "institution" and institution is not None:
        if institution.status in ("proposed", "under_review"):
            items.append(("application", "Application", reverse("qa_portal:application")))
        if institution.status == "approved":
            # Phase 3: SOP-02 QA requests and queries
            items.append(("qa_requests", "QA requests", reverse("qa_portal:qa_requests")))
            items.append(("queries", "Queries", reverse("qa_portal:queries")))
            items.append(("inspections", "Inspections", reverse("qa_portal:inspections")))  # Phase 5
        items += [
            ("documents", "Documents", reverse("qa_portal:documents")),
            ("payments", "Payments", reverse("qa_portal:payments")),
            ("institution", "Institution", reverse("qa_portal:institution")),
            ("timeline", "Timeline", reverse("qa_portal:timeline")),
        ]
    if getattr(user, "user_type", "") == "resource_person":  # Phase 5: resource-person portal
        items += [("engagements", "Engagements", reverse("qa_portal:rp_engagements")),
                  ("service", "History", reverse("qa_portal:rp_history")),
                  ("profile", "Profile", reverse("qa_portal:rp_profile"))]
    if getattr(user, "user_type", "") in ("institution", "resource_person") and (
            institution is not None or getattr(user, "user_type", "") == "resource_person"):
        items.append(("complaints", "Complaints", reverse("qa_portal:complaints")))  # Phase 7
    items.append(("account", "Account", reverse("qa_portal:account")))
    many = len(items) > 3
    secondary = {"timeline", "account"}
    if any(k == "qa_requests" for k, _l, _u in items):
        secondary |= {"institution", "queries", "inspections"}  # Phase 3: keep the top bar to five links
    if sum(1 for k, _l, _u in items if k not in secondary) > 5:
        secondary.add("complaints")  # Phase 7: still five links; Complaints moves to the user menu
    # Phase 7: the phone tab bar shows the four most-used sections the user has.
    order = ["dashboard", "application", "qa_requests", "engagements", "payments", "complaints", "documents",
             "service", "profile"]
    present = [k for k, _l, _u in items]
    tabs = [k for k in order if k in present][:4]
    return [{"key": k, "label": label, "url": url, "active": k == section, "icon": NAV_ICONS.get(k, ""),
             "tab": k in tabs,
             # Timeline and Account live in the user menu when the bar is full.
             "primary": not (many and k in secondary)}
            for k, label, url in items]


def portal_login_required(view):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if is_htmx(request):
                from django.http import HttpResponse

                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("qa_portal:login") + "?next=" + request.get_full_path()
                return response
            return redirect_to_login(request.get_full_path(), login_url=reverse("qa_portal:login"))
        blocked = _access_blocked(request)
        if blocked:
            return blocked
        return view(request, *args, **kwargs)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _access_blocked(request):
    """Delisted institutions and suspended portal access cannot use the
    portal (§6). A read-only impersonation session can still look."""
    user = request.user
    if getattr(user, "user_type", "") != "institution" or getattr(request, "is_impersonating", False):
        return None
    institution = _institution_for(user)
    if institution is not None and institution.portal_enabled and institution.status != "delisted":
        return None
    reason = "delisted" if institution is not None and institution.status == "delisted" else "suspended"
    logout(request)
    response = render(request, "qa_portal/access_blocked.html",
                      {"portal_name": PORTAL_NAME, "reason": reason, "base_template": FULL_BASE,
                       "section": "auth"}, status=403)
    if is_htmx(request):
        response["HX-Redirect"] = reverse("qa_portal:login")
    return response


def institution_required(view):
    """Portal pages for institution accounts only (resource persons get 404)."""
    @portal_login_required
    def wrapped(request, *args, **kwargs):
        if getattr(request.user, "user_type", "") != "institution" or _institution_for(request.user) is None:
            from django.http import Http404

            raise Http404("This page is for institution accounts.")
        return view(request, *args, **kwargs)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


# ---------------------------------------------------------------------------
# Sign in / out
# ---------------------------------------------------------------------------
class PortalLoginView(auth_views.LoginView):
    template_name = "qa_portal/login.html"
    authentication_form = PortalAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        url = self.get_redirect_url()
        return url or reverse("qa_portal:dashboard")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_shell_context(self.request, "login"))
        return ctx

    def _ident(self):
        email = (self.request.POST.get("username") or "").strip().lower()
        return f"portal|{email}|{ratelimit.client_ip(self.request)}"

    def post(self, request, *args, **kwargs):
        limit, _window = ratelimit.LOGIN_FAILURES
        if ratelimit.is_limited("login", self._ident(), limit):
            from django.forms.utils import ErrorDict

            form = self.get_form()
            form.cleaned_data = {}
            form._errors = ErrorDict()
            form.add_error(None, "Too many failed sign-in attempts. Wait 15 minutes, or reset your password.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        ratelimit.hit("login", self._ident(), ratelimit.LOGIN_FAILURES[1])
        return super().form_invalid(form)

    def form_valid(self, form):
        ratelimit.reset("login", self._ident())
        return super().form_valid(form)


@require_POST
def portal_logout(request):
    logout(request)
    messages.success(request, "You have signed out.")
    return redirect("qa_portal:login")


# ---------------------------------------------------------------------------
# Forgot / set password (one-time links, never plaintext passwords)
# ---------------------------------------------------------------------------
@require_http_methods(["GET", "POST"])
def password_forgot(request):
    form = PortalPasswordResetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        ip = ratelimit.client_ip(request)
        limit, window = ratelimit.PASSWORD_RESETS
        if not (ratelimit.is_limited("portal-reset-ip", ip, limit)
                or ratelimit.is_limited("portal-reset-email", email, limit)):
            ratelimit.hit("portal-reset-ip", ip, window)
            ratelimit.hit("portal-reset-email", email, window)
            for user in form.get_users(email):
                send_set_password_link(request, user)
        # Same answer either way, so the page never reveals who has an account.
        return redirect("qa_portal:password_forgot_done")
    return render(request, "qa_portal/password_forgot.html", _shell_context(request, "auth", form=form))


def password_forgot_done(request):
    return render(request, "qa_portal/password_forgot_done.html",
                  _shell_context(request, "auth", valid_hours=link_valid_hours()))


def _cooldown_key(user):
    return f"qa:credentials:cooldown:{user.pk}"


def send_set_password_link(request, user):
    """Email a one-time set-password link (respects the resend cooldown).
    Returns True if a link was sent."""
    from . import mail

    try:
        cooldown = max(0, int(config.get("qa.credentials.resend_cooldown_minutes", 30)))
    except (TypeError, ValueError):
        cooldown = 30
    if cooldown and cache.get(_cooldown_key(user)):
        return False
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = portal_token_generator.make_token(user)
    path = reverse("qa_portal:password_set", kwargs={"uidb64": uid, "token": token})
    mail.send("Set your NBTE Quality Assurance Portal password", [user.email], "emails/qa_set_password.html",
              {"user": user, "set_url": portal_url(request, path), "valid_hours": link_valid_hours()},
              category="reset")
    if cooldown:
        cache.set(_cooldown_key(user), timezone.now().isoformat(), cooldown * 60)
    return True


class PortalSetPasswordView(auth_views.PasswordResetConfirmView):
    template_name = "qa_portal/password_set.html"
    form_class = PortalSetPasswordForm
    token_generator = portal_token_generator
    success_url = reverse_lazy("qa_portal:password_set_done")
    reset_url_token = "set-password"

    def get_user(self, uidb64):
        user = super().get_user(uidb64)
        if user is not None and getattr(user, "user_type", "staff") == "staff":
            return None  # staff never set passwords through the portal
        return user

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_shell_context(self.request, "auth", valid_hours=link_valid_hours()))
        return ctx


def password_set_done(request):
    return render(request, "qa_portal/password_set_done.html", _shell_context(request, "auth"))


@portal_login_required
@require_http_methods(["GET", "POST"])
def password_change(request):
    forced = bool(request.user.must_change_password)
    form = PortalPasswordChangeForm(request.user, request.POST or None, forced=forced)
    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, "Your password has been updated.")
        if forced:
            return redirect("qa_portal:dashboard")
        if is_htmx(request):
            return render(request, "qa_portal/_password_card.html",
                          _shell_context(request, "account", password_form=PortalPasswordChangeForm(request.user),
                                         password_saved=True))
        return redirect("qa_portal:account")
    if is_htmx(request) and request.headers.get("HX-Target") == "qa-password-card":
        return render(request, "qa_portal/_password_card.html",
                      _shell_context(request, "account", password_form=form))
    template = "qa_portal/password_change.html" if forced else "qa_portal/account.html"
    ctx = _shell_context(request, "account", form=form, password_form=form, forced=forced,
                         profile_form=AccountProfileForm(instance=request.user))
    return render(request, template, ctx)


# ---------------------------------------------------------------------------
# Dashboard and account
# ---------------------------------------------------------------------------
@portal_login_required
def dashboard(request):
    from .models import TimelineEvent

    user = request.user
    recent = []
    extra = {}
    if user.user_type == "staff":
        # Phase 3: the Staff Portal Dashboard (third dashboard). Staff see
        # only their own work list here, never institution records; every
        # link goes to the internal system.
        from accreditation.qa_portal_views import staff_dashboard_context

        ctx = _shell_context(request, "dashboard", **staff_dashboard_context(request))
        return render(request, "qa_portal/staff_dashboard.html", ctx)
    if user.user_type == "institution":
        recent = list(TimelineEvent.objects.for_user(user).filter(visibility="public")[:6])
        institution = _institution_for(user)
        if institution is not None:
            from accreditation.qa_portal_views import qa_action_items
            from institutions.portal_views import dashboard_context

            extra = dashboard_context(request, institution)
            extra["action_items"] = qa_action_items(request, institution) + extra.get("action_items", [])
    ctx = _shell_context(request, "dashboard", recent_activity=recent,
                         is_resource_person=user.user_type == "resource_person", **extra)
    return render(request, "qa_portal/dashboard.html", ctx)


# ---------------------------------------------------------------------------
# Restricted files on the portal (slips, advices, proofs, documents)
# ---------------------------------------------------------------------------
PORTAL_FILE_MODELS = ("institutions.institutiondocument", "accreditation.evidence", "accreditation.response",
                      "payments.paymentslip", "payments.paymentadvice",
                      "payments.proofofpayment")


@portal_login_required
def portal_file(request, label, pk):
    """The portal's protected download: the row is found through
    ``for_user`` (scoped) and ``can_download`` decides (§10.4)."""
    import mimetypes
    from urllib.parse import quote

    from django.apps import apps
    from django.conf import settings as dj_settings
    from django.http import FileResponse, Http404, HttpResponse

    from core import audit

    if label not in PORTAL_FILE_MODELS:
        raise Http404
    model = apps.get_model(label)
    obj = model.objects.for_user(request.user).filter(pk=pk).first()
    if obj is None or not obj.file or not obj.can_download(request.user):
        raise Http404
    audit.log("other", obj, actor=getattr(request, "impersonator", None) or request.user,
              note="file downloaded (portal" + (", impersonation" if getattr(request, "is_impersonating", False)
                                               else "") + ")")
    filename = obj.original_name or obj.file.name.rsplit("/", 1)[-1]
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if getattr(dj_settings, "USE_X_ACCEL_REDIRECT", False):
        response = HttpResponse(content_type=ctype)
        response["X-Accel-Redirect"] = dj_settings.PROTECTED_MEDIA_URL + quote(obj.file.name)
    else:
        response = FileResponse(obj.file.open("rb"), content_type=ctype)
    inline = request.GET.get("inline") == "1" and ctype == "application/pdf"
    response["Content-Disposition"] = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename)}"
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


# ---------------------------------------------------------------------------
# Read-only impersonation (start / ended)
# ---------------------------------------------------------------------------
def impersonation_start(request, token):
    from django.http import Http404

    from institutions.services import impersonation_session_for_token

    session = impersonation_session_for_token(token)
    if session is None:
        raise Http404("This view-as link is not valid.")
    logout(request)
    from .impersonation import SESSION_KEY

    request.session[SESSION_KEY] = session.pk
    return redirect("qa_portal:dashboard")


def impersonation_ended(request):
    return render(request, "qa_portal/impersonation_ended.html",
                  {"portal_name": PORTAL_NAME, "base_template": FULL_BASE, "section": "auth",
                   "internal_home": internal_url(request, "/qa/institutions/")})


@portal_login_required
@require_http_methods(["GET", "POST"])
def account(request):
    profile_form = AccountProfileForm(request.POST if request.method == "POST" else None, instance=request.user)
    saved = False
    if request.method == "POST" and profile_form.is_valid():
        profile_form.save()
        saved = True
        if not is_htmx(request):
            messages.success(request, "Your details have been saved.")
            return redirect("qa_portal:account")
    if is_htmx(request) and request.headers.get("HX-Target") == "qa-profile-card":
        response = render(request, "qa_portal/_profile_card.html",
                          _shell_context(request, "account", profile_form=profile_form, profile_saved=saved))
        if saved:
            from .ui import toast
            toast(response, "success", "Your details have been saved")
        return response
    ctx = _shell_context(request, "account", profile_form=profile_form,
                         password_form=PortalPasswordChangeForm(request.user))
    return render(request, "qa_portal/account.html", ctx)


@require_POST
def impersonation_stop(request):
    """End an impersonation session (the Phase 1 superuser one or the Phase 2
    read-only view-as session) and go back to the internal system."""
    from .impersonation import SESSION_KEY

    request.session.pop(IMPERSONATE_SESSION_KEY, None)
    session = getattr(request, "qa_impersonation", None)
    target = "/home/"
    if session is not None:
        from institutions.services import end_impersonation

        end_impersonation(session, "stopped")
        target = f"/qa/institutions/{session.institution.code}/"
    request.session.pop(SESSION_KEY, None)
    return HttpResponseRedirect(internal_url(request, target))


# ---------------------------------------------------------------------------
# Error handlers for the portal URLconf (no internal URL may be reversed)
# ---------------------------------------------------------------------------
def page_not_found(request, exception=None):
    return render(request, "qa_portal/errors/404.html", {"portal_name": PORTAL_NAME}, status=404)


def permission_denied(request, exception=None):
    return render(request, "qa_portal/errors/403.html", {"portal_name": PORTAL_NAME}, status=403)


def server_error(request):
    from django.template import loader
    from django.http import HttpResponseServerError

    return HttpResponseServerError(loader.get_template("qa_portal/errors/500.html").render({}))



# ---------------------------------------------------------------------------
# Phase 7: every notification (the bell's "View all")
# ---------------------------------------------------------------------------
@portal_login_required
@require_http_methods(["GET", "POST"])
def notifications(request):
    """The signed-in user's own notifications, newest first, 25 per page."""
    from notifications.models import Notification

    from .ui import paginate

    mine = Notification.objects.filter(recipient=request.user)
    if request.method == "POST":
        mine.filter(is_read=False).update(is_read=True)
        if not is_htmx(request):
            return redirect("qa_portal:notifications")
    show = request.GET.get("show", "")
    qs = mine.order_by("-created_at")
    if show == "unread":
        qs = qs.filter(is_read=False)
    ctx = _shell_context(request, "notifications", page=paginate(request, list(qs)), show=show,
                         unread_total=mine.filter(is_read=False).count())
    if is_htmx(request) and request.headers.get("HX-Target") == "qa-notif-rows":
        return render(request, "qa_portal/_notification_rows.html", ctx)
    return render(request, "qa_portal/notifications.html", ctx)
