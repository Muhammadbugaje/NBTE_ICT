"""Accounts middleware.

Enforces the "must change password" state on every authenticated request:
an authenticated user flagged with `must_change_password=True` is redirected
to the change-password page and can't reach any other route — with a small
allowlist of endpoints (login, logout, password change itself, static, admin
auth gates) that they must still be able to reach.
"""
from __future__ import annotations

from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse


# Paths a user with `must_change_password=True` is still allowed to hit.
# Namespaced names now, matching accounts/urls.py.
ALLOWED_URL_NAMES = (
    "accounts:force_password_change",
    "accounts:password_change",
    "accounts:logout",
    "accounts:login",           # in case they get bounced back through login
    "accounts:password_reset",
    "accounts:password_reset_done",
    "accounts:password_reset_confirm",
    "accounts:password_reset_complete",
)

# Path prefixes that always pass through (Django admin, static, media,
# health checks). Adjust if you mount the app under a sub-path.
ALLOWED_PATH_PREFIXES = (
    "/static/",
    "/media/",
    # Review L2: only the admin's own login/logout pages. Exempting all of
    # /admin/ let a flagged staff user work in the admin indefinitely
    # without ever replacing the temporary password.
    "/admin/login/",
    "/admin/logout/",
    "/favicon.ico",
    "/robots.txt",
)


# QA portal (Master Plan §3): the portal host has its own URLconf, so a user
# who must change their password there is sent to the portal's own page.
PORTAL_ALLOWED_URL_NAMES = (
    "qa_portal:password_change",
    "qa_portal:logout",
    "qa_portal:login",
    "qa_portal:password_forgot",
    "qa_portal:password_forgot_done",
    "qa_portal:password_set",
    "qa_portal:password_set_done",
    "qa_portal:impersonation_stop",
)


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if (user is not None and user.is_authenticated and getattr(user, "must_change_password", False)
                and not getattr(request, "is_impersonating", False)
                and getattr(request, "is_qa_portal", False)):
            return self._portal(request)

        if user is not None and user.is_authenticated and getattr(user, "must_change_password", False):
            path = request.path or "/"

            # Never bounce a request that's already on an allowed route.
            if not path.startswith(ALLOWED_PATH_PREFIXES):
                # Resolve each allowed URL name once per request and compare
                # against the current path. Reversing here is safe — if a name
                # is missing, we skip it rather than crashing the whole site.
                current_is_allowed = False
                for name in ALLOWED_URL_NAMES:
                    try:
                        target = reverse(name)
                    except NoReverseMatch:
                        continue
                    if path == target or path.startswith(target.rstrip("/") + "/"):
                        current_is_allowed = True
                        break

                if not current_is_allowed:
                    try:
                        return redirect("accounts:force_password_change")
                    except NoReverseMatch:
                        # Fallback: something is wrong with URL config.
                        # Let the request through rather than infinite-loop.
                        return self.get_response(request)

        return self.get_response(request)

    def _portal(self, request):
        """Portal-host branch: same rule, portal URL names."""
        path = request.path or "/"
        if path.startswith(ALLOWED_PATH_PREFIXES):
            return self.get_response(request)
        for name in PORTAL_ALLOWED_URL_NAMES:
            try:
                target = reverse(name)
            except NoReverseMatch:
                continue
            if "<" in target:
                continue
            if path == target or path.startswith(target.rstrip("/") + "/"):
                return self.get_response(request)
        # Set-password links carry a token in the path.
        if path.startswith("/password/set/"):
            return self.get_response(request)
        try:
            return redirect("qa_portal:password_change")
        except NoReverseMatch:
            return self.get_response(request)


# ---------------------------------------------------------------------------
# QA: keep each kind of user on its own front door (Master Plan §3)
# ---------------------------------------------------------------------------
def staff_portal_enabled():
    """`qa.portal.staff_sign_in_enabled` (default true, Phase 3)."""
    try:
        import config

        value = config.get("qa.portal.staff_sign_in_enabled", True)
    except Exception:  # settings table not ready (migrations)
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


