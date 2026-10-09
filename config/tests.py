import pytest
from django.core.exceptions import ValidationError

import config
from config.models import ConfigEntry

pytestmark = pytest.mark.django_db


def test_get_typed_default_and_cache_invalidation():
    assert config.get("tickets.max_open", 7) == 7  # missing -> default (and caches the miss)
    e = ConfigEntry.objects.create(key="tickets.max_open", value="5", value_type="int")
    assert config.get("tickets.max_open", 7) == 5  # write invalidated the cached miss
    e.value = "9"
    e.save()
    assert config.get("tickets.max_open") == 9  # no restart needed
    e.delete()
    assert config.get("tickets.max_open", 1) == 1


def test_falsy_values_are_cached_correctly(django_assert_num_queries):
    ConfigEntry.objects.create(key="flag", value="false", value_type="bool")
    ConfigEntry.objects.create(key="zero", value="0", value_type="int")
    assert config.get("flag", True) is False
    assert config.get("zero", 5) == 0
    with django_assert_num_queries(0):
        assert config.get("flag", True) is False
        assert config.get("zero", 5) == 0


def test_scope_override_falls_back_to_global():
    ConfigEntry.objects.create(key="sla.hours", value="24", value_type="int")
    ConfigEntry.objects.create(key="sla.hours", scope="office:ts", value="4", value_type="int")
    assert config.get("sla.hours", scope="office:ts") == 4
    assert config.get("sla.hours", scope="office:dev") == 24


def test_json_and_validation_and_secrets_rejected():
    ConfigEntry.objects.create(key="cats", value='["a","b"]', value_type="json")
    assert config.get("cats") == ["a", "b"]
    with pytest.raises(ValidationError):
        ConfigEntry(key="brevo.api_key", value="x").full_clean()
    with pytest.raises(ValidationError):
        ConfigEntry(key="n", value="abc", value_type="int").full_clean()
