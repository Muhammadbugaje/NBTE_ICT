import pytest

pytestmark = pytest.mark.django_db


def test_seeded_settings_fit_config_columns():
    from config.models import ConfigEntry
    from qa_core.settings_catalogue import SETTINGS

    entry = ConfigEntry.objects.get(key="qa.privacy.mode", scope="global")
    assert entry.value == "assignment"
    assert len(entry.description) <= ConfigEntry._meta.get_field("description").max_length
    bounded_fields = {
        "key": 0,
        "value_type": 2,
        "group": 3,
        "description": 4,
        "editable_by": 5,
    }
    for row in SETTINGS:
        for field_name, index in bounded_fields.items():
            limit = ConfigEntry._meta.get_field(field_name).max_length
            assert len(str(row[index] or "")) <= limit, (row[0], field_name, limit)
