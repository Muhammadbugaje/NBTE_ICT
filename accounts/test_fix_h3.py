"""Regression tests for review finding H3 (user management)."""
import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department, Staff

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def _grant(user, *codes):
    from django.contrib.auth.models import Permission
    from accounts.models import UserPermissionOverride
    for code in codes:
        app, codename = code.split(".")
        UserPermissionOverride.objects.create(
            user=user, effect="grant",
            permission=Permission.objects.get(codename=codename, content_type__app_label=app))


@pytest.fixture
def hr(ict, roles):
    u = mk("hr3@x.org", ict, roles["admin-staff"])
    _grant(u, "accounts.view_user", "accounts.change_user")
    return u


def test_cannot_reset_superuser(client, hr):
    boss = mk("boss3@x.org", is_superuser=True)
    client.force_login(hr)
    resp = client.post(reverse("accounts:user_reset_password", args=[boss.pk]),
                       {"action": "set", "password": "Taken0ver!!x"})
    assert resp.status_code in (403, 404)  # 404 once M7 scopes the user list
    boss.refresh_from_db()
    assert not boss.check_password("Taken0ver!!x")


def test_cannot_reset_higher_rank(client, hr, ict, roles):
    director = mk("dir3@x.org", ict, roles["director"])
    client.force_login(hr)
    assert client.post(reverse("accounts:user_reset_password", args=[director.pk]),
                       {"action": "set", "password": "Taken0ver!!x"}).status_code == 403


def test_cannot_deactivate_superuser(client, hr):
    boss = mk("boss3b@x.org", is_superuser=True)
    client.force_login(hr)
    assert client.post(reverse("accounts:user_toggle_active", args=[boss.pk])).status_code in (403, 404)
    boss.refresh_from_db()
    assert boss.is_active


def test_can_reset_junior_but_password_never_flashed(client, hr, ict, roles):
    junior = mk("jr3@x.org", ict, roles["staff"])
    client.force_login(hr)
    resp = client.post(reverse("accounts:user_reset_password", args=[junior.pk]),
                       {"action": "set", "password": "Fresh-Pass-2026", "force_change": "on"},
                       follow=True)
    junior.refresh_from_db()
    assert junior.check_password("Fresh-Pass-2026")
    assert b"Fresh-Pass-2026" not in resp.content
    assert "Fresh-Pass-2026" not in str(client.cookies)


def test_weak_password_rejected(client, hr, ict, roles):
    junior = mk("jr3b@x.org", ict, roles["staff"])
    client.force_login(hr)
    client.post(reverse("accounts:user_reset_password", args=[junior.pk]),
                {"action": "set", "password": "password"})
    junior.refresh_from_db()
    assert not junior.check_password("password")


def test_generated_password_requires_email(client, hr, ict, roles):
    junior = mk("jr3c@x.org", ict, roles["staff"])
    client.force_login(hr)
    old_hash = junior.password
    client.post(reverse("accounts:user_reset_password", args=[junior.pk]), {"action": "generate", "send_email": "off"})
    junior.refresh_from_db()
    assert junior.password == old_hash


def test_can_act_on_user_helper(ict, roles):
    from accounts.permissions import can_act_on_user
    su = mk("su3@x.org", is_superuser=True)
    staff = mk("s3@x.org", ict, roles["staff"])
    director = mk("d3@x.org", ict, roles["director"])
    assert can_act_on_user(su, director)
    assert can_act_on_user(director, staff)
    assert not can_act_on_user(staff, director)
    assert not can_act_on_user(director, su)
