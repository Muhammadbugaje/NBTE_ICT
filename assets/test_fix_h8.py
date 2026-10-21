"""Regression tests for review finding H8 (asset permissions)."""
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
from assets.models import Asset, AssetCategory


@pytest.fixture
def asset(ict):
    cat = AssetCategory.objects.create(name="Laptop8", code="laptop8")
    return Asset.objects.create(asset_tag="ICT-LAP-00008", category=cat,
                                owner_department=ict, state="in_service")


def test_general_staff_cannot_change_state(client, ict, roles, asset):
    holder = mk("gs8@x.org", ict, roles["staff"])
    asset.current_custodian = holder     # can see it (holds it) but may not change its state
    asset.save()
    client.force_login(holder)
    assert client.post(reverse("assets:set_state", args=[asset.asset_tag]),
                       {"state": "retired", "reason": "x"}).status_code == 403
    asset.refresh_from_db()
    assert asset.state == "in_service"


def test_general_staff_cannot_create(client, ict, roles):
    client.force_login(mk("gs8b@x.org", ict, roles["staff"]))
    assert client.get(reverse("assets:create")).status_code == 403


def test_view_only_access_all_cannot_edit(ict, roles, asset):
    from assets.views import _can_edit_asset
    deputy = mk("dep8@x.org", ict, roles["deputy-director"])  # access_all, view only
    assert not _can_edit_asset(User.objects.get(pk=deputy.pk), asset)


def test_manager_in_department_can_edit(ict, roles, asset):
    from assets.views import _can_edit_asset
    director = mk("dir8@x.org", ict, roles["director"])
    assert _can_edit_asset(User.objects.get(pk=director.pk), asset)


def test_change_asset_holder_limited_to_own_department(roles, asset):
    from django.contrib.auth.models import Permission
    from accounts.models import UserPermissionOverride
    from assets.views import _can_edit_asset
    fin = Department.objects.create(name="Finance8", code="finance8")
    u = mk("fin8@x.org", fin, roles["staff"])
    UserPermissionOverride.objects.create(
        user=u, effect="grant",
        permission=Permission.objects.get(codename="change_asset", content_type__app_label="assets"))
    assert not _can_edit_asset(User.objects.get(pk=u.pk), asset)
