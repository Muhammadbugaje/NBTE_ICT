"""manage.py check_email (nominal-roll follow-up)."""
import io

import pytest
from django.core.management import CommandError, call_command


@pytest.mark.django_db
def test_reports_missing_configuration(settings):
    settings.DEFAULT_FROM_EMAIL = "NBTE ICT <no-reply@localhost>"
    settings.EMAIL_PROVIDERS = {k: {**v, "OPTIONS": {**v.get("OPTIONS", {}), "username": "", "password": ""}}
                                for k, v in settings.EMAIL_PROVIDERS.items()}
    settings.ANYMAIL = {"BREVO_API_KEY": ""}
    out = io.StringIO()
    with pytest.raises(CommandError):
        call_command("check_email", stdout=out)
    text = out.getvalue()
    assert "DEFAULT_FROM_EMAIL" in text and "No provider is configured" in text


@pytest.mark.django_db
def test_send_through_engine(settings, monkeypatch):
    from notifications import engine
    settings.DEFAULT_FROM_EMAIL = "NBTE ICT <no-reply@nbte.gov.ng>"
    monkeypatch.setattr(engine, "provider_order", lambda category="general": ["gmail"])
    settings.EMAIL_PROVIDERS = {**settings.EMAIL_PROVIDERS,
                                "gmail": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend", "OPTIONS": {}}}
    from django.core import mail
    out = io.StringIO()
    call_command("check_email", "--send", "--to", "jabir@example.com", stdout=out)
    assert "Sent to jabir@example.com via gmail" in out.getvalue()
    assert mail.outbox[-1].to == ["jabir@example.com"] and mail.outbox[-1].from_email.endswith("@nbte.gov.ng>")
