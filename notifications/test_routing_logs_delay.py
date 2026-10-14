from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from django.test import Client

import config
from accounts.models import Role, User
from config.models import ConfigEntry
from notifications import api, engine
from notifications.api import queue_email
from notifications.models import EmailLog, EmailSendSlot
from notifications.tests import FailingBackend, Tagging, providers  # noqa: F401  (fixture re-export)

pytestmark = pytest.mark.django_db


def _set(key, value, vtype="str"):
    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": str(value), "value_type": vtype})


# ---------------- routing by category (same idea as NAMETS) ----------------
@pytest.mark.parametrize("category,first", [
    ("credentials", "brevo"), ("reset", "brevo"), ("task", "brevo"), ("approval", "brevo"),
    ("general", "brevo"), ("announcement", "brevo"),
])
def test_all_categories_try_brevo_first(providers, category, first):
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category=category) == first


def test_general_mail_falls_back_to_gmail_when_brevo_fails(providers, settings):
    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.FailingBackend"}
    trail = []
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="general", trail=trail) == "gmail"
    assert [(item["provider"], item["ok"]) for item in trail] == [
        ("brevo", False), ("gmail", True),
    ]


def test_synchronous_mail_adapter_uses_the_shared_brevo_first_engine(providers):
    from core.emails import send as send_sync

    provider = send_sync(
        subject="Workflow update",
        to="a@x.org",
        text_body="Status updated",
        html_body="<p>Status updated</p>",
        reply_to="reply@x.org",
    )

    assert provider == "brevo"
    assert Tagging.sent[-1] == ("brevo", ["a@x.org"])


def test_quota_still_applies_to_critical_mail_no_bypass(providers):
    _set("email.brevo_daily_limit", 0, "int")
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="credentials") == "gmail"  # not sent over the limit


def test_general_mail_falls_back_to_brevo_when_gmail_is_exhausted(providers):
    _set("email.gmail_daily_limit", 0, "int")
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="general") == "brevo"


def test_legacy_critical_category_list_does_not_change_provider_order(providers):
    _set("email.critical_categories", '["newsletter"]', "json")
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="newsletter") == "brevo"
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="credentials") == "brevo"


def test_manual_override_pins_first_choice_for_any_category_and_still_fails_over(providers, settings):
    _set("email.provider_override", "brevo")
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="general") == "brevo"      # pinned over category order
    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.FailingBackend"}
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="general") == "gmail"      # pinned one failed -> other tried


# ---------------- the trail ----------------
def test_trail_records_every_provider_tried(providers, settings):
    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.FailingBackend"}
    trail = []
    assert engine.send("s", ["a@x.org"], "<p>x</p>", category="credentials", trail=trail) == "gmail"
    assert [(t["provider"], t["ok"]) for t in trail] == [("brevo", False), ("gmail", True)]
    assert "provider down" in trail[0]["error"]


def test_trail_notes_quota_skips(providers):
    _set("email.brevo_daily_limit", 0, "int")
    trail = []
    engine.send("s", ["a@x.org"], "<p>x</p>", category="credentials", trail=trail)
    assert trail[0]["provider"] == "brevo" and "daily limit" in trail[0]["error"]


# ---------------- email log ----------------
def _ctx():
    return {"user": None, "username": "u", "temp_password": "SECRET-PW", "site_name": "S", "site_url": "",
            "reset_url": "http://x/reset"}


def test_log_links_user_stores_recipient_provider_and_failed_attempts(providers, settings, django_capture_on_commit_callbacks):
    u = User.objects.create_user("Person@X.org", "Pass-word-123", first_name="Per", last_name="Son")
    settings.EMAIL_PROVIDERS["brevo"] = {"BACKEND": "notifications.tests.FailingBackend"}
    with django_capture_on_commit_callbacks(execute=True):
        log = queue_email("Welcome", ["person@x.org"], "emails/password_reset.html", _ctx(), category="task")
    log.refresh_from_db()
    assert log.user == u and log.recipient_text == "person@x.org"
    assert (log.status, log.provider) == ("sent", "gmail")
    assert [(t["provider"], t["ok"], t["attempt"]) for t in log.attempt_trail] == [("brevo", False, 1), ("gmail", True, 1)]
    assert log.sent_at is not None and log.scheduled_for is not None


def test_body_stored_for_normal_mail_but_never_for_sensitive(django_capture_on_commit_callbacks, providers):
    with django_capture_on_commit_callbacks(execute=True):
        normal = queue_email("Notice", ["a@x.org"], "emails/password_reset.html", _ctx(), category="task")
        secret = queue_email("Creds", ["a@x.org"], "emails/credentials.html", _ctx(), category="credentials")
        reset = queue_email("Reset", ["a@x.org"], "emails/password_reset.html", _ctx(), category="reset")
    assert "reset" in normal.body_html.lower()
    assert secret.body_html == "" and reset.body_html == ""
    assert "SECRET-PW" not in str(list(EmailLog.objects.values_list("body_html", "subject", "last_error")))


