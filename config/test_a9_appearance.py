"""v2.1 Phase A: appearance engine (A9), personal preferences (A10), motion
kit (A9/A.6) and the CSS consolidation (V7, T-UX-2, T-CONS-7)."""
import json
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client

from config import appearance as ap
from config.models import ConfigEntry
from qa_core.tests.conftest import *  # noqa: F401,F403  (make_user, make_institution, qa_seed, portal_host)

PW = "Test-pass-2026!"
BASE_DIR = Path(settings.BASE_DIR)


@pytest.fixture
def admin(make_user):
    return make_user("site.admin@t.local", superuser=True)


@pytest.fixture
def staff(make_user):
    return make_user("plain.staff@t.local")


def _client(user=None, host="localhost"):
    c = Client(HTTP_HOST=host)
    if user is not None:
        c.force_login(user)
    return c


def _prefs(html):
    m = re.search(r'<script id="nbte-prefs" type="application/json">(.*?)</script>', html, re.S)
    assert m, "nbte-prefs json_script missing"
    return json.loads(m.group(1))


def _set(key, value):
    from config.settings_catalogue import SETTINGS

    row = {r[0]: r for r in SETTINGS}[key]
    entry, _ = ConfigEntry.objects.get_or_create(key=key, scope="global", defaults={
        "value": row[1], "value_type": row[2], "group": row[3], "editable_by": row[5]})
    entry.value = value
    entry.save()


def _form(**over):
    data = {"palette": "green", "custom_primary": "#0e7490", "custom_accent": "#f59e0b", "theme": "system",
            "scene": "laminar", "motion": "full", "density": "comfortable", "allow_user_palette": "true",
            "portal_follows_site": "true"}
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


# ---------------------------------------------------------------------------
# A9 site appearance
# ---------------------------------------------------------------------------
def test_a9_appearance_settings_saved_with_audit(db, admin):
    from core.models import AuditLogEntry

    c = _client(admin)
    r = c.get("/settings/appearance/")
    assert r.status_code == 200 and b"Colour palette" in r.content and b"data-apx-iframe" in r.content
    r = c.post("/settings/appearance/", _form(palette="blue", scene="komorebi", motion="reduced"))
    assert r.status_code == 302
    assert ConfigEntry.objects.get(key="appearance.palette").value == "blue"
    assert ConfigEntry.objects.get(key="appearance.scene").value == "komorebi"
    entry = ConfigEntry.objects.get(key="appearance.palette")
    assert AuditLogEntry.objects.filter(model_label="config.ConfigEntry", object_pk=str(entry.pk)).exists()


def test_a9_appearance_htmx_save_returns_form_fragment(db, admin):
    r = _client(admin).post("/settings/appearance/", _form(palette="plum"), HTTP_HX_REQUEST="true")
    html = r.content.decode()
    assert r.status_code == 200 and 'id="apx-form"' in html and "<html" not in html
    assert "Appearance saved" in html and "m-check" in html


def test_a9_appearance_page_needs_permission(db, staff):
    assert _client(staff).get("/settings/appearance/").status_code == 403
    assert _client(staff).post("/settings/appearance/", _form(palette="blue")).status_code == 403
    assert not ConfigEntry.objects.filter(key="appearance.palette", value="blue").exists()


def test_a9_palette_recolours_both_portals(db, admin, staff, make_user, make_institution, qa_seed, portal_host):
    _client(admin).post("/settings/appearance/", _form(palette="teal"))
    # internal system: staff home and the public landing
    assert _prefs(_client(staff).get("/home/").content.decode())["palette"] == "teal"
    assert _prefs(_client().get("/").content.decode())["palette"] == "teal"
    # QA portal: public landing and an institution user's dashboard
    assert _prefs(_client(host=portal_host).get("/").content.decode())["palette"] == "teal"
    inst = make_institution("APA")
    iu = make_user("inst.admin@t.local", user_type="institution", institution=inst, role="inst-admin")
    html = _client(iu, host=portal_host).get("/").content.decode()
    assert _prefs(html)["palette"] == "teal"
    assert "nbte-core.bundle.css" in html


def test_a9_portal_follows_site_off_keeps_institutions_green(db, make_user, make_institution, qa_seed, portal_host):
    _set("appearance.palette", "plum")
    _set("appearance.portal_follows_site", "false")
    inst = make_institution("APB")
    iu = make_user("inst.b@t.local", user_type="institution", institution=inst, role="inst-admin")
    assert _prefs(_client(iu, host=portal_host).get("/").content.decode())["palette"] == "green"
    assert _prefs(_client(host=portal_host).get("/").content.decode())["palette"] == "green"
    assert _prefs(_client().get("/").content.decode())["palette"] == "plum"


