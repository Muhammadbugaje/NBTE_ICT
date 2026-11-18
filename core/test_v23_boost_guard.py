"""v2.3 Phase 1 (D9): HTMX requests redirected to sign-in or another host
become full-page navigations; the boost guard script is on both shells."""
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.middleware import HtmxVaryMiddleware, htmx_full_redirect


def _run(location, *, htmx=True, host="ict.example.org"):
    request = RequestFactory().get("/qa/cases/", HTTP_HOST=host,
                                   **({"HTTP_HX_REQUEST": "true", "HTTP_HX_BOOSTED": "true"} if htmx else {}))
    middleware = HtmxVaryMiddleware(lambda req: HttpResponseRedirect(location))
    return middleware(request)


@override_settings(ALLOWED_HOSTS=["ict.example.org", "qa.example.org"])
class HtmxFullRedirectTests(SimpleTestCase):
    def test_redirect_to_sign_in_becomes_hx_redirect(self):
        response = _run("/accounts/login/?next=/qa/cases/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], "/accounts/login/?next=/qa/cases/")

    def test_portal_login_path_also_caught(self):
        response = _run("/login/")
        self.assertEqual(response.status_code, 204)

    def test_redirect_to_other_host_becomes_hx_redirect(self):
        response = _run("https://qa.example.org/dashboard/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], "https://qa.example.org/dashboard/")

    def test_same_host_redirect_left_for_htmx_to_follow(self):
        response = _run("/qa/cases/41/")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response.has_header("HX-Redirect"))

    def test_absolute_same_host_redirect_left_alone(self):
        response = _run("http://ict.example.org/qa/cases/41/")
        self.assertEqual(response.status_code, 302)

    def test_normal_navigation_untouched(self):
        response = _run("/accounts/login/", htmx=False)
        self.assertEqual(response.status_code, 302)

    def test_cookies_survive(self):
        request = RequestFactory().get("/x/", HTTP_HOST="ict.example.org", HTTP_HX_REQUEST="true")
        original = HttpResponseRedirect("/login/")
        original.set_cookie("sessionid", "abc")
        redirect = htmx_full_redirect(request, original)
        self.assertIn("sessionid", redirect.cookies)

    def test_non_redirect_untouched(self):
        request = RequestFactory().get("/x/", HTTP_HOST="ict.example.org", HTTP_HX_REQUEST="true")
        response = HtmxVaryMiddleware(lambda req: HttpResponse("<p>ok</p>"))(request)
        self.assertEqual(response.status_code, 200)


class BoostGuardScriptTests(SimpleTestCase):
    base = Path(settings.BASE_DIR)

    def test_script_exists_and_guards_full_documents(self):
        script = (self.base / "static/js/boost-guard.js").read_text()
        for needle in ("htmx:beforeSwap", "htmx:responseError", "htmx:sendError", "htmx:timeout",
                       "window.location.assign", "SLOW_MS"):
            self.assertIn(needle, script)

    def test_both_shells_load_it_after_htmx(self):
        for name in ("templates/base.html", "templates/qa_portal/base_portal.html"):
            html = (self.base / name).read_text()
            self.assertIn("js/boost-guard.js", html, name)
            self.assertLess(html.index("vendor/htmx.min.js"), html.index("js/boost-guard.js"), name)
