"""Host routing for the QA portal (Master Plan §1 `QAPortalHostMiddleware`, §3).

Requests whose host is `settings.QA_PORTAL_HOST` are served by the portal's
own URLconf (`qa_core.portal_urls`), which contains **no** internal route:
`/tasks/`, `/settings/`, `/admin/` … simply do not exist there (404).

It must run before CommonMiddleware (APPEND_SLASH resolves against the
request's URLconf) and before anything that calls `reverse()`.
"""
from django.conf import settings
from django.urls import set_urlconf

from .hosts import is_portal_request


class QAPortalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # WORKAROUND FOR RENDER: Allow the QA portal to be accessed via the /qa-portal/ path
        if request.path.startswith("/qa-portal/"):
            request.is_qa_portal = True
            return self.get_response(request)

        request.is_qa_portal = is_portal_request(request)
        if request.is_qa_portal:
            urlconf = getattr(settings, "QA_PORTAL_URLCONF", "qa_core.portal_urls")
            request.urlconf = urlconf
            # Middleware below us may reverse() before Django resolves the
            # request; make the portal URLconf current for this thread now.
            set_urlconf(urlconf)
        try:
            return self.get_response(request)
        finally:
            if request.is_qa_portal:
                set_urlconf(None)