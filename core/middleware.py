from .audit import set_current_actor
from .impersonation import IMPERSONATE_SESSION_KEY

from django.contrib import messages as django_messages


class DrainHtmxMessagesMiddleware:
    """Django messages added during an HTMX request become toasts.

    HTMX partial responses never render base.html, so a message added during
    an HTMX request would sit in the session and surface later on an
    unrelated page (a stale toast). Instead, this middleware takes the
    messages off the storage and sends them with the same response as an
    ``HX-Trigger`` event, ``nbte:messages`` (app.js shows each as a toast) —
    so every HTMX action gets its feedback right away, on both shells.

    When the response sends the browser elsewhere (HX-Redirect, HX-Refresh,
    or a 3xx the XHR will follow) the messages stay put: the next page shows
    them. Messages a partial template already rendered are consumed already.
    """

    NAV_HEADERS = ("HX-Redirect", "HX-Refresh", "HX-Location")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("HX-Request", "").lower() != "true":
            return response
        if 300 <= response.status_code < 400 or any(response.has_header(h) for h in self.NAV_HEADERS):
            return response
        try:
            # Iterating the storage marks everything read/consumed.
            items = [{"kind": (m.level_tag or "info"), "title": str(m)}
                     for m in django_messages.get_messages(request)]
        except Exception:  # noqa: BLE001 — feedback must never break a response
            return response
        if items:
            self._trigger(response, items)
        return response

    @staticmethod
    def _trigger(response, items):
        import json

        payload = {}
        existing = response.get("HX-Trigger")
        if existing:
            try:
                payload = json.loads(existing)
            except ValueError:
                payload = {name.strip(): True for name in existing.split(",") if name.strip()}
        if not isinstance(payload, dict):
            payload = {}
        payload["nbte:messages"] = items[:5]
        response["HX-Trigger"] = json.dumps(payload)


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

HTMX_VARY = ("HX-Request", "HX-Target", "HX-History-Restore-Request")
_HX_META = ("HTTP_HX_REQUEST", "HTTP_HX_TARGET", "HTTP_HX_TRIGGER", "HTTP_HX_TRIGGER_NAME",
            "HTTP_HX_BOOSTED")


class HtmxVaryMiddleware:
    """Phase 0 / N2, V6, T-UX-1: make the browser Back button safe with HTMX.

    * Every HTML response varies on the HTMX headers, so a browser (or proxy)
      cache never hands a partial to a normal navigation of the same URL.
    * HTMX partial responses are ``Cache-Control: no-store``.
    * A history-restore request (``HX-History-Restore-Request: true``, sent by
      htmx on a history-cache miss) is a request for the FULL page: the HTMX
      headers are removed before any view sees them, so every
      ``is_htmx()``/``hx_target()`` helper takes the full-page branch.

    Sits right after SecurityMiddleware so no other middleware has read
    ``request.headers`` yet.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        meta = request.META
        restore = meta.get("HTTP_HX_HISTORY_RESTORE_REQUEST", "").lower() == "true"
        if restore:
            for key in _HX_META:
                meta.pop(key, None)
            request.__dict__.pop("headers", None)  # drop the cached HttpHeaders
            request.htmx_history_restore = True
        partial = (not restore) and meta.get("HTTP_HX_REQUEST", "").lower() == "true"
        response = self.get_response(request)
        if response.get("Content-Type", "").startswith("text/html"):
            from django.utils.cache import patch_vary_headers

            patch_vary_headers(response, HTMX_VARY)
            if partial and not response.has_header("Cache-Control"):
                response["Cache-Control"] = "no-store"
        return response


class ContentSecurityPolicyMiddleware:
    """v2.2 (Phase G): a site-wide Content-Security-Policy. Everything the
    platform loads is self-hosted, so the policy is tight on origins
    ('self' only, no plugins, no framing by other sites). Inline scripts and
    styles are still allowed because the templates use them. A response that
    already set its own CSP (e.g. file previews) keeps it. Settings:
    CSP_ENABLED (default True), CSP_REPORT_ONLY (default False)."""

    POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
              "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; media-src 'self' blob:; "
              "frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; "
              "form-action 'self' https:")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings

        response = self.get_response(request)
        if not getattr(settings, "CSP_ENABLED", True) or request.path.startswith("/admin/"):
            return response
        header = ("Content-Security-Policy-Report-Only" if getattr(settings, "CSP_REPORT_ONLY", False)
                  else "Content-Security-Policy")
        if "Content-Security-Policy" not in response and "Content-Security-Policy-Report-Only" not in response:
            response[header] = getattr(settings, "CSP_POLICY", self.POLICY)
        return response


class HealthzMiddleware:
    """v2.2: ``/healthz`` liveness probe for the platform (Render health check).

    Answers before host validation, the HTTPS redirect, sessions and the
    database, so a probe sent to the container's internal address or over
    plain HTTP is never refused, and a probe costs nothing. It only says the
    process is up; ``/health/`` checks the database, cache and disk."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info == "/healthz" and request.method in ("GET", "HEAD"):
            from django.http import HttpResponse

            response = HttpResponse("ok", content_type="text/plain")
            response["Cache-Control"] = "no-store"
            return response
        return self.get_response(request)
