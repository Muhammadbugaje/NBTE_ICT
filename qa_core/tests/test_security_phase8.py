"""Phase 8 security review of ExternalUserGuardMiddleware (Master Plan §3)
and the Phase 7 surfaces it must cover.

Findings checked here:
1. Every internal QA URL (including /qa/complaints/) refuses institution and
   resource-person sessions with 403 and logs them out — never a page.
2. Staff on the portal reach only the Staff Portal Dashboard and their own
   account pages; the new complaint and help pages send them back.
3. The staff check resolves request.path_info, so a deployment under a
   script prefix (FORCE_SCRIPT_NAME) cannot turn every portal page into a
   "404, let it through" (fixed in Phase 8).
4. An anonymous request is never redirected by the guard (views decide).
"""
import pytest
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import URLPattern, URLResolver, get_resolver

from accounts.middleware import ExternalUserGuardMiddleware


def _routes(urlconf):
    def walk(patterns, pre):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, pre + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield pre + str(p.pattern)
    return list(walk(get_resolver(urlconf).url_patterns, ""))


def _fill(route):
    import re
    return "/" + re.sub(r"<[^>]+>", "1", re.sub(r"<int:\w+>", "1", route))


@pytest.fixture
def externals(qa_seed, make_institution, make_user):
    inst = make_institution("SEC")
    return [make_user("a@sec.local", user_type="institution", institution=inst, role="inst-admin"),
            make_user("rp@sec.local", user_type="resource_person", role="resource-person")]


@pytest.mark.django_db
def test_external_sessions_refused_on_every_internal_qa_route(externals):
    from django.conf import settings

    routes = [r for r in _routes(settings.ROOT_URLCONF) if r.startswith("qa/")]
    assert any(r.startswith("qa/complaints/") for r in routes)
    for user in externals:
        for route in routes:
            c = Client(HTTP_HOST="localhost")
            c.force_login(user)
            r = c.get(_fill(route))
            assert r.status_code == 403, (user.email, route, r.status_code)
            assert "_auth_user_id" not in c.session  # logged out


@pytest.mark.django_db
def test_staff_on_the_portal_cannot_open_complaints_or_help(qa_seed, make_user, portal_host):
    officer = make_user("o@sec.local", role="officer", dept="dpap")
    c = Client(HTTP_HOST=portal_host)
    c.force_login(officer)
    for path in ("/complaints/", "/complaints/new/", "/complaints/1/", "/help/", "/help/how-complaints-work/",
                 "/payments/", "/timeline/"):
        r = c.get(path)
        assert r.status_code == 302 and r["Location"] == "/", (path, r.status_code)
    assert c.get("/account/").status_code == 200
    r = c.get("/complaints/", HTTP_HX_REQUEST="true")
    assert r.status_code == 204 and r["HX-Redirect"] == "/"


@pytest.mark.django_db
def test_staff_check_resolves_path_info_under_a_script_prefix(qa_seed, make_user, settings):
    """Before Phase 8 the guard resolved request.path; under a script prefix
    that never resolves, and the "unknown path → let the portal 404" branch
    let staff open institution pages."""
    officer = make_user("o2@sec.local", role="officer", dept="dpap")
    seen = []

    def view(request):
        seen.append(request.path_info)
        return HttpResponse("institution page")

    guard = ExternalUserGuardMiddleware(view)
    request = RequestFactory().get("/payments/", SCRIPT_NAME="/qa-portal")
    assert request.path == "/qa-portal/payments/" and request.path_info == "/payments/"
    request.user = officer
    request.is_qa_portal = True
    request.urlconf = settings.QA_PORTAL_URLCONF
    response = guard(request)
    assert response.status_code == 302 and not seen


@pytest.mark.django_db
def test_anonymous_requests_pass_through_untouched(portal_host):
    for host, path in (("localhost", "/qa/complaints/"), (portal_host, "/complaints/")):
        r = Client(HTTP_HOST=host).get(path)
        assert r.status_code == 302 and "login" in r["Location"]
