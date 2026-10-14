import pytest
from django.core import mail
from django.core.mail.backends.base import BaseEmailBackend
from django.test import Client, override_settings
from unittest import mock

import config
from accounts.models import Role, User
from config.models import ConfigEntry
from django.contrib.auth.models import Permission
from notifications import engine
from notifications.api import queue_email
from notifications.models import EmailLog, EmailQuotaTracker
from notifications.tasks import MAX_RETRIES

pytestmark = pytest.mark.django_db


class FailingBackend(BaseEmailBackend):
    def send_messages(self, messages):
        raise ConnectionError("provider down")


class ZeroSentBackend(BaseEmailBackend):
    def send_messages(self, messages):
        return 0


class Tagging(BaseEmailBackend):
    """Records which provider handled a message, via a class attribute."""
    sent = []
    name = "?"

    def send_messages(self, messages):
        for m in messages:
            Tagging.sent.append((self.name, m.to))
        return len(messages)


class BrevoTag(Tagging):
    name = "brevo"


class GmailTag(Tagging):
    name = "gmail"


@pytest.fixture
def providers(settings):
    Tagging.sent = []
    settings.EMAIL_PROVIDERS = {
        "brevo": {"BACKEND": "notifications.tests.BrevoTag"},
        "gmail": {"BACKEND": "notifications.tests.GmailTag"},
    }
    return Tagging


def send(*args, **kwargs):
    """Existing failover tests assume the Brevo-first order, i.e. a critical category."""
    kwargs.setdefault("category", "credentials")
    return engine.send(*args, **kwargs)


def _set(key, value, vtype="str"):
    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": str(value), "value_type": vtype})


def test_default_sends_via_brevo_and_counts_quota(providers):
    assert send("s", ["a@x.org"], "<p>x</p>") == "brevo"
    assert engine.used_today("brevo") == 1 and engine.used_today("gmail") == 0


def test_fails_over_to_gmail_when_brevo_quota_exhausted(providers):
    _set("email.brevo_daily_limit", 2, "int")
    used = [send("s", [f"{i}@x.org"], "<p>x</p>") for i in range(4)]
    assert used == ["brevo", "brevo", "gmail", "gmail"]
    assert engine.used_today("brevo") == 2 and engine.used_today("gmail") == 2


def test_multi_recipient_send_counts_each_recipient_and_respects_limit(providers):
    _set("email.brevo_daily_limit", 3, "int")
    assert send("s", ["a@x.org", "b@x.org"], "<p>x</p>") == "brevo"      # 2 of 3
    assert send("s", ["c@x.org", "d@x.org"], "<p>x</p>") == "gmail"      # would be 4 > 3
    assert engine.used_today("brevo") == 2


def test_fails_over_on_provider_error_and_releases_quota(providers, settings):
    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.FailingBackend"}
    assert send("s", ["a@x.org"], "<p>x</p>") == "gmail"
    assert engine.used_today("brevo") == 0  # failed send did not burn quota
    assert engine.used_today("gmail") == 1


def test_fails_over_when_provider_reports_zero_messages_and_releases_quota(providers, settings):
    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.ZeroSentBackend"}
    trail = []

    assert send("s", ["a@x.org"], "<p>x</p>", trail=trail) == "gmail"
    assert engine.used_today("brevo") == 0
    assert engine.used_today("gmail") == 1
    assert trail[0]["ok"] is False
    assert "returned 0 sent messages" in trail[0]["error"]


def test_manual_override_pins_gmail_first(providers):
    _set("email.provider_override", "gmail")
    assert send("s", ["a@x.org"], "<p>x</p>") == "gmail"
    _set("email.provider_override", "auto")  # cache invalidated on save -> immediate
    assert send("s", ["a@x.org"], "<p>x</p>") == "brevo"


def test_override_still_fails_over_if_pinned_provider_is_down(providers, settings):
    settings.EMAIL_PROVIDERS["gmail"] = {"BACKEND": "notifications.tests.FailingBackend"}
    _set("email.provider_override", "gmail")
    assert send("s", ["a@x.org"], "<p>x</p>") == "brevo"


def test_all_exhausted_raises_quota_exhausted(providers):
    _set("email.brevo_daily_limit", 0, "int")
    _set("email.gmail_daily_limit", 0, "int")
    with pytest.raises(engine.QuotaExhausted):
        send("s", ["a@x.org"], "<p>x</p>")