class ExternalUserGuardMiddleware:
    """Both directions of the login separation (Master Plan §3).

    | Who / where                          | What happens                              |
    |--------------------------------------|-------------------------------------------|
    | user_type != 'staff' on internal host| 403, session logged out, "Please sign in  |
    |                                      | at the QA portal"                         |
    | user_type == 'staff' on portal host  | Phase 3: allowed on the Staff Portal      |
    |                                      | Dashboard and their own account pages     |
    |                                      | only; every institution page sends them   |
    |                                      | back to that dashboard. With              |
    |                                      | qa.portal.staff_sign_in_enabled = false   |
    |                                      | they go to the internal host (Phase 1)    |

    `user_type` is an indexed column on the user row already loaded by
    AuthenticationMiddleware, so the check is O(1) per request. Hidden menus
    are not the control; this is.
    """

    # Paths that always pass (static files, health checks).
    PASS_PREFIXES = ("/static/", "/media/", "/favicon.ico", "/robots.txt")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        path = request.path or "/"
        if path.startswith(self.PASS_PREFIXES):
            return self.get_response(request)

        user_type = getattr(user, "user_type", "staff")
        on_portal = bool(getattr(request, "is_qa_portal", False))
        impersonating = bool(getattr(request, "is_impersonating", False))

        # On the portal, request.user is the impersonation *target* when a
        # session is impersonating: an institution target is the documented
        # exception (shown with a banner); a staff target is not.
        if on_portal and user_type == "staff":
            if not staff_portal_enabled():
                return self._to_internal(request)
            # Phase 8 hardening: resolve the path *inside* the script prefix
            # (path_info). With FORCE_SCRIPT_NAME set, request.path carries the
            # prefix, never resolves, and the 404 branch would let staff through.
            if self._staff_may_open(request, request.path_info or path):
                return self.get_response(request)
            return self._to_staff_dashboard(request)

        if not on_portal and user_type != "staff":
            if impersonating and self._is_impersonation_stop(path):
                return self.get_response(request)
            return self._refuse_external_on_internal(request, impersonating)

        return self.get_response(request)

    @staticmethod
    def _is_impersonation_stop(path):
        try:
            return path == reverse("core:impersonate_stop")
        except NoReverseMatch:
            return False

    # -- staff on the portal ---------------------------------------------------
    # Portal pages NBTE staff may open (Phase 3, Staff Portal Dashboard). No
    # institution page is listed: staff never see institution data here.
    STAFF_PORTAL_URL_NAMES = frozenset({
        "dashboard", "login", "logout", "account", "password_change", "password_set", "password_set_done",
        "password_forgot", "password_forgot_done", "impersonation_start", "impersonation_stop",
        "impersonation_ended", "verify", "webhook_paystack", "webhook_remita", "pay_simulator",
    })

    @classmethod
    def _staff_may_open(cls, request, path):
        from django.urls import Resolver404, resolve

        try:
            match = resolve(path, urlconf=getattr(request, "urlconf", None))
        except Resolver404:
            return True  # the portal answers 404 itself
        return match.namespace == "qa_portal" and match.url_name in cls.STAFF_PORTAL_URL_NAMES

    @staticmethod
    def _to_staff_dashboard(request):
        from django.http import HttpResponse, HttpResponseRedirect

        target = reverse("qa_portal:dashboard", urlconf=getattr(request, "urlconf", None))
        if request.headers.get("HX-Request", "").lower() == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return HttpResponseRedirect(target)

    def _to_internal(self, request):
        from django.http import HttpResponseRedirect

        from qa_core.hosts import internal_url

        if request.headers.get("HX-Request", "").lower() == "true":
            from django.http import HttpResponse

            response = HttpResponse(status=204)
            response["HX-Redirect"] = internal_url(request, "/home/")
            return response
        return HttpResponseRedirect(internal_url(request, "/home/"))

    # -- institutions / resource persons on the internal host -------------------
    def _refuse_external_on_internal(self, request, impersonating):
        from django.contrib.auth import logout
        from django.template.loader import render_to_string
        from django.http import HttpResponseForbidden

        from qa_core.hosts import portal_url

        if impersonating:
            # A superuser viewing as an institution: never log the real user
            # out; institution views happen on the portal host.
            message = ("You are viewing as an institution user. Institution screens are on "
                       "the QA portal; stop impersonating to use the internal system.")
        else:
            logout(request)
            message = "Please sign in at the QA portal."
        html = render_to_string("qa_core/guard_403.html", {
            "message": message,
            "portal_url": portal_url(request, "/login/"),
            "impersonating": impersonating,
        }, request=request)
        return HttpResponseForbidden(html)
