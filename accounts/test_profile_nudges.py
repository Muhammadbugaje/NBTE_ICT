"""Phase 8: profile completeness nudges (banner, reminders, interrupt log)."""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

import config
from accounts import profile_nudges
from accounts.models import User
from config.models import ConfigEntry
from core.models import AuditLogEntry
from notifications.models import Notification

pytestmark = pytest.mark.django_db


def _set(key, value, vtype="int"):
    ConfigEntry.objects.update_or_create(key=key, scope="global", defaults={"value": str(value), "value_type": vtype})
    from django.core.cache import cache
    cache.clear()


def _u(email, **kw):
    return User.objects.create_user(email, "Pass-word-123", must_change_password=False, **kw)


def test_incomplete_user_gets_status_with_missing_fields():
    u = _u("p1@x.org")
    st = profile_nudges.status(u)
    assert not st["complete"] and st["percent"] < st["threshold"]
    assert "Profile photo" in st["missing"]
    assert st["missing"][0] == "Profile photo"  # heaviest first


def test_threshold_setting_is_respected():
    u = _u("p2@x.org")
    _set("profile.completion_threshold", 0)
    assert profile_nudges.status(u)["complete"]


def test_reminder_is_sent_once_per_interval():
    u = _u("p3@x.org")
    _set("profile.reminder_days", 7)
    n = profile_nudges.maybe_send_reminder(u)
    assert n is not None and n.metadata["kind"] == "profile_reminder"
    assert profile_nudges.maybe_send_reminder(u) is None
    later = timezone.now() + timedelta(days=8)
    assert profile_nudges.maybe_send_reminder(u, now=later) is not None


def test_reminders_can_be_turned_off_and_skip_complete_profiles():
    u = _u("p4@x.org")
    _set("profile.reminder_days", 0)
    assert profile_nudges.maybe_send_reminder(u) is None
    _set("profile.reminder_days", 7)
    _set("profile.completion_threshold", 0)
    assert profile_nudges.maybe_send_reminder(u) is None


def test_command_sends_to_incomplete_staff_only():
    _u("p5@x.org")
    _u("p6@x.org", is_active=False)
    _set("profile.reminder_days", 3)
    call_command("send_profile_reminders", verbosity=0)
    assert Notification.objects.filter(metadata__kind="profile_reminder", recipient__email="p5@x.org").count() == 1
    assert not Notification.objects.filter(recipient__email="p6@x.org").exists()
    call_command("send_profile_reminders", verbosity=0)
    assert Notification.objects.filter(metadata__kind="profile_reminder").count() == 1


def test_banner_and_interrupt_data_render_for_incomplete_user():
    u = _u("p7@x.org")
    c = Client()
    c.force_login(u)
    html = c.get(reverse("tickets:list")).content.decode()
    assert "data-profile-banner" in html and 'id="profile-nudge-data"' in html
    assert "/tickets/new/" in html  # gated paths shipped to the interrupt script


def test_no_banner_when_complete_or_disabled():
    u = _u("p8@x.org")
    c = Client()
    c.force_login(u)
    _set("profile.banner_enabled", 0)
    html = c.get(reverse("tickets:list")).content.decode()
    assert "data-profile-banner" not in html and "profile-nudge-data" in html
    _set("profile.completion_threshold", 0)
    assert "profile-nudge-data" not in c.get(reverse("tickets:list")).content.decode()


def test_continue_anyway_is_logged():
    u = _u("p9@x.org")
    c = Client()
    c.force_login(u)
    r = c.post(reverse("accounts:profile_nudge_continue"), {"target": "/tickets/new/"})
    assert r.status_code == 204
    entry = AuditLogEntry.objects.get(note__startswith="continued with an incomplete profile")
    assert entry.actor == u and entry.changes["target"] == "/tickets/new/"
    assert c.get(reverse("accounts:profile_nudge_continue")).status_code == 405


def test_gated_paths_cover_request_starts():
    paths = profile_nudges.gated_paths()
    for p in ("/tickets/new/", "/workflow/new/", "/tasks/new/", "/messages/memos/new/", "/qa/institutions/actions/"):
        assert p in paths