def test_a9_custom_pair_printed_only_when_valid(db, staff):
    _set("appearance.custom_primary", "#0b5d6b")
    _set("appearance.palette", "custom")
    html = _client(staff).get("/home/").content.decode()
    assert '<style id="nbte-custom-pair">:root{--custom-primary:#0b5d6b;--custom-accent:#f59e0b}</style>' in html


def test_colour_validator_rejects_injection(db, admin):
    for bad in ("red", "#12345", "#0e7490;}</style><script>alert(1)</script>", "url(x)", "#GGGGGG"):
        with pytest.raises(ValidationError):
            ap.validate_hex(bad)
        entry = ConfigEntry(key="appearance.custom_primary", value=bad, value_type="str")
        with pytest.raises(ValidationError):
            entry.full_clean()
    # contrast: a pale primary cannot carry white text
    with pytest.raises(ValidationError) as exc:
        ap.validate_primary_colour("#f5e663")
    assert "contrast" in str(exc.value)
    assert ap.validate_primary_colour("#0E7490") == "#0e7490"
    # the form refuses it with a message and saves nothing
    r = _client(admin).post("/settings/appearance/", _form(palette="custom", custom_primary="#f5e663"))
    assert r.status_code == 200 and b"contrast ratio" in r.content
    r = _client(admin).post("/settings/appearance/", _form(palette="custom", custom_primary="</style><b>"))
    assert b"#rrggbb" in r.content and b"</style><b>" not in r.content
    assert not ConfigEntry.objects.filter(key="appearance.palette", value="custom").exists()


def test_colour_validator_unknown_choice_refused(db):
    with pytest.raises(ValidationError):
        ConfigEntry(key="appearance.palette", value="neon", value_type="str").full_clean()


def test_a9_bad_stored_value_never_reaches_page(db, staff):
    # bypass validation (e.g. a raw SQL edit): the context processor still sanitises
    ConfigEntry.objects.bulk_create([
        ConfigEntry(key="appearance.palette", value="custom", value_type="str", scope="global"),
        ConfigEntry(key="appearance.custom_primary", value="red;}body{display:none", value_type="str", scope="global"),
    ])
    html = _client(staff).get("/home/").content.decode()
    assert "display:none" not in html and "--custom-primary:#0e7490" in html


def test_a9_setting_tag(db):
    from django.template import Context, Template

    _set("appearance.scene", "blinds")
    t = Template('{% load nbte_settings %}[{% setting "appearance.scene" "laminar" %}]'
                 '[{% setting "no.such.key" "dflt" %}]{% setting "appearance.scene" as s %}<{{ s }}>')
    assert t.render(Context()) == "[blinds][dflt]<blinds>"


def test_a9_scene_on_staff_home(db, staff):
    _set("appearance.scene", "quicksilver")
    html = _client(staff).get("/home/").content.decode()
    assert "scene scene--quicksilver home-hero-scene" in html
    _set("appearance.scene", "none")
    assert "home-hero-scene" not in _client(staff).get("/home/").content.decode()


def test_a9_preview_is_framable_same_origin_only(db, admin, staff):
    r = _client(admin).get("/settings/appearance/preview/home/")
    assert r.status_code == 200 and r["X-Frame-Options"] == "SAMEORIGIN"
    assert _client(staff).get("/settings/appearance/preview/home/").status_code == 403
    assert _client(admin).get("/settings/appearance/preview/nope/").status_code == 404


# ---------------------------------------------------------------------------
# A10 personal preferences
# ---------------------------------------------------------------------------
def test_a10_user_preferences_endpoint(db, staff):
    c = _client(staff)
    r = c.post("/me/preferences/", json.dumps({"palette": "blue", "theme": "dark", "density": "compact"}),
               content_type="application/json")
    assert r.status_code == 200
    staff.refresh_from_db()
    assert staff.preferences["palette"] == "blue" and staff.preferences["theme"] == "dark"
    assert isinstance(staff.preferences["stamp"], int)
    got = c.get("/me/preferences/").json()
    assert got["effective"]["palette"] == "blue" and got["effective"]["density"] == "compact"
    # back to the site default
    c.post("/me/preferences/", json.dumps({"palette": ""}), content_type="application/json")
    staff.refresh_from_db()
    assert "palette" not in staff.preferences and staff.preferences["theme"] == "dark"
    # invalid values and keys are refused
    assert c.post("/me/preferences/", json.dumps({"palette": "neon"}), content_type="application/json").status_code == 400
    assert c.post("/me/preferences/", json.dumps({"is_superuser": True}),
                  content_type="application/json").status_code == 400
    assert c.post("/me/preferences/", "{nope", content_type="application/json").status_code == 400
    # anonymous
    assert _client().post("/me/preferences/", json.dumps({"palette": "blue"}),
                          content_type="application/json").status_code == 401


