"""v2.1 Phase A (A10): GET/PATCH me/appearance/ mirrors POST /me/preferences/."""
from config.models import ConfigEntry

from .conftest import BASE, bearer


def test_a10_api_appearance_get_patch(staff_api, staff_user):
    c = bearer(staff_api, staff_user)
    r = c.get(BASE + "me/appearance/")
    assert r.status_code == 200 and r.json()["effective"]["palette"] == "green"
    assert "plum" in r.json()["choices"]["palette"]
    r = c.patch(BASE + "me/appearance/", {"palette": "plum", "theme": "dark"}, format="json")
    assert r.status_code == 200 and r.json()["effective"]["palette"] == "plum"
    staff_user.refresh_from_db()
    assert staff_user.preferences["palette"] == "plum"
    assert c.patch(BASE + "me/appearance/", {"palette": "neon"}, format="json").status_code == 400
    r = c.patch(BASE + "me/appearance/", {"palette": ""}, format="json")
    assert r.json()["effective"]["palette"] == "green"


def test_a10_api_appearance_site_lock(staff_api, staff_user):
    ConfigEntry.objects.create(key="appearance.allow_user_palette", value="false", value_type="bool",
                               group="Appearance", editable_by="config.change_configentry")
    c = bearer(staff_api, staff_user)
    assert c.patch(BASE + "me/appearance/", {"palette": "blue"}, format="json").status_code == 403
    assert c.patch(BASE + "me/appearance/", {"theme": "dark"}, format="json").status_code == 200


def test_a10_api_appearance_needs_auth(staff_api):
    assert staff_api.get(BASE + "me/appearance/").status_code == 401


def test_a10_api_appearance_portal_door(portal_api, make_user, institution):
    iu = make_user("inst.api@t.local", user_type="institution", institution=institution, role="inst-admin")
    r = bearer(portal_api, iu).patch(BASE + "me/appearance/", {"theme": "light"}, format="json")
    assert r.status_code == 200
    iu.refresh_from_db()
    assert iu.preferences["theme"] == "light"
