"""v2.1 Phase 0, X49 slice: public, rate-limited LGA options for the register
page; FCT alias; validate_lga rejects LGAs of other states."""
import pytest
from django.core.exceptions import ValidationError
from django.test import Client


@pytest.fixture
def anon(portal_host):
    return Client(HTTP_HOST=portal_host)


def test_x49_public_lga_endpoint_anonymous(lookups, anon):
    from django.core.cache import cache

    cache.clear()
    r = anon.get(f"/register/lgas/?state={lookups.kaduna.pk}")
    assert r.status_code == 200
    html = r.content.decode()
    assert html.count("<option value=\"") - html.count("<option value=\"\"") == 23  # Kaduna's 23 LGAs
    assert "Zaria" in html and "Kaduna North" in html
    assert "max-age" in r["Cache-Control"]


def test_x49_fct_alias(qa_seed, anon):
    from django.core.cache import cache

    from institutions import services
    from staff.models import StateOfOrigin

    cache.clear()
    fct, _ = StateOfOrigin.objects.get_or_create(name="FCT", defaults={"code": "fc", "zone": "NC"})
    assert services.lgas_for(fct) == ["Abaji", "Abuja", "Bwari", "Gwagwalada", "Kuje", "Kwali"]
    assert "Gwagwalada" in anon.get(f"/register/lgas/?state={fct.pk}").content.decode()
    with pytest.raises(ValidationError):
        services.validate_lga(fct, "Zaria")


def test_x49_validate_lga_rejects_other_state(lookups):
    from institutions import services

    services.validate_lga(lookups.kaduna, "Zaria")
    services.validate_lga(lookups.kaduna, "zaria")  # case-insensitive
    with pytest.raises(ValidationError):
        services.validate_lga(lookups.kaduna, "Ikeja")


def test_x49_public_lga_rate_limited(lookups, anon, settings):
    from django.core.cache import cache

    from config.models import ConfigEntry

    cache.clear()
    ConfigEntry.objects.update_or_create(key="qa.public.lga_rate_per_minute", scope="global",
                                         defaults={"value": "3", "value_type": "int"})
    cache.clear()
    codes = [anon.get(f"/register/lgas/?state={lookups.kaduna.pk}").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_x49_register_page_has_real_lga_select(lookups, anon):
    from django.core.cache import cache

    cache.clear()
    html = anon.get("/register/").content.decode()
    assert '<select class="input" name="lga" id="rg-lga"' in html
    assert "/register/lgas/" in html
    assert '<optgroup label="Kaduna">' in html  # no-JS fallback: every LGA grouped by state
    assert 'list="qa-lga-options"' not in html