def test_a10_personal_palette_follows_the_user(db, staff, make_user):
    _client(staff).post("/me/preferences/", json.dumps({"palette": "plum"}), content_type="application/json")
    # another browser / device: a fresh session
    prefs = _prefs(_client(staff).get("/home/").content.decode())
    assert prefs["palette"] == "plum" and prefs["stamp"] == staff.__class__.objects.get(pk=staff.pk).preferences["stamp"]
    # nobody else gets it
    other = make_user("other.person@t.local")
    assert _prefs(_client(other).get("/home/").content.decode())["palette"] == "green"


def test_a10_site_lock_allows_theme_only(db, staff):
    _set("appearance.allow_user_palette", "false")
    c = _client(staff)
    assert c.post("/me/preferences/", json.dumps({"palette": "blue"}), content_type="application/json").status_code == 403
    assert c.post("/me/preferences/", json.dumps({"theme": "dark"}), content_type="application/json").status_code == 200
    staff.preferences = {"palette": "blue", "theme": "dark", "stamp": 1}
    staff.save()
    prefs = _prefs(c.get("/home/").content.decode())
    assert prefs["palette"] == "green" and prefs["theme"] == "dark" and prefs["lock"] is True


def test_a10_motion_site_is_a_ceiling(db, staff):
    _set("appearance.motion", "reduced")
    staff.preferences = {"motion": "full"}
    staff.save()
    assert _prefs(_client(staff).get("/home/").content.decode())["motion"] == "reduced"
    staff.preferences = {"motion": "off"}
    staff.save()
    assert _prefs(_client(staff).get("/home/").content.decode())["motion"] == "off"


def test_a10_profile_card_htmx(db, staff):
    c = _client(staff)
    html = c.get("/accounts/profile/").content.decode()
    assert 'id="profile-appearance"' in html and 'hx-post="/me/preferences/"' in html
    r = c.post("/me/preferences/", {"palette": "gold", "theme": "system", "motion": "system", "density": ""},
               HTTP_HX_REQUEST="true")
    frag = r.content.decode()
    assert r.status_code == 200 and "Saved to your account" in frag and "<html" not in frag
    staff.refresh_from_db()
    assert staff.preferences.get("palette") == "gold" and "motion" not in staff.preferences
    # plain form post (no JavaScript) redirects back
    r = c.post("/me/preferences/", {"palette": "teal", "next": "/accounts/profile/"})
    assert r.status_code == 302 and r["Location"] == "/accounts/profile/"
    r = c.post("/me/preferences/", {"palette": "teal", "next": "https://evil.example/"})
    assert r["Location"] == "/"


def test_a10_preferences_refused_while_impersonating(db, admin, staff):
    from core.impersonation import IMPERSONATE_SESSION_KEY

    c = _client(admin)
    s = c.session
    s[IMPERSONATE_SESSION_KEY] = staff.pk
    s.save()
    r = c.post("/me/preferences/", json.dumps({"palette": "blue"}), content_type="application/json")
    assert r.status_code == 403
    staff.refresh_from_db()
    assert "palette" not in (staff.preferences or {})


def test_a10_preferences_on_portal_host(db, make_user, make_institution, qa_seed, portal_host):
    inst = make_institution("APC")
    iu = make_user("inst.c@t.local", user_type="institution", institution=inst, role="inst-admin")
    r = _client(iu, host=portal_host).post("/me/preferences/", json.dumps({"theme": "dark"}),
                                           content_type="application/json")
    assert r.status_code == 200
    iu.refresh_from_db()
    assert iu.preferences["theme"] == "dark"


# ---------------------------------------------------------------------------
# A.6 motion kit
# ---------------------------------------------------------------------------
def test_a6_motion_kit_installed(db, staff):
    html = _client(staff).get("/home/").content.decode()
    assert "js/motion.js" in html and 'class="app-content m-page"' in html
    assert 'class="home-kpis m-stagger"' in html
    css = (BASE_DIR / "static/css/motion.css").read_text()
    # gated by html.m-on, entry animations never leave content hidden (fill-mode backwards)
    assert ".m-on .m-stagger > *" in css and "backwards" in css and "forwards" not in css
    assert ':root[data-motion="off"] *' in css and "prefers-reduced-motion" in css
    init = (BASE_DIR / "static/js/theme-init.js").read_text()
    assert "m-on" in init and "osReduce()" in init


def test_a6_kpis_render_their_value_without_javascript(db, staff):
    html = _client(staff).get("/home/").content.decode()
    nums = re.findall(r'class="kpi-num" data-count="(\d+)">(\d+)</div>', html)
    assert nums and all(a == b for a, b in nums)