def test_all_failing_raises_all_providers_failed(providers, settings):
    settings.EMAIL_PROVIDERS = {p: {"BACKEND": "notifications.tests.FailingBackend"} for p in ("brevo", "gmail")}
    with pytest.raises(engine.AllProvidersFailed):
        send("s", ["a@x.org"], "<p>x</p>")


def test_no_configured_provider_fails_clearly_without_retrying_backends(settings):
    settings.ANYMAIL = {"BREVO_API_KEY": ""}
    settings.EMAIL_PROVIDERS = {
        "brevo": {"BACKEND": "anymail.backends.brevo.EmailBackend", "OPTIONS": {}},
        "gmail": {
            "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "OPTIONS": {
                "host": "smtp.gmail.com",
                "username": "staff@example.org",
                "password": "",
            },
        },
    }

    assert engine.provider_order("general") == []
    with pytest.raises(engine.NoEmailProviderConfigured, match="No email provider is configured"):
        engine.send("s", ["a@x.org"], "<p>x</p>")
    assert EmailQuotaTracker.objects.count() == 0


def test_console_backend_is_not_treated_as_a_delivery_provider(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    settings.EMAIL_PROVIDERS = {"brevo": {}, "gmail": {}}

    assert engine.provider_order("general") == []
    with pytest.raises(engine.NoEmailProviderConfigured):
        engine.send("s", ["a@x.org"], "<p>x</p>")


def test_quota_row_is_per_day(providers):
    from datetime import timedelta
    send("s", ["a@x.org"], "<p>x</p>")
    EmailQuotaTracker.objects.update(date=engine.today() - timedelta(days=1))  # "yesterday"
    assert engine.used_today("brevo") == 0  # new day, fresh counter


def test_queue_email_defers_until_commit_and_log_has_no_body(django_capture_on_commit_callbacks, providers):
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        log = queue_email("Hi", ["a@x.org"], "emails/credentials.html",
                          {"user": None, "username": "u", "temp_password": "SECRET-PW", "site_name": "S", "site_url": ""},
                          category="credentials")
    assert len(callbacks) == 1 and Tagging.sent == []  # nothing sent in the "request"
    assert log.status == EmailLog.QUEUED
    for cb in callbacks:
        cb()
    log.refresh_from_db()
    assert (log.status, log.provider, log.attempts) == ("sent", "brevo", 1)
    assert "SECRET-PW" not in repr(log.__dict__)  # body never stored


def test_delivery_error_diagnostics_redact_provider_secrets(settings):
    settings.ANYMAIL = {"BREVO_API_KEY": "test-brevo-secret"}
    settings.EMAIL_PROVIDERS["gmail"]["OPTIONS"]["password"] = "test-gmail-secret"

    message = engine.safe_error_message(
        RuntimeError("test-brevo-secret test-gmail-secret rejected")
    )

    assert "test-brevo-secret" not in message
    assert "test-gmail-secret" not in message
    assert "[redacted]" in message


def test_task_marks_failed_after_retries_when_nothing_can_send(providers, settings, django_capture_on_commit_callbacks):
    settings.EMAIL_PROVIDERS = {p: {"BACKEND": "notifications.tests.FailingBackend"} for p in ("brevo", "gmail")}
    with django_capture_on_commit_callbacks(execute=True):
        log = queue_email("Hi", ["a@x.org"], "emails/password_reset.html", {"user": None, "reset_url": "u"})
    log.refresh_from_db()
    assert log.status == "failed" and log.attempts == MAX_RETRIES + 1
    assert "provider down" in log.last_error


def test_send_templated_email_never_raises(django_capture_on_commit_callbacks):
    from core.email_utils import send_templated_email
    with mock.patch("notifications.api.queue_email", side_effect=RuntimeError("boom")):
        assert send_templated_email("s", ["a@x.org"], "emails/credentials.html") is None


# ---------------- password reset ----------------
@override_settings(EMAIL_PROVIDERS={
    "brevo": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend", "OPTIONS": {}},
    "gmail": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend", "OPTIONS": {}},
})
def test_password_reset_full_flow_clears_force_change(django_capture_on_commit_callbacks):
    import re
    u = User.objects.create_user("r@x.org", "Old-Passw0rd-1", first_name="R")  # must_change_password=True
    c = Client()
    with django_capture_on_commit_callbacks(execute=True):
        r = c.post("/accounts/password-reset/", {"email": "r@x.org"})
    assert r.status_code == 302 and r["Location"].endswith("/accounts/password-reset/done/")
    assert len(mail.outbox) == 1
    link = re.search(r"https?://[^\s\"<]+/accounts/reset/[^\s\"<]+", mail.outbox[0].body).group(0)
    path = "/" + link.split("/", 3)[3]
    r = c.get(path, follow=True)                       # token URL redirects to the set-password form
    assert r.status_code == 200 and b"Choose a new password" in r.content
    post_url = r.request["PATH_INFO"]
    r = c.post(post_url, {"new_password1": "Fresh-Passw0rd-77", "new_password2": "Fresh-Passw0rd-77"})
    assert r.status_code == 302
    u.refresh_from_db()
    assert u.check_password("Fresh-Passw0rd-77") and u.must_change_password is False
    assert c.get(path).status_code == 200 and b"Link not valid" in c.get(path, follow=True).content  # single use


def test_password_reset_unknown_email_reveals_nothing(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        r = Client().post("/accounts/password-reset/", {"email": "nobody@x.org"})
    assert r.status_code == 302 and r["Location"].endswith("/accounts/password-reset/done/")
    assert mail.outbox == []


def test_password_reset_ignores_inactive_users(django_capture_on_commit_callbacks):
    User.objects.create_user("i@x.org", "Old-Passw0rd-1", is_active=False)
    with django_capture_on_commit_callbacks(execute=True):
        Client().post("/accounts/password-reset/", {"email": "i@x.org"})
    assert mail.outbox == []


# ---------------- settings UI ----------------
def _admin_client(perms=("change_configentry", "view_configentry")):
    role = Role.objects.create(code="cfg", name="Cfg")
    role.permissions.set(Permission.objects.filter(codename__in=perms))
    u = User.objects.create_user("cfg@x.org", "Pass-word-123", role=role, must_change_password=False)
    c = Client()
    c.force_login(u)
    return c, u


def test_settings_page_requires_permission():
    u = User.objects.create_user("n@x.org", "Pass-word-123", must_change_password=False)
    c = Client()
    c.force_login(u)
    assert c.get("/settings/").status_code == 403
    assert Client().get("/settings/").status_code == 302


def test_settings_edit_takes_effect_immediately_and_validates():
    from django.core.management import call_command
    call_command("seed_config")
    c, _ = _admin_client()
    page = c.get("/settings/")
    assert page.status_code == 200 and b"email.brevo_daily_limit" in page.content
    entry = ConfigEntry.objects.get(key="email.brevo_daily_limit")
    assert config.get("email.brevo_daily_limit") == 300          # warms the cache
    c.post(f"/settings/{entry.pk}/save/", {"value": "42"})
    assert config.get("email.brevo_daily_limit") == 42           # no restart, cache invalidated
    c.post(f"/settings/{entry.pk}/save/", {"value": "abc"})
    assert config.get("email.brevo_daily_limit") == 42           # invalid value rejected
    assert c.post(f"/settings/{entry.pk}/save/", {"value": "abc"}, follow=True).content.count(b"valid") >= 1


def test_settings_test_email_reports_provider(providers):
    from notifications.models import EmailLog

    c, u = _admin_client()
    r = c.post("/settings/test-email/", follow=True)
    assert b"accepted by brevo" in r.content
    assert Tagging.sent[-1] == ("brevo", [u.email])
    log = EmailLog.objects.get(category="test", user=u)
    assert (log.status, log.provider, log.attempts) == ("sent", "brevo", 1)
    assert log.attempt_trail[0]["provider"] == "brevo"
    assert b"Recent test sends" in c.get("/settings/").content


def test_settings_test_email_logs_brevo_failure_and_gmail_fallback(providers, settings):
    from notifications.models import EmailLog

    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.FailingBackend"}
    c, u = _admin_client()

    r = c.post("/settings/test-email/", follow=True)

    assert b"accepted by gmail" in r.content
    log = EmailLog.objects.get(category="test", user=u)
    assert log.provider == "gmail"
    assert [(item["provider"], item["ok"]) for item in log.attempt_trail] == [
        ("brevo", False), ("gmail", True),
    ]
    assert b"brevo: failed" in c.get("/settings/").content


def test_health_public_minimal_and_detailed_for_admins():
    public = Client().get("/health/").json()
    assert public == {"status": "ok"}
    c, _ = _admin_client()
    detail = c.get("/health/").json()
    assert detail["checks"]["database"] == "ok" and "used_percent" in detail["disk"]
