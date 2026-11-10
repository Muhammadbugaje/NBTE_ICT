"""v2.1 Phase E (E.5: L10, T-CONS-3): announcements on every staff dashboard."""
import importlib

import pytest
from django.apps import apps as global_apps
from django.test import Client
from django.utils import timezone

from accounts.models import User
from announcements.models import Announcement

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_user():
    u = User.objects.create_user("ann.staff@t.org", "x-pass-12345", first_name="Ann", last_name="Staff")
    u.must_change_password = False
    u.save()
    return u


def _client(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


def test_l10_home_card_uses_announcements_app(staff_user):
    from announcements.templatetags.announcement_tags import panel_items
    from core.views import _module_cards  # noqa: F401  (exists)

    a = Announcement.objects.create(title="Durbar on Friday", body="Main hall.", published_at=timezone.now(),
                                    pinned=True)
    Announcement.objects.create(title="Older notice", body="x", published_at=timezone.now())
    items, unread = panel_items(staff_user)
    assert items[0] == a and unread == 2
    html = _client(staff_user).get("/home/").content.decode()
    assert "Durbar on Friday" in html and "ann-panel" in html
    assert "/announcements/" in html          # the module card links to the announcements app


def test_l10_panel_acknowledge_htmx(staff_user):
    a = Announcement.objects.create(title="Read the policy", body="x", requires_ack=True,
                                    kind=Announcement.ACTION_NEEDED, published_at=timezone.now())
    r = _client(staff_user).post(f"/announcements/{a.pk}/acknowledge/", {"view": "panel"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and "Acknowledged" in r.content.decode()


def test_t_cons_3_portal_staff_announcements_migrated(staff_user):
    from portal.models import Announcement as Old, AnnouncementCategory

    cat = AnnouncementCategory.objects.create(name="General T", slug="general-t")
    staff_note = Old.objects.create(title="Staff note", body="For staff", category=cat, author=staff_user,
                                    visibility="staff", is_pinned=True)
    public = Old.objects.create(title="Public note", body="For everyone", category=cat, author=staff_user,
                                visibility="public")
    mig = importlib.import_module("announcements.migrations.0002_e5_migrate_portal_announcements")
    mig.forwards(global_apps, None)
    new = Announcement.objects.get(title="Staff note")
    assert new.audience == Announcement.ALL_STAFF and new.pinned and new.published_at
    staff_note.refresh_from_db()
    public.refresh_from_db()
    assert staff_note.archived_at is not None and public.archived_at is None
    assert not Announcement.objects.filter(title="Public note").exists()
