"""Phase 0 / N2, V6, T-UX-1: browser Back with HTMX.

* every HTML response varies on the HTMX headers;
* HTMX partials are not stored by the browser cache;
* a history-restore request is answered with the full page;
* every URL a template pushes to history renders a full page on a plain GET;
* fragment-only endpoints redirect a plain request to their host page.
"""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.urls import NoReverseMatch, reverse

from core.middleware import HtmxVaryMiddleware
from qa_core.hosts import portal_reverse
from qa_core.tests.phase2_fixtures import *  # noqa: F401,F403
from qa_core.ui import is_htmx

HX = {"HTTP_HX_REQUEST": "true"}
RESTORE = {"HTTP_HX_REQUEST": "true", "HTTP_HX_HISTORY_RESTORE_REQUEST": "true", "HTTP_HX_TARGET": "main"}


def _view(request):
    if is_htmx(request):
        return HttpResponse("<div>partial</div>")
    return HttpResponse("<!doctype html><html><body>full</body></html>")


def _run(**headers):
    mw = HtmxVaryMiddleware(_view)
    return mw(RequestFactory().get("/x/", **headers))


def test_n2_vary_header_on_html_responses():
    r = _run()
    vary = {v.strip() for v in r["Vary"].split(",")}
    assert {"HX-Request", "HX-Target", "HX-History-Restore-Request"} <= vary


@pytest.mark.django_db
def test_n2_vary_header_on_real_page():
    r = Client().get("/accounts/login/")
    assert r.status_code == 200
    assert "HX-Request" in r["Vary"] and "HX-History-Restore-Request" in r["Vary"]


def test_n2_partial_responses_not_cached():
    r = _run(**HX)
    assert b"partial" in r.content
    assert "no-store" in r["Cache-Control"]
    assert not _run().has_header("Cache-Control")


def test_n2_history_restore_gets_full_page():
    r = _run(**RESTORE)
    assert b"<html>" in r.content and b"partial" not in r.content
    assert not r.has_header("Cache-Control")


@pytest.mark.django_db
def test_n2_history_restore_full_page_on_render_qa(make_user, qa_seed, client_for):
    """render_qa answers HX-Target with a partial, but never on history restore."""
    user = make_user("hist.es@t.local", role="executive-secretary", superuser=True)
    c = client_for(user)
    url = reverse("qa_inst:list")
    part = c.get(url, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-rows")
    full = c.get(url, **RESTORE)
    assert full.status_code == 200
    assert b"<html" in full.content.lower()
    assert len(full.content) > len(part.content)


# --------------------------------------------------------------------------
# Every pushed URL renders a full page without the HTMX headers.
# --------------------------------------------------------------------------
TAG = re.compile(r"<[a-zA-Z][^>]*hx-push-url=\"(?!false)[^\"]*\"[^>]*>", re.S)
URL_NAME = re.compile(r"hx-get=\"\{%\s*url\s+'([\w:-]+)'\s*%\}")


def _pushed_url_names():
    names = set()
    for path in Path(settings.BASE_DIR, "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for tag in TAG.findall(text):
            names.update(URL_NAME.findall(tag))
    return sorted(names)


def test_n2_template_scan_finds_pushed_urls():
    assert len(_pushed_url_names()) > 10


@pytest.mark.django_db
def test_n2_pushed_urls_render_full_page_without_hx_header(make_user, qa_seed, client_for, inst_user,
                                                          portal_host):
    """Every pushed URL that takes no arguments, opened in a new tab (no HTMX
    headers), is a full page (or a redirect / refusal), never a bare partial."""
    staff = make_user("hist.su@t.local", role="executive-secretary", superuser=True)
    staff_c = client_for(staff)
    portal_c = client_for(inst_user, host=portal_host)
    checked, bad = 0, []
    for name in _pushed_url_names():
        try:
            if name.startswith("qa_portal:"):
                url, client = portal_reverse(name), portal_c
            else:
                url, client = reverse(name), staff_c
        except NoReverseMatch:
            continue  # needs arguments: covered by the live crawl (docs)
        r = client.get(url)
        checked += 1
        if r.status_code == 200 and "text/html" in r.get("Content-Type", ""):
            if b"<html" not in r.content[:4000].lower():
                bad.append(name)
    assert checked >= 10
    assert not bad, f"pushed URLs rendering a bare partial: {bad}"


# --------------------------------------------------------------------------
# Fragment-only endpoints (e.g. /application/programmes/, shot 58b).
# --------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("name,parent", [
    ("qa_portal:programmes", "qa_portal:application"),
    ("qa_portal:programme_estimate", "qa_portal:application"),
    ("qa_portal:institution_users", "qa_portal:institution"),
    ("qa_portal:documents_summary", "qa_portal:documents"),
    ("qa_portal:complaint_suggest", "qa_portal:complaint_new"),
])
def test_n2_fragment_urls_redirect_plain_get_to_full_page(name, parent, inst_user, client_for, portal_host):
    c = client_for(inst_user, host=portal_host)
    url = portal_reverse(name)
    plain = c.get(url)
    assert plain.status_code == 302
    assert plain["Location"].split("#")[0] == portal_reverse(parent)
    page = c.get(url, follow=True)
    assert page.status_code == 200 and b"<html" in page.content.lower()
    # HTMX still gets the fragment; a history restore gets the full page.
    frag = c.get(url, **HX)
    assert frag.status_code in (200, 404)  # estimate 404s without a programme
    if frag.status_code == 200:
        assert b"<html" not in frag.content.lower()
    assert c.get(url, **RESTORE).status_code == 302


@pytest.mark.django_db
def test_n2_programmes_plain_post_redirects(inst_user, client_for, portal_host):
    """A no-JS form post never leaves the user on a bare fragment."""
    c = client_for(inst_user, host=portal_host)
    r = c.post(portal_reverse("qa_portal:programmes"), {"name": "x"})
    # Not at programme selection: refused (404) rather than a partial page.
    assert r.status_code in (302, 404)
