"""Regression test for review finding M5 (fresh-install roles)."""
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


def test_fresh_install_gives_es_role_permissions():
    from django.core.management import call_command
    es = Role.objects.get(code="executive-secretary")  # created bare by migration 0007
    # (Migrations such as attendance/0005 and assets/0004 already attach a few
    # permissions to the ES role at migrate time, so it is not literally empty.)
    call_command("seed_roles", verbosity=0)  # no --sync, as in a first install
    assert es.permissions.filter(codename="view_all_requests").exists()


def test_seed_without_sync_does_not_readd_removed_permission():
    from django.core.management import call_command
    call_command("seed_roles", verbosity=0)
    director = Role.objects.get(code="director")
    # Directors carry the department-scoped permission; "view all" is ES / super admin only.
    assert not director.permissions.filter(codename="view_all_requests").exists()
    perm = director.permissions.get(codename="view_department_requests")
    director.permissions.remove(perm)
    call_command("seed_roles", verbosity=0)
    assert not director.permissions.filter(codename="view_department_requests").exists()
