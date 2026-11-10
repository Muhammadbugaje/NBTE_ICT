import pytest
from django.core.cache import cache


# Payment-provider keys from the developer's .env (real Paystack/Remita TEST
# keys) would switch the in-process simulator off and send tests to the live
# sandboxes. Tests never use the network: blank the keys (a test that needs
# them sets its own) and refuse any real outbound provider call.
PAYMENT_ENV_KEYS = ("PAYSTACK_SECRET_KEY", "PAYSTACK_PUBLIC_KEY", "REMITA_MERCHANT_ID", "REMITA_API_KEY",
                    "REMITA_API_TOKEN")


@pytest.fixture(autouse=True)
def _offline_payment_providers(settings, monkeypatch):
    import urllib.error
    import urllib.request

    for name in PAYMENT_ENV_KEYS:
        setattr(settings, name, "")

    def _no_network(*args, **kwargs):
        raise urllib.error.URLError(OSError("network access is disabled in tests"))

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    yield


@pytest.fixture(autouse=True)
def _offline_email(settings):
    """Tests never send real e-mail: the .env Brevo keys would otherwise make
    notifications.engine deliver through the live Brevo API / SMTP relay."""
    providers = {}
    for name, cfg in (getattr(settings, "EMAIL_PROVIDERS", None) or {}).items():
        providers[name] = {**cfg, "BACKEND": "django.core.mail.backends.locmem.EmailBackend", "OPTIONS": {}}
    settings.EMAIL_PROVIDERS = providers
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    yield


@pytest.fixture(autouse=True)
def _isolation(settings, tmp_path):
    """Every test gets its own media roots and an empty cache."""
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.PROTECTED_MEDIA_ROOT = tmp_path / "protected"
    cache.clear()
    from qa_core.departments import clear_cache
    clear_cache()
    yield
    cache.clear()
    clear_cache()