def test_a6_reduced_motion_levels_in_css(db):
    base = (BASE_DIR / "static/css/base.css").read_text()
    assert '[data-motion="off"]' in base and '[data-motion="reduced"]' in base


# ---------------------------------------------------------------------------
# V7 / T-UX-2 / T-CONS-7 CSS consolidation
# ---------------------------------------------------------------------------
def test_v7_css_bundles_up_to_date():
    from core import css_bundles

    assert css_bundles.stale() == [], "run: python manage.py build_css"
    core = (BASE_DIR / "static/css/nbte-core.bundle.css").read_text()
    assert core.index("@layer tokens, base, components, motion, pages;") < core.index("@layer tokens {")


def _sheets(html):
    return re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)


def test_v7_pages_load_at_most_five_stylesheets(db, staff, admin, make_user, make_institution, qa_seed, portal_host):
    inst = make_institution("APD")
    iu = make_user("inst.d@t.local", user_type="institution", institution=inst, role="inst-admin")
    pages = [(_client(), "/"), (_client(), "/accounts/login/"), (_client(staff), "/home/"),
             (_client(staff), "/accounts/profile/"), (_client(admin), "/settings/appearance/"),
             (_client(admin), "/settings/"), (_client(host=portal_host), "/"),
             (_client(host=portal_host), "/login/"), (_client(iu, host=portal_host), "/")]
    for c, url in pages:
        r = c.get(url)
        assert r.status_code == 200, url
        sheets = _sheets(r.content.decode())
        assert len(sheets) <= 5, (url, sheets)
        assert any("nbte-core.bundle.css" in s for s in sheets), url


def test_v7_legacy_css_fallback(db, staff):
    _set("ui.legacy_css", "true")
    sheets = _sheets(_client(staff).get("/home/").content.decode())
    assert "/static/css/tokens.css" in sheets and "/static/css/apple.css" in sheets
    assert not any("bundle" in s for s in sheets)


def test_v7_no_manual_cache_busters_in_templates():
    hits = [str(p) for p in (BASE_DIR / "templates").rglob("*.html") if re.search(r"%\}\?v=", p.read_text())]
    assert hits == []


def test_v7_components_use_semantic_brand_tokens():
    """Palettes recolour components because components never name the raw
    brand scale or the brand hex (tokens.css defines them; the printed staff
    ID card keeps its fixed green on purpose)."""
    bad = []
    for p in (BASE_DIR / "static/css").glob("*.css"):
        if p.name in ("tokens.css", "staff-id.css") or p.name.endswith(".bundle.css"):
            continue
        text = p.read_text()
        for pat in (r"var\(--brand-(?:\d|glow)", r"var\(--green-\d", r"#1f6b3e(?![^;]*fallback)", r"rgba\(31, ?107, ?62"):
            for m in re.finditer(pat, text, re.I):
                line = text[:m.start()].count("\n") + 1
                snippet = text.splitlines()[line - 1]
                if text[max(0, m.start() - 2):m.start()] == ", ":   # a var(--semantic, #fallback)
                    continue
                bad.append(f"{p.name}:{line}")
    assert bad == []


def test_v7_palettes_reassign_semantic_tokens_only():
    css = (BASE_DIR / "static/css/tokens.css").read_text()
    blocks = re.findall(r':root\[data-palette[^{]*\{([^}]*)\}', css)
    assert len(blocks) >= 8
    for body in blocks:
        for name in re.findall(r"(--[a-z0-9-]+)\s*:", body):
            assert not name.startswith(("--brand-", "--qa-tile", "--qa-c-", "--qa-glow", "--success", "--danger")), name


def test_static_manifest_builds(tmp_path, settings):
    """collectstatic with the production storage (hashed names) succeeds."""
    from django.core.management import call_command

    settings.STATIC_ROOT = tmp_path / "static"
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "core.storage.NBTEManifestStaticFilesStorage"}}
    call_command("collectstatic", "--noinput", verbosity=0)
    manifest = json.loads((tmp_path / "static" / "staticfiles.json").read_text())["paths"]
    assert re.match(r"css/nbte-core\.bundle\.[0-9a-f]{12}\.css", manifest["css/nbte-core.bundle.css"])


def test_v7_prod_settings_use_manifest_storage():
    text = (BASE_DIR / "nbte_ict/settings/prod.py").read_text()
    assert "core.storage.NBTEManifestStaticFilesStorage" in text
    from django.conf import settings as s
    assert "Manifest" not in s.STORAGES["staticfiles"]["BACKEND"]   # tests/dev stay plain
