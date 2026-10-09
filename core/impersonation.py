"""Super-admin impersonation.

Allows a superuser to temporarily become another user for testing and support
purposes. The impersonated user is stored in the session and swapped in via
middleware. Only superusers can start or stop impersonation.

While impersonating:
  - `request.user` is the impersonated user
  - `request.impersonator` is the real superuser
  - `request.is_impersonating` is True
  - The `must_change_password` middleware is bypassed (otherwise you'd be
    stuck on the change-password screen if the target user hasn't set a
    password yet).
"""
IMPERSONATE_SESSION_KEY = "impersonate_user_id"


def get_impersonator(request):
    """Return the real user if the request is impersonating, else None."""
    return getattr(request, "impersonator", None)


def is_impersonating(request):
    return bool(getattr(request, "is_impersonating", False))