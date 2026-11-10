"""v2.1 X52: the button spacing standard."""
from pathlib import Path

import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db
CSS = Path(settings.BASE_DIR) / "static" / "css" / "components-buttons.css"


def test_x52_button_group_css_present():
    css = CSS.read_text()
    for rule in (".btn-group {", ".btn-group--end", ".btn-group--between", ".btn-group--stack-sm",
                 "var(--btn-gap", ":where(.btn, .att-btn, .qa-btn) + :where(.btn, .att-btn, .qa-btn)"):
        assert rule in css, rule


def test_x52_stylesheet_loaded_everywhere(client):
    # v2.1 V7: both files are in the core bundle (components / pages layers)
    from core import css_bundles

    html = client.get("/").content.decode()
    assert "css/nbte-core.bundle.css" in html
    files = [f for _layer, f in css_bundles.source_files("core")]
    assert "components-buttons.css" in files and "brand.css" in files


def test_x52_button_gap_setting(client):
    from config.models import ConfigEntry
    from config.registry import invalidate

    assert "--btn-gap" not in client.get("/").content.decode()   # default 8px: no override
    e, _ = ConfigEntry.objects.get_or_create(key="appearance.button_gap", scope="global",
                                             defaults={"value": "8", "value_type": "int"})
    e.value = "12"
    e.save()
    invalidate("appearance.button_gap")
    assert ":root { --btn-gap: 12px; }" in client.get("/").content.decode()
    e.value = "500"   # out of range falls back to the default
    e.save()
    invalidate("appearance.button_gap")
    assert "--btn-gap" not in client.get("/").content.decode()


def test_x52_attendance_actions_use_btn_group():
    tpl = (Path(settings.BASE_DIR) / "templates" / "attendance" / "home.html").read_text()
    assert tpl.count('att-today-action btn-group') == 3
    for name in ("all.html", "department.html", "my_attendance.html"):
        assert 'qa-hero__actions btn-group' in (Path(settings.BASE_DIR) / "templates" / "attendance" / name).read_text()
