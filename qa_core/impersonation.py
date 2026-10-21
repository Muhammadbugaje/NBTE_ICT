"""Read-only, time-boxed "view as institution" on the portal host (Master Plan §6).

A staff member with ``institutions.impersonate_institution`` starts a
session on the internal host (the ``institutions.impersonate`` action: a
reason is required and the start is audited). That creates an
``ImpersonationSession`` with a one-time token; the staff member's browser
opens ``/impersonate/<token>/`` on the portal host, which binds the session.

While bound, on the portal host only:

* ``request.user`` is the institution's portal account, so every page is
  scoped exactly as the institution sees it;
* ``request.is_impersonating`` is true and ``request.impersonator`` is the
  staff member (the amber banner says so);
* anything but GET/HEAD/OPTIONS is refused (read-only) unless the session is
  write mode (superuser + ``qa.impersonation.allow_write``);
* every page view is audited and counted on the session;
* the session ends at ``expires_at`` (``qa.impersonation.max_minutes``).
"""
from __future__ import annotations

from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone

SESSION_KEY = "qa_imp_session_id"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
ALWAYS_ALLOWED = ("/impersonation/stop/", "/impersonate/")
PAGE_LOG_LIMIT = 200


class QAImpersonationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.qa_impersonation = None
        if not getattr(request, "is_qa_portal", False):
            return self.get_response(request)
        session_id = request.session.get(SESSION_KEY) if hasattr(request, "session") else None
        if not session_id:
            return self.get_response(request)
        from core import audit
        from institutions.models import ImpersonationSession
        from institutions.services import end_impersonation

        session = (ImpersonationSession.unscoped.select_related("staff", "view_as", "institution")
                   .filter(pk=session_id).first())
        path = request.path or "/"
        if session is None or not session.is_live:
            if session is not None and session.ended_at is None:
                end_impersonation(session, "expired")
            request.session.pop(SESSION_KEY, None)
            if path.startswith(("/static/", "/impersonation/ended/")):
                return self.get_response(request)
            return HttpResponseRedirect("/impersonation/ended/")
        request.user = session.view_as
        request.impersonator = session.staff
        request.is_impersonating = True
        request.qa_impersonation = session
        if path.startswith("/static/"):
            return self.get_response(request)
        if request.method not in SAFE_METHODS and not session.write_mode \
                and not path.startswith(ALWAYS_ALLOWED):
            body = ("<p><strong>Read-only view.</strong> You are viewing the portal as "
                    f"{session.institution.name}; changes are not allowed.</p>")
            response = HttpResponse(body, status=403)
            if request.headers.get("HX-Request", "").lower() == "true":
                from .ui import toast

                toast(response, "warning", "Read-only view", "You cannot make changes while viewing as an "
                                                               "institution.")
            return response
        if request.method == "GET":
            log = list(session.page_log or [])[-(PAGE_LOG_LIMIT - 1):]
            log.append({"at": timezone.now().isoformat(timespec="seconds"), "path": path[:200]})
            ImpersonationSession.unscoped.filter(pk=session.pk).update(
                pages_viewed=session.pages_viewed + 1, page_log=log, updated_at=timezone.now())
            audit.log("other", session, actor=session.staff, note=f"impersonation page view: {path[:200]}")
        return self.get_response(request)
