"""Host routing (Master Plan §3, §16): the portal host has its own URLconf
that contains no internal route; the two URL tables are disjoint."""
import importlib

import pytest
from django.conf import settings
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from qa_core import hosts


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p


def _routes(urlconf):
    return {route: p for route, p in _walk(get_resolver(urlconf).url_patterns)}


SHARED_INFRA = ("^static/", "static/")  # dev-only static file serving exists on both


def _business(routes):
    return {r for r in routes if not r.startswith(SHARED_INFRA)}


def test_url_tables_are_disjoint():
    internal = _business(_routes(settings.ROOT_URLCONF))
    portal = _business(_routes(settings.QA_PORTAL_URLCONF))
    assert portal, "portal URLconf is empty"
    # The portal's own pages; listed so a reviewer sees exactly what exists.
    assert portal == {
        "", "login/", "logout/", "password/forgot/", "password/forgot/sent/", "password/set/done/",
        "password/set/<uidb64>/<token>/", "password/change/", "account/", "impersonation/stop/",
        # Phase 2: institution record, documents, onboarding application,
        # payments (incl. provider webhooks, slip verification, dev simulator),
        # protected downloads and QA impersonation hand-off.
        "register/", "institution/", "institution/users/", "institution/lgas/", "documents/",
        "documents/summary/", "documents/upload/<int:kind_id>/", "timeline/", "application/",
        "application/start/", "application/submit/", "application/programmes/",
        "application/programmes/<int:pk>/remove/", "payments/", "payments/<int:pk>/", "payments/<int:pk>/slip/",
        "payments/<int:pk>/online/", "payments/<int:pk>/proof/", "payments/<int:pk>/receipt/",
        "payments/webhooks/paystack/", "payments/webhooks/remita/", "payments/simulator/<str:provider>/<str:ref>/",
        "verify/<str:token>/", "download/<str:label>/<int:pk>/", "impersonate/<str:token>/", "impersonation/ended/",
        # Phase 3: QA requests (SOP-02), the SSQ workspace and queries.
        "qa-requests/", "qa-requests/new/", "qa-requests/<int:pk>/", "qa-requests/<int:pk>/submit/",
        "qa-requests/<int:pk>/cancel/", "qa-requests/<int:pk>/add/", "qa-requests/<int:pk>/programmes/<int:row>/drop/",
        "qa-requests/<int:pk>/programmes/<int:row>/replace/", "qa-requests/<int:pk>/ssq/<int:ssq_pk>/",
        "qa-requests/<int:pk>/ssq/<int:ssq_pk>/save/", "qa-requests/<int:pk>/ssq/<int:ssq_pk>/upload/<slug:field>/",
        "qa-requests/<int:pk>/ssq/<int:ssq_pk>/reuse/<slug:field>/",
        "qa-requests/<int:pk>/ssq/<int:ssq_pk>/evidence/<int:ev>/remove/",
        "qa-requests/<int:pk>/ssq/<int:ssq_pk>/department/", "qa-requests/<int:pk>/ssq/<int:ssq_pk>/recall/",
        "qa-requests/<int:pk>/ssq/<int:ssq_pk>/department-done/", "qa-requests/<int:pk>/ssq/<int:ssq_pk>/submit/",
        "qa-requests/<int:pk>/ssq/<int:ssq_pk>/revise/", "queries/", "queries/<int:pk>/",
        # Phase 5: the resource-person portal and the institution's inspections list.
        "engagements/", "service-history/", "profile/", "inspections/",
        # Phase 6: the public how-to page and the Word guides it offers.
        "how-to/", "guides/<slug:slug>/",
        # Phase 7: every notification (the bell's "View all"), complaints and the knowledge base.
        "alerts/", "complaints/", "complaints/new/", "complaints/suggest/", "complaints/<int:pk>/",
        "complaints/<int:pk>/reply/", "complaints/<int:pk>/act/<str:action>/",
        "complaints/<int:pk>/files/<int:file_pk>/", "help/", "help/<slug:slug>/",
    }
    # "" (the portal dashboard) is the only literal both share: on the internal
    # host "" is the public landing page. Everything else is disjoint.
    assert internal & portal <= {""}
    views_internal = {f"{p.callback.__module__}.{p.callback.__name__}" for p in _routes(settings.ROOT_URLCONF).values()}
    views_portal = {f"{p.callback.__module__}.{p.callback.__name__}" for p in _routes(settings.QA_PORTAL_URLCONF).values()}
    assert not ({v for v in views_portal if "staticfiles" not in v} & views_internal)


def test_portal_urlconf_has_no_internal_prefixes():
    portal = _business(_routes(settings.QA_PORTAL_URLCONF))
    for prefix in ("admin", "accounts", "settings", "qa/", "tasks", "workflow", "staff", "messages",
                   "notifications", "dashboards", "tickets", "assets", "attendance", "leave", "portal",
                   "home", "manage", "showcase"):
        assert not any(r.startswith(prefix) for r in portal), prefix


def _internal_paths():
    """Every internal route without parameters, reversed to a real path."""
    paths = set()
    for route, p in _routes(settings.ROOT_URLCONF).items():
        if "<" in route or "(?P" in route or route.startswith(SHARED_INFRA) or route in ("",):
            continue
        paths.add("/" + route.lstrip("^").rstrip("$"))
    return sorted(paths)


@pytest.mark.django_db
def test_every_internal_url_is_404_on_the_portal_host(portal_host, make_user):
    client = Client(HTTP_HOST=portal_host)
    paths = _internal_paths()
    assert len(paths) > 50
    leaked = []
    for path in paths:
        r = client.get(path)
        if r.status_code != 404:
            leaked.append((path, r.status_code))
    assert not leaked, leaked[:10]


@pytest.mark.django_db
def test_qa_internal_screens_404_on_portal_even_for_staff(qa_seed, portal_host, officer):
    client = Client(HTTP_HOST=portal_host)
    client.force_login(officer)
    for name in ("qa:settings_rules", "qa:pending_list", "qa:settings_formulas"):
        path = reverse(name)  # reversed against the internal URLconf
        r = client.get(path)
        assert r.status_code in (302, 404)  # staff are redirected off the portal first
        if r.status_code == 302:
            assert hosts.internal_host_for(r.wsgi_request) in r["Location"]


@pytest.mark.django_db
def test_portal_pages_answer_on_portal_host_only(portal_host):
    portal = Client(HTTP_HOST=portal_host)
    assert portal.get("/login/").status_code == 200
    internal = Client(HTTP_HOST="localhost")
    assert internal.get("/login/").status_code == 404
    assert internal.get("/password/forgot/").status_code == 404


def test_host_matching_ignores_port_and_case(rf, settings):
    settings.QA_PORTAL_HOST = "qa.nbte.gov.ng"
    req = rf.get("/", HTTP_HOST="QA.NBTE.GOV.NG:8443")
    settings.ALLOWED_HOSTS = ["*"]
    assert hosts.is_portal_request(req)
    assert not hosts.is_portal_request(rf.get("/", HTTP_HOST="ict.nbte.gov.ng"))
    settings.INTERNAL_HOST = ""
    assert hosts.internal_host_for(req) == "nbte.gov.ng:8443"


def test_portal_urlconf_imports_cleanly():
    importlib.import_module(settings.QA_PORTAL_URLCONF)
