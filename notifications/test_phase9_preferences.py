"""Phase 9: notification preferences, quiet hours and should_deliver()."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from accounts.models import User
from notifications.models import Notification, NotificationPreference
from notifications.preferences import (
    CATEGORY_TO_KIND,
    KIND_FIELDS,
    get_preferences,
    in_quiet_hours,
    kind_for,
    save_preferences,
    should_deliver,
)

pytestmark = pytest.mark.django_db
LAGOS = ZoneInfo("Africa/Lagos")


@pytest.fixture
def user(db):
    return User.objects.create_user("pref@nbte.test", "Pass-word-123", must_change_password=False)


def at(hour, minute=0):
    return dt.datetime(2026, 10, 21, hour, minute, tzinfo=LAGOS)


def test_defaults_without_a_row(user):
    pref = get_preferences(user)
    assert pref.pk is None and pref.email_digest and not pref.quiet_hours_enabled
    assert pref.quiet_from == dt.time(22, 0) and pref.quiet_to == dt.time(6, 30)
    assert pref.timezone == "Africa/Lagos" and pref.show_previews == "always" and pref.transcribe_voice_notes
    assert not NotificationPreference.objects.exists()  # reading never writes
    for kind in KIND_FIELDS:
        assert should_deliver(user, kind) and should_deliver(user, kind, channel="email")


def test_every_category_maps_to_a_kind():
    for value, _label in Notification.CATEGORY_CHOICES:
        assert kind_for(value) in KIND_FIELDS
    assert set(CATEGORY_TO_KIND.values()) <= set(KIND_FIELDS)
    assert kind_for("ticket") == "tasks_tickets" and kind_for("qa") == "qa_cases"
    assert kind_for("message") == "chats" and kind_for("request") == "approvals_memos"
    assert kind_for("payments") == "payments" and kind_for("unknown-thing") == "general"


def test_inapp_is_always_delivered(user):
    save_preferences(user, {k: False for k in KIND_FIELDS} | {"email_digest": False, "quiet_hours_enabled": True})
    for kind in KIND_FIELDS:
        assert should_deliver(user, kind, channel="inapp", now=at(23))


def test_kind_switch_blocks_push_and_email(user):
    save_preferences(user, {"tasks_tickets": False})
    assert not should_deliver(user, "ticket")
    assert not should_deliver(user, "task", channel="email")
    assert should_deliver(user, "qa")


def test_email_digest_off_blocks_email_only(user):
    save_preferences(user, {"email_digest": False})
    assert not should_deliver(user, "qa_cases", channel="email")
    assert should_deliver(user, "qa_cases", channel="push")


@pytest.mark.parametrize("hour, minute, quiet", [
    (21, 59, False), (22, 0, True), (23, 30, True), (0, 0, True), (6, 29, True), (6, 30, False), (12, 0, False),
])
def test_quiet_hours_across_midnight(user, hour, minute, quiet):
    pref = save_preferences(user, {"quiet_hours_enabled": True})
    assert in_quiet_hours(pref, now=at(hour, minute)) is quiet


def test_quiet_hours_same_day_window_and_timezone(user):
    pref = save_preferences(user, {"quiet_hours_enabled": True, "quiet_from": dt.time(13, 0),
                                   "quiet_to": dt.time(14, 0), "timezone": "Europe/London"})
    # 13:30 in London (BST, UTC+1) is 13:30 Lagos (UTC+1) in October 2026 before DST ends
    assert in_quiet_hours(pref, now=dt.datetime(2026, 10, 21, 12, 30, tzinfo=dt.timezone.utc))
    assert not in_quiet_hours(pref, now=dt.datetime(2026, 10, 21, 14, 30, tzinfo=dt.timezone.utc))


def test_quiet_hours_hold_push_but_urgent_can_break_through(user):
    save_preferences(user, {"quiet_hours_enabled": True})
    assert not should_deliver(user, "chats", now=at(23))
    assert should_deliver(user, "chats", urgent=True, now=at(23))
    assert should_deliver(user, "chats", channel="email", now=at(23))  # e-mail is not held
    save_preferences(user, {"allow_urgent_during_quiet": False})
    assert not should_deliver(user, "chats", urgent=True, now=at(23))
    assert should_deliver(user, "chats", now=at(9))


def test_unknown_channel_raises(user):
    with pytest.raises(ValueError):
        should_deliver(user, "general", channel="sms")


def test_internal_settings_page_saves(user):
    c = Client()
    c.force_login(user)
    r = c.get(reverse("notifications:settings"))
    html = r.content.decode()
    assert r.status_code == 200 and "Quiet hours" in html and "sh-switch" in html and "Tasks and tickets" in html
    data = {"chats": "on", "payments": "on", "qa_cases": "on", "general": "on",  # tasks/approvals/announcements off
            "quiet_hours_enabled": "on", "quiet_from": "21:30", "quiet_to": "07:00",
            "show_previews": "never", "timezone": "Africa/Lagos"}
    r = c.post(reverse("notifications:settings"), data)
    assert r.status_code == 302
    pref = NotificationPreference.objects.get(user=user)
    assert not pref.tasks_tickets and not pref.approvals_memos and not pref.announcements and pref.chats
    assert not pref.email_digest and pref.quiet_hours_enabled and pref.quiet_from == dt.time(21, 30)
    assert not pref.allow_urgent_during_quiet and not pref.transcribe_voice_notes and pref.show_previews == "never"


def test_internal_settings_rejects_bad_timezone(user):
    c = Client()
    c.force_login(user)
    r = c.post(reverse("notifications:settings"), {"timezone": "Mars/Base", "quiet_from": "22:00",
                                                   "quiet_to": "06:30", "show_previews": "always"})
    assert r.status_code == 200 and not NotificationPreference.objects.exists()


def test_links_to_settings(user):
    c = Client()
    c.force_login(user)
    assert reverse("notifications:settings") in c.get(reverse("notifications:list")).content.decode()
    assert reverse("notifications:settings") in c.get(reverse("accounts:profile")).content.decode()


def test_portal_save_keeps_internal_only_kinds(db):
    from institutions.models import Institution
    from qa_core.hosts import portal_reverse
    from staff.models import Department

    dept, _ = Department.objects.get_or_create(code="dpap", defaults={"name": "DPAP"})
    inst = Institution.unscoped.create(code="PREF1", name="Pref Poly", handling_department=dept, status="approved")
    u = User.objects.create_user("reg@pref.test", "Pass-word-123", must_change_password=False,
                                 user_type="institution", institution=inst)
    host = settings.QA_PORTAL_HOST.split(":")[0]
    c = Client(HTTP_HOST=host)
    c.force_login(u)
    html = c.get("/account/").content.decode()
    assert "Quality assurance" in html and "Tasks and tickets" not in html
    r = c.post(portal_reverse("qa_portal:account_notifications"),
               {"qa_cases": "on", "email_digest": "on", "quiet_from": "22:00", "quiet_to": "06:30",
                "show_previews": "always", "timezone": "Africa/Lagos"})
    assert r.status_code == 302
    pref = NotificationPreference.objects.get(user=u)
    assert pref.qa_cases and not pref.payments and not pref.general
    assert pref.tasks_tickets and pref.approvals_memos  # not on the portal form: unchanged


def test_qa_email_respects_preference(db, monkeypatch):
    from qa_core import mail, notify

    sent = []
    monkeypatch.setattr(mail, "send", lambda subject, recipients, *a, **k: sent.append(list(recipients)))
    yes = User.objects.create_user("yes@nbte.test", "Pass-word-123")
    no = User.objects.create_user("no@nbte.test", "Pass-word-123")
    save_preferences(no, {"email_digest": False})
    notify._email("Subject", "<p>Body</p>", [yes, no])
    assert sent == [["yes@nbte.test"]]
    sent.clear()
    save_preferences(yes, {"qa_cases": False})
    notify._email("Subject", "<p>Body</p>", [yes, no])
    assert sent == []
