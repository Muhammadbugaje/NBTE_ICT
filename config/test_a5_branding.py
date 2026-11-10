"""v2.1 Phase A: SVG brand marks and slots (A5, A6, T-CONS-7)."""
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from config import branding
from config.models import BrandLogo, ConfigEntry
from core.models import AuditLogEntry

pytestmark = pytest.mark.django_db
ROOT = Path(settings.BASE_DIR)


def _user(email, *perms):
    role = None
    if perms:
        role = Role.objects.create(code=email[:40], name=email)
        for p in perms:
            role.permissions.add(Permission.objects.get(codename=p))
    return User.objects.create_user(email, "Pass-word-123", role=role, must_change_password=False)


def test_no_png_logo_references():
    """No template, stylesheet or script names the old PNG logos (A5, T-CONS-7)."""
    hits = []
    for folder, pattern in (("templates", "*.html"), ("static/css", "*.css"), ("static/js", "*.js")):
        for f in (ROOT / folder).rglob(pattern):
            if re.search(r"[NM]BTE_logo\.png", f.read_text(errors="ignore")):
                hits.append(str(f.relative_to(ROOT)))
    assert hits == []
    assert not (ROOT / "static/img/MBTE_logo.png").exists()


def test_a5_brand_files_present():
    for name in ("nbte-logo-light.svg", "nbte-logo-dark.svg", "nbte-logo-mono.svg", "nbte-app-icon.svg",
                 "nbte-logo-mono-white.svg", "nbte-wordmark.svg", "nbte-logo-512.png", "nbte-icon-32.png"):
        assert (branding.BRAND_DIR / name).exists(), name
    assert Path(settings.PDF_LOGO_PATH).name == "nbte-logo-512.png"
    assert branding.pdf_logo_path("formal").suffix == ".png"


def test_favicon_view_serves_svg(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert r["Content-Type"] == "image/svg+xml"
    assert b"<svg" in r.content
    assert client.get("/favicon.ico").status_code == 302


def test_a6_brand_logo_tag_light_and_dark():
    branding.seed_builtin_logos()
    html = Template('{% load brand %}{% brand_logo "primary" 40 %}').render(Context({}))
    assert "brand-logo__light" in html and "brand-logo__dark" in html
    assert "nbte-logo-light.svg" in html and "nbte-logo-dark.svg" in html
    assert 'height="40"' in html


def test_a5_crest_partial_is_inline_and_animatable():
    html = Template('{% load brand %}{% crest "wm" %}{% crest "wm" %}{% crest "mark" label="NBTE" %}').render(Context({}))
    assert html.count("<svg") == 3
    assert 'class="nbte-cog"' in html and "nbte-ribbon" in html
    ids = re.findall(r'mask id="([^"]+)"', html)
    assert len(ids) == 2 and ids[0] != ids[1]          # unique ids per inline copy
    assert "var(--cr-teeth" in html                     # the colour mark follows the palette tokens


def test_a5_branding_select_changes_slot_with_audit():
    branding.seed_builtin_logos()
    admin = _user("brand.admin@demo.nbte.local", "manage_branding")
    c = Client()
    c.force_login(admin)
    assert c.get(reverse("config:branding")).status_code == 200
    r = c.post(reverse("config:branding_select"), {"slot": "primary", "code": "nbte-crest-mono"},
               HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"brand-slots-title" in r.content
    assert ConfigEntry.objects.get(key="brand.logo.primary").value == "nbte-crest-mono"
    assert AuditLogEntry.objects.filter(model_label="config.ConfigEntry", note="brand logo chosen").exists()
    assert "nbte-logo-mono.svg" in branding.active_logo_markup("primary")
    # an unknown slot or code changes nothing
    c.post(reverse("config:branding_select"), {"slot": "nope", "code": "nbte-crest"})
    assert ConfigEntry.objects.get(key="brand.logo.primary").value == "nbte-crest-mono"


def test_a5_branding_needs_permission():
    c = Client()
    c.force_login(_user("plain.staff@demo.nbte.local"))
    assert c.get(reverse("config:branding")).status_code == 403
    assert c.post(reverse("config:branding_select"), {"slot": "primary", "code": "x"}).status_code == 403


def test_a5_upload_refuses_svg(tmp_path):
    admin = _user("brand.up@demo.nbte.local", "manage_branding")
    c = Client()
    c.force_login(admin)
    svg = SimpleUploadedFile("evil.svg", b"<svg onload='alert(1)'/>", content_type="image/svg+xml")
    c.post(reverse("config:branding_upload"), {"name": "Evil", "image": svg})
    assert not BrandLogo.objects.filter(name="Evil").exists()


def test_a5_unfold_logo_follows_slot():
    from nbte_ict.settings.base import UNFOLD

    assert "nbte-logo-light.svg" in UNFOLD["SITE_LOGO"]["light"](None)
    assert "nbte-logo-dark.svg" in UNFOLD["SITE_LOGO"]["dark"](None)
