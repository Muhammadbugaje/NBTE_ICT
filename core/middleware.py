from .audit import set_current_actor
from .impersonation import IMPERSONATE_SESSION_KEY

from django.contrib import messages as django_messages


class DrainHtmxMessagesMiddleware:
    """Consume Django messages on HTMX requests.

    HTMX partial responses never render base.html, so any message added
    during an HTMX request sits in the session and surfaces later on the
    next full-page render — looking like a stale toast on an unrelated page.

    This middleware iterates the message storage on HTMX responses, which
    consumes (discards) those messages before they can leak.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request", "").lower() == "true":
            try:
                # Iterating the storage marks everything read/consumed.
                list(django_messages.get_messages(request))
            except Exception:
                pass
        return response


class CurrentUserMiddleware:
    """Makes request.user available to audit signals (who did this)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        set_current_actor(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            set_current_actor(None)


class ImpersonationMiddleware:
    """Swap request.user for the impersonated user, if the session says so.

    Must run AFTER AuthenticationMiddleware and BEFORE any middleware that
    checks permissions or passwords.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.impersonator = None
        request.is_impersonating = False

        target_id = request.session.get(IMPERSONATE_SESSION_KEY)
        real_user = getattr(request, "user", None)

        if target_id and real_user and real_user.is_authenticated and real_user.is_superuser:
            from accounts.models import User
            target = User.objects.filter(pk=target_id, is_active=True).first()
            if target is not None:
                request.impersonator = real_user
                request.user = target
                request.is_impersonating = True
            else:
                request.session.pop(IMPERSONATE_SESSION_KEY, None)

        return self.get_response(request)