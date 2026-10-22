"""Error envelope for 400/401/403/404/429/500, headers, health, schema/docs."""
import io

import pytest
from django.core.management import call_command
from django.test import override_settings

from .conftest import BASE, bearer, err, login

pytestmark = pytest.mark.django_db


def _is_envelope(r, code, status):
    assert r.status_code == status, r.content
    assert r["Content-Type"].startswith("application/json")
    body = r.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code and body["error"]["message"]
    assert r["X-NBTE-Min-App-Version"] == "1.0.0"
    return body["error"]


def test_400_validation_error(staff_api):
    e = _is_envelope(staff_api.post(BASE + "auth/token/", {}, format="json"), "validation_error", 400)
    assert set(e["fields"]) == {"email", "password"}


def test_400_bad_json(staff_api):
    r = staff_api.post(BASE + "auth/token/", "{not json", content_type="application/json")
    _is_envelope(r, "validation_error", 400)


def test_401_not_authenticated(staff_api):
    r = staff_api.get(BASE + "me/")
    _is_envelope(r, "not_authenticated", 401)
    assert r["WWW-Authenticate"].startswith("Bearer")
    staff_api.credentials(HTTP_AUTHORIZATION="Bearer garbage")
    _is_envelope(staff_api.get(BASE + "me/"), "not_authenticated", 401)


@override_settings(ROOT_URLCONF="api.tests.urls_failing")
def test_403_permission_denied(staff_api, staff_user):
    _is_envelope(bearer(staff_api, staff_user).get("/api/v1/denied/"), "permission_denied", 403)


def test_404_unknown_api_url(staff_api, portal_api):
    _is_envelope(staff_api.get(BASE + "no-such-endpoint/"), "not_found", 404)
    _is_envelope(portal_api.get(BASE + "no-such-endpoint/"), "not_found", 404)


@override_settings(ROOT_URLCONF="api.tests.urls_failing")
def test_404_from_a_view(staff_api):
    _is_envelope(staff_api.get("/api/v1/missing/"), "not_found", 404)


def test_429_throttled(staff_api, staff_user):
    for _ in range(10):
        login(staff_api, staff_user.email)
    e = _is_envelope(login(staff_api, staff_user.email), "throttled", 429)
    assert isinstance(e["retry_after"], int)


@override_settings(ROOT_URLCONF="api.tests.urls_failing")
def test_500_server_error_hides_internals(staff_api):
    r = staff_api.get("/api/v1/boom/")
    e = _is_envelope(r, "server_error", 500)
    assert "hunter2" not in r.content.decode() and "RuntimeError" not in r.content.decode()
    assert set(e) == {"code", "message"}


def test_405_method(staff_api):
    _is_envelope(staff_api.get(BASE + "auth/token/"), "method_not_allowed", 405)


def test_health_has_no_auth(staff_api, portal_api):
    for client in (staff_api, portal_api):
        r = client.get(BASE + "health/")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["min_app_version"] == "1.0.0" and "T" in body["server_time"]
        assert r["X-NBTE-Min-App-Version"] == "1.0.0"


def test_website_pages_get_no_api_header(staff_api):
    assert "X-NBTE-Min-App-Version" not in staff_api.get("/accounts/login/")


# ---------------------------------------------------------------------------
# Schema + docs
# ---------------------------------------------------------------------------
def test_docs_closed_in_production_for_non_superusers(staff_api, staff_user, make_user):
    assert staff_api.get(BASE + "schema/").status_code == 401
    assert bearer(staff_api, staff_user).get(BASE + "schema/").status_code == 403
    admin = make_user("root.docs@t.local", superuser=True)
    r = bearer(staff_api, admin).get(BASE + "schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json")
    assert r.status_code == 200 and r.json()["info"]["title"] == "Digital NBTE API"


@override_settings(DEBUG=True)
def test_docs_open_in_debug_on_both_doors(staff_api, portal_api):
    staff_schema = staff_api.get(BASE + "schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json").json()
    portal_schema = portal_api.get(BASE + "schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json").json()
    assert "/api/v1/me/staff-id/" in staff_schema["paths"]
    assert "/api/v1/me/staff-id/" not in portal_schema["paths"]
    assert "/api/v1/auth/token/" in portal_schema["paths"]
    for client in (staff_api, portal_api):
        r = client.get(BASE + "docs/")
        assert r.status_code == 200 and b"swagger" in r.content.lower()


@pytest.mark.parametrize("urlconf", ["nbte_ict.urls", "qa_core.portal_urls"])
def test_schema_generates_without_warnings(urlconf, tmp_path):
    out = tmp_path / "schema.yaml"
    call_command("spectacular", "--validate", "--fail-on-warn", "--urlconf", urlconf, "--file", str(out),
                 stdout=io.StringIO(), stderr=io.StringIO())
    text = out.read_text()
    assert "Digital NBTE API" in text and "/api/v1/home/" in text and "bearerAuth" in text
