"""UI overhaul Phase 5: custom 403 / 404 / 500 templates."""
import pytest
from django.template import loader
from django.test import override_settings

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("name, text", [
    ("403.html", "You don't have access"),
    ("404.html", "find that page"),
    ("500.html", "Something went wrong"),
])
def test_error_templates_render_without_context(name, text):
    # 500.html is rendered by Django with an empty context — no request.
    html = loader.get_template(name).render({})
    assert text in html.replace("&#x27;", "'")
    assert "<svg" in html and "errors.css" in html


@override_settings(DEBUG=False)
def test_unknown_url_uses_custom_404(client):
    resp = client.get("/this-page-does-not-exist-ui/")
    assert resp.status_code == 404
    assert b"find that page" in resp.content