def test_multi_recipient_log_has_no_user_but_all_addresses(django_capture_on_commit_callbacks, providers):
    User.objects.create_user("a@x.org", "Pass-word-123")
    with django_capture_on_commit_callbacks(execute=True):
        log = queue_email("Hi", ["a@x.org", "b@x.org"], "emails/password_reset.html", _ctx())
    assert log.user is None and log.recipient_text == "a@x.org, b@x.org"


def test_failed_email_log_shows_each_attempt_and_error(providers, settings, django_capture_on_commit_callbacks):
    settings.EMAIL_PROVIDERS = {p: {"BACKEND": "notifications.tests.FailingBackend"} for p in ("brevo", "gmail")}
    with django_capture_on_commit_callbacks(execute=True):
        log = queue_email("Hi", ["a@x.org"], "emails/password_reset.html", _ctx(), category="task")
    log.refresh_from_db()
    assert log.status == "failed"
    assert len(log.attempt_trail) == 2 * log.attempts and not any(t["ok"] for t in log.attempt_trail)


def _superuser_client():
    su = User.objects.create_superuser("su@x.org", "Pass-word-123")
    c = Client()
    c.force_login(su)
    return c


def test_admin_can_search_log_by_email_and_by_user_name_and_view_detail(providers, django_capture_on_commit_callbacks):
    User.objects.create_user("ada@x.org", "Pass-word-123", first_name="Ada", last_name="Lovelace")
    with django_capture_on_commit_callbacks(execute=True):
        queue_email("Hello Ada", ["ada@x.org"], "emails/password_reset.html", _ctx(), category="task")
        queue_email("Other", ["zed@x.org"], "emails/password_reset.html", _ctx(), category="task")
        queue_email("Creds", ["ada@x.org"], "emails/credentials.html", _ctx(), category="credentials")
    c = _superuser_client()
    by_email = c.get("/admin/notifications/emaillog/?q=zed@x.org").content
    assert b"Other" in by_email and b"Hello Ada" not in by_email
    by_name = c.get("/admin/notifications/emaillog/?q=Lovelace").content
    assert b"Hello Ada" in by_name and b"Other" not in by_name
    assert c.get("/admin/notifications/emaillog/?status=sent&provider=gmail").status_code == 200
    log = EmailLog.objects.get(subject="Hello Ada")
    detail = c.get(f"/admin/notifications/emaillog/{log.pk}/change/").content
    assert b"#1 brevo - OK" in detail
    creds = EmailLog.objects.get(subject="Creds")
    assert b"not stored" in c.get(f"/admin/notifications/emaillog/{creds.pk}/change/").content


def test_user_page_shows_that_users_email_history(providers, django_capture_on_commit_callbacks):
    u = User.objects.create_user("ada@x.org", "Pass-word-123")
    with django_capture_on_commit_callbacks(execute=True):
        queue_email("Hello Ada", ["ada@x.org"], "emails/password_reset.html", _ctx(), category="task")
    page = _superuser_client().get(f"/admin/accounts/user/{u.pk}/change/").content
    assert b"Hello Ada" in page


# ---------------- slight delay / spacing ----------------
T = None


@pytest.fixture
def frozen(settings):
    from django.utils import timezone
    base = timezone.now()
    box = {"now": base}
    with mock.patch.object(api.timezone, "now", side_effect=lambda: box["now"]):
        yield box


def test_emails_are_spaced_out_with_initial_delay(frozen, providers):
    d = [api._next_slot()[1] for _ in range(4)]
    assert d == [10, 15, 20, 25]                      # defaults: 10s first, then 5s apart


def test_spacing_comes_from_config(frozen, providers):
    _set("email.initial_delay_seconds", 0, "int")
    _set("email.min_gap_seconds", 2, "int")
    assert [api._next_slot()[1] for _ in range(3)] == [0, 2, 4]


def test_gap_resets_after_a_quiet_period(frozen, providers):
    api._next_slot(); api._next_slot()
    frozen["now"] += timedelta(minutes=10)
    assert api._next_slot()[1] == 10                  # burst is long over: only the initial delay


def test_queue_hands_countdown_to_celery(frozen, providers, django_capture_on_commit_callbacks):
    with mock.patch("notifications.api.send_email_task.apply_async") as aa:
        with django_capture_on_commit_callbacks(execute=True):
            queue_email("A", ["a@x.org"], "emails/password_reset.html", _ctx())
            queue_email("B", ["b@x.org"], "emails/password_reset.html", _ctx())
    assert [c.kwargs["countdown"] for c in aa.call_args_list] == [10, 15]
    first, second = EmailLog.objects.order_by("id")
    assert second.scheduled_for - first.scheduled_for == timedelta(seconds=5)


def test_slot_row_is_a_singleton(frozen, providers):
    for _ in range(3):
        api._next_slot()
    assert EmailSendSlot.objects.count() == 1
