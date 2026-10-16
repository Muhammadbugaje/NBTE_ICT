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


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

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