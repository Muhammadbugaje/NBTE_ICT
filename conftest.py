import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _isolation(settings, tmp_path):
    """Every test gets its own media roots and an empty cache."""
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.PROTECTED_MEDIA_ROOT = tmp_path / "protected"
    cache.clear()
    yield
    cache.clear()
